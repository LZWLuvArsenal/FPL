import html

import pandas as pd
import plotly.express as px
import pulp
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_element_summary, get_event_live
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
    return f"{p['web_name']} ({p['team_short']})"


def fmt_count(n):
    if n is None:
        return None
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def round_stat(element_id, field):
    """Looks up a per-gameweek archived value (ownership, transfers) from the player's own
    history — bootstrap's event object only names who led each category, not by how much."""
    if element_id is None or element_id not in players.index:
        return None
    history = get_element_summary(element_id)["history"]
    row = next((h for h in history if h["round"] == event["id"]), None)
    return row[field] if row else None


col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Transfers Made", f"{event['transfers_made']:,}")
top_scorer = event.get("top_element_info")
col2.metric("Top Scorer", player_label(top_scorer["id"]) if top_scorer else "-", f"{top_scorer['points']} pts" if top_scorer else "")
selected_count = round_stat(event["most_selected"], "selected")
col3.metric("Most Selected", player_label(event["most_selected"]), f"{fmt_count(selected_count)} owned" if selected_count else "")
transferred_in_count = round_stat(event["most_transferred_in"], "transfers_in")
col4.metric(
    "Most Transferred In", player_label(event["most_transferred_in"]), f"+{fmt_count(transferred_in_count)}" if transferred_in_count else ""
)

col5, col6, col7 = st.columns(3)
captained_owned = round_stat(event["most_captained"], "selected")
col5.metric("Most Captained", player_label(event["most_captained"]), f"{fmt_count(captained_owned)} owned" if captained_owned else "")
vice_owned = round_stat(event["most_vice_captained"], "selected")
col6.metric("Most Vice-Captained", player_label(event["most_vice_captained"]), f"{fmt_count(vice_owned)} owned" if vice_owned else "")

is_live_gw = event["id"] == current_event(bootstrap)
if is_live_gw:
    most_out = max(bootstrap["elements"], key=lambda e: e["transfers_out_event"])
    col7.metric(
        "Most Transferred Out (live)",
        player_label(most_out["id"]),
        f"-{fmt_count(most_out['transfers_out_event'])}",
    )
else:
    col7.metric("Most Transferred Out", "N/A", "only available live, not archived per past GW")
st.caption(
    "'Owned' is total ownership for that player, not a captaincy-specific count — FPL doesn't "
    "publish how many managers captained any specific player, only who was picked the most."
)

st.subheader("🏆 Team of the Week")
st.caption(
    "The best possible XI from this gameweek's actual points (formation rules: 1 GK, 3-5 DEF, "
    "2-5 MID, 1-3 FWD), no budget limit — a look back at who really delivered, not a pick for next week."
)
live = get_event_live(event["id"])
live_rows = []
for e in live["elements"]:
    if e["id"] not in players.index or e["stats"]["minutes"] <= 0:
        continue
    p = players.loc[e["id"]]
    live_rows.append(
        {
            "id": e["id"],
            "web_name": p["web_name"],
            "team_short": p["team_short"],
            "position": p["position"],
            "price": p["price"],
            "points": e["stats"]["total_points"],
        }
    )
totw_pool = pd.DataFrame(live_rows).set_index("id") if live_rows else pd.DataFrame()

if totw_pool.empty:
    st.info("No player data available for this gameweek yet.")
