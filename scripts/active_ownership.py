"""Player ownership for two groups the FPL API doesn't publish figures for (its `selected_by_percent`
counts all ~11m teams, including the many abandoned ones):

- Active managers: made at least 1 transfer or played a chip in the last ACTIVE_WINDOW gameweeks
  (current one included). Estimated by sampling random team ids and keeping the active ones.
- Top 10k: every team in the top TOP_N of the overall league, by rank going into the gameweek.

Both tally each team's picks for the current gameweek.

Run by .github/workflows/snapshot.yml once the current gameweek's deadline has passed. A gameweek
already on disk is skipped, so the daily schedule only does the work once per gameweek.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import season_name  # noqa: E402

BASE_URL = "https://fantasy.premierleague.com/api"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ownership"
OVERALL_LEAGUE_ID = 314
TOP_N = 10_000
ACTIVE_WINDOW = 5
WORKERS = 6
BATCH = 300

session = requests.Session()
session.headers.update({"User-Agent": "fpl-dashboard/1.0"})


def get(path: str) -> dict | None:
    """None for teams that don't exist (deleted ids); retries rate limits and server errors."""
    for attempt in range(5):
        try:
            resp = session.get(f"{BASE_URL}{path}", timeout=30)
        except requests.RequestException:
            time.sleep(2**attempt)
            continue
        if resp.status_code == 404:
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2 ** (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    return None


def is_active(history: dict, gw: int) -> bool:
    first = gw - ACTIVE_WINDOW + 1
    if any(h["event_transfers"] > 0 for h in history["current"] if h["event"] >= first):
        return True
    return any(c["event"] >= first for c in history.get("chips", []))


def check_entry(entry_id: int, gw: int) -> tuple[bool, list[dict] | None]:
    """(team exists, active team's picks for gw or None)."""
    history = get(f"/entry/{entry_id}/history/")
    if history is None:
        return False, None
    if not is_active(history, gw):
        return True, None
    picks = get(f"/entry/{entry_id}/event/{gw}/picks/")
    return True, picks["picks"] if picks else None


def sample(gw: int, total_players: int, target: int, max_sampled: int) -> tuple[int, int, list[list[dict]]]:
    rng = random.Random()
    seen: set[int] = set()
    existing = 0
    active_picks: list[list[dict]] = []
    with ThreadPoolExecutor(WORKERS) as pool:
        while len(active_picks) < target and len(seen) < max_sampled:
            ids = []
            while len(ids) < BATCH:
                i = rng.randint(1, total_players)
                if i not in seen:
                    seen.add(i)
                    ids.append(i)
            for exists, picks in pool.map(lambda i: check_entry(i, gw), ids):
                existing += exists
                if picks:
                    active_picks.append(picks)
            print(f"  sampled {len(seen):,}, existing {existing:,}, active {len(active_picks):,}", flush=True)
    return len(seen), existing, active_picks


def top_entries(n: int) -> list[int]:
    pages = (n + 49) // 50
    with ThreadPoolExecutor(WORKERS) as pool:
        results = pool.map(lambda p: get(f"/leagues-classic/{OVERALL_LEAGUE_ID}/standings/?page_standings={p}"), range(1, pages + 1))
        return [r["entry"] for page in results if page for r in page["standings"]["results"]][:n]


def top_picks(gw: int) -> list[list[dict]]:
    entries = top_entries(TOP_N)
    print(f"  fetched {len(entries):,} top entries", flush=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        picks = pool.map(lambda e: get(f"/entry/{e}/event/{gw}/picks/"), entries)
        return [p["picks"] for p in picks if p]


def tally(all_picks: list[list[dict]]) -> dict[str, dict]:
    owners, starters, captains, multipliers = Counter(), Counter(), Counter(), Counter()
    for picks in all_picks:
        for p in picks:
            el = p["element"]
            owners[el] += 1
            starters[el] += p["multiplier"] > 0
            captains[el] += p["multiplier"] >= 2
            multipliers[el] += p["multiplier"]
    n = len(all_picks)
    return {
        str(el): {
            "owned_pct": round(owners[el] / n * 100, 2),
            "start_pct": round(starters[el] / n * 100, 2),
            "captain_pct": round(captains[el] / n * 100, 2),
            # Effective ownership: summed multipliers, so a captain counts 2 (3 if triple captained)
            # and a benched player 0 (1 under Bench Boost).
            "eo_pct": round(multipliers[el] / n * 100, 2),
        }
        for el in owners
    }


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1))
    print(f"Saved {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=2000, help="active teams to collect")
    parser.add_argument("--max-sampled", type=int, default=12000, help="give up after this many ids")
    parser.add_argument("--only", choices=["active", "top"], help="compute just one group")
    parser.add_argument("--force", action="store_true", help="recompute even if the file exists")
    args = parser.parse_args()

    bootstrap = get("/bootstrap-static/")
    current = next((e for e in bootstrap["events"] if e["is_current"]), None)
    if current is None:
        print("No gameweek has started yet.")
        return 0
    gw = current["id"]
    out_dir = DATA_DIR / season_name(bootstrap)
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731

    active_path = out_dir / f"gw{gw:02d}.json"
    if args.only in (None, "active") and (args.force or not active_path.exists()):
        print(f"Sampling active managers for GW{gw}...")
        sampled, existing, active_picks = sample(gw, bootstrap["total_players"], args.target, args.max_sampled)
        if active_picks:
            save(
                active_path,
                {
                    "gw": gw,
                    "generated_at": now(),
                    "active_window": ACTIVE_WINDOW,
                    "sampled_ids": sampled,
                    "existing_teams": existing,
                    "active_teams": len(active_picks),
                    "players": tally(active_picks),
                },
            )

    top_path = out_dir / f"top10k_gw{gw:02d}.json"
    if args.only in (None, "top") and (args.force or not top_path.exists()):
        print(f"Fetching the top {TOP_N:,} for GW{gw}...")
        picks = top_picks(gw)
        if picks:
            save(top_path, {"gw": gw, "generated_at": now(), "teams": len(picks), "players": tally(picks)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
