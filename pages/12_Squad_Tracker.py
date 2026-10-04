import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings, require_owner
from src.fpl_api import get_bootstrap_static, get_event_live
from src.player_dialog import player_dataframe
from src.tracker import delete_snapshot, load_snapshots

st.set_page_config(page_title="Squad Tracker - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Squad Tracker")
require_owner()
st.caption(
    "Tracks how the single-gameweek 'optimal squad' saved from the Recommendations page actually "
    "performed, using each subsequent gameweek's real results. The starting XI and captain are "
    "frozen at save time — this simulates a 'buy and hold, no transfers' run, not you actively "
    "managing it, so treat it as a benchmark for the recommender rather than a prediction for you."
)

snapshots = load_snapshots()
if not snapshots:
    st.info("No squads saved yet — go to the Recommendations page and click 'Save optimal GW squad to track'.")
    st.stop()

bootstrap = get_bootstrap_static()
finished_events = {e["id"]: e for e in bootstrap["events"] if e["finished"] or e["data_checked"]}
avg_by_event = {eid: e["average_entry_score"] for eid, e in finished_events.items()}

live_cache: dict[int, dict] = {}


def get_live(gw: int) -> dict:
    if gw not in live_cache:
        live_cache[gw] = get_event_live(gw)
    return live_cache[gw]


for snap in sorted(snapshots, key=lambda s: s["saved_at"], reverse=True):
    start_gw = snap["start_gw"]
    tracked_gws = sorted(gw for gw in finished_events if gw >= start_gw)
    saved_date = snap["saved_at"][:10]

    with st.expander(f"GW{start_gw} squad — saved {saved_date} (£{snap['total_cost']:.1f}m)", expanded=True):
        if not tracked_gws:
            st.caption(f"GW{start_gw} hasn't finished yet — check back after it does.")
        else:
            starters = [p for p in snap["players"] if p["is_starter"]]
            captain = next((p for p in snap["players"] if p["is_captain"]), None)
            vice = next((p for p in snap["players"] if p["is_vice"]), None)

            weekly_rows = []
            for gw in tracked_gws:
                live = get_live(gw)
                points_by_id = {e["id"]: e["stats"] for e in live["elements"]}

                gw_total = 0
                for p in starters:
                    stats = points_by_id.get(p["id"])
                    gw_total += stats["total_points"] if stats else 0

                captain_stats = points_by_id.get(captain["id"]) if captain else None
                captain_played = bool(captain_stats and captain_stats["minutes"] > 0)
                if captain_played:
                    gw_total += captain_stats["total_points"]
                elif vice:
                    vice_stats = points_by_id.get(vice["id"])
                    if vice_stats and vice_stats["minutes"] > 0:
                        gw_total += vice_stats["total_points"]

                weekly_rows.append(
                    {
                        "GW": gw,
                        "Squad Points": gw_total,
                        "Average Manager": avg_by_event.get(gw),
                        "Captain": captain["name"] if captain_played else (vice["name"] if vice else "-"),
                    }
                )

            weekly_df = pd.DataFrame(weekly_rows)
            weekly_df["Running Total"] = weekly_df["Squad Points"].cumsum()
            weekly_df["Running Avg"] = weekly_df["Average Manager"].cumsum()

            total_squad = int(weekly_df["Squad Points"].sum())
            total_avg = int(weekly_df["Average Manager"].sum())
            diff = total_squad - total_avg

            col1, col2, col3 = st.columns(3)
            col1.metric("Total Points", total_squad)
            col2.metric("vs. Average Manager", f"{diff:+d}", f"{total_avg} average")
            col3.metric("Gameweeks Tracked", len(tracked_gws))

            verdict = "🔥 Crushing it" if diff > 20 else "👍 Ahead of average" if diff > 0 else "😐 Roughly average" if diff > -10 else "📉 Behind average"
            st.markdown(f"**{verdict}**")

            st.dataframe(
                weekly_df.rename(
                    columns={
                        "Average Manager": "Avg. Manager (GW)",
                        "Running Total": "Squad (Cumulative)",
                        "Running Avg": "Average (Cumulative)",
                    }
                ),
                hide_index=True,
                width="stretch",
            )

            with st.popover("Squad details"):
                squad_df = pd.DataFrame(snap["players"])
                squad_df["Role"] = squad_df.apply(
                    lambda r: "Captain" if r["is_captain"] else ("Vice-Captain" if r["is_vice"] else ("Starting" if r["is_starter"] else "Bench")),
                    axis=1,
                )
                player_dataframe(
                    squad_df[["name", "team_short", "position", "Role"]].rename(
                        columns={"name": "Player", "team_short": "Team", "position": "Pos"}
                    ),
                    squad_df["id"],
                    key=f"tracker_squad_{snap['id']}",
                    hide_index=True,
                    width="stretch",
                )

        if st.button("🗑️ Delete this snapshot", key=f"delete_{snap['id']}"):
            delete_snapshot(snap["id"])
            st.rerun()
