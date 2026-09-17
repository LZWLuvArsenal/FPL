import html

import pandas as pd
import pulp
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_fixtures
from src.utils import next_event, players_df, teams_df

st.set_page_config(page_title="Recommendations - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Recommendations")
st.caption(
    "Built from FPL's own underlying stats: per-90 expected goal involvement, team-level xG for/against "
    "(derived the same way as the Team Stats page), a configurable run of upcoming fixtures, and ownership."
)
with st.expander("What this can't do yet"):
    st.markdown(
        "Corner counts, goals conceded from corners, and flank-by-flank (left/middle/right) chance "
        "creation aren't available through FPL's API — that's Opta/StatsBomb-tier data, sold to clubs "
        "and broadcasters, not exposed publicly. Sites like fbref show some of it on their pages, but "
        "scraping them isn't practical here: no official API, and their terms cap automated requests "
        "hard enough that it can't support a live dashboard. If this would be valuable, a slower, "
        "opt-in offline job that pulls a few pages a week (not live, rate-limited) is possible — just "
        "ask and I'll set it up separately. Everything below uses xG/xGC as the closest available proxy "
        "for attacking/defensive strength, which is directionally similar but less granular."
    )

bootstrap = get_bootstrap_static()
fixtures = get_fixtures()
players = players_df(bootstrap)
teams = teams_df(bootstrap)
team_short = dict(zip(teams["id"], teams["short_name"]))

gw = next_event(bootstrap)
if gw is None:
    st.info("Season's over — no upcoming gameweek to recommend for.")
    st.stop()

for col in ("goals_scored", "goals_conceded", "minutes"):
    players[col] = pd.to_numeric(players[col], errors="coerce")

xg_for_by_team = players.groupby("team")["expected_goals"].sum()
xg_against_by_team = players[players["position"] == "GKP"].groupby("team")["expected_goals_conceded"].sum()

team_games_played = {tid: 0 for tid in team_short}
for f in fixtures:
    if not f["finished"]:
        continue
    team_games_played[f["team_h"]] = team_games_played.get(f["team_h"], 0) + 1
    team_games_played[f["team_a"]] = team_games_played.get(f["team_a"], 0) + 1
games_played_series = players["team"].map(team_games_played).replace(0, pd.NA)
players["mins_per_game"] = (players["minutes"] / games_played_series).fillna(0)

num_gw = st.slider("Look ahead (gameweeks)", 1, 8, 5)
gw_range = set(range(gw, gw + num_gw))

team_fixtures = {}
for f in fixtures:
    if f["event"] not in gw_range:
        continue
    team_fixtures.setdefault(f["team_h"], []).append((f["team_a"], f["team_h_difficulty"]))
    team_fixtures.setdefault(f["team_a"], []).append((f["team_h"], f["team_a_difficulty"]))


def team_window_summary(team_id):
    fx = team_fixtures.get(team_id, [])
    if not fx:
        return None
    return {
        "opponents": ", ".join(team_short.get(opp, "?") for opp, _ in fx),
        "difficulty": sum(d for _, d in fx) / len(fx),
        "opp_xgc": sum(xg_against_by_team.get(opp, 0) for opp, _ in fx) / len(fx),
        "opp_xg_for": sum(xg_for_by_team.get(opp, 0) for opp, _ in fx) / len(fx),
        "num_fixtures": len(fx),
    }


summaries = {tid: team_window_summary(tid) for tid in team_short}
players["opponents"] = players["team"].map(lambda t: (summaries.get(t) or {}).get("opponents"))
players["difficulty"] = players["team"].map(lambda t: (summaries.get(t) or {}).get("difficulty"))
players["opp_xgc"] = players["team"].map(lambda t: (summaries.get(t) or {}).get("opp_xgc"))
players["opp_xg_for"] = players["team"].map(lambda t: (summaries.get(t) or {}).get("opp_xg_for"))
players["num_fixtures"] = players["team"].map(lambda t: (summaries.get(t) or {}).get("num_fixtures"))

blank_gw_teams = players.loc[players["opponents"].isna(), "team_name"].unique()
if len(blank_gw_teams):
    st.caption(f"No fixture in this window for: {', '.join(blank_gw_teams)} — their players are excluded below.")

STATUS_LABELS = {"a": "Available", "d": "Doubtful", "i": "Injured", "s": "Suspended", "u": "Unavailable"}
unavailable = players[players["status"].isin(["i", "s", "u"]) & (players["minutes"] >= 180)]
if not unavailable.empty:
    with st.expander(f"🚫 {len(unavailable)} injured/suspended/unavailable players excluded from all lists below"):
        for _, p in unavailable.sort_values("full_name").iterrows():
            st.caption(f"**{p['full_name']}** ({STATUS_LABELS[p['status']]}) — {p['news'] or 'no details'}")

eligible = players[
    players["opponents"].notna() & (players["minutes"] >= 180) & (~players["status"].isin(["i", "s", "u"]))
].copy()

