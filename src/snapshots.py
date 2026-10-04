"""Reads the daily price snapshots that scripts/snapshot.py archives into data/prices/<season>/,
and the per-gameweek active-manager / top-10k ownership that scripts/active_ownership.py saves to data/ownership/.

Fetched from the GitHub repo first, so both the deployed app and a local copy see the latest
snapshot without a redeploy or `git pull`; falls back to the local file if GitHub is unreachable.
"""
from __future__ import annotations

import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

RAW_URL = "https://raw.githubusercontent.com/LZWLuvArsenal/FPL/main/data/prices"
LOCAL_DIR = Path(__file__).resolve().parent.parent / "data" / "prices"
OWNERSHIP_RAW_URL = "https://raw.githubusercontent.com/LZWLuvArsenal/FPL/main/data/ownership"
OWNERSHIP_LOCAL_DIR = Path(__file__).resolve().parent.parent / "data" / "ownership"
UK = ZoneInfo("Europe/London")


def uk_today() -> date:
    """FPL's price day runs on UK time — changes land around 1:30am UK."""
    return datetime.now(UK).date()


@st.cache_data(ttl=3600, show_spinner=False)
def load_price_snapshot(season: str, day: date) -> pd.DataFrame | None:
    name = f"{season}/{day.isoformat()}.csv"
    try:
        resp = requests.get(f"{RAW_URL}/{name}", timeout=10)
        if resp.status_code == 200:
            return pd.read_csv(io.StringIO(resp.text))
    except requests.RequestException:
        pass
    local = LOCAL_DIR / name
    return pd.read_csv(local) if local.exists() else None


def previous_snapshot(season: str, max_days_back: int = 7) -> tuple[date, pd.DataFrame] | None:
    """The most recent snapshot from before today (UK). It was taken after that day's price update,
    so live prices minus it = every change since, normally just today's."""
    today = uk_today()
    for back in range(1, max_days_back + 1):
        day = today - timedelta(days=back)
        snap = load_price_snapshot(season, day)
        if snap is not None:
            return day, snap
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def load_group_ownership(season: str, gw: int, group: str = "active") -> dict | None:
    """Ownership among active managers (group "active", sampled) or the top 10k ("top10k") for a
    gameweek, or None if it hasn't been computed."""
    prefix = "top10k_" if group == "top10k" else ""
    name = f"{season}/{prefix}gw{gw:02d}.json"
    try:
        resp = requests.get(f"{OWNERSHIP_RAW_URL}/{name}", timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        pass
    local = OWNERSHIP_LOCAL_DIR / name
    return json.loads(local.read_text()) if local.exists() else None
