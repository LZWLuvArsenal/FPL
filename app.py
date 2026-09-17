from datetime import datetime, timezone

import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static
from src.utils import current_event, next_event

st.set_page_config(page_title="FPL Dashboard", page_icon="⚽", layout="wide")

render_sidebar_settings()

st.title("⚽ FPL Dashboard")

bootstrap = get_bootstrap_static()
event_id = current_event(bootstrap)
event = next(e for e in bootstrap["events"] if e["id"] == event_id)
status = "Final" if event["finished"] and event["data_checked"] else "In Progress"

col1, col2, col3 = st.columns(3)
col1.metric(f"{event['name']} ({status})", event.get("highest_score", "-"), help="Highest score this gameweek")
col2.metric("Average Score", event.get("average_entry_score", "-"))

next_id = next_event(bootstrap)
if next_id:
    next_gw = next(e for e in bootstrap["events"] if e["id"] == next_id)
    deadline = datetime.fromisoformat(next_gw["deadline_time"].replace("Z", "+00:00"))
    remaining = deadline - datetime.now(timezone.utc)
    days, hours = remaining.days, remaining.seconds // 3600
    col3.metric(f"{next_gw['name']} Deadline", deadline.strftime("%a %d %b, %H:%M UTC"), f"{days}d {hours}h left")
else:
    col3.metric("Next Deadline", "Season complete")

st.markdown(
    """
Use the sidebar to set your **Team ID** and **Mini-League ID**, then explore the pages:

- **My Team** — your squad, points history, rank progression, chips used
- **League Standings** — your mini-league table
- **Player Explorer** — filter and sort every PL player
- **Fixture Planner** — upcoming fixture difficulty by team
- **Team Stats** — xG/xGC by team, finishing and defensive over/under-performance
- **Gameweek Insights** — chip usage, transfer volume, most captained/selected by GW
- **Price Changes** — today's risers/fallers and season-to-date price movement
- **League Explorer** — any mini-league member's squad, captain, and transfers
- **Recommendations** — attacking/clean-sheet picks and differentials for the next gameweek
"""
)
