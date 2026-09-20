import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_set_piece_notes

st.set_page_config(page_title="Set Piece Notes - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Set Piece Takers")
st.caption(
    "FPL's own editorial notes on each team's penalty/free-kick/corner order — not a numeric "
    "frequency stat (the API doesn't expose one), just short text updates from their editors, "
    "so some teams may just say to check back later, especially early in the season."
)

bootstrap = get_bootstrap_static()
team_names = {t["id"]: t["name"] for t in bootstrap["teams"]}
team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

data = get_set_piece_notes()
st.caption(f"Last updated: {data['last_updated']}")

PLACEHOLDER = "check back for additional notes soon"

published, pending = [], []
for team in data["teams"]:
    notes = [n["info_message"] for n in team["notes"] if n["info_message"].strip()]
    is_pending = not notes or all(PLACEHOLDER in n.lower() for n in notes)
    (pending if is_pending else published).append((team["id"], notes))

if published:
    for team_id, notes in sorted(published, key=lambda x: team_names[x[0]]):
        with st.expander(f"{team_names[team_id]} ({team_short[team_id]})", expanded=True):
            for note in notes:
                st.markdown(f"- {note}")
else:
    st.info("No teams have published set piece notes yet this season.")

if pending:
    with st.expander(f"{len(pending)} team(s) with no notes published yet"):
        st.write(", ".join(team_names[tid] for tid, _ in sorted(pending, key=lambda x: team_names[x[0]])))
