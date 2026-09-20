import html

import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_set_piece_notes

st.set_page_config(page_title="Set Piece Takers - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Set Piece Takers")
st.caption(
    "Who takes penalties, direct free kicks, and corners / indirect free kicks for each club, in FPL's own "
    "pecking order (1 = first choice). It's a ranking maintained by FPL's editors, not a count of how often "
    "each player has taken them, so check the news before a big move."
)

bootstrap = get_bootstrap_static()
team_names = {t["id"]: t["name"] for t in bootstrap["teams"]}
team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

CATEGORIES = [
    ("penalties_order", "🥅 Penalties"),
    ("direct_freekicks_order", "🎯 Direct free kicks"),
    ("corners_and_indirect_freekicks_order", "🚩 Corners & indirect FKs"),
]
STATUS_ICONS = {"d": "⚠️", "i": "🩹", "s": "🟥", "u": "❌", "n": "❌"}

takers: dict[int, dict[str, list[dict]]] = {tid: {key: [] for key, _ in CATEGORIES} for tid in team_names}
for e in bootstrap["elements"]:
    for key, _ in CATEGORIES:
        if e.get(key):
            takers[e["team"]][key].append(e)

team_choice = st.selectbox("Team", ["All teams"] + sorted(team_names.values()))
shown_ids = [tid for tid, name in sorted(team_names.items(), key=lambda x: x[1]) if team_choice in ("All teams", name)]


def _taker_cell(players_for_role: list[dict], key: str) -> str:
    if not players_for_role:
        return '<span style="opacity:0.5;">—</span>'
    lines = []
    for p in sorted(players_for_role, key=lambda p: p[key]):
        first = p[key] == 1
        icon = STATUS_ICONS.get(p["status"], "")
        tip = html.escape(p["news"] or "")
        weight = "700" if first else "400"
        opacity = "1" if first else "0.8"
        lines.append(
            f'<div style="font-weight:{weight}; opacity:{opacity}; white-space:nowrap;" title="{tip}">'
            f'<span style="opacity:0.55; display:inline-block; width:1.1em;">{p[key]}</span>'
            f'{html.escape(p["web_name"])} {icon}</div>'
        )
    return "".join(lines)


header = "".join(
    f'<th style="text-align:left; padding:8px 10px; font-size:0.78rem; opacity:0.7;">{label}</th>'
    for label in ["Team"] + [label for _, label in CATEGORIES]
)
rows = []
for tid in shown_ids:
    cells = "".join(
        f'<td style="padding:8px 10px; vertical-align:top;">{_taker_cell(takers[tid][key], key)}</td>' for key, _ in CATEGORIES
    )
    rows.append(
        '<tr style="border-top:1px solid rgba(128,128,128,0.2);">'
        f'<td style="padding:8px 10px; vertical-align:top; font-weight:700; white-space:nowrap;">'
        f'{html.escape(team_names[tid])} <span style="opacity:0.55; font-weight:400;">({team_short[tid]})</span></td>'
        f"{cells}</tr>"
    )
st.markdown(
    '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.88rem;">'
    f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>",
    unsafe_allow_html=True,
)
st.caption("⚠️ doubtful · 🩹 injured · 🟥 suspended · ❌ unavailable — hover a name for the latest news.")

# --- FPL's separate editorial notes (often just a placeholder early in the season) ---
data = get_set_piece_notes()
PLACEHOLDER = "check back for additional notes soon"
published = []
for team in data["teams"]:
    notes = [n["info_message"] for n in team["notes"] if n["info_message"].strip()]
    if notes and not all(PLACEHOLDER in n.lower() for n in notes) and team["id"] in shown_ids:
        published.append((team["id"], notes))

if published:
    st.subheader("Editorial notes")
    st.caption(f"Short updates from FPL's editors. Last updated: {data['last_updated']}")
    for team_id, notes in sorted(published, key=lambda x: team_names[x[0]]):
        with st.expander(f"{team_names[team_id]} ({team_short[team_id]})", expanded=True):
            for note in notes:
                st.markdown(f"- {note}")
