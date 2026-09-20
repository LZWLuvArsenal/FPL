import pandas as pd
import streamlit as st

from src.config import load_config, render_sidebar_settings, update_config
from src.fpl_api import get_entry, get_league_standings

st.set_page_config(page_title="League Standings - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Mini-League Standings")

team_id = st.session_state.get("team_id")
if not team_id:
    st.info("Enter your Team ID in the sidebar first — I'll look up which mini-leagues it belongs to.")
    st.stop()
try:
    team_id = int(team_id)
except ValueError:
    st.error("Team ID must be a number.")
    st.stop()

my_entry = get_entry(team_id)
private_leagues = [l for l in my_entry.get("leagues", {}).get("classic", []) if l["league_type"] == "x"]
if not private_leagues:
    st.warning(
        "No private mini-leagues found for this Team ID (only FPL's automatic leagues like "
        "'Overall' or your country league, which aren't shown here)."
    )
    st.stop()

league_options = {f"{l['name']} ({l['id']})": l["id"] for l in private_leagues}
labels = list(league_options.keys())
default_league_id = load_config().get("default_league_id")
default_idx = next((i for i, v in enumerate(league_options.values()) if v == default_league_id), 0)

select_col, save_col = st.columns([4, 1])
chosen_label = select_col.selectbox("Mini-League", labels, index=default_idx)
league_id = league_options[chosen_label]

save_col.markdown("<div style='margin-top:1.8rem'></div>", unsafe_allow_html=True)
if league_id == default_league_id:
    save_col.markdown("⭐ Default")
elif save_col.button("Set as default"):
    update_config(default_league_id=league_id)
    st.rerun()

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

highlight_mask = standings["entry"] == team_id


def highlight_row(row):
    return ["background-color: rgba(56, 0, 120, 0.15)" if highlight_mask.iloc[row.name] else "" for _ in row]


st.dataframe(display.style.apply(highlight_row, axis=1), hide_index=True, width="stretch")

if data["standings"]["has_next"]:
    st.caption("Showing page 1 only — this league has more than 50 teams.")