DIFF_THRESHOLD = 10
diffs_only = st.checkbox("Show differentials only (≤10% owned)", value=False)
st.caption(
    "Mins/Game = average minutes per match played so far — a quick sub-risk check. Close to 90 means "
    "nailed on; well below it means rotation risk or a habitual late substitute."
)

FDR_COLORS = {
    1: "rgba(0, 166, 90, 0.5)",
    2: "rgba(0, 166, 90, 0.25)",
    3: "rgba(255, 193, 7, 0.2)",
    4: "rgba(220, 53, 69, 0.25)",
    5: "rgba(220, 53, 69, 0.5)",
}


def _doubtful_badge(row):
    if row["status"] != "d":
        return ""
    chance = row["chance_of_playing_next_round"]
    chance_text = f"{chance:.0f}%" if chance is not None else "?"
    return (
        f' <span style="background:rgba(255,193,7,0.4); font-size:0.6rem; font-weight:700; '
        f'padding:1px 4px; border-radius:4px; margin-left:4px;" title="{html.escape(row["news"] or "")}">'
        f"DOUBT {chance_text}</span>"
    )


def _fixture_chips_html(team_id):
    chips = []
    for opp, diff in team_fixtures.get(team_id, []):
        color = FDR_COLORS.get(round(diff), "rgba(128,128,128,0.15)")
        code = html.escape(team_short.get(opp, "?"))
        chips.append(
            f'<span style="background:{color}; border-radius:4px; padding:2px 6px; font-size:0.72rem; '
            f'white-space:nowrap; display:inline-block; margin:1px;">{code}</span>'
        )
    return "".join(chips)


def render_recommendation_table(df, stat_col, stat_label, opp_stat_col, opp_stat_label, show_defcon=True):
    if df.empty:
        st.caption("No players match at the current thresholds.")
        return
    columns = [
        ("Player", "left"),
        ("Team", "left"),
        ("Fixtures", "left"),
        ("Price", "right"),
        ("Owned %", "right"),
        ("Mins/Game", "right"),
        (stat_label, "right"),
        (opp_stat_label, "right"),
        ("FPL Diff (avg)", "right"),
    ]
    if show_defcon:
        columns.append(("DEFCON/90", "right"))
    header = "<tr>" + "".join(
        f'<th style="text-align:{align}; padding:6px 8px; font-size:0.78rem; opacity:0.7; '
        f'white-space:nowrap;">{label}</th>'
        for label, align in columns
    ) + "</tr>"
    rows_html = []
    for _, r in df.iterrows():
        diff_badge = (
            ' <span style="background:rgba(46,204,113,0.3); font-size:0.6rem; font-weight:700; '
            'padding:1px 4px; border-radius:4px; margin-left:4px;">DIFF</span>'
            if r["differential"]
            else ""
        )
        defcon_cell = (
            f'<td style="padding:6px 8px; text-align:right;">{r["defensive_contribution_per_90"]:.2f}</td>'
            if show_defcon
            else ""
        )
        rows_html.append(
            '<tr style="border-top:1px solid rgba(128,128,128,0.15);">'
            f'<td style="padding:6px 8px; font-weight:600; white-space:nowrap;">'
            f'{html.escape(r["full_name"])}{diff_badge}{_doubtful_badge(r)}</td>'
            f'<td style="padding:6px 8px; white-space:nowrap;">{html.escape(r["team_short"])}</td>'
            f'<td style="padding:6px 8px;">{_fixture_chips_html(r["team"])}</td>'
            f'<td style="padding:6px 8px; text-align:right; white-space:nowrap;">£{r["price"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["selected_by_percent"]:.1f}%</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["mins_per_game"]:.0f}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700;">{r[stat_col]:.2f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r[opp_stat_col]:.2f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["difficulty"]:.1f}</td>'
            f"{defcon_cell}"
            "</tr>"
        )
    st.markdown(
        '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f"<thead>{header}</thead><tbody>{''.join(rows_html)}</tbody></table></div>",
        unsafe_allow_html=True,
    )


league_median_xgc = xg_against_by_team.median()
league_median_xgfor = xg_for_by_team.median()
gk_minutes_by_team = players[players["position"] == "GKP"].groupby("team")["minutes"].sum()


def _attacking_section(position, label, emoji, n=15):
    st.divider()
    st.subheader(f"{emoji} {label} (Next {num_gw} GW{'s' if num_gw > 1 else ''})")
    st.caption(
        "Sorted by expected goal involvement per 90, restricted to players facing an opponent whose "
        "defense concedes more xG than the league median, averaged over the window above."
    )
    rows = eligible[
        (eligible["position"] == position) & (eligible["opp_xgc"] >= league_median_xgc)
    ].sort_values("expected_goal_involvements_per_90", ascending=False).copy()
    rows["differential"] = rows["selected_by_percent"] <= DIFF_THRESHOLD
    if diffs_only:
        rows = rows[rows["differential"]]
    rows = rows.head(n)
    render_recommendation_table(
        rows, "expected_goal_involvements_per_90", "xGI/90", "opp_xgc", "Opp. xGC (avg)"
    )
    return rows


