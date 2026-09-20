import html
import math
import uuid
from datetime import datetime, timezone

import pandas as pd
import pulp
import streamlit as st

from src.config import is_owner, render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_entry, get_event_live, get_fixtures
from src.tracker import add_snapshot
from src.utils import next_event, players_df, teams_df

st.set_page_config(page_title="Recommendations - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Recommendations")
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

for col in ("goals_scored", "goals_conceded", "minutes"):
    players[col] = pd.to_numeric(players[col], errors="coerce")

team_games_played = {tid: 0 for tid in team_short}
for f in fixtures:
    # finished_provisional covers matches that have ended in a gameweek that isn't fully closed yet;
    # players' minutes already include those matches, so the denominator must too.
    if not (f["finished"] or f.get("finished_provisional")):
        continue
    team_games_played[f["team_h"]] = team_games_played.get(f["team_h"], 0) + 1
    team_games_played[f["team_a"]] = team_games_played.get(f["team_a"], 0) + 1
games_played_series = players["team"].map(team_games_played).replace(0, pd.NA)
players["mins_per_game"] = (players["minutes"] / games_played_series).fillna(0)

finished_events = sorted(e["id"] for e in bootstrap["events"] if e["finished"])

if finished_events:
    form_window = st.slider(
        "Recent form window (gameweeks)",
        1,
        len(finished_events),
        len(finished_events),
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

GOAL_PTS = {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
DC_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}
MULT_MIN, MULT_MAX = 0.6, 1.6


def _recent_stats(window):
    """One pass over the most recent `window` finished gameweeks' live data, producing both
    team-level attacking/defensive per-90 rates (for fixture difficulty) and each player's own
    per-match averages (bonus, cards, penalties, own goals, defensive-contribution hit-rate,
    saves) — the inputs the expected-points model below is built from."""
    events = finished_events[-window:] if window > 0 else []
    id_to_team = dict(zip(players["id"], players["team"]))
    id_to_pos = dict(zip(players["id"], players["position"]))
    xgf_sum = {tid: 0.0 for tid in team_short}
    xgc_sum = {tid: 0.0 for tid in team_short}
    team_games = {tid: 0 for tid in team_short}
    player_games, player_bonus = {}, {}
    player_card_pts, player_pen_pts, player_og_pts = {}, {}, {}
    player_dc_hits, player_saves = {}, {}

    for ev in events:
        for el in get_event_live(ev)["elements"]:
            stats = el["stats"]
            if stats["minutes"] <= 0:
                continue
            pid = el["id"]
            team = id_to_team.get(pid)
            if team is None:
                continue
            pos = id_to_pos.get(pid)
            xgf_sum[team] += float(stats["expected_goals"])
            if pos == "GKP":
                xgc_sum[team] += float(stats["expected_goals_conceded"])

            player_games[pid] = player_games.get(pid, 0) + 1
            player_bonus[pid] = player_bonus.get(pid, 0) + stats["bonus"]
            player_card_pts[pid] = (
                player_card_pts.get(pid, 0.0) - stats["yellow_cards"] - 3 * stats["red_cards"]
            )
            player_pen_pts[pid] = (
                player_pen_pts.get(pid, 0.0) + 5 * stats["penalties_saved"] - 2 * stats["penalties_missed"]
            )
            player_og_pts[pid] = player_og_pts.get(pid, 0.0) - 2 * stats["own_goals"]
            player_saves[pid] = player_saves.get(pid, 0) + stats["saves"]
            threshold = DC_THRESHOLD.get(pos)
            if threshold is not None and stats["defensive_contribution"] >= threshold:
                player_dc_hits[pid] = player_dc_hits.get(pid, 0) + 1

        for f in fixtures:
            if f["event"] != ev or not f["finished"]:
                continue
            team_games[f["team_h"]] = team_games.get(f["team_h"], 0) + 1
            team_games[f["team_a"]] = team_games.get(f["team_a"], 0) + 1

    games_srs = pd.Series(team_games, dtype="float64").replace(0, pd.NA)
    xgf90 = (pd.Series(xgf_sum).reindex(games_srs.index) / games_srs).to_dict()
    xgc90 = (pd.Series(xgc_sum).reindex(games_srs.index) / games_srs).to_dict()

    player_window = pd.DataFrame(
        {
            "games": player_games,
            "bonus": player_bonus,
            "card_pts": player_card_pts,
            "pen_pts": player_pen_pts,
            "og_pts": player_og_pts,
            "dc_hits": player_dc_hits,
            "saves": player_saves,
        }
    ).fillna(0)
    return xgf90, xgc90, player_window


team_xgf_per90, team_xgc_per90, player_window = _recent_stats(form_window)
league_median_xgf90 = pd.Series(team_xgf_per90).median()
league_median_xgc90 = pd.Series(team_xgc_per90).median()

_pw = player_window.reindex(players["id"]).fillna(0)
_pw_games = _pw["games"].replace(0, pd.NA)
players["avg_bonus_pts"] = (_pw["bonus"] / _pw_games).fillna(0).to_numpy()
players["avg_card_pts"] = (_pw["card_pts"] / _pw_games).fillna(0).to_numpy()
players["avg_pen_pts"] = (_pw["pen_pts"] / _pw_games).fillna(0).to_numpy()
players["avg_og_pts"] = (_pw["og_pts"] / _pw_games).fillna(0).to_numpy()
players["dc_rate"] = (_pw["dc_hits"] / _pw_games).fillna(0).to_numpy()
players["avg_saves"] = (_pw["saves"] / _pw_games).fillna(0).to_numpy()

num_gw = st.slider("Look ahead (gameweeks)", 1, 8, 5)
gw_range = set(range(gw, gw + num_gw))

team_fixtures = {}
for f in fixtures:
    if f["event"] not in gw_range:
        continue
    team_fixtures.setdefault(f["team_h"], []).append((f["team_a"], f["team_h_difficulty"], True))
    team_fixtures.setdefault(f["team_a"], []).append((f["team_h"], f["team_a_difficulty"], False))

next_gw_team_fixtures = {}
for f in fixtures:
    if f["event"] != gw:
        continue
    next_gw_team_fixtures.setdefault(f["team_h"], []).append((f["team_a"], f["team_h_difficulty"], True))
    next_gw_team_fixtures.setdefault(f["team_a"], []).append((f["team_h"], f["team_a_difficulty"], False))


def _safe_lookup(mapping, key, default):
    val = mapping.get(key)
    return default if val is None or pd.isna(val) else val


def _window_summary(team_id, fixtures_by_team):
    fx = fixtures_by_team.get(team_id, [])
    if not fx:
        return None
    return {
        "opponents": ", ".join(team_short.get(opp, "?") for opp, _, _h in fx),
        "difficulty": sum(d for _, d, _h in fx) / len(fx),
        "opp_xgc_per90": sum(_safe_lookup(team_xgc_per90, opp, league_median_xgc90) for opp, _, _h in fx) / len(fx),
        "opp_xgf_per90": sum(_safe_lookup(team_xgf_per90, opp, league_median_xgf90) for opp, _, _h in fx) / len(fx),
        "num_fixtures": len(fx),
    }


def _add_window_columns(df, fixtures_by_team):
    """Attaches this fixture window's opponent difficulty/xG stats, plus each player's own
    team's xGC/90, to every row — the shared inputs the score and points model below are built from."""
    summaries = {tid: _window_summary(tid, fixtures_by_team) for tid in team_short}
    df = df.copy()
    df["opponents"] = df["team"].map(lambda t: (summaries.get(t) or {}).get("opponents"))
    df["difficulty"] = df["team"].map(lambda t: (summaries.get(t) or {}).get("difficulty"))
    df["opp_xgc_per90"] = df["team"].map(lambda t: (summaries.get(t) or {}).get("opp_xgc_per90"))
    df["opp_xgf_per90"] = df["team"].map(lambda t: (summaries.get(t) or {}).get("opp_xgf_per90"))
    df["num_fixtures"] = df["team"].map(lambda t: (summaries.get(t) or {}).get("num_fixtures"))
    df["own_xgc_per90"] = df["team"].map(lambda t: _safe_lookup(team_xgc_per90, t, league_median_xgc90))
    return df


def _zscore(s):
    std = s.std(ddof=0)
    if not std or pd.isna(std):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def _add_scores(df):
    """Equal-weight composite: attackers get own xGI/90 + opponent xGC/90 - FDR (all z-scored
    against the eligible pool); keepers/defenders get -(FDR + own xGC/90 + opponent xG/90), since
    all three point the same way there (lower is better)."""
    df = df.copy()
    df["score"] = 0.0
    att_mask = df["position"].isin(["MID", "FWD"])
    def_mask = df["position"].isin(["GKP", "DEF"])
    if att_mask.any():
        sub = df.loc[att_mask]
        z_xgi = _zscore(sub["expected_goal_involvements_per_90"])
        z_opp_xgc = _zscore(sub["opp_xgc_per90"])
        z_fdr = _zscore(sub["difficulty"])
        df.loc[att_mask, "score"] = (z_xgi + z_opp_xgc - z_fdr) / 3
    if def_mask.any():
        sub = df.loc[def_mask]
        z_fdr = _zscore(sub["difficulty"])
        z_own_xgc = _zscore(sub["own_xgc_per90"])
        z_opp_xgf = _zscore(sub["opp_xgf_per90"])
        df.loc[def_mask, "score"] = -(z_fdr + z_own_xgc + z_opp_xgf) / 3
    return df


def _project_points(row, fx):
    """Expected FPL points summed across the fixtures in `fx`, built from underlying per-90 rates
    rather than points-per-game: appearance + goals/assists (own attacking rate scaled by the
    specific opponent's defensive record) + clean-sheet probability (Poisson zero-goal chance from
    own defensive rate scaled by the opponent's attacking record) + goals-conceded/saves, plus
    bonus/cards/penalties/own-goals/defensive-contribution taken as this window's per-match average."""
    if not fx:
        return 0.0
    pos = row["position"]
    flat = row["avg_bonus_pts"] + row["avg_card_pts"] + row["avg_pen_pts"] + row["avg_og_pts"]
    if pos != "GKP":
        flat += row["dc_rate"] * 2.0
    total = 0.0
    for opp, _diff, _home in fx:
        opp_xgc = _safe_lookup(team_xgc_per90, opp, league_median_xgc90)
        opp_xgf = _safe_lookup(team_xgf_per90, opp, league_median_xgf90)
        atk_mult = (opp_xgc / league_median_xgc90) if league_median_xgc90 else 1.0
        def_mult = (opp_xgf / league_median_xgf90) if league_median_xgf90 else 1.0
        atk_mult = min(max(atk_mult, MULT_MIN), MULT_MAX)
        def_mult = min(max(def_mult, MULT_MIN), MULT_MAX)

        pts = 2.0 + flat  # appearance points — eligible pool already averages 75+ mins/game
        pts += row["expected_goals_per_90"] * atk_mult * GOAL_PTS[pos]
        pts += row["expected_assists_per_90"] * atk_mult * 3.0

        if pos != "FWD":
            exp_conceded = row["own_xgc_per90"] * def_mult
            pts += math.exp(-exp_conceded) * CS_PTS[pos]
            if pos in ("GKP", "DEF"):
                pts -= 0.5 * exp_conceded
                if pos == "GKP":
                    pts += (row["avg_saves"] * def_mult) / 3.0
        total += pts
    return total


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
    for opp, diff, is_home in team_fixtures.get(team_id, []):
        color = FDR_COLORS.get(round(diff), "rgba(128,128,128,0.15)")
        code = html.escape(team_short.get(opp, "?")) + (" (H)" if is_home else " (A)")
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


def _attacking_section(position, label, emoji, n=15):
    st.divider()
    st.subheader(f"{emoji} {label} (Next {num_gw} GW{'s' if num_gw > 1 else ''})")
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
    st.subheader(f"{emoji} {label} (Next {num_gw} GW{'s' if num_gw > 1 else ''})")
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


gk_pool = _defensive_section("GKP", "Goalkeepers", "🥅", n=10)
def_pool = _defensive_section("DEF", "Defenders", "🛡️")
mid_pool = _attacking_section("MID", "Midfielders", "🎯")
fwd_pool = _attacking_section("FWD", "Forwards", "⚡")


def _solve_squad(pool, squad_budget):
    """Best 15-man squad (2 GK, 5 DEF, 5 MID, 3 FWD, max 3/club) within budget, maximizing
    total Projected Pts. Returns None if infeasible."""
    prob = pulp.LpProblem("squad", pulp.LpMaximize)
    pick = {i: pulp.LpVariable(f"pick_{i}", cat="Binary") for i in pool.index}
    prob += pulp.lpSum(pool.loc[i, "projected_pts"] * pick[i] for i in pool.index)
    prob += pulp.lpSum(pool.loc[i, "price"] * pick[i] for i in pool.index) <= squad_budget
    for pos, count in [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]:
        prob += pulp.lpSum(pick[i] for i in pool.index if pool.loc[i, "position"] == pos) == count
    for club_id in pool["team"].unique():
        prob += pulp.lpSum(pick[i] for i in pool.index if pool.loc[i, "team"] == club_id) <= 3
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[prob.status] != "Optimal":
        return None
    ids = [i for i in pool.index if pick[i].value() == 1]
    return pool.loc[ids].copy()


def _solve_starting_xi(squad):
    """Best valid XI (1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD) plus captain/vice-captain, maximizing
    total Projected Pts. Returns (squad with is_starter column, captain_id, vice_id)."""
    prob = pulp.LpProblem("starting_xi", pulp.LpMaximize)
    start = {i: pulp.LpVariable(f"start_{i}", cat="Binary") for i in squad.index}
    prob += pulp.lpSum(squad.loc[i, "projected_pts"] * start[i] for i in squad.index)
    prob += pulp.lpSum(start[i] for i in squad.index) == 11
    prob += pulp.lpSum(start[i] for i in squad.index if squad.loc[i, "position"] == "GKP") == 1
    prob += pulp.lpSum(start[i] for i in squad.index if squad.loc[i, "position"] == "DEF") >= 3
    prob += pulp.lpSum(start[i] for i in squad.index if squad.loc[i, "position"] == "DEF") <= 5
    prob += pulp.lpSum(start[i] for i in squad.index if squad.loc[i, "position"] == "MID") >= 2
    prob += pulp.lpSum(start[i] for i in squad.index if squad.loc[i, "position"] == "MID") <= 5
    prob += pulp.lpSum(start[i] for i in squad.index if squad.loc[i, "position"] == "FWD") >= 1
    prob += pulp.lpSum(start[i] for i in squad.index if squad.loc[i, "position"] == "FWD") <= 3
    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    starter_ids = {i for i in squad.index if start[i].value() == 1}
    squad = squad.copy()
    squad["is_starter"] = squad.index.isin(starter_ids)
    starters = squad[squad["is_starter"]]
    captain_id = starters["projected_pts"].idxmax()
    remaining = starters.drop(index=captain_id)
    vice_id = remaining["projected_pts"].idxmax() if not remaining.empty else None
    return squad, captain_id, vice_id


st.divider()
st.subheader("💰 Recommended 15 — Best Squad Within Budget")

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

    if is_owner():
        st.divider()
        st.markdown("**📌 Track this GW's optimal squad**")
        st.caption(
            f"Saves a squad optimized for GW{gw} alone (not the {num_gw}-GW window above — a fair, single "
            "gameweek snapshot is what you'd actually compare against real results). Check its performance "
            "on the Squad Tracker page after the gameweek finishes."
        )
    if is_owner() and st.button(f"Save optimal GW{gw} squad to track"):
        snap_players = _add_window_columns(players, next_gw_team_fixtures)
        snap_base = snap_players[
            snap_players["opponents"].notna()
            & (snap_players["minutes"] >= 180)
            & (snap_players["mins_per_game"] >= 75)
            & (~snap_players["status"].isin(["i", "s", "u"]))
        ].copy()
        snap_base = _add_scores(snap_base)
        snap_base["projected_pts"] = snap_base.apply(
            lambda r: _project_points(r, next_gw_team_fixtures.get(r["team"], [])), axis=1
        )

        snap_candidates = pd.concat(
            [
                snap_base[snap_base["position"] == "GKP"].sort_values("score", ascending=False).head(10),
                snap_base[snap_base["position"] == "DEF"].sort_values("score", ascending=False).head(15),
                snap_base[snap_base["position"] == "MID"].sort_values("score", ascending=False).head(15),
                snap_base[snap_base["position"] == "FWD"].sort_values("score", ascending=False).head(15),
            ]
        )
        snap_pool = snap_candidates.set_index("id")
        snap_squad = _solve_squad(snap_pool, budget)

        if snap_squad is None:
            st.warning("Couldn't find a valid single-gameweek squad under this budget.")
        else:
            snap_squad, snap_captain_id, snap_vice_id = _solve_starting_xi(snap_squad)
            snapshot = {
                "id": str(uuid.uuid4()),
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "start_gw": int(gw),
                "budget": float(budget),
                "total_cost": float(snap_squad["price"].sum()),
                "players": [
                    {
                        "id": int(idx),
                        "name": row["web_name"],
                        "team_short": row["team_short"],
                        "position": row["position"],
                        "is_starter": bool(row["is_starter"]),
                        "is_captain": bool(idx == snap_captain_id),
                        "is_vice": bool(idx == snap_vice_id),
                    }
                    for idx, row in snap_squad.iterrows()
                ],
            }
            add_snapshot(snapshot)
            st.success(f"Saved! Head to the Squad Tracker page to follow how this GW{gw} squad performs.")
