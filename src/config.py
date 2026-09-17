"""Persists the user's FPL team ID / league ID locally so they don't retype them every run."""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

CONFIG_PATH = Path(__file__).resolve().parent.parent / "user_config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def render_sidebar_settings() -> None:
    """Renders the Team ID / League ID inputs shared by every page, backed by session_state + disk."""
    if "config_loaded" not in st.session_state:
        saved = load_config()
        st.session_state["team_id"] = saved.get("team_id", "")
        st.session_state["league_id"] = saved.get("league_id", "")
        st.session_state["config_loaded"] = True

    with st.sidebar:
        st.header("Settings")
        team_id = st.text_input("Your FPL Team ID", value=st.session_state["team_id"])
        league_id = st.text_input("Mini-League ID", value=st.session_state["league_id"])
        if st.button("Save"):
            st.session_state["team_id"] = team_id
            st.session_state["league_id"] = league_id
            save_config({"team_id": team_id, "league_id": league_id})
            st.success("Saved")
        st.caption(
            "Find your Team ID in the URL when viewing 'Points' on the official FPL "
            "site (fantasy.premierleague.com/entry/**12345**/event/1). League ID is in "
            "the league's URL the same way."
        )