def _defensive_section(position, label, emoji, n=15):
    st.divider()
    st.subheader(f"{emoji} {label} (Next {num_gw} GW{'s' if num_gw > 1 else ''})")
    st.caption(
        "Sorted by the player's own team defensive xG record, restricted to players facing an opponent "
        "whose attack creates less xG than the league median, averaged over the window above."
    )
    rows = eligible[
        (eligible["position"] == position) & (eligible["opp_xg_for"] <= league_median_xgfor)
    ].copy()
    rows["own_team_xgc_per90"] = rows["team"].map(xg_against_by_team) / rows["team"].map(gk_minutes_by_team) * 90
    rows = rows.sort_values("own_team_xgc_per90")
    rows["differential"] = rows["selected_by_percent"] <= DIFF_THRESHOLD
    if diffs_only:
        rows = rows[rows["differential"]]
    rows = rows.head(n)
    render_recommendation_table(
        rows,
        "own_team_xgc_per90",
        "Own xGC/90",
        "opp_xg_for",
        "Opp. xG For (avg)",
        show_defcon=(position != "GKP"),
    )
    return rows


gk_pool = _defensive_section("GKP", "Goalkeepers", "🥅", n=10)
def_pool = _defensive_section("DEF", "Defenders", "🛡️")
mid_pool = _attacking_section("MID", "Midfielders", "🎯")
fwd_pool = _attacking_section("FWD", "Forwards", "⚡")

st.divider()
st.subheader("💰 Recommended 15 — Best Squad Within £100m")
st.caption(
    "A full 15-man squad (2 GK, 5 DEF, 5 MID, 3 FWD, max 3 players per club) that maximizes total "
    "points-per-game while staying at or under a £100m budget. Solved as an integer optimization "
    "problem (PuLP) — the best affordable combination under FPL's real squad rules, not a guess. "
    "Drawn only from the players already recommended in the four sections above, so every pick here "
    "is one you've already seen and can trace back to its stats."
)

candidate_pool = pd.concat([gk_pool, def_pool, mid_pool, fwd_pool])
pool = candidate_pool[candidate_pool["points_per_game"] > 0].set_index("id")

prob = pulp.LpProblem("squad", pulp.LpMaximize)
pick = {i: pulp.LpVariable(f"pick_{i}", cat="Binary") for i in pool.index}
prob += pulp.lpSum(pool.loc[i, "points_per_game"] * pick[i] for i in pool.index)
prob += pulp.lpSum(pool.loc[i, "price"] * pick[i] for i in pool.index) <= 100.0
for pos, count in [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]:
    prob += pulp.lpSum(pick[i] for i in pool.index if pool.loc[i, "position"] == pos) == count
for team_id in pool["team"].unique():
    prob += pulp.lpSum(pick[i] for i in pool.index if pool.loc[i, "team"] == team_id) <= 3
prob.solve(pulp.PULP_CBC_CMD(msg=0))

if pulp.LpStatus[prob.status] != "Optimal":
    st.warning("Couldn't find a valid squad under these constraints — try widening the look-ahead window.")
else:
    squad_ids = [i for i in pool.index if pick[i].value() == 1]
    squad = pool.loc[squad_ids]
    POSITION_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    squad = squad.assign(_order=squad["position"].map(POSITION_ORDER)).sort_values(["_order", "price"], ascending=[True, False])

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Cost", f"£{squad['price'].sum():.1f}m")
    col2.metric("Budget Remaining", f"£{100.0 - squad['price'].sum():.1f}m")
    col3.metric("Total PPG", f"{squad['points_per_game'].sum():.1f}")

    header = "".join(
        f'<th style="text-align:{align}; padding:6px 8px; font-size:0.78rem; opacity:0.7; white-space:nowrap;">{label}</th>'
        for label, align in [
            ("Pos", "left"),
            ("Player", "left"),
            ("Team", "left"),
            ("Fixtures", "left"),
            ("Price", "right"),
            ("Owned %", "right"),
            ("Mins/Game", "right"),
            ("PPG", "right"),
            ("DEFCON/90", "right"),
        ]
    )
    rows_html = []
    for _, r in squad.iterrows():
        rows_html.append(
            '<tr style="border-top:1px solid rgba(128,128,128,0.15);">'
            f'<td style="padding:6px 8px; opacity:0.7;">{r["position"]}</td>'
            f'<td style="padding:6px 8px; font-weight:600; white-space:nowrap;">'
            f'{html.escape(r["full_name"])}{_doubtful_badge(r)}</td>'
            f'<td style="padding:6px 8px; white-space:nowrap;">{html.escape(r["team_short"])}</td>'
            f'<td style="padding:6px 8px;">{_fixture_chips_html(r["team"])}</td>'
            f'<td style="padding:6px 8px; text-align:right; white-space:nowrap;">£{r["price"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["selected_by_percent"]:.1f}%</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["mins_per_game"]:.0f}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700;">{r["points_per_game"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["defensive_contribution_per_90"]:.2f}</td>'
            "</tr>"
        )
    st.markdown(
        '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
