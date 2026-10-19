"""Local persistence for saved 'Recommended 15' squads, so their real-world performance can be
tracked gameweek by gameweek after the fact — a way to see whether the recommender's picks
actually delivered.

Squads saved from the Recommendations page live in the gitignored squad_tracker.json. Squads the
scheduled job saves (scripts/autosave_squad.py) are committed under data/tracker/<season>/gwNN.json
instead, since GitHub Actions can't reach the local file; both are read back together.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACKER_PATH = ROOT / "squad_tracker.json"
AUTO_DIR = ROOT / "data" / "tracker"


def _load_local() -> list[dict]:
    if TRACKER_PATH.exists():
        return json.loads(TRACKER_PATH.read_text())
    return []


def _auto_files() -> dict[str, Path]:
    return {json.loads(p.read_text())["id"]: p for p in sorted(AUTO_DIR.glob("*/gw*.json"))}


def load_snapshots() -> list[dict]:
    return _load_local() + [json.loads(p.read_text()) for p in _auto_files().values()]


def save_snapshots(snapshots: list[dict]) -> None:
    TRACKER_PATH.write_text(json.dumps(snapshots, indent=2))


def add_snapshot(snapshot: dict) -> None:
    snapshots = _load_local()
    snapshots.append(snapshot)
    save_snapshots(snapshots)


def delete_snapshot(snapshot_id: str) -> None:
    auto = _auto_files().get(snapshot_id)
    if auto is not None:
        auto.unlink()
        return
    save_snapshots([s for s in _load_local() if s["id"] != snapshot_id])
