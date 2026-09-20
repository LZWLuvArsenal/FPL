"""Thin client for the public Fantasy Premier League API."""
from __future__ import annotations

import requests
import streamlit as st

BASE_URL = "https://fantasy.premierleague.com/api"

_session = requests.Session()
_session.headers.update({"User-Agent": "fpl-dashboard/1.0"})


def _get(path: str, **params) -> dict | list:
    resp = _session.get(f"{BASE_URL}{path}", params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


@st.cache_data(ttl=3600)
def get_bootstrap_static() -> dict:
    """Players, teams, gameweeks - the reference data almost everything else joins against."""
    return _get("/bootstrap-static/")


@st.cache_data(ttl=3600)
def get_fixtures() -> list[dict]:
    return _get("/fixtures/")


@st.cache_data(ttl=3600)
def get_element_summary(element_id: int) -> dict:
    return _get(f"/element-summary/{element_id}/")


@st.cache_data(ttl=300)
def get_event_live(event: int) -> dict:
    return _get(f"/event/{event}/live/")


@st.cache_data(ttl=300)
def get_entry(team_id: int) -> dict:
    return _get(f"/entry/{team_id}/")


@st.cache_data(ttl=300)
def get_entry_history(team_id: int) -> dict:
    return _get(f"/entry/{team_id}/history/")


@st.cache_data(ttl=300)
def get_entry_transfers(team_id: int) -> list[dict]:
    return _get(f"/entry/{team_id}/transfers/")


@st.cache_data(ttl=300)
def get_entry_picks(team_id: int, event: int) -> dict:
    return _get(f"/entry/{team_id}/event/{event}/picks/")


@st.cache_data(ttl=300)
def get_league_standings(league_id: int, page: int = 1) -> dict:
    return _get(f"/leagues-classic/{league_id}/standings/", page_standings=page)


@st.cache_data(ttl=3600)
def get_set_piece_notes() -> dict:
    """FPL's own editorial notes on penalty/free-kick/corner order per team. Free text, not
    structured per-player frequency stats — and often placeholder text early in the season."""
    return _get("/team/set-piece-notes/")
