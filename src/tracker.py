"""Local persistence for saved 'Recommended 15' squads, so their real-world performance can be
tracked gameweek by gameweek after the fact — a way to see whether the recommender's picks
actually delivered.
"""
from __future__ import annotations

import json
from pathlib import Path

TRACKER_PATH = Path(__file__).resolve().parent.parent / "squad_tracker.json"


def load_snapshots() -> list[dict]:
    if TRACKER_PATH.exists():
        return json.loads(TRACKER_PATH.read_text())
    return []


def save_snapshots(snapshots: list[dict]) -> None:
    TRACKER_PATH.write_text(json.dumps(snapshots, indent=2))


def add_snapshot(snapshot: dict) -> None:
    snapshots = load_snapshots()
    snapshots.append(snapshot)
    save_snapshots(snapshots)


def delete_snapshot(snapshot_id: str) -> None:
    snapshots = [s for s in load_snapshots() if s["id"] != snapshot_id]
    save_snapshots(snapshots)
