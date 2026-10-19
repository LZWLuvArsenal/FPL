"""Saves the Recommendations page's single-gameweek optimal squad for the Squad Tracker, so it gets
recorded every gameweek without anyone having to click 'Save' in time.

Run every few hours by .github/workflows/autosave.yml; it only saves once the next deadline is within
SAVE_WINDOW and that gameweek isn't already on disk, so most runs exit after one API call. Output:
data/tracker/<season>/gwNN.json (read by src/tracker.py alongside the local squad_tracker.json).

    python scripts/autosave_squad.py                # scheduled use
    python scripts/autosave_squad.py --force        # save the next GW now, whatever the deadline
    python scripts/autosave_squad.py --rebuild 7 --prices data/prices/2026-27/2026-10-16.csv
        # reconstruct a missed GW from archived data: stats from data/gameweeks (GWs before it),
        # prices/injury flags from the given pre-deadline price snapshot
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.squad_model import (  # noqa: E402
    DEFAULT_FORM_WINDOW,
    add_mins_per_game,
    fixtures_by_team,
    optimal_gw_snapshot,
    recent_stats,
)
from src.utils import next_event, players_df, season_name, teams_df  # noqa: E402

BASE_URL = "https://fantasy.premierleague.com/api"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SAVE_WINDOW = timedelta(hours=24)
# No team value is available to a scheduled job, so auto-saves use the standard starting budget.
BUDGET = 100.0

session = requests.Session()
session.headers.update({"User-Agent": "fpl-dashboard/1.0"})
_live_cache: dict[int, dict] = {}


def get(path: str) -> dict | list:
    resp = session.get(f"{BASE_URL}{path}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_live(event: int) -> dict:
    if event not in _live_cache:
        _live_cache[event] = get(f"/event/{event}/live/")
    return _live_cache[event]


def rewind(bootstrap: dict, fixtures: list[dict], players: pd.DataFrame, gw: int, prices_csv: Path, season: str):
    """Rolls live data back to just before `gw`'s deadline: matches from `gw` on count as unplayed,
    season totals and per-90 rates are rebuilt from the archived earlier gameweeks only, and price,
    status and news come from the pre-deadline price snapshot."""
    fixtures = [
        {**f, "finished": False, "finished_provisional": False} if f["event"] and f["event"] >= gw else f
        for f in fixtures
    ]
    finished = [e["id"] for e in bootstrap["events"] if e["id"] < gw]

    gw_files = [DATA_DIR / "gameweeks" / season / f"gw{ev:02d}.csv" for ev in finished]
    missing = [p for p in gw_files if not p.exists()]
    if missing:
        raise SystemExit(f"Missing archived gameweeks: {', '.join(p.name for p in missing)}")
    totals_cols = [
        "minutes",
        "goals_scored",
        "goals_conceded",
        "expected_goals",
        "expected_assists",
        "expected_goal_involvements",
        "expected_goals_conceded",
    ]
    totals = (
        pd.concat(pd.read_csv(p, usecols=["id", *totals_cols]) for p in gw_files).groupby("id")[totals_cols].sum()
    )
    totals = totals.reindex(players["id"]).fillna(0)
    for col in totals_cols:
        players[col] = totals[col].to_numpy()
    mins = players["minutes"].replace(0, pd.NA)
    for col in ("expected_goals", "expected_assists", "expected_goal_involvements", "expected_goals_conceded"):
        players[f"{col}_per_90"] = (players[col] * 90 / mins).fillna(0).astype(float)

    prices = pd.read_csv(prices_csv).set_index("id")
    players = players[players["id"].isin(prices.index)].copy()
    snap = prices.loc[players["id"]]
    players["now_cost"] = snap["now_cost"].to_numpy()
    players["price"] = players["now_cost"] / 10
    players["status"] = snap["status"].to_numpy()
    players["news"] = snap["news"].fillna("").to_numpy()
    players["chance_of_playing_next_round"] = snap["chance_of_playing_next_round"].to_numpy()
    return fixtures, players, finished


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="save the next GW now, ignoring the deadline window")
    parser.add_argument("--rebuild", type=int, metavar="GW", help="reconstruct a past GW from archived data")
    parser.add_argument("--prices", type=Path, help="pre-deadline price snapshot CSV (with --rebuild)")
    args = parser.parse_args()
    if args.rebuild and not args.prices:
        parser.error("--rebuild needs --prices")

    bootstrap = get("/bootstrap-static/")
    season = season_name(bootstrap)

    if args.rebuild:
        gw = args.rebuild
    else:
        gw = next_event(bootstrap)
        if gw is None:
            print("Season's over — nothing to save.")
            return 0
        deadline = datetime.fromisoformat(
            next(e for e in bootstrap["events"] if e["id"] == gw)["deadline_time"].replace("Z", "+00:00")
        )
        until = deadline - datetime.now(timezone.utc)
        if not args.force and until > SAVE_WINDOW:
            print(f"GW{gw} deadline is {until} away — not saving yet.")
            return 0

    out = DATA_DIR / "tracker" / season / f"gw{gw:02d}.json"
    if out.exists():
        print(f"{out} already exists — skipping.")
        return 0

    fixtures = get("/fixtures/")
    players = players_df(bootstrap)
    team_short = dict(zip(teams_df(bootstrap)["id"], teams_df(bootstrap)["short_name"]))

    if args.rebuild:
        fixtures, players, finished = rewind(bootstrap, fixtures, players, gw, args.prices, season)
    else:
        finished = sorted(e["id"] for e in bootstrap["events"] if e["finished"])

    players = add_mins_per_game(players, fixtures, team_short)
    players, rates = recent_stats(players, fixtures, finished[-DEFAULT_FORM_WINDOW:], get_live, team_short)
    snapshot = optimal_gw_snapshot(
        players,
        fixtures_by_team(fixtures, {gw}),
        team_short,
        rates,
        gw,
        BUDGET,
        source="rebuilt" if args.rebuild else "auto",
    )
    if snapshot is None:
        print(f"No valid GW{gw} squad fits £{BUDGET}m.")
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2))
    print(f"Saved {out} (£{snapshot['total_cost']:.1f}m)")
    for p in snapshot["players"]:
        role = " (C)" if p["is_captain"] else " (VC)" if p["is_vice"] else "" if p["is_starter"] else " [bench]"
        print(f"  {p['position']} {p['name']} {p['team_short']}{role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
