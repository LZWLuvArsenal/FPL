import html

import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import (
    get_bootstrap_static,
    get_element_summary,
    get_entry,
    get_entry_history,
    get_entry_picks,
    get_entry_transfers,
    get_fixtures,
    get_league_standings,
)
from src.pitch import render_pitch, upcoming_fixture_labels
from src.utils import FDR_STYLE_UNKNOWN, FDR_STYLES, current_event, next_event, players_df

OVERALL_LEAGUE_ID = 314

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

rank_history = sorted(history["current"], key=lambda h: h["event"])
rank_delta = None
if len(rank_history) >= 2:
    rank_delta = rank_history[-2]["overall_rank"] - rank_history[-1]["overall_rank"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Overall Points", entry["summary_overall_points"])
if rank_delta is None:
    col2.metric("Overall Rank", f"{entry['summary_overall_rank']:,}")
elif rank_delta == 0:
    col2.metric("Overall Rank", f"{entry['summary_overall_rank']:,}", "No change", delta_color="off")
else:
    col2.metric("Overall Rank", f"{entry['summary_overall_rank']:,}", f"{rank_delta:+,} vs last GW")
col3.metric("GW Points", entry["summary_event_points"])
col4.metric("Team Value", f"£{entry['last_deadline_value'] / 10:.1f}m")

this_event = next((e for e in bootstrap["events"] if e["id"] == entry["current_event"]), None)
if this_event and this_event.get("average_entry_score") is not None:
    my_points = entry["summary_event_points"]
    avg_points = this_event["average_entry_score"]
    diff = my_points - avg_points
    if diff >= 20:
        rating = "🔥 Elite"
    elif diff >= 10:
        rating = "💪 Great"
    elif diff >= 0:
        rating = "🙂 Above Average"
    elif diff >= -10:
        rating = "😐 Below Average"
    else:
        rating = "😬 Rough Week"

    st.subheader(f"How Did GW{this_event['id']} Go?")
    rcol1, rcol2, rcol3 = st.columns(3)
    rcol1.metric("Gameweek Rating", rating, f"{diff:+d} vs average")
    rcol2.metric("Average Score (All Managers)", avg_points)
    rcol3.metric("Highest Score This GW", this_event["highest_score"])

CHIP_NAMES = {"bboost": "Bench Boost", "freehit": "Free Hit", "wildcard": "Wildcard", "3xc": "Triple Captain"}
chip_by_event = {c["event"]: CHIP_NAMES.get(c["name"], c["name"]) for c in history.get("chips", [])}

season_history = pd.DataFrame(history["current"])
if not season_history.empty:
    st.subheader("Season Progression")
    display_history = season_history[
        ["event", "points", "points_on_bench", "event_transfers", "event_transfers_cost", "overall_rank", "value"]
    ].copy()
    display_history["value"] = display_history["value"] / 10
    display_history["chip"] = display_history["event"].map(chip_by_event).fillna("")
    display_history = display_history.sort_values("event", ascending=False).rename(
        columns={
            "event": "GW",
            "points": "Points",
            "points_on_bench": "Bench Pts",
            "event_transfers": "Transfers",
            "event_transfers_cost": "Hit Cost",
            "overall_rank": "Overall Rank",
            "value": "Team Value",
            "chip": "Chip",
        }
    )
    display_history["Overall Rank"] = display_history["Overall Rank"].apply(lambda r: f"{r:,}")
    st.dataframe(
        display_history,
        hide_index=True,
        width="stretch",
        column_config={
            "Team Value": st.column_config.NumberColumn(format="£%.1f"),
        },
    )

st.subheader("Current Squad")
event_id = current_event(bootstrap)
picks_df = None
try:
    picks = get_entry_picks(team_id, event_id)
    picks_df = pd.DataFrame(picks["picks"]).rename(columns={"position": "slot"}).merge(
        players[
            [
                "id",
                "team",
                "web_name",
                "team_name",
                "team_short",
                "position",
                "price",
                "event_points",
                "total_points",
                "points_per_game",
                "selected_by_percent",
            ]
        ],
        left_on="element",
        right_on="id",
    )
    picks_df["role"] = picks_df.apply(
        lambda r: "Captain" if r["is_captain"] else ("Vice-Captain" if r["is_vice_captain"] else ""), axis=1
    )
    picks_df["player"] = picks_df.apply(
        lambda r: f"{r['web_name']} ({'C' if r['is_captain'] else 'VC' if r['is_vice_captain'] else ''})"
        if r["is_captain"] or r["is_vice_captain"]
        else r["web_name"],
        axis=1,
    )
    squad_view = st.radio("Squad view", ["Pitch view", "List view"], horizontal=True, label_visibility="collapsed")
    if squad_view == "Pitch view":
        team_codes = {t["id"]: t["code"] for t in bootstrap["teams"]}
        team_shorts = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
        st.markdown(
            render_pitch(picks_df, team_codes, upcoming_fixture_labels(get_fixtures(), event_id, team_shorts)),
            unsafe_allow_html=True,
        )
        st.caption(
            "Shows each player's points this gameweek, or their opponent if the match hasn't kicked off yet. "
            "C = captain, V = vice-captain. Pink = 1 point or fewer, green = 8 or more."
        )
    else:
        squad_header = "".join(
            f'<th style="text-align:left; padding:6px 8px; font-size:0.78rem; opacity:0.7;">{label}</th>'
            for label in ["Player", "Team", "Pos", "Price", "GW Pts", "Total Pts"]
        )
        squad_rows_html = []
        for _, r in picks_df.sort_values("slot").iterrows():
            benched = r["slot"] > 11
            row_style = "background:rgba(128,128,128,0.16);" if benched else ""
            squad_rows_html.append(
                f'<tr style="border-top:1px solid rgba(128,128,128,0.15); {row_style}">'
                f'<td style="padding:6px 8px; font-weight:600; white-space:nowrap;">{html.escape(r["player"])}</td>'
                f'<td style="padding:6px 8px;">{html.escape(r["team_name"])}</td>'
                f'<td style="padding:6px 8px;">{r["position"]}</td>'
                f'<td style="padding:6px 8px; white-space:nowrap;">£{r["price"]:.1f}</td>'
                f'<td style="padding:6px 8px;">{r["event_points"]}</td>'
                f'<td style="padding:6px 8px;">{r["total_points"]}</td>'
                "</tr>"
            )
        st.markdown(
            '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
            f"<thead><tr>{squad_header}</tr></thead><tbody>{''.join(squad_rows_html)}</tbody></table></div>",
            unsafe_allow_html=True,
        )
        st.caption("Greyed-out rows are on the bench.")
except Exception:
    st.warning(f"Picks for GW{event_id} aren't available yet.")

st.subheader("Room for Improvement")
if picks_df is None:
    st.caption("Needs this gameweek's squad picks — see the warning above.")
else:
    imp_col1, imp_col2 = st.columns(2)
    with imp_col1:
        st.markdown("**😬 Underperformers in your squad**")
        st.caption("This GW's points vs. their own season points-per-game — the biggest personal misses.")
        flops = picks_df.copy()
        flops["vs_form"] = flops["event_points"] - flops["points_per_game"]
        flops = flops.sort_values("vs_form").head(5)
        st.dataframe(
            flops[["web_name", "team_short", "role", "price", "event_points", "points_per_game", "vs_form"]]
            .round(1)
            .rename(
                columns={
                    "web_name": "Player",
                    "team_short": "Team",
                    "role": "Role",
                    "price": "Price",
                    "event_points": "GW Pts",
                    "points_per_game": "Season PPG",
                    "vs_form": "vs. Own Form",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={"Price": st.column_config.NumberColumn(format="£%.1f")},
        )

    with imp_col2:
        st.markdown("**📈 Template players hurting your rank**")
        st.caption("Widely-owned players (across all ~11m managers) who returned well this GW and aren't in your squad.")
        my_ids = set(picks_df["element"])
        missed = players[~players["id"].isin(my_ids) & (players["event_points"] > 0)].copy()
        missed["impact_score"] = missed["selected_by_percent"] * missed["event_points"]
        missed = missed.sort_values("impact_score", ascending=False).head(5)
        st.dataframe(
            missed[["web_name", "team_short", "price", "selected_by_percent", "event_points"]].round(1).rename(
                columns={
                    "web_name": "Player",
                    "team_short": "Team",
                    "price": "Price",
                    "selected_by_percent": "Owned %",
                    "event_points": "GW Pts",
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={"Price": st.column_config.NumberColumn(format="£%.1f")},
        )
    st.caption(
        "Both lists are heuristics, not certainties: 'vs. Own Form' just flags a bad week relative to a "
        "player's own average, and 'template' ranks by ownership × points scored — a rough proxy for how "
        "much ground you lost by not having them, not an exact rank-points calculation."
    )

    st.markdown("**🔻 To consider selling**")
    st.caption(
        "Blanked (no goals, no assists) in each of their last 3 appearances. Keepers/defenders also get "
        "a pass for a clean sheet (worth a full 4pts for them), but midfielders and forwards don't — a "
        "mid's clean sheet is only worth 1pt, not enough to call the week productive on its own."
    )
    fixtures = get_fixtures()
    team_short_map = players.set_index("team")["team_short"].to_dict()

    def blanked_last_3(element_id, position):
        played = [h for h in get_element_summary(int(element_id))["history"] if h["minutes"] > 0]
        if len(played) < 3:
            return False

        def is_blank(h):
            no_involvement = h["goals_scored"] == 0 and h["assists"] == 0
            if position in ("MID", "FWD"):
                return no_involvement
            return no_involvement and h["clean_sheets"] == 0

        return all(is_blank(h) for h in played[-3:])

    def fixture_chips_html(team_id, n=5):
        upcoming = sorted(
            (f for f in fixtures if not f["finished"] and (f["team_h"] == team_id or f["team_a"] == team_id)),
            key=lambda f: f["event"] or 9_999,
        )[:n]
        if not upcoming:
            return "-"
        chips = []
        for f in upcoming:
            is_home = f["team_h"] == team_id
            opp = f["team_a"] if is_home else f["team_h"]
            diff = f["team_h_difficulty"] if is_home else f["team_a_difficulty"]
            style = FDR_STYLES.get(diff, FDR_STYLE_UNKNOWN)
            label = html.escape(f"{team_short_map.get(opp, '?')} {'(H)' if is_home else '(A)'}")
            chips.append(
                f'<span style="{style}; border-radius:4px; padding:2px 6px; font-size:0.78rem; font-weight:600; '
                f'white-space:nowrap; display:inline-block; margin:1px;">{label}</span>'
            )
        return "".join(chips)

    sell_rows = [p for _, p in picks_df.iterrows() if blanked_last_3(p["element"], p["position"])]

    if not sell_rows:
        st.caption("No one in your squad has blanked 3 straight — nothing flagged.")
    else:
        # Same fixture-adjusted projection as the Recommendations page, reused here to rank replacements.
        xg_for_by_team = players.groupby("team")["expected_goals"].sum()
        xg_against_by_team = players[players["position"] == "GKP"].groupby("team")["expected_goals_conceded"].sum()
        league_median_xgc = xg_against_by_team.median()
        league_median_xgfor = xg_for_by_team.median()

        repl_start_gw = next_event(bootstrap) or event_id
        REPL_WINDOW = 5
        repl_gw_range = set(range(repl_start_gw, repl_start_gw + REPL_WINDOW))
        repl_fixtures = {}
        for f in fixtures:
            if f["event"] not in repl_gw_range:
                continue
            repl_fixtures.setdefault(f["team_h"], []).append((f["team_a"], f["team_h_difficulty"]))
            repl_fixtures.setdefault(f["team_a"], []).append((f["team_h"], f["team_a_difficulty"]))

        def fixture_mult(position, opponent_id):
            if position in ("MID", "FWD"):
                opp_xgc = xg_against_by_team.get(opponent_id, league_median_xgc)
                raw = opp_xgc / league_median_xgc if league_median_xgc else 1.0
            else:
                opp_xg_for = xg_for_by_team.get(opponent_id, league_median_xgfor)
                raw = league_median_xgfor / opp_xg_for if opp_xg_for else 1.0
            return min(max(raw, 0.6), 1.6)

        def projected_pts(row):
            fx = repl_fixtures.get(row["team"], [])
            if not fx:
                return 0.0
            ppg = row["points_per_game"] or 0.0
            return sum(ppg * fixture_mult(row["position"], opp) for opp, _ in fx)

        my_ids = set(picks_df["element"])
        for p in sell_rows:
            with st.expander(f"🔻 {p['web_name']} ({p['team_short']}, {p['position']}) — £{p['price']:.1f}"):
                st.markdown(f"**Upcoming 5:** {fixture_chips_html(p['team'])}", unsafe_allow_html=True)
                st.markdown("**Potential replacements** (same position, same price or cheaper)")
                candidates = players[
                    (players["position"] == p["position"])
                    & (~players["id"].isin(my_ids))
                    & (players["price"] <= p["price"])
                    & (players["minutes"] >= 180)
                    & (~players["status"].isin(["i", "s", "u"]))
                ].copy()
                candidates["projected_pts"] = candidates.apply(projected_pts, axis=1)
                top_replacements = candidates.sort_values("projected_pts", ascending=False).head(3)
                if top_replacements.empty:
                    st.caption("No eligible replacements found at this price point.")
                else:
                    for _, rep in top_replacements.iterrows():
                        st.markdown(
                            f"- **{rep['web_name']}** ({rep['team_short']}) — £{rep['price']:.1f} · "
                            f"{rep['projected_pts']:.1f} proj. pts over next {REPL_WINDOW} GWs · "
                            f"{fixture_chips_html(rep['team'])}",
                            unsafe_allow_html=True,
                        )
        st.caption(
            f"Replacement projections use the same fixture-adjusted points-per-game model as the "
            f"Recommendations page, over the next {REPL_WINDOW} gameweeks."
        )

st.subheader("Rank Milestones")
st.caption(
    "How many more overall (season-total) points you'd need to reach specific rank cutoffs, read from "
    "the live Overall league standings. A true *average* score across, say, the whole top 100k would mean "
    "pulling every entry up to that rank — thousands of requests, not practical live — so this shows the "
    "cutoff score to just reach that rank instead, which is the number that actually matters. Milestones "
    "continue in 1m steps past 3m up to your own rank, capped at 6m."
)
RANK_MILESTONES = [1, 100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 2_000_000, 3_000_000]
MAX_MILESTONE = 6_000_000
my_rank_for_milestones = entry["summary_overall_rank"]
next_m = 4_000_000
rank_ceiling = min(MAX_MILESTONE, -(-my_rank_for_milestones // 1_000_000) * 1_000_000)
while next_m <= rank_ceiling:
    RANK_MILESTONES.append(next_m)
    next_m += 1_000_000

if st.button("Load rank milestones"):
    my_total = entry["summary_overall_points"]
    rows = []
    progress = st.progress(0.0)
    for i, rank in enumerate(RANK_MILESTONES):
        page = -(-rank // 50)  # ceil division: standings return 50 entries per page
        try:
            results = get_league_standings(OVERALL_LEAGUE_ID, page=page)["standings"]["results"]
            idx = (rank - 1) % 50
            target_total = results[idx]["total"] if idx < len(results) else results[-1]["total"]
        except Exception:
            target_total = None
        rows.append(
            {
                "Rank": rank,
                "Points at Rank": target_total,
                "Gap": (target_total - my_total) if target_total is not None else None,
            }
        )
        progress.progress((i + 1) / len(RANK_MILESTONES))
    progress.empty()
    st.session_state["rank_milestones"] = rows

milestone_rows = st.session_state.get("rank_milestones")
if milestone_rows:
    m_df = pd.DataFrame(milestone_rows)
    m_df["Status"] = m_df["Gap"].apply(lambda g: "✅ Already there" if g is not None and g <= 0 else "")
    st.dataframe(
        m_df.rename(columns={"Rank": "Rank ≤"}),
        hide_index=True,
        width="stretch",
    )

st.subheader("Transfer History")
transfers = get_entry_transfers(team_id)
if not transfers:
    st.caption("No transfers made this season.")
else:
    players_by_id = players.set_index("id")["web_name"]

    def points_since(element_id, from_gw):
        history = get_element_summary(int(element_id))["history"]
        return sum(h["total_points"] for h in history if h["round"] >= from_gw)

    rows = []
    for t in transfers:
        in_pts = points_since(t["element_in"], t["event"])
        out_pts = points_since(t["element_out"], t["event"])
        rows.append(
            {
                "GW": t["event"],
                "Player In": players_by_id.get(t["element_in"], "?"),
                "Cost In": t["element_in_cost"] / 10,
                "Player Out": players_by_id.get(t["element_out"], "?"),
                "Cost Out": t["element_out_cost"] / 10,
                "Points In (since)": in_pts,
                "Points Out (since)": out_pts,
                "Points Swing": in_pts - out_pts,
                "_time": t["time"],
            }
        )
    t_df = pd.DataFrame(rows).sort_values("_time", ascending=False).drop(columns="_time")

    total_swing = int(t_df["Points Swing"].sum())
    st.metric("Total Points Swing (all transfers)", f"{total_swing:+d}")
    st.dataframe(
        t_df,
        hide_index=True,
        width="stretch",
        column_config={
            "Cost In": st.column_config.NumberColumn(format="£%.1f"),
            "Cost Out": st.column_config.NumberColumn(format="£%.1f"),
        },
    )
    st.caption(
        "Points Swing = points scored by the player brought in minus points scored by the player let go, "
        "both counted from the transfer's gameweek up to now. Positive means the transfer has paid off "
        "so far; negative means you'd have been better off keeping the player you sold."
    )
