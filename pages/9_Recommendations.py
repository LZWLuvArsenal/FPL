import html

import pandas as pd
import pulp
import streamlit as st

from src.config import is_owner, render_sidebar_settings
from src.fpl_api import (
    get_bootstrap_static,
    get_entry,
    get_entry_history,
    get_entry_picks,
    get_event_live,
    get_fixtures,
)
from src.player_dialog import player_picker
from src.section_nav import SectionNav
from src.squad_model import (
    add_mins_per_game,
    add_scores,
    add_window_columns,
    fixtures_by_team,
    optimal_gw_snapshot,
    project_points,
    recent_stats,
    solve_squad,
    solve_starting_xi,
)
from src.tracker import add_snapshot
from src.utils import FDR_STYLE_UNKNOWN, FDR_STYLES, current_event, next_event, players_df, teams_df

st.set_page_config(page_title="Recommendations - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Recommendations")
nav = SectionNav()
st.caption(
    "Each position list below is ranked by Next GW: a modeled expected-points estimate for the "
    "upcoming gameweek alone, built from underlying per-90 rates (goals, assists, clean sheets, bonus, "
    "cards, defensive contribution), each scaled by the specific opponent — not points-per-game, which "
    "can be skewed by one-off results or a run against weak/strong opposition. Score (also shown) is a "
    "separate equally-weighted, standardized (z-scored) blend of stats and fixtures: for "
    "Midfielders/Forwards, own xGI/90, opponent xGC/90, fixture difficulty (FDR); for "
    "Goalkeepers/Defenders, fixture difficulty (FDR), own team xGC/90, opponent xG/90. The squad "
    "builder further down uses Proj Pts — the same points model summed over the full look-ahead "
    "window — to pick the best 15 for your budget."
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

with st.expander("How Next GW / Proj Pts are calculated"):
    st.markdown(
        "Both are the same expected-points model — Next GW sums it over gameweek "
        f"{gw} only, Proj Pts sums it over the full look-ahead window below. For each fixture in a "
        "player's window, it adds up:\n\n"
        "- **Appearance: +2** — the pool is already filtered to 75+ mins/game, so this is treated as certain.\n"
        "- **Goals + assists**: own `expected_goals_per_90` / `expected_assists_per_90`, scaled up or "
        "down by how the specific upcoming opponent compares to the league-median defense (capped to "
        "0.6×–1.6× so one small-sample fixture can't dominate), then converted to points at the "
        "position's rate (goals: 6/6/5/4 for GKP/DEF/MID/FWD; assists: 3 for everyone).\n"
        "- **Clean sheet**: modeled as a probability, not a coin flip — it takes the team's own xG "
        "conceded per 90 (scaled by how dangerous this specific opponent's attack is) and runs it "
        "through the Poisson chance of conceding exactly zero, i.e. `e^-(expected goals against)`. "
        "That probability is multiplied by the clean-sheet points on offer (4 for GKP/DEF, 1 for MID, "
        "0 for FWD).\n"
        "- **Goals-conceded penalty** (GKP/DEF only): −0.5 × the same expected-goals-against number "
        "(FPL docks 1 point per 2 goals conceded, so this is its continuous expectation).\n"
        "- **Saves** (GKP only): season-average saves per match, scaled by the opponent's attacking "
        "strength the same way, ÷3 (FPL's save-points rate).\n"
        "- **Bonus, cards, penalties, own goals, defensive contribution**: rather than modeling these "
        "from scratch, each player's own per-match average over the \"recent form window\" above is "
        "used directly — pulled gameweek-by-gameweek from FPL's live data, not the season-cumulative "
        "total. Defensive contribution counts as a hit-rate (how often they've cleared the "
        "10-action/12-action threshold for their position) × the 2 points on offer.\n\n"
        "Score is a different, separate metric (see the caption above each position table) — it's not "
        "an input to either points number, and isn't in points at all."
    )

players = add_mins_per_game(players, fixtures, team_short)

finished_events = sorted(e["id"] for e in bootstrap["events"] if e["finished"])

if finished_events:
    form_window = st.slider(
        "Recent form window (gameweeks)",
        1,
        len(finished_events),
        min(5, len(finished_events)),
        help="How many of the most recently completed gameweeks feed each team's own/opponent xG/xGC "
        "and each player's bonus/cards/defensive-contribution averages below. Turn it down to weight "
        "recent form over a flat season-to-date average.",
    )
    if form_window < len(finished_events):
        st.caption(
            f"Team and player averages below are drawn from only the last {form_window} completed "
            f"gameweek{'s' if form_window > 1 else ''}, not the full season."
        )
else:
    form_window = 0

players, team_rates = recent_stats(
    players, fixtures, finished_events[-form_window:] if form_window > 0 else [], get_event_live, team_short
)

num_gw = st.slider("Look ahead (gameweeks)", 1, 8, 5)
gw_range = set(range(gw, gw + num_gw))

team_fixtures = fixtures_by_team(fixtures, gw_range)
next_gw_team_fixtures = fixtures_by_team(fixtures, {gw})


def _add_window_columns(df, fixtures_by_team):
    return add_window_columns(df, fixtures_by_team, team_short, team_rates)


def _add_scores(df):
    return add_scores(df)


def _project_points(row, fx):
    return project_points(row, fx, team_rates)


players = _add_window_columns(players, team_fixtures)

blank_gw_teams = players.loc[players["opponents"].isna(), "team_name"].unique()
if len(blank_gw_teams):
    st.caption(f"No fixture in this window for: {', '.join(blank_gw_teams)} — their players are excluded below.")

STATUS_LABELS = {"a": "Available", "d": "Doubtful", "i": "Injured", "s": "Suspended", "u": "Unavailable"}
unavailable = players[players["status"].isin(["i", "s", "u"]) & (players["minutes"] >= 180)]
if not unavailable.empty:
    with st.expander(f"🚫 {len(unavailable)} injured/suspended/unavailable players excluded from all lists below"):
        for _, p in unavailable.sort_values("web_name").iterrows():
            st.caption(f"**{p['web_name']}** ({STATUS_LABELS[p['status']]}) — {p['news'] or 'no details'}")

eligible = players[
    players["opponents"].notna()
    & (players["minutes"] >= 180)
    & (players["mins_per_game"] >= 75)
    & (~players["status"].isin(["i", "s", "u"]))
].copy()
eligible = _add_scores(eligible)
eligible["projected_pts"] = eligible.apply(lambda r: _project_points(r, team_fixtures.get(r["team"], [])), axis=1)
eligible["projected_pts_next_gw"] = eligible.apply(
    lambda r: _project_points(r, next_gw_team_fixtures.get(r["team"], [])), axis=1
)

DIFF_THRESHOLD = 10
diffs_only = st.checkbox("Show differentials only (≤10% owned)", value=False)
st.caption(
    "Mins/Game = average minutes per match played so far. Players averaging under 75 mins/game are "
    "excluded everywhere below as rotation risks or habitual late substitutes."
)

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
    for opp, diff, is_home in team_fixtures.get(team_id, []):
        style = FDR_STYLES.get(round(diff), FDR_STYLE_UNKNOWN)
        code = html.escape(team_short.get(opp, "?")) + (" (H)" if is_home else " (A)")
        chips.append(
            f'<span style="{style}; border-radius:4px; padding:2px 6px; font-size:0.72rem; font-weight:600; '
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
        ("Score", "right"),
        ("Next GW", "right"),
        ("Proj Pts", "right"),
        (stat_label, "right"),
        (opp_stat_label, "right"),
        ("FDR (avg)", "right"),
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
            f'{html.escape(r["web_name"])}{diff_badge}{_doubtful_badge(r)}</td>'
            f'<td style="padding:6px 8px; white-space:nowrap;">{html.escape(r["team_short"])}</td>'
            f'<td style="padding:6px 8px;">{_fixture_chips_html(r["team"])}</td>'
            f'<td style="padding:6px 8px; text-align:right; white-space:nowrap;">£{r["price"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["selected_by_percent"]:.1f}%</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["mins_per_game"]:.0f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["score"]:+.2f}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700;">{r["projected_pts_next_gw"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["projected_pts"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r[stat_col]:.2f}</td>'
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
    player_picker(df["id"], df["web_name"], key=f"rec_picker_{df['position'].iloc[0]}")


def _attacking_section(position, label, emoji, n=15):
    st.divider()
    nav.section(f"{emoji} {label} (Next {num_gw} GW{'s' if num_gw > 1 else ''})", f"{emoji} {label}")
    st.caption(
        "Ranked by Next GW: modeled expected points for gameweek "
        f"{gw} alone. Score (also shown) is a separate equal-weight blend of own xGI/90, the upcoming "
        "opponents' xGC/90 (leakier defense = better), and fixture difficulty (easier = better), each "
        "standardized (z-scored) against this eligible pool. Proj Pts is the same points model summed "
        "over the full look-ahead window (see squad builder below). Doubles/blanks count properly. The "
        "squad builder's shortlist for this position also includes anyone in the multi-week Proj Pts "
        "top players, even if they don't crack this Next-GW list."
    )
    base = eligible[eligible["position"] == position].copy()
    base["differential"] = base["selected_by_percent"] <= DIFF_THRESHOLD
    if diffs_only:
        base = base[base["differential"]]
    by_next_gw = base.sort_values(["projected_pts_next_gw", "selected_by_percent"], ascending=[False, False]).head(n)
    by_window = base.sort_values(["projected_pts", "selected_by_percent"], ascending=[False, False]).head(n)
    render_recommendation_table(
        by_next_gw, "expected_goal_involvements_per_90", "xGI/90", "opp_xgc_per90", "Opp. xGC/90"
    )
    return pd.concat([by_next_gw, by_window]).drop_duplicates(subset="id")


def _defensive_section(position, label, emoji, n=15):
    st.divider()
    nav.section(f"{emoji} {label} (Next {num_gw} GW{'s' if num_gw > 1 else ''})", f"{emoji} {label}")
    st.caption(
        f"Ranked by Next GW: modeled expected points for gameweek {gw} alone. Score (also shown) is a "
        "separate equal-weight blend of fixture difficulty, own team's xGC/90, and the upcoming "
        "opponents' xG/90 — all three standardized (z-scored) against this eligible pool, lower being "
        "better on every one, so same-team players on the same fixtures can still tie on Score. Proj "
        "Pts is the same points model summed over the full look-ahead window (see squad builder below). "
        "Doubles/blanks count properly. The squad builder's shortlist for this position also includes "
        "anyone in the multi-week Proj Pts top players, even if they don't crack this Next-GW list."
    )
    base = eligible[eligible["position"] == position].copy()
    base["differential"] = base["selected_by_percent"] <= DIFF_THRESHOLD
    if diffs_only:
        base = base[base["differential"]]
    by_next_gw = base.sort_values(["projected_pts_next_gw", "selected_by_percent"], ascending=[False, False]).head(n)
    by_window = base.sort_values(["projected_pts", "selected_by_percent"], ascending=[False, False]).head(n)
    render_recommendation_table(
        by_next_gw,
        "own_xgc_per90",
        "Own xGC/90",
        "opp_xgf_per90",
        "Opp. xG/90",
        show_defcon=(position != "GKP"),
    )
    return pd.concat([by_next_gw, by_window]).drop_duplicates(subset="id")


def _solve_squad(pool, squad_budget):
    return solve_squad(pool, squad_budget)


def _solve_starting_xi(squad):
    return solve_starting_xi(squad)


def _availability(row):
    """Share of the gameweek this player is expected to be fit for: 0 if injured/suspended/unavailable,
    the news' chance of playing if flagged doubtful, otherwise 1."""
    if row["status"] in ("i", "s", "u", "n"):
        return 0.0
    chance = row["chance_of_playing_next_round"]
    if row["status"] == "d" and chance is not None and not pd.isna(chance):
        return float(chance) / 100
    return 1.0


def _expected_pts(row, fixtures_by_team):
    """The page's points model for one player over the given fixtures, scaled by their chance of playing
    (injury news) and by rotation risk (mins/game ÷ 75, capped at 1)."""
    raw = _project_points(row, fixtures_by_team.get(row["team"], []))
    raw = 0.0 if pd.isna(raw) else float(raw)
    minutes_factor = min(1.0, float(row["mins_per_game"]) / 75) if row["mins_per_game"] else 0.0
    return raw * _availability(row) * minutes_factor


def _best_xi_total(df, column):
    """Best valid XI's expected points (captain counted twice) using the given projection column."""
    d = df.copy()
    d["projected_pts"] = d[column]
    best, captain_id, vice_id = _solve_starting_xi(d)
    xi = best[best["is_starter"]]
    return xi["projected_pts"].sum() + best.loc[captain_id, "projected_pts"], best, captain_id


def _estimate_free_transfers(team_id):
    """Free transfers banked for the next deadline, replayed from the entry's gameweek history: +1 after
    each gameweek (max 5), minus transfers made; Wildcard / Free Hit weeks leave the bank untouched.
    None if the history can't be loaded. Doesn't know about one-off top-ups FPL sometimes grants."""
    try:
        history = get_entry_history(int(team_id))
    except Exception:
        return None
    rows = history.get("current") or []
    if not rows:
        return 1
    chip_weeks = {c["event"] for c in history.get("chips", []) if c["name"] in ("wildcard", "freehit")}
    ft = 1  # after the entry's first gameweek (squad-building transfers there are free)
    for row in rows[1:]:
        if row["event"] not in chip_weeks:
            ft = max(0, ft - row["event_transfers"])
        ft = min(5, ft + 1)
    return ft


TRANSFER_BENCH_WEIGHT = 0.1
TRANSFER_POOL_PER_POS = 40
TRANSFER_MIN_GAIN = 1.0
TRANSFER_MAX = 5


def _solve_transfers(pool, squad_ids, squad_budget, n_transfers, keep_ids=(), sell_ids=()):
    """Best squad reachable from the current one with exactly `n_transfers` changes, maximizing the
    window's best XI + captain, with the bench counted at a small weight so bench upgrades aren't free.
    `keep_ids` can't be sold and `sell_ids` must be. Same squad rules as the builder below. Returns the
    new squad's ids, or None if infeasible."""
    ids = list(pool.index)
    pts, pos = pool["_win"].to_dict(), pool["position"].to_dict()
    price, club = pool["price"].to_dict(), pool["team"].to_dict()
    prob = pulp.LpProblem("transfers", pulp.LpMaximize)
    pick = {i: pulp.LpVariable(f"pick_{i}", cat="Binary") for i in ids}
    start = {i: pulp.LpVariable(f"start_{i}", cat="Binary") for i in ids}
    cap = {i: pulp.LpVariable(f"cap_{i}", cat="Binary") for i in ids}
    prob += pulp.lpSum(
        pts[i] * (start[i] + cap[i] + TRANSFER_BENCH_WEIGHT * (pick[i] - start[i])) for i in ids
    )
    for i in ids:
        prob += start[i] <= pick[i]
        prob += cap[i] <= start[i]
    prob += pulp.lpSum(cap.values()) == 1
    prob += pulp.lpSum(start.values()) == 11
    for p, count, lo, hi in [("GKP", 2, 1, 1), ("DEF", 5, 3, 5), ("MID", 5, 2, 5), ("FWD", 3, 1, 3)]:
        prob += pulp.lpSum(pick[i] for i in ids if pos[i] == p) == count
        prob += pulp.lpSum(start[i] for i in ids if pos[i] == p) >= lo
        prob += pulp.lpSum(start[i] for i in ids if pos[i] == p) <= hi
    for club_id in set(club.values()):
        prob += pulp.lpSum(pick[i] for i in ids if club[i] == club_id) <= 3
    prob += pulp.lpSum(price[i] * pick[i] for i in ids) <= squad_budget + 1e-6
    prob += pulp.lpSum(pick[i] for i in squad_ids) == 15 - n_transfers
    for i in keep_ids:
        prob += pick[i] == 1
    for i in sell_ids:
        prob += pick[i] == 0
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        return None
    return [i for i in ids if pick[i].value() > 0.5]


def _pair_moves(sells, buys, all_players):
    """Pairs each outgoing player with an incoming one of the same position (the solver keeps
    position counts fixed, so a pairing always exists)."""
    buys_left = list(buys)
    pairs = []
    for s in sorted(sells, key=lambda s: -all_players.loc[s, "price"]):
        b = next(b for b in buys_left if all_players.loc[b, "position"] == all_players.loc[s, "position"])
        buys_left.remove(b)
        pairs.append((s, b))
    return pairs


def _sell_label(r):
    return f"{r['web_name']} ({r['position']}, {r['team_short']}, £{r['price']:.1f})"


def _buy_label(r):
    return (
        f"{'🚫 ' if r['status'] in ('i', 's', 'u') else ''}{r['web_name']} ({r['team_short']}, £{r['price']:.1f})"
        f" — GW{gw} {r['_exp']:.1f} · next {num_gw} GW {r['_win']:.1f}"
    )


def _render_transfer_suggestions(squad_rows, all_players, bank, free_transfers, est_ft):
    st.markdown("#### 🤖 Suggested transfers")
    if bank is None:
        st.caption("Couldn't load your bank balance, so transfer suggestions are unavailable right now.")
        return
    allow_gk = st.checkbox(
        "Include goalkeeper transfers", value=False, key="tp_allow_gk",
        help="Off by default: keepers have a low points ceiling, so a keeper transfer is rarely the best use "
        "of one. With this off, your keepers stay and the solver finds the best outfield moves instead.",
    )
    squad_ids = list(squad_rows.index)
    unavailable = [i for i in squad_ids if all_players.loc[i, "status"] in ("i", "s", "u", "n", "d")]
    sell_unavailable = st.checkbox(
        f"Transfer out injured / doubtful / suspended players first ({len(unavailable)} in your squad)",
        value=False, key="tp_sell_injured", disabled=not unavailable,
        help="Forces every injured, doubtful (75% / 50% / 25%), suspended or unavailable player in your squad "
        "out, then finds the best buys plus any extra moves.",
    )
    sell_ids = unavailable if sell_unavailable else []
    keep_ids = [] if allow_gk else [
        i for i in squad_ids if squad_rows.loc[i, "position"] == "GKP" and i not in sell_ids
    ]
    budget = bank + all_players.loc[squad_ids, "price"].sum()
    key = (
        "suggest", gw, num_gw, form_window, tuple(sorted(squad_ids)), round(bank, 1), allow_gk,
        tuple(sorted(sell_ids)),
    )
    if st.session_state.get("_suggest_key") != key:
        excluded = ["i", "s", "u", "n"] + (["d"] if sell_ids else [])
        available = all_players[~all_players["status"].isin(excluded) & (all_players["_win"] > 0)]
        top = available.sort_values("_win", ascending=False).groupby("position").head(TRANSFER_POOL_PER_POS)
        pool = all_players.loc[list(dict.fromkeys(list(top.index) + squad_ids))]
        before, _, _ = _best_xi_total(squad_rows, "projected_pts_window")
        options = []
        for k in range(max(1, len(sell_ids)), max(TRANSFER_MAX, len(sell_ids)) + 1):
            new_ids = _solve_transfers(pool, squad_ids, budget, k, keep_ids, sell_ids)
            if new_ids is None:
                continue
            sells = [i for i in squad_ids if i not in new_ids]
            buys = [i for i in new_ids if i not in squad_ids]
            after = all_players.loc[new_ids].assign(projected_pts_window=lambda d: d["_win"])
            after_total, _, _ = _best_xi_total(after, "projected_pts_window")
            options.append({"k": k, "pairs": _pair_moves(sells, buys, all_players), "gain": after_total - before})
        st.session_state["_suggest_key"] = key
        st.session_state["_suggest_options"] = options
    options = st.session_state["_suggest_options"]
    if not options:
        st.caption("The solver couldn't find a valid set of transfers within your budget.")
        return

    for o in options:
        o["hit"] = 4 * max(0, o["k"] - int(free_transfers))
        o["net"] = o["gain"] - o["hit"]
    best = max(options, key=lambda o: o["net"])
    span = f"the next {num_gw} GW{'s' if num_gw > 1 else ''}"
    if best["net"] < TRANSFER_MIN_GAIN:
        st.info(
            f"🧊 **Hold / roll your transfer.** The best move found is worth only {best['net']:+.1f} pts over "
            f"{span} after hits — inside the model's noise."
        )
    else:
        n = best["k"]
        hit_text = f" after the −{best['hit']} hit" if best["hit"] else ""
        st.success(
            f"✅ **Best: {n} transfer{'s' if n > 1 else ''}** — about {best['net']:+.1f} pts over {span}{hit_text}."
        )

    head = "".join(
        f'<th style="text-align:{a}; padding:6px 8px; font-size:0.78rem; opacity:0.7; white-space:nowrap;">{l}</th>'
        for l, a in [("Transfers", "left"), ("Net", "right"), ("Gain", "right"), ("Hit", "right"), ("Out → In", "left")]
    )
    rows_html = []
    for o in options:
        moves = "<br>".join(
            f'<span style="color:#ff4b6e; font-weight:700;">▼</span> {html.escape(all_players.loc[s, "web_name"])} '
            f'<span style="opacity:0.6;">£{all_players.loc[s, "price"]:.1f}</span> → '
            f'<span style="color:#00d97e; font-weight:700;">▲</span> <b>{html.escape(all_players.loc[b, "web_name"])}</b> <span style="opacity:0.6;">'
            f'({all_players.loc[b, "team_short"]}, £{all_players.loc[b, "price"]:.1f})</span>{_doubtful_badge(all_players.loc[b])}'
            for s, b in o["pairs"]
        )
        highlight = "background:rgba(0,255,135,0.12);" if o is best and best["net"] >= TRANSFER_MIN_GAIN else ""
        hit_cell = f"−{o['hit']}" if o["hit"] else "—"
        rows_html.append(
            f'<tr style="border-top:1px solid rgba(128,128,128,0.15); {highlight}">'
            f'<td style="padding:6px 8px; white-space:nowrap;">{o["k"]}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700;">{o["net"]:+.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{o["gain"]:+.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{hit_cell}</td>'
            f'<td style="padding:6px 8px; white-space:nowrap;">{moves}</td>'
            "</tr>"
        )
    st.markdown(
        '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>",
        unsafe_allow_html=True,
    )

    def _load(pairs):
        st.session_state["tp_n"] = len(pairs)
        st.session_state["tp_chip"] = "None"
        for i, (s, b) in enumerate(pairs):
            st.session_state[f"tp_sell_{i}"] = _sell_label(squad_rows.loc[s])
            st.session_state[f"tp_buy_{i}"] = _buy_label(all_players.loc[b])

    cols = st.columns(len(options))
    for col, o in zip(cols, options):
        col.button(
            f"Load {o['k']}-transfer plan", key=f"tp_load_{o['k']}",
            on_click=_load, args=(o["pairs"],), width="stretch",
        )
    ft_note = (
        f"{est_ft} free transfer{'s' if est_ft != 1 else ''} estimated from your transfer history"
        if est_ft is not None else "free transfers as entered below"
    )
    st.caption(
        f"For {max(1, len(sell_ids))} to {max(TRANSFER_MAX, len(sell_ids))} transfers"
        f"{'' if allow_gk else ' (keepers kept)'}{', flagged players sold first' if sell_ids else ''}, the solver searches every affordable combination (your bank + the current "
        f"price of whoever you sell, max 3 per club) for the squad with the best XI + captain over {span}, "
        f"using the same projections as the table above. Gain is measured the same way as the planner below; "
        f"hits assume {ft_note}. Selling prices use current prices — FPL's real selling price (half of any "
        f"rise) isn't public, so check your budget in the app. Suggestions under {TRANSFER_MIN_GAIN:+.0f} pt "
        "net are treated as noise."
    )


@st.fragment
def _render_transfer_planner(squad_rows, my_team_id):
    """Suggested transfers plus a before/after comparison for planned ones. A fragment, so changing a
    dropdown only reruns this block instead of the whole page."""
    bank = None
    try:
        bank = get_entry(int(my_team_id))["last_deadline_bank"] / 10
    except Exception:
        pass
    est_ft = _estimate_free_transfers(my_team_id)

    cache_key = ("planner_proj", gw, num_gw, form_window, len(players))
    if st.session_state.get("_planner_cache_key") != cache_key:
        projected = players.set_index("id")
        projected = projected.assign(
            _exp=projected.apply(lambda r: _expected_pts(r, next_gw_team_fixtures), axis=1),
            _win=projected.apply(lambda r: _expected_pts(r, team_fixtures), axis=1),
        )
        st.session_state["_planner_cache"] = projected
        st.session_state["_planner_cache_key"] = cache_key
    all_players = st.session_state["_planner_cache"]

    suggestions = st.container()

    st.markdown("#### 🔁 Plan a transfer — compare before / after")
    st.caption(
        "Pick who you'd sell and who you'd buy (same position). Both sides are compared using their best "
        "possible XI, so the change shown is what the transfer is really worth."
    )
    # Defaults go through session state (not value=) because "Load plan" buttons also write these keys.
    st.session_state.setdefault("tp_n", 1)
    st.session_state.setdefault("tp_chip", "None")
    st.session_state.setdefault("tp_ft", 1 if est_ft is None else est_ft)
    ctrl1, ctrl2, ctrl3 = st.columns(3)
    n_transfers = int(ctrl1.number_input("Number of transfers", min_value=1, max_value=15, key="tp_n"))
    chip = ctrl2.selectbox(
        "Chip", ["None", "Wildcard / Free Hit"], key="tp_chip",
        help="Both chips give unlimited free transfers, so no points hit is applied.",
    )
    free_transfers = ctrl3.number_input(
        "Free transfers you have", min_value=0, max_value=5, key="tp_ft", disabled=chip != "None",
        help=None if est_ft is None else f"Pre-filled with {est_ft}, estimated from your transfer history.",
    )
    with suggestions:
        _render_transfer_suggestions(squad_rows, all_players, bank, free_transfers, est_ft)
        st.divider()

    position_order = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    sellable = squad_rows.assign(_o=squad_rows["position"].map(position_order)).sort_values(["_o", "web_name"])
    sell_labels = {_sell_label(r): pid for pid, r in sellable.iterrows()}
    squad_ids = set(squad_rows.index)

    sells, buys = [], []
    for i in range(n_transfers):
        left, right = st.columns(2)
        remaining = [l for l, pid in sell_labels.items() if pid not in sells]
        sell_choice = left.selectbox(f"Transfer {i + 1}: sell", ["— choose —"] + remaining, key=f"tp_sell_{i}")
        if sell_choice == "— choose —":
            right.selectbox(f"Transfer {i + 1}: buy", ["— choose a player to sell first —"], key=f"tp_buy_{i}", disabled=True)
            continue
        sell_id = sell_labels[sell_choice]
        pos = squad_rows.loc[sell_id, "position"]
        pool = all_players[(all_players["position"] == pos) & ~all_players.index.isin(squad_ids | set(buys))]
        pool = pool.sort_values("_exp", ascending=False)
        buy_labels = {_buy_label(r): pid for pid, r in pool.iterrows()}
        buy_choice = right.selectbox(
            f"Transfer {i + 1}: buy ({pos}, best Next GW first)", ["— choose —"] + list(buy_labels), key=f"tp_buy_{i}"
        )
        if buy_choice == "— choose —":
            continue
        sells.append(sell_id)
        buys.append(buy_labels[buy_choice])

    if not sells:
        st.info("Choose a player to sell and one to buy to see the before / after comparison.")
        return

    buy_rows = all_players.loc[buys].copy()
    buy_rows["projected_pts"] = buy_rows["_exp"]
    buy_rows["projected_pts_window"] = buy_rows["_win"]
    after_squad = pd.concat([squad_rows.drop(index=sells), buy_rows], sort=False)

    gw_before, _, _ = _best_xi_total(squad_rows, "projected_pts")
    gw_after, after_best, after_captain = _best_xi_total(after_squad, "projected_pts")
    win_before, _, _ = _best_xi_total(squad_rows, "projected_pts_window")
    win_after, _, _ = _best_xi_total(after_squad, "projected_pts_window")
    hit = 0 if chip != "None" else 4 * max(0, len(sells) - int(free_transfers))

    a1, a2, a3 = st.columns(3)
    a1.metric(f"GW{gw} — best XI before", f"{gw_before:.1f}")
    a2.metric(f"GW{gw} — best XI after", f"{gw_after:.1f}")
    a3.metric("Change (next GW)", f"{gw_after - gw_before:+.1f}")
    b1, b2, b3 = st.columns(3)
    b1.metric(f"Next {num_gw} GW — before", f"{win_before:.1f}")
    b2.metric(f"Next {num_gw} GW — after", f"{win_after:.1f}")
    b3.metric(
        f"Change after {'−' + str(hit) + ' hit' if hit else 'no hit'}",
        f"{win_after - win_before - hit:+.1f}",
        f"{win_after - win_before:+.1f} before the hit" if hit else None,
        delta_color="off",
    )

    # --- legality checks (budget uses current prices; FPL's actual selling price can be slightly lower) ---
    sell_value = squad_rows.loc[sells, "price"].sum()
    buy_value = buy_rows["price"].sum()
    left_over = None if bank is None else bank + sell_value - buy_value
    club_counts = after_squad["team_short"].value_counts()
    too_many = club_counts[club_counts > 3]
    problems = []
    if left_over is not None and left_over < -0.001:
        problems.append(f"over budget by £{-left_over:.1f}m")
    if not too_many.empty:
        problems.append("more than 3 players from " + ", ".join(too_many.index))

    net = win_after - win_before - hit
    span = f"the next {num_gw} GW{'s' if num_gw > 1 else ''}"
    hit_text = f" after the −{hit} hit" if hit else ""
    if problems:
        st.warning(
            f"🚫 Not possible as chosen — {' and '.join(problems)}. On paper the change is {net:+.1f} pts over {span}{hit_text}."
        )
    elif net > 1.0:
        st.success(f"✅ Looks worth it: about {net:+.1f} pts over {span}{hit_text}.")
    elif net < -1.0:
        st.warning(f"❌ Probably not worth it: about {net:+.1f} pts over {span}{hit_text}.")
    else:
        st.info(f"➖ Marginal: about {net:+.1f} pts over {span}{hit_text} — within the model's noise.")
    if left_over is not None and left_over >= -0.001:
        st.caption(
            f"💰 Bank after transfers: £{left_over:.1f}m (bank £{bank:.1f}m + sales £{sell_value:.1f}m − purchases "
            f"£{buy_value:.1f}m, at current prices)."
        )

    # --- the moves side by side ---
    move_rows = []
    for sell_id, buy_id in zip(sells, buys):
        out, inn = squad_rows.loc[sell_id], buy_rows.loc[buy_id]
        move_rows.append(
            '<tr style="border-top:1px solid rgba(128,128,128,0.15);">'
            f'<td style="padding:6px 8px; font-weight:600;">⬇️ {html.escape(out["web_name"])} <span style="opacity:0.6;">({out["team_short"]}, £{out["price"]:.1f})</span></td>'
            f'<td style="padding:6px 8px; font-weight:600;">⬆️ {html.escape(inn["web_name"])} <span style="opacity:0.6;">({inn["team_short"]}, £{inn["price"]:.1f})</span>{_doubtful_badge(inn)}</td>'
            f'<td style="padding:6px 8px;">{_fixture_chips_html(inn["team"])}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{out["projected_pts"]:.1f} → <b>{inn["projected_pts"]:.1f}</b></td>'
            f'<td style="padding:6px 8px; text-align:right;">{out["projected_pts_window"]:.1f} → <b>{inn["projected_pts_window"]:.1f}</b></td>'
            "</tr>"
        )
    head = "".join(
        f'<th style="text-align:{a}; padding:6px 8px; font-size:0.78rem; opacity:0.7; white-space:nowrap;">{l}</th>'
        for l, a in [("Out", "left"), ("In", "left"), ("Incoming fixtures", "left"), (f"GW{gw} pts", "right"), (f"Next {num_gw} GW pts", "right")]
    )
    st.markdown(
        '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(move_rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    with st.expander("Suggested XI after these transfers"):
        xi = after_best[after_best["is_starter"]].assign(_o=lambda d: d["position"].map(position_order))
        xi = xi.sort_values(["_o", "projected_pts"], ascending=[True, False])
        st.markdown(
            "  \n".join(
                f"{r['position']} — **{r['web_name']}**{' (C)' if pid == after_captain else ''} ({r['projected_pts']:.1f} pts)"
                for pid, r in xi.iterrows()
            )
        )


def _render_my_squad_advice():
    st.divider()
    nav.section(f"🧑‍💼 Your Squad — Who to Start & Bench (GW{gw})", "🧑‍💼 Your Squad")
    my_team_id = str(st.session_state.get("team_id") or "").strip()
    if not my_team_id.isdigit():
        st.info("Enter your Team ID in the sidebar to get start / bench advice for your current squad.")
        return
    try:
        picks = get_entry_picks(int(my_team_id), current_event(bootstrap))["picks"]
    except Exception:
        st.warning("Couldn't load your squad right now — check your Team ID in the sidebar.")
        return

    pick_df = pd.DataFrame(picks)
    squad_rows = players.set_index("id").loc[pick_df["element"]].copy()
    squad_rows["slot"] = pick_df["position"].to_numpy()
    squad_rows["is_captain_now"] = pick_df["is_captain"].to_numpy()
    squad_rows["is_vice_now"] = pick_df["is_vice_captain"].to_numpy()
    squad_rows["fixtures_gw"] = squad_rows["team"].map(lambda t: len(next_gw_team_fixtures.get(t, [])))

    squad_rows["projected_pts"] = squad_rows.apply(lambda r: _expected_pts(r, next_gw_team_fixtures), axis=1)
    squad_rows["projected_pts_window"] = squad_rows.apply(lambda r: _expected_pts(r, team_fixtures), axis=1)
    squad_rows["current_starter"] = squad_rows["slot"] <= 11

    best, best_captain, best_vice = _solve_starting_xi(squad_rows)
    exp = best["projected_pts"]

    current_xi = best[best["current_starter"]]
    best_xi = best[best["is_starter"]]
    cur_captain = best.index[best["is_captain_now"]][0]
    cur_vice = best.index[best["is_vice_now"]][0]
    cur_cap_pts = exp[cur_captain] if _availability(best.loc[cur_captain]) > 0 else exp[cur_vice]
    current_total = current_xi["projected_pts"].sum() + cur_cap_pts
    best_total = best_xi["projected_pts"].sum() + exp[best_captain]

    c1, c2, c3 = st.columns(3)
    c1.metric("Your current XI (expected pts, incl. captain)", f"{current_total:.1f}")
    c2.metric("Suggested XI", f"{best_total:.1f}")
    c3.metric("Gain from changes", f"{best_total - current_total:+.1f}")

    # --- the advice ---
    to_start = list(best.index[best["is_starter"] & ~best["current_starter"]])
    to_bench = list(best.index[~best["is_starter"] & best["current_starter"]])
    lines = []
    for pid in to_start:
        partner = next((b for b in to_bench if best.loc[b, "position"] == best.loc[pid, "position"]), None)
        if partner is None and to_bench:
            partner = to_bench[0]
        if partner is not None:
            to_bench.remove(partner)
            lines.append(
                f"⬆️ **Start {best.loc[pid, 'web_name']}** ({best.loc[pid, 'position']}, {exp[pid]:.1f} pts) "
                f"instead of **{best.loc[partner, 'web_name']}** ({best.loc[partner, 'position']}, {exp[partner]:.1f} pts)"
            )
    if not lines:
        st.success("Your starting XI already matches the model's best XI for this gameweek.")
    for line in lines:
        st.markdown(line)
    if best_captain != cur_captain:
        st.markdown(
            f"©️ **Captain {best.loc[best_captain, 'web_name']}** ({exp[best_captain]:.1f} pts) — currently "
            f"{best.loc[cur_captain, 'web_name']} ({exp[cur_captain]:.1f} pts). "
            f"Vice: {best.loc[best_vice, 'web_name']}."
        )
    else:
        st.markdown(f"©️ Captain {best.loc[best_captain, 'web_name']} is the model's best captain too.")

    warnings = []
    for pid, r in best.iterrows():
        if r["fixtures_gw"] == 0:
            warnings.append(f"**{r['web_name']}** has no fixture in GW{gw}.")
        elif r["status"] in ("i", "s", "u", "n"):
            warnings.append(f"**{r['web_name']}** is out — {r['news'] or STATUS_LABELS.get(r['status'], 'unavailable')}.")
        elif r["status"] == "d":
            warnings.append(f"**{r['web_name']}** is doubtful — {r['news'] or 'check the latest news'}.")
    if warnings:
        with st.expander(f"⚠️ {len(warnings)} availability warning(s) in your squad", expanded=True):
            for w in warnings:
                st.markdown(f"- {w}")

    # --- full squad table ---
    order = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    # Starters by position; bench in FPL's substitute order (keeper first, then outfield by expected points).
    table = best.assign(
        _bench=~best["is_starter"],
        _pos=[
            (0 if pos == "GKP" else 1) if bench_flag else order[pos]
            for pos, bench_flag in zip(best["position"], ~best["is_starter"])
        ],
    )
    table = table.sort_values(["_bench", "_pos", "projected_pts"], ascending=[True, True, False])
    bench_labels, outfield_n = {}, 0
    for pid, r in table[table["_bench"]].iterrows():
        if r["position"] == "GKP":
            bench_labels[pid] = "BENCH GK"
        else:
            outfield_n += 1
            bench_labels[pid] = f"BENCH {outfield_n}"
    header = "".join(
        f'<th style="text-align:{align}; padding:6px 8px; font-size:0.78rem; opacity:0.7; white-space:nowrap;">{label}</th>'
        for label, align in [
            ("Suggested", "left"), ("Now", "left"), ("Player", "left"), ("Team", "left"), ("Pos", "left"),
            ("Fixtures", "left"), (f"GW{gw} Pts", "right"), (f"Next {num_gw} GW Pts", "right"), ("Mins/Game", "right"),
        ]
    )
    rows_html = []
    for pid, r in table.iterrows():
        badge_style = "background:#00ff87; color:#14001c;" if r["is_starter"] else "background:rgba(128,128,128,0.35);"
        role = " (C)" if pid == best_captain else " (VC)" if pid == best_vice else ""
        moved = r["is_starter"] != r["current_starter"]
        move_mark = " ⬆️" if moved and r["is_starter"] else " ⬇️" if moved else ""
        rows_html.append(
            '<tr style="border-top:1px solid rgba(128,128,128,0.15);">'
            f'<td style="padding:6px 8px; white-space:nowrap;"><span style="{badge_style} font-size:0.7rem; '
            f'font-weight:700; padding:2px 8px; border-radius:4px;">{"START" if r["is_starter"] else bench_labels[pid]}</span>{move_mark}</td>'
            f'<td style="padding:6px 8px; opacity:0.75;">{"XI" if r["current_starter"] else "Bench"}</td>'
            f'<td style="padding:6px 8px; font-weight:600; white-space:nowrap;">{html.escape(r["web_name"])}{role}{_doubtful_badge(r)}</td>'
            f'<td style="padding:6px 8px;">{html.escape(r["team_short"])}</td>'
            f'<td style="padding:6px 8px;">{r["position"]}</td>'
            f'<td style="padding:6px 8px;">{_fixture_chips_html(r["team"])}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700;">{r["projected_pts"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["projected_pts_window"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["mins_per_game"]:.0f}</td>'
            "</tr>"
        )
    st.markdown(
        '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    player_picker(table.index, table["web_name"], key="rec_picker_my_squad")
    st.caption(
        f"GW{gw} Pts is the Next GW projection from the model above (GW{gw} only, doubles and blanks counted), "
        "scaled by each player's chance of playing (injury news) and by rotation risk (mins/game ÷ 75, capped "
        f"at 1). Next {num_gw} GW Pts is the same model summed over the look-ahead window set by the slider above. "
        "The suggested XI, captain and vice are picked by the same optimizer as the squad builder below, "
        "within FPL's formation rules. Your squad is as of the last deadline — transfers made since then aren't "
        "visible to FPL's public data, so re-check if you've changed your team."
    )
    st.divider()
    _render_transfer_planner(squad_rows, my_team_id)


_render_my_squad_advice()

gk_pool = _defensive_section("GKP", "Goalkeepers", "🥅", n=10)
def_pool = _defensive_section("DEF", "Defenders", "🛡️")
mid_pool = _attacking_section("MID", "Midfielders", "🎯")
fwd_pool = _attacking_section("FWD", "Forwards", "⚡")


POSITION_LIST = {"GKP": ("Goalkeepers", 10), "DEF": ("Defenders", 15), "MID": ("Midfielders", 15), "FWD": ("Forwards", 15)}


def _lookup_reason(row):
    """Why this player is or isn't in their position's list above, given the same filters and cutoffs."""
    if pd.isna(row["opponents"]):
        return "No fixture in this look-ahead window"
    if row["status"] in ("i", "s", "u"):
        return f"{STATUS_LABELS[row['status']]} — {row['news'] or 'no details'}"
    if row["minutes"] < 180:
        return f"Only {row['minutes']:.0f} mins played this season (needs 180+)"
    if row["mins_per_game"] < 75:
        return f"Averages {row['mins_per_game']:.0f} mins/game — rotation risk (needs 75+)"
    label, n = POSITION_LIST[row["position"]]
    if diffs_only and row["selected_by_percent"] > DIFF_THRESHOLD:
        return f"Hidden by the differentials filter ({row['selected_by_percent']:.1f}% owned)"
    pool = eligible[eligible["position"] == row["position"]]
    if diffs_only:
        pool = pool[pool["selected_by_percent"] <= DIFF_THRESHOLD]
    ranked = pool.sort_values(["projected_pts_next_gw", "selected_by_percent"], ascending=[False, False])
    rank = list(ranked["id"]).index(row["id"]) + 1
    if rank <= n:
        return f"In the {label} list (#{rank})"
    return f"#{rank} of {len(ranked)} {label.lower()} by Next GW — list shows the top {n}"


def _player_lookup_section():
    st.divider()
    nav.section("🔎 Look Up Any Player", "🔎 Look Up")
    st.caption(
        "Not in the lists above? Search any player to see the same Next GW / Proj Pts model and why they "
        "were left out. For players who fail the minutes or availability filters, the points still assume "
        "they start and play the full match, so treat them as a ceiling."
    )
    by_name = players.sort_values("web_name")
    labels = {
        int(pid): f"{name} ({team_short.get(team, '?')}, {pos}, £{price:.1f})"
        for pid, name, team, pos, price in zip(
            by_name["id"], by_name["web_name"], by_name["team"], by_name["position"], by_name["price"]
        )
    }
    chosen = st.multiselect(
        "Players", list(labels), format_func=lambda i: labels.get(i, ""), key="rec_lookup",
        placeholder="Type a player's name",
    )
    if not chosen:
        return
    rows = players[players["id"].isin(chosen)].copy()
    rows["next_gw_pts"] = rows.apply(lambda r: _project_points(r, next_gw_team_fixtures.get(r["team"], [])), axis=1)
    rows["window_pts"] = rows.apply(lambda r: _project_points(r, team_fixtures.get(r["team"], [])), axis=1)
    rows["reason"] = rows.apply(_lookup_reason, axis=1)
    rows = rows.set_index("id").loc[chosen].reset_index()

    columns = [
        ("Player", "left"), ("Team", "left"), ("Pos", "left"), ("Fixtures", "left"), ("Price", "right"),
        ("Owned %", "right"), ("Mins/Game", "right"), ("Next GW", "right"), ("Proj Pts", "right"),
        ("Why not listed", "left"),
    ]
    header = "<tr>" + "".join(
        f'<th style="text-align:{align}; padding:6px 8px; font-size:0.78rem; opacity:0.7; '
        f'white-space:nowrap;">{label}</th>'
        for label, align in columns
    ) + "</tr>"
    rows_html = []
    for _, r in rows.iterrows():
        listed = r["reason"].startswith("In the ")
        reason_style = "opacity:0.75;" if listed else "color:#FFC107;"
        rows_html.append(
            '<tr style="border-top:1px solid rgba(128,128,128,0.15);">'
            f'<td style="padding:6px 8px; font-weight:600; white-space:nowrap;">'
            f'{html.escape(r["web_name"])}{_doubtful_badge(r)}</td>'
            f'<td style="padding:6px 8px; white-space:nowrap;">{html.escape(r["team_short"])}</td>'
            f'<td style="padding:6px 8px;">{r["position"]}</td>'
            f'<td style="padding:6px 8px;">{_fixture_chips_html(r["team"])}</td>'
            f'<td style="padding:6px 8px; text-align:right; white-space:nowrap;">£{r["price"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["selected_by_percent"]:.1f}%</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["mins_per_game"]:.0f}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700;">{r["next_gw_pts"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["window_pts"]:.1f}</td>'
            f'<td style="padding:6px 8px; {reason_style}">{html.escape(r["reason"])}</td>'
            "</tr>"
        )
    st.markdown(
        '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f"<thead>{header}</thead><tbody>{''.join(rows_html)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    player_picker(rows["id"], rows["web_name"], key="rec_picker_lookup")


_player_lookup_section()


st.divider()
nav.section("💰 Recommended 15 — Best Squad Within Budget", "💰 Recommended 15")

default_budget = 100.0
team_id = st.session_state.get("team_id")
if team_id:
    try:
        default_budget = get_entry(int(team_id))["last_deadline_value"] / 10
    except Exception:
        pass

budget = st.number_input(
    "Squad budget (£m)",
    min_value=80.0,
    max_value=110.0,
    value=round(float(default_budget), 1),
    step=0.1,
)
if team_id and abs(budget - default_budget) < 0.01:
    st.caption(f"Pre-filled from your team's actual value (£{default_budget:.1f}m) — adjust if you're planning ahead.")

st.caption(
    f"A full 15-man squad (2 GK, 5 DEF, 5 MID, 3 FWD, max 3 players per club) that maximizes total "
    f"Projected Pts over the next {num_gw} GW{'s' if num_gw > 1 else ''} while staying at or under the "
    "budget above. Solved as an integer optimization problem (PuLP) — the best affordable combination "
    "under FPL's real squad rules, not a guess. Drawn from each position's top players by *both* Next "
    "GW and multi-week Proj Pts (not just the rows shown above), so a great multi-week fixture run "
    "isn't excluded just because next week alone is tough, and vice versa."
)

candidate_pool = pd.concat([gk_pool, def_pool, mid_pool, fwd_pool])
pool = candidate_pool.set_index("id")

squad = _solve_squad(pool, budget)

if squad is None:
    st.warning("Couldn't find a valid squad under these constraints — try widening the look-ahead window.")
else:
    POSITION_ORDER = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    squad = squad.assign(_order=squad["position"].map(POSITION_ORDER)).sort_values(["_order", "price"], ascending=[True, False])

    squad, captain_id, vice_id = _solve_starting_xi(squad)
    starters = squad[squad["is_starter"]]
    bench = squad[~squad["is_starter"]]
    bench_gk = bench[bench["position"] == "GKP"]
    bench_outfield = bench[bench["position"] != "GKP"].sort_values("projected_pts", ascending=False)
    bench_ordered = pd.concat([bench_outfield, bench_gk])
    display_order = pd.concat([starters, bench_ordered])
    starting_xi_pts = starters["projected_pts"].sum() + starters.loc[captain_id, "projected_pts"]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Cost", f"£{squad['price'].sum():.1f}m")
    col2.metric("Budget Remaining", f"£{budget - squad['price'].sum():.1f}m")
    col3.metric(f"Squad Projected Pts ({num_gw} GW)", f"{squad['projected_pts'].sum():.1f}")
    col4.metric("Starting XI Pts (incl. Captain)", f"{starting_xi_pts:.1f}")
    st.caption(
        "Starting XI picked by the same optimizer, subject to FPL's formation rules (1 GK, 3-5 DEF, "
        "2-5 MID, 1-3 FWD) — captain is the highest-projected starter, vice-captain the next best. "
        "Bench order: outfield subs by projected points, keeper last, matching FPL's own auto-sub priority."
    )

    header = "".join(
        f'<th style="text-align:{align}; padding:6px 8px; font-size:0.78rem; opacity:0.7; white-space:nowrap;">{label}</th>'
        for label, align in [
            ("Status", "left"),
            ("Pos", "left"),
            ("Player", "left"),
            ("Team", "left"),
            ("Fixtures", "left"),
            ("Price", "right"),
            ("Owned %", "right"),
            ("Mins/Game", "right"),
            ("PPG", "right"),
            ("Score", "right"),
            ("Proj Pts", "right"),
            ("DEFCON/90", "right"),
        ]
    )
    rows_html = []
    for idx, r in display_order.iterrows():
        role_badge = ""
        if idx == captain_id:
            role_badge = (
                ' <span style="background:#ffb300; color:#1a1a1a; font-size:0.62rem; font-weight:700; '
                'padding:1px 4px; border-radius:4px; margin-left:2px;">C</span>'
            )
        elif idx == vice_id:
            role_badge = (
                ' <span style="background:rgba(128,128,128,0.35); font-size:0.62rem; font-weight:700; '
                'padding:1px 4px; border-radius:4px; margin-left:2px;">VC</span>'
            )
        row_style = "background:rgba(128,128,128,0.16);" if not r["is_starter"] else ""
        rows_html.append(
            f'<tr style="border-top:1px solid rgba(128,128,128,0.15); {row_style}">'
            f'<td style="padding:6px 8px; font-size:0.75rem; opacity:0.7;">{"Starting" if r["is_starter"] else "Bench"}</td>'
            f'<td style="padding:6px 8px; opacity:0.7;">{r["position"]}</td>'
            f'<td style="padding:6px 8px; font-weight:600; white-space:nowrap;">'
            f'{html.escape(r["web_name"])}{role_badge}{_doubtful_badge(r)}</td>'
            f'<td style="padding:6px 8px; white-space:nowrap;">{html.escape(r["team_short"])}</td>'
            f'<td style="padding:6px 8px;">{_fixture_chips_html(r["team"])}</td>'
            f'<td style="padding:6px 8px; text-align:right; white-space:nowrap;">£{r["price"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["selected_by_percent"]:.1f}%</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["mins_per_game"]:.0f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["points_per_game"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["score"]:+.2f}</td>'
            f'<td style="padding:6px 8px; text-align:right; font-weight:700;">{r["projected_pts"]:.1f}</td>'
            f'<td style="padding:6px 8px; text-align:right;">{r["defensive_contribution_per_90"]:.2f}</td>'
            "</tr>"
        )
    st.markdown(
        '<div style="overflow-x:auto;"><table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    player_picker(display_order.index, display_order["web_name"], key="rec_picker_best15")

    if is_owner():
        st.divider()
        st.markdown("**📌 Track this GW's optimal squad**")
        st.caption(
            f"Saves a squad optimized for GW{gw} alone (not the {num_gw}-GW window above — a fair, single "
            "gameweek snapshot is what you'd actually compare against real results). Check its performance "
            "on the Squad Tracker page after the gameweek finishes."
        )
    if is_owner() and st.button(f"Save optimal GW{gw} squad to track"):
        snapshot = optimal_gw_snapshot(players, next_gw_team_fixtures, team_short, team_rates, gw, budget)
        if snapshot is None:
            st.warning("Couldn't find a valid single-gameweek squad under this budget.")
        else:
            add_snapshot(snapshot)
            st.success(f"Saved! Head to the Squad Tracker page to follow how this GW{gw} squad performs.")
