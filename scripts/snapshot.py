"""Archives FPL data the public API doesn't keep: a daily price/transfer snapshot for every player,
and each finished gameweek's per-player stats (the API wipes the whole season at the summer reset).

Run daily by .github/workflows/snapshot.yml, after FPL's ~1:30am UK price update. Safe to re-run:
a day's snapshot file is simply overwritten, and gameweeks already on disk are skipped.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils import season_name  # noqa: E402

BASE_URL = "https://fantasy.premierleague.com/api"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
UK = ZoneInfo("Europe/London")

PRICE_COLS = [
    "id",
    "code",
    "web_name",
    "team",
    "element_type",
    "now_cost",
    "cost_change_event",
    "cost_change_start",
    "selected_by_percent",
    "transfers_in_event",
    "transfers_out_event",
    "transfers_in",
    "transfers_out",
    "status",
    "chance_of_playing_next_round",
    "news",
]

session = requests.Session()
session.headers.update({"User-Agent": "fpl-dashboard/1.0"})


def get(path: str) -> dict:
    resp = session.get(f"{BASE_URL}{path}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def save_prices(bootstrap: dict, season: str) -> Path:
    now = datetime.now(UK)
    snap = pd.DataFrame(bootstrap["elements"])[PRICE_COLS]
    snap.insert(0, "date", now.date().isoformat())
    snap.insert(1, "captured_at", now.isoformat(timespec="seconds"))

    path = DATA_DIR / "prices" / season / f"{now.date().isoformat()}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    snap.to_csv(path, index=False)
    return path


def save_gameweeks(bootstrap: dict, season: str) -> list[Path]:
    """One CSV per finished gameweek; keyed by `code` too, since player ids are renumbered each season."""
    id_to = {e["id"]: e for e in bootstrap["elements"]}
    out_dir = DATA_DIR / "gameweeks" / season
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for ev in bootstrap["events"]:
        path = out_dir / f"gw{ev['id']:02d}.csv"
        if not (ev["finished"] and ev["data_checked"]) or path.exists():
            continue
        rows = []
        for el in get(f"/event/{ev['id']}/live/")["elements"]:
            player = id_to.get(el["id"], {})
            rows.append(
                {
                    "id": el["id"],
                    "code": player.get("code"),
                    "web_name": player.get("web_name"),
                    "team": player.get("team"),
                    "element_type": player.get("element_type"),
                    "fixtures": ";".join(str(x["fixture"]) for x in el.get("explain", [])),
                    **el["stats"],
                }
            )
        pd.DataFrame(rows).to_csv(path, index=False)
        saved.append(path)
    return saved


def main() -> int:
    bootstrap = get("/bootstrap-static/")
    season = season_name(bootstrap)
    print(f"Saved prices to {save_prices(bootstrap, season)}")
    for path in save_gameweeks(bootstrap, season):
        print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
