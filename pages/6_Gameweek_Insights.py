import pandas as pd
import plotly.express as px
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static
from src.utils import current_event, players_df

st.set_page_config(page_title="Gameweek Insights - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Gameweek Insights")

CHIP_NAMES = {"bboost": "Bench Boost", "freehit": "Free Hit", "wildcard": "Wildcard", "3xc": "Triple Captain"}

bootstrap = get_bootstrap_static()
players = players_df(bootstrap).set_index("id")

played_events = [e for e in bootstrap["events"] if e["chip_plays"]]
if not played_events:
    st.info("No gameweek data available yet.")
    st.stop()

event_names = [e["name"] for e in played_events]
default_idx = next(
    (i for i, e in enumerate(played_events) if e["id"] == current_event(bootstrap)), len(played_events) - 1
)
chosen_name = st.selectbox("Gameweek", event_names, index=default_idx)
event = next(e for e in played_events if e["name"] == chosen_name)


def player_label(element_id):
    if element_id is None or element_id not in players.index:
        return "-"
    p = players.loc[element_id]
    return f"{p['full_name']} ({p['team_name']})"


col1, col2, col3 = st.columns(3)
col1.metric("Total Transfers Made", f"{event['transfers_made']:,}")
top_scorer = event.get("top_element_info")
col2.metric("Top Scorer", player_label(top_scorer["id"]) if top_scorer else "-", f"{top_scorer['points']} pts" if top_scorer else "")
col3.metric("Most Selected", player_label(event["most_selected"]))

col4, col5, col6 = st.columns(3)
col4.metric("Most Captained", player_label(event["most_captained"]))
col5.metric("Most Vice-Captained", player_label(event["most_vice_captained"]))
col6.metric("Most Transferred In", player_label(event["most_transferred_in"]))

st.subheader("Chips Played")
chips_df = pd.DataFrame(event["chip_plays"])
chips_df["chip_name"] = chips_df["chip_name"].map(CHIP_NAMES).fillna(chips_df["chip_name"])
chips_df = chips_df.sort_values("num_played", ascending=True)
fig = px.bar(chips_df, x="num_played", y="chip_name", orientation="h", labels={"num_played": "Times Played", "chip_name": ""})
st.plotly_chart(fig, width="stretch")

st.caption(
    "This is everything the public FPL API exposes at gameweek granularity. It does not publish a "
    "full captaincy distribution (only the single most-picked captain) or historical per-player "
    "transfer counts for past gameweeks — those aren't available through any official endpoint."
)