else:
    totw_prob = pulp.LpProblem("totw", pulp.LpMaximize)
    totw_pick = {i: pulp.LpVariable(f"totw_{i}", cat="Binary") for i in totw_pool.index}
    totw_prob += pulp.lpSum(totw_pool.loc[i, "points"] * totw_pick[i] for i in totw_pool.index)
    totw_prob += pulp.lpSum(totw_pick[i] for i in totw_pool.index) == 11
    totw_prob += pulp.lpSum(totw_pick[i] for i in totw_pool.index if totw_pool.loc[i, "position"] == "GKP") == 1
    totw_prob += pulp.lpSum(totw_pick[i] for i in totw_pool.index if totw_pool.loc[i, "position"] == "DEF") >= 3
    totw_prob += pulp.lpSum(totw_pick[i] for i in totw_pool.index if totw_pool.loc[i, "position"] == "DEF") <= 5
    totw_prob += pulp.lpSum(totw_pick[i] for i in totw_pool.index if totw_pool.loc[i, "position"] == "MID") >= 2
    totw_prob += pulp.lpSum(totw_pick[i] for i in totw_pool.index if totw_pool.loc[i, "position"] == "MID") <= 5
    totw_prob += pulp.lpSum(totw_pick[i] for i in totw_pool.index if totw_pool.loc[i, "position"] == "FWD") >= 1
    totw_prob += pulp.lpSum(totw_pick[i] for i in totw_pool.index if totw_pool.loc[i, "position"] == "FWD") <= 3
    totw_prob.solve(pulp.PULP_CBC_CMD(msg=0))

    if pulp.LpStatus[totw_prob.status] != "Optimal":
        st.warning("Couldn't build a valid Team of the Week for this gameweek.")
    else:
        totw_ids = [i for i in totw_pool.index if totw_pick[i].value() == 1]
        totw = totw_pool.loc[totw_ids]
        TOTW_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
        totw = totw.assign(_order=totw["position"].map(TOTW_ORDER)).sort_values(
            ["_order", "points"], ascending=[True, False]
        )
        captain_id = totw["points"].idxmax()

        st.metric("Team of the Week Total (incl. Captain)", f"{totw['points'].sum() + totw.loc[captain_id, 'points']:.0f}")

        totw_header = "".join(
            f'<th style="text-align:{align}; padding:6px 8px; font-size:0.78rem; opacity:0.7;">{label}</th>'
            for label, align in [("Pos", "left"), ("Player", "left"), ("Team", "left"), ("Price", "right"), ("Points", "right")]
        )
        totw_rows_html = []
        for idx, r in totw.iterrows():
            badge = (
                ' <span style="background:#ffb300; color:#1a1a1a; font-size:0.62rem; font-weight:700; '
                'padding:1px 4px; border-radius:4px; margin-left:2px;">C</span>'
                if idx == captain_id
                else ""
            )
            totw_rows_html.append(
                '<tr style="border-top:1px solid rgba(128,128,128,0.15);">'
                f'<td style="padding:6px 8px; opacity:0.7;">{r["position"]}</td>'
                f'<td style="padding:6px 8px; font-weight:600;">{html.escape(r["web_name"])}{badge}</td>'
                f'<td style="padding:6px 8px;">{html.escape(r["team_short"])}</td>'
                f'<td style="padding:6px 8px; text-align:right;">£{r["price"]:.1f}</td>'
                f'<td style="padding:6px 8px; text-align:right; font-weight:700;">{r["points"]}</td>'
                "</tr>"
            )
        st.markdown(
            '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
            f"<thead><tr>{totw_header}</tr></thead><tbody>{''.join(totw_rows_html)}</tbody></table></div>",
            unsafe_allow_html=True,
        )

st.subheader("Chips Played")
chips_df = pd.DataFrame(event["chip_plays"])
chips_df["chip_name"] = chips_df["chip_name"].map(CHIP_NAMES).fillna(chips_df["chip_name"])
chips_df = chips_df.sort_values("num_played", ascending=True)
fig = px.bar(
    chips_df,
    x="num_played",
    y="chip_name",
    orientation="h",
    text="num_played",
    labels={"num_played": "Times Played", "chip_name": ""},
)
fig.update_traces(texttemplate="%{text:,}", textposition="outside", cliponaxis=False)
fig.update_layout(margin=dict(r=60))
st.plotly_chart(fig, width="stretch")

st.caption(
    "This is everything the public FPL API exposes at gameweek granularity. It does not publish a "
    "full captaincy distribution (only the single most-picked captain), and 'most transferred out' "
    "is only ever available live (for the current transfer window ahead of the next deadline) — "
    "FPL doesn't archive it per past gameweek the way it does 'most transferred in'."
)
