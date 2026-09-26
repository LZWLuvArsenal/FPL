"""Entry point: registers the pages explicitly so the home page can be labelled "Home Page" in the sidebar
(with automatic pages/ discovery it would be named after this file). url_path keeps the old page URLs."""
import os
import sys

import streamlit as st


def _src_modules() -> list[str]:
    return [name for name in sys.modules if name == "src" or name.startswith("src.")]


def _record_src_mtimes() -> None:
    """Stamps each newly imported src/ module with its file's mtime, so a later change is detectable."""
    for name in _src_modules():
        module = sys.modules[name]
        path = getattr(module, "__file__", None)
        if path and os.path.exists(path):
            module.__dict__.setdefault("_loaded_mtime", os.path.getmtime(path))


def _drop_stale_src_modules() -> None:
    """Streamlit Cloud pulls new commits into the running app without restarting it. Pages re-run
    fresh, but src/ modules stay as first imported, so a page using something new in src/ fails with
    ImportError. If any src/ file changed since it was loaded, forget them all so they re-import."""
    mods = _src_modules()
    for name in mods:
        module = sys.modules[name]
        path = getattr(module, "__file__", None)
        loaded = module.__dict__.get("_loaded_mtime")
        if not path or not os.path.exists(path) or (loaded is not None and os.path.getmtime(path) != loaded):
            for stale in mods:
                del sys.modules[stale]
            return


_drop_stale_src_modules()

from src.config import is_owner  # noqa: E402

PAGES = [
    st.Page("home.py", title="Home Page", default=True),
    st.Page("pages/1_My_Team.py", title="My Team", url_path="My_Team"),
    st.Page("pages/3_Player_Explorer.py", title="Player Explorer", url_path="Player_Explorer"),
    st.Page("pages/4_Fixture_Planner.py", title="Fixture Planner", url_path="Fixture_Planner"),
    st.Page("pages/5_Team_Stats.py", title="Team Stats", url_path="Team_Stats"),
    st.Page("pages/6_Gameweek_Insights.py", title="Gameweek Insights", url_path="Gameweek_Insights"),
    st.Page("pages/7_Price_Changes.py", title="Price Changes", url_path="Price_Changes"),
    st.Page("pages/8_League_Explorer.py", title="League Explorer", url_path="League_Explorer"),
    st.Page("pages/9_Recommendations.py", title="Recommendations", url_path="Recommendations"),
    st.Page("pages/10_Head_to_Head.py", title="Head to Head", url_path="Head_to_Head"),
    st.Page("pages/11_Set_Piece_Notes.py", title="Set Piece Takers", url_path="Set_Piece_Notes"),
]
if is_owner():  # the squad tracker is private to the owner, so visitors don't even see it in the menu
    PAGES.append(st.Page("pages/12_Squad_Tracker.py", title="Squad Tracker", url_path="Squad_Tracker"))
PAGES.append(st.Page("pages/13_How_It_Works.py", title="How It Works", url_path="How_It_Works"))

try:
    st.navigation(PAGES).run()
finally:
    _record_src_mtimes()
