"""Unofficial client for understat.com — used only for multi-season player-vs-opponent history,
which the official FPL API does not expose (element-summary only tags opponents for the current
season). Understat is not affiliated with the Premier League or FPL; this hits the same internal
AJAX endpoints their own site's JS uses, so it can break if they change their backend.
"""
from __future__ import annotations

import difflib
import re
from datetime import date
from urllib.parse import quote

import requests
import streamlit as st

BASE_URL = "https://understat.com"

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (fpl-dashboard; personal research use)",
        "X-Requested-With": "XMLHttpRequest",
    }
)

# Understat spells a handful of clubs differently to the FPL API's short/colloquial names.
TEAM_NAME_ALIASES = {
    "man utd": "Manchester United",
    "man city": "Manchester City",
    "spurs": "Tottenham",
    "wolves": "Wolverhampton Wanderers",
    "nott'm forest": "Nottingham Forest",
    "west brom": "West Bromwich Albion",
    "sheffield utd": "Sheffield United",
    "newcastle": "Newcastle United",
}


def understat_team_name(fpl_name: str) -> str:
    return TEAM_NAME_ALIASES.get(fpl_name.strip().lower(), fpl_name)


@st.cache_data(ttl=6 * 3600)
def search_players(query: str) -> list[dict]:
    """Name search understat's own site uses for autocomplete. Returns [{id, player, team}, ...]."""
    resp = _session.get(f"{BASE_URL}/main/getPlayersName/{quote(query)}", timeout=15)
    resp.raise_for_status()
    data = resp.json().get("response", {})
    return data.get("players", []) if data.get("success") else []


@st.cache_data(ttl=24 * 3600)
def get_player_matches(understat_id: str) -> list[dict]:
    """Full match-by-match history for a player, across every season Understat has for them."""
    resp = _session.post(f"{BASE_URL}/main/getPlayerMatches/{understat_id}", timeout=15)
    resp.raise_for_status()
    data = resp.json().get("response", {})
    return data.get("matches", []) if data.get("success") else []


def find_player_id(full_name: str, expected_team: str | None = None, web_name: str | None = None) -> str | None:
    """Search by name, preferring a result whose club matches the player's current team.

    Understat's search does a literal substring match server-side, not a fuzzy one — so it's brittle
    against FPL's full name field, which often includes extra surnames Understat doesn't use (e.g.
    FPL's "Moisés Caicedo Corozo" vs Understat's "Moisés Caicedo": searching "Corozo", the last word
    of the full name, finds nothing). FPL's own `web_name` is usually the exact surname Understat
    lists, so it's tried as a query too, and results from every query are pooled before picking.
    """
    queries = [full_name]
    if web_name and web_name not in queries:
        queries.append(web_name)
    last_word = full_name.split()[-1]
    if last_word not in queries:
        queries.append(last_word)

    pooled: dict[str, dict] = {}
    for q in queries:
        for r in search_players(q):
            pooled.setdefault(r["id"], r)
    if not pooled:
        return None

    results = list(pooled.values())
    if expected_team:
        for r in results:
            if r["team"].strip().lower() == expected_team.strip().lower():
                return r["id"]

    names = {r["player"]: r["id"] for r in results}
    close = difflib.get_close_matches(full_name, names.keys(), n=1, cutoff=0.4)
    if close:
        return names[close[0]]
    return results[0]["id"]


def _own_team(matches: list[dict], i: int, window: int = 3) -> str | None:
    """Understat's player matches don't say which side the player was on. Their own club is the one
    that also turns up in the games either side of this one, which holds across transfers too."""
    m = matches[i]
    sides = [m.get("h_team"), m.get("a_team")]
    nearby = matches[max(0, i - window):i] + matches[i + 1:i + 1 + window]
    counts = [sum(t in (n.get("h_team"), n.get("a_team")) for n in nearby) for t in sides]
    return sides[0] if counts[0] > counts[1] else sides[1] if counts[1] > counts[0] else None


def matches_vs_opponent(matches: list[dict], opponent_name: str) -> list[dict]:
    """Matches *against* the opponent — not ones where the player was playing for them."""
    ordered = sorted(matches, key=lambda m: m.get("date", ""))
    return [
        m
        for i, m in enumerate(ordered)
        if opponent_name in (m.get("h_team"), m.get("a_team")) and _own_team(ordered, i) != opponent_name
    ]


def format_season(season: str) -> str:
    return f"{season}/{str(int(season) + 1)[-2:]}"


@st.cache_data(ttl=3600)
def current_season(league: str = "EPL") -> str:
    """The season Understat's own league page defaults to — safer than computing one from
    today's date, since that can disagree with Understat by a season around the July/August
    turnover (pre-season vs. the previous season not yet archived)."""
    resp = _session.get(f"{BASE_URL}/league/{league}", timeout=15)
    resp.raise_for_status()
    match = re.search(r'value="(\d{4})"\s+selected', resp.text)
    if match:
        return match.group(1)
    return str(date.today().year if date.today().month >= 7 else date.today().year - 1)


@st.cache_data(ttl=3600)
def get_league_team_data(season: str, league: str = "EPL") -> dict:
    """Per-team, match-by-match season data: xG, xGA, PPDA, deep completions, xPTS, etc."""
    resp = _session.get(f"{BASE_URL}/getLeagueData/{league}/{season}", timeout=15)
    resp.raise_for_status()
    return resp.json()["teams"]


EARLIEST_SEASON = 2014


def seasons_since_earliest(current: str) -> list[str]:
    return [str(y) for y in range(EARLIEST_SEASON, int(current) + 1)]


@st.cache_data(ttl=6 * 3600)
def get_league_fixtures(season: str, league: str = "EPL") -> list[dict]:
    """Full season fixture list with results and opponent identity — the per-team 'history' from
    get_league_team_data only has aggregate match stats (xG, PPDA, ...), no opponent name, so
    head-to-head lookups need this instead."""
    resp = _session.get(f"{BASE_URL}/getLeagueData/{league}/{season}", timeout=15)
    resp.raise_for_status()
    return resp.json()["dates"]


def team_h2h_matches(fixtures: list[dict], team_a: str, team_b: str) -> list[dict]:
    """Every played meeting between two teams within one season's fixture list (0, 1, or 2 legs)."""
    return [
        f
        for f in fixtures
        if f.get("isResult") and {f["h"]["title"], f["a"]["title"]} == {team_a, team_b}
    ]


def aggregate_team_season(history: list[dict]) -> dict:
    ppda_att = sum(h["ppda"]["att"] for h in history)
    ppda_def = sum(h["ppda"]["def"] for h in history)
    oppda_att = sum(h["ppda_allowed"]["att"] for h in history)
    oppda_def = sum(h["ppda_allowed"]["def"] for h in history)
    return {
        "matches": len(history),
        "xg": sum(h["xG"] for h in history),
        "xga": sum(h["xGA"] for h in history),
        "npxg": sum(h["npxG"] for h in history),
        "npxga": sum(h["npxGA"] for h in history),
        "deep": sum(h["deep"] for h in history),
        "deep_allowed": sum(h["deep_allowed"] for h in history),
        "ppda": ppda_att / ppda_def if ppda_def else None,
        "ppda_allowed": oppda_att / oppda_def if oppda_def else None,
        "xpts": sum(h["xpts"] for h in history),
    }
