import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_league_standings

st.set_page_config(page_title="League Standings - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Mini-League Standings")

league_id = st.session_state.get("league_id")
if not league_id:
    st.info("Enter your Mini-League ID in the sidebar to see standings.")
    st.stop()

try:
    league_id = int(league_id)
except ValueError:
    st.error("League ID must be a number.")
    st.stop()

data = get_league_standings(league_id)
st.subheader(data["league"]["name"])

standings = pd.DataFrame(data["standings"]["results"])
if standings.empty:
    st.warning("No standings found for this league.")
    st.stop()

standings["movement"] = standings["last_rank"] - standings["rank"]
display = standings[["rank", "entry_name", "player_name", "event_total", "total", "movement"]].rename(
    columns={
        "rank": "Rank",
        "entry_name": "Team",
        "player_name": "Manager",
        "event_total": "GW Points",
        "total": "Total Points",
        "movement": "Movement",
    }
)

my_team_id = st.session_state.get("team_id")
if my_team_id:
    try:
        my_team_id = int(my_team_id)
        highlight_mask = standings["entry"] == my_team_id

        def highlight_row(row):
            return ["background-color: rgba(56, 0, 120, 0.15)" if highlight_mask.iloc[row.name] else "" for _ in row]

        st.dataframe(display.style.apply(highlight_row, axis=1), hide_index=True, width="stretch")
    except ValueError:
        st.dataframe(display, hide_index=True, width="stretch")
else:
    st.dataframe(display, hide_index=True, width="stretch")

if data["standings"]["has_next"]:
    st.caption("Showing page 1 only — this league has more than 50 teams.")
