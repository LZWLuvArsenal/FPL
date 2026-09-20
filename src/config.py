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


def style_chart(fig):
    """Makes a Plotly figure readable on the dark theme: light text, transparent background and clearly
    visible gridlines. Returns the figure so it can be used inline."""
    grid = "rgba(255, 255, 255, 0.16)"
    fig.update_layout(font=dict(color="#F5F0F8", size=13), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    fig.update_xaxes(gridcolor=grid, zerolinecolor="rgba(255, 255, 255, 0.35)", linecolor=grid)
    fig.update_yaxes(gridcolor=grid, zerolinecolor="rgba(255, 255, 255, 0.35)", linecolor=grid)
    return fig


def require_owner() -> None:
    """Stops the page for anyone but the owner (used by pages backed by local-only files)."""
    if not is_owner():
        st.info("This page is only available to the app owner.")
        st.stop()


def inject_theme_css() -> None:
    """Premier League-inspired look (purple/green) on top of the dark theme in .streamlit/config.toml.
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
            letter-spacing: 0.01em;
        }
        h1 { font-size: 2.6rem !important; }
        h2, h3 { color: #00FF87 !important; }

        [data-testid="stAppViewContainer"] {
            background: radial-gradient(ellipse at top left, #3A0A55 0%, #170022 55%) fixed;
        }
        [data-testid="stHeader"] {
            background: rgba(23, 0, 34, 0.85);
            border-bottom: 3px solid #00FF87;
        }
        .block-container { padding-top: 3.5rem; max-width: 1300px; }

        /* Metric tiles as cards */
        [data-testid="stMetric"] {
            background: linear-gradient(160deg, #3B0D52 0%, #2A0A3D 100%);
            border: 1px solid #4A2266;
            border-top: 3px solid #00FF87;
            border-radius: 12px;
            padding: 14px 18px;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
        }
        [data-testid="stMetricLabel"] { opacity: 0.8; }
        [data-testid="stMetricValue"] { color: #FFFFFF; }

        /* Bordered containers double as cards (used for the home page grid) */
        [data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 12px !important;
            border-color: #4A2266 !important;
            background: rgba(42, 10, 61, 0.55);
            transition: border-color 0.15s, transform 0.15s;
        }
        [data-testid="stVerticalBlockBorderWrapper"]:hover { border-color: #00FF87 !important; }

        /* Tabs */
        button[data-baseweb="tab"] { font-weight: 600; }
        button[data-baseweb="tab"][aria-selected="true"] { color: #00FF87; }
        [data-baseweb="tab-highlight"] { background-color: #00FF87 !important; }

        /* Buttons */
        .stButton > button, [data-testid="stBaseButton-secondary"] {
            border-radius: 999px;
            border: 1px solid #00FF87;
            font-weight: 600;
        }
        .stButton > button:hover { background: #00FF87; color: #37003C; border-color: #00FF87; }

        /* Tables / expanders */
        [data-testid="stDataFrame"] { border: 1px solid #4A2266; border-radius: 10px; overflow: hidden; }
        [data-testid="stExpander"] { border-color: #4A2266 !important; border-radius: 10px !important; }

        /* Sidebar */
        [data-testid="stSidebar"] { border-right: 3px solid #00FF87; }
        [data-testid="stSidebarNav"] a[aria-current="page"] {
            background: rgba(0, 255, 135, 0.16);
            border-left: 3px solid #00FF87;
        }

        /* Home page hero */
        .fpl-hero {
            background: linear-gradient(120deg, #37003C 0%, #6A0DAD 55%, #00A86B 130%);
            border-radius: 16px;
            padding: 28px 32px;
            margin-bottom: 18px;
            border: 1px solid #5B2A7A;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
        }
        .fpl-hero h1 { margin: 0 0 4px 0 !important; padding: 0 !important; color: #FFFFFF; }
        .fpl-hero p { margin: 0; opacity: 0.85; font-size: 1.05rem; }
        .fpl-card-icon { font-size: 1.7rem; line-height: 1; margin-bottom: 4px; }
        p.fpl-card-title {
            font-family: 'Barlow Condensed', sans-serif !important; font-weight: 700 !important;
            font-size: 1.45rem !important; color: #FFFFFF; margin: 0 !important;
        }
        p.fpl-card-desc { opacity: 0.75; font-size: 0.92rem !important; min-height: 3.2em; margin: 2px 0 6px 0 !important; }

        @media (max-width: 640px) {
            .block-container { padding-left: 0.9rem; padding-right: 0.9rem; padding-top: 3rem; }
            h1 { font-size: 2rem !important; }
            .fpl-hero { padding: 18px; }
            [data-testid="stMetric"] { padding: 10px 12px; }
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
        st.caption("Unofficial fan project — not affiliated with the Premier League or Fantasy Premier League.")
