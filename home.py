from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st

from src.config import is_owner, render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_entry
from src.utils import current_event, next_event

st.set_page_config(page_title="FPL Dashboard", page_icon="⚽", layout="wide")

render_sidebar_settings()

st.markdown(
    '<div class="fpl-hero"><h1>⚽ FPL Dashboard</h1>'
    "<p>Squads, leagues, fixtures and projections — everything for your next gameweek in one place.</p></div>",
    unsafe_allow_html=True,
)

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
    try:  # show the deadline in the viewer's browser timezone, falling back to UTC
        local_deadline = deadline.astimezone(ZoneInfo(st.context.timezone))
    except Exception:
        local_deadline = deadline
    tz_label = local_deadline.tzname() or "UTC"
    if tz_label[0] in "+-":  # zones without an abbreviation report "+08" — show that as "UTC+08"
        tz_label = f"UTC{tz_label}"
    col3.metric(
        f"{next_gw['name']} Deadline ({tz_label})", local_deadline.strftime("%a %d %b, %H:%M"), f"{days}d {hours}h left"
    )
else:
    col3.metric("Next Deadline", "Season complete")

# --- Your team at a glance (only once a Team ID has been entered) ---
team_id = st.session_state.get("team_id", "").strip()
if team_id.isdigit():
    try:
        entry = get_entry(int(team_id))
    except Exception:
        st.warning("Couldn't load that Team ID — double-check it in the sidebar.")
    else:
        st.subheader(f"Your team — {entry['name']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Overall Points", f"{entry['summary_overall_points']:,}")
        c2.metric("Overall Rank", f"{entry['summary_overall_rank']:,}")
        c3.metric("GW Points", entry["summary_event_points"])
        c4.metric("Squad Value", f"£{(entry['last_deadline_value'] + entry['last_deadline_bank']) / 10:.1f}m")
else:
    st.info("👈 Enter your **Team ID** in the sidebar and click Save to see your team here.")

# --- Page directory ---
st.subheader("Explore")

PAGES = [
    ("pages/1_My_Team.py", "🧑‍💼", "My Team", "Your squad, points history, rank progression and chips used."),
    ("pages/3_Player_Explorer.py", "🔎", "Player Explorer", "Filter and sort every Premier League player."),
    ("pages/4_Fixture_Planner.py", "📅", "Fixture Planner", "Upcoming fixture difficulty for every team."),
    ("pages/5_Team_Stats.py", "📊", "Team Stats", "xG / xGC by team, finishing and defensive over- and under-performance."),
    ("pages/6_Gameweek_Insights.py", "💡", "Gameweek Insights", "Chip usage, transfer volume, most captained and selected."),
    ("pages/7_Price_Changes.py", "💷", "Price Changes", "Today's risers and fallers plus season-to-date movement."),
    ("pages/8_League_Explorer.py", "🕵️", "League Explorer", "Any mini-league member's squad, captain and transfers."),
    ("pages/9_Recommendations.py", "⭐", "Recommendations", "Attacking, clean-sheet and differential picks for the next gameweek."),
    ("pages/10_Head_to_Head.py", "⚔️", "Head to Head", "Projected goals, win probabilities and record for any fixture."),
    ("pages/11_Set_Piece_Notes.py", "🎯", "Set Piece Takers", "Who takes penalties, free kicks and corners for each team."),
    ("pages/13_How_It_Works.py", "📖", "How It Works", "Full explanations of every projected goal, point and probability."),
]
if is_owner():
    PAGES.insert(-1, ("pages/12_Squad_Tracker.py", "📌", "Squad Tracker", "How your saved optimal squads actually performed."))

COLS = 3
for start in range(0, len(PAGES), COLS):
    cols = st.columns(COLS)
    for col, (path, icon, title, desc) in zip(cols, PAGES[start : start + COLS]):
        with col.container(border=True):
            st.markdown(
                f'<div class="fpl-card-icon">{icon}</div><p class="fpl-card-title">{title}</p>'
                f'<p class="fpl-card-desc">{desc}</p>',
                unsafe_allow_html=True,
            )
            st.page_link(path, label="Open →")
