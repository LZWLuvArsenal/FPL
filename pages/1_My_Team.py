import pandas as pd
import plotly.express as px
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_entry, get_entry_history, get_entry_picks
from src.utils import current_event, players_df

st.set_page_config(page_title="My Team - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("My Team")

team_id = st.session_state.get("team_id")
if not team_id:
    st.info("Enter your Team ID in the sidebar to see your squad.")
    st.stop()

try:
    team_id = int(team_id)
except ValueError:
    st.error("Team ID must be a number.")
    st.stop()

entry = get_entry(team_id)
history = get_entry_history(team_id)
bootstrap = get_bootstrap_static()
players = players_df(bootstrap)

st.subheader(f"{entry['name']} — {entry['player_first_name']} {entry['player_last_name']}")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Overall Points", entry["summary_overall_points"])
col2.metric("Overall Rank", f"{entry['summary_overall_rank']:,}")
col3.metric("GW Points", entry["summary_event_points"])
col4.metric("Team Value", f"£{entry['last_deadline_value'] / 10:.1f}m")

season_history = pd.DataFrame(history["current"])
if not season_history.empty:
    st.subheader("Season Progression")
    tab1, tab2 = st.tabs(["Points per Gameweek", "Overall Rank"])
    with tab1:
        fig = px.bar(season_history, x="event", y="points", labels={"event": "Gameweek", "points": "Points"})
        st.plotly_chart(fig, width="stretch")
    with tab2:
        fig = px.line(
            season_history, x="event", y="overall_rank", labels={"event": "Gameweek", "overall_rank": "Overall Rank"}
        )
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, width="stretch")

chips = history.get("chips", [])
if chips:
    st.subheader("Chips Used")
    st.dataframe(pd.DataFrame(chips)[["name", "event"]], hide_index=True, width="stretch")

st.subheader("Current Squad")
event_id = current_event(bootstrap)
try:
    picks = get_entry_picks(team_id, event_id)
    picks_df = pd.DataFrame(picks["picks"]).merge(
        players[["id", "full_name", "team_name", "position", "price", "event_points", "total_points"]],
        left_on="element",
        right_on="id",
    )
    picks_df["role"] = picks_df.apply(
        lambda r: "Captain" if r["is_captain"] else ("Vice-Captain" if r["is_vice_captain"] else ""), axis=1
    )
    display_cols = [
        "full_name",
        "team_name",
        "position",
        "price",
        "multiplier",
        "role",
        "event_points",
        "total_points",
    ]
    st.dataframe(
        picks_df.sort_values("position")[display_cols],
        hide_index=True,
        width="stretch",
        column_config={"price": st.column_config.NumberColumn(format="£%.1f")},
    )
except Exception:
    st.warning(f"Picks for GW{event_id} aren't available yet.")
