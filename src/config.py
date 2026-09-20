"""Persists the user's FPL team ID / league ID locally so they don't retype them every run."""
from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

CONFIG_PATH = Path(__file__).resolve().parent.parent / "user_config.json"


def is_owner() -> bool:
    """True only when the app is launched by the owner (run_fpl.bat sets FPL_OWNER=1). A deployed
    copy never sets it, so visitors get per-session settings and can't reach the squad tracker."""
    return os.environ.get("FPL_OWNER") == "1"


def load_config() -> dict:
    if not is_owner():
        return st.session_state.setdefault("_visitor_config", {})
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config: dict) -> None:
    if not is_owner():
        st.session_state["_visitor_config"] = config
        return
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def update_config(**kwargs) -> None:
    """Merges into the saved config instead of overwriting it, so unrelated keys survive."""
    config = load_config()
    config.update(kwargs)
    save_config(config)


def require_owner() -> None:
    """Stops the page for anyone but the owner (used by pages backed by local-only files)."""
    if not is_owner():
        st.info("This page is only available to the app owner.")
        st.stop()


def inject_theme_css() -> None:
    """Premier League-inspired font on top of the purple/green palette in .streamlit/config.toml.
    Uses data-testid selectors (stable, documented) rather than Streamlit's auto-generated emotion-
    cache class names (which can change between Streamlit versions and silently stop matching)."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&family=Barlow+Condensed:wght@600;700&display=swap');

        html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"] {
            font-family: 'Barlow', sans-serif !important;
        }
        h1, h2, h3, [data-testid="stMetricValue"] {
            font-family: 'Barlow Condensed', sans-serif !important;
            font-weight: 700 !important;
        }
        [data-testid="stHeader"] {
            border-bottom: 3px solid #00FF87;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_settings() -> None:
    """Renders the Team ID / League ID inputs shared by every page, backed by session_state (+ disk for the owner)."""
    inject_theme_css()
    if "config_loaded" not in st.session_state:
        saved = load_config()
        st.session_state["team_id"] = saved.get("team_id") or st.query_params.get("team", "")
        st.session_state["config_loaded"] = True

    with st.sidebar:
        st.header("Settings")
        team_id = st.text_input("Your FPL Team ID", value=st.session_state["team_id"])
        if st.button("Save"):
            st.session_state["team_id"] = team_id
            update_config(team_id=team_id)
            if not is_owner():
                st.query_params["team"] = team_id  # bookmarkable link, since visitors have no saved config
            st.success("Saved")
        st.caption(
            "Find your Team ID in the URL when viewing 'Points' on the official FPL "
            "site (fantasy.premierleague.com/entry/**12345**/event/1). Your mini-leagues "
            "are looked up automatically from this Team ID."
        )
