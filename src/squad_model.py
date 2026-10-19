"""The expected-points model and squad solver behind the Recommendations page, kept free of Streamlit
so the scheduled auto-save job (scripts/autosave_squad.py) picks squads exactly the way the page does.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd
import pulp

GOAL_PTS = {"GKP": 6, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
DC_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}
MULT_MIN, MULT_MAX = 0.6, 1.6
DEFAULT_FORM_WINDOW = 5


@dataclass
class TeamRates:
    """Each team's attacking (xG for) and defensive (xG conceded) per-90 rates over the form window,
    plus league medians as the fallback for teams with no games in it."""

    xgf90: dict
    xgc90: dict
    median_xgf90: float
    median_xgc90: float

    def xgf(self, team):
        return _safe_lookup(self.xgf90, team, self.median_xgf90)

    def xgc(self, team):
        return _safe_lookup(self.xgc90, team, self.median_xgc90)


def _safe_lookup(mapping, key, default):
    val = mapping.get(key)
    return default if val is None or pd.isna(val) else val


def add_mins_per_game(players, fixtures, team_ids):
    for col in ("goals_scored", "goals_conceded", "minutes"):
        players[col] = pd.to_numeric(players[col], errors="coerce")
    team_games_played = {tid: 0 for tid in team_ids}
    for f in fixtures:
        # finished_provisional covers matches that have ended in a gameweek that isn't fully closed yet;
        # players' minutes already include those matches, so the denominator must too.
        if not (f["finished"] or f.get("finished_provisional")):
            continue
        team_games_played[f["team_h"]] = team_games_played.get(f["team_h"], 0) + 1
        team_games_played[f["team_a"]] = team_games_played.get(f["team_a"], 0) + 1
    games_played_series = players["team"].map(team_games_played).replace(0, pd.NA)
    players["mins_per_game"] = (players["minutes"] / games_played_series).fillna(0)
    return players


def recent_stats(players, fixtures, events, get_live, team_ids):
    """One pass over the given finished gameweeks' live data, producing both team-level
    attacking/defensive per-90 rates (for fixture difficulty) and each player's own per-match
    averages (bonus, cards, penalties, own goals, defensive-contribution hit-rate, saves) — the
    inputs the expected-points model is built from."""
    id_to_team = dict(zip(players["id"], players["team"]))
    id_to_pos = dict(zip(players["id"], players["position"]))
    xgf_sum = {tid: 0.0 for tid in team_ids}
    xgc_sum = {tid: 0.0 for tid in team_ids}
    team_games = {tid: 0 for tid in team_ids}
    player_games, player_bonus = {}, {}
    player_card_pts, player_pen_pts, player_og_pts = {}, {}, {}
    player_dc_hits, player_saves = {}, {}

    for ev in events:
        for el in get_live(ev)["elements"]:
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
    rates = TeamRates(xgf90, xgc90, pd.Series(xgf90).median(), pd.Series(xgc90).median())

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

    pw = player_window.reindex(players["id"]).fillna(0)
    pw_games = pw["games"].replace(0, pd.NA)
    players["avg_bonus_pts"] = (pw["bonus"] / pw_games).fillna(0).to_numpy()
    players["avg_card_pts"] = (pw["card_pts"] / pw_games).fillna(0).to_numpy()
    players["avg_pen_pts"] = (pw["pen_pts"] / pw_games).fillna(0).to_numpy()
    players["avg_og_pts"] = (pw["og_pts"] / pw_games).fillna(0).to_numpy()
    players["dc_rate"] = (pw["dc_hits"] / pw_games).fillna(0).to_numpy()
    players["avg_saves"] = (pw["saves"] / pw_games).fillna(0).to_numpy()
    return players, rates


def fixtures_by_team(fixtures, events):
    out = {}
    for f in fixtures:
        if f["event"] not in events:
            continue
        out.setdefault(f["team_h"], []).append((f["team_a"], f["team_h_difficulty"], True))
        out.setdefault(f["team_a"], []).append((f["team_h"], f["team_a_difficulty"], False))
    return out


def _window_summary(team_id, fixtures_by_team, team_short, rates):
    fx = fixtures_by_team.get(team_id, [])
    if not fx:
        return None
    return {
        "opponents": ", ".join(team_short.get(opp, "?") for opp, _, _h in fx),
        "difficulty": sum(d for _, d, _h in fx) / len(fx),
        "opp_xgc_per90": sum(rates.xgc(opp) for opp, _, _h in fx) / len(fx),
        "opp_xgf_per90": sum(rates.xgf(opp) for opp, _, _h in fx) / len(fx),
        "num_fixtures": len(fx),
    }


def add_window_columns(df, fixtures_by_team, team_short, rates):
    """Attaches this fixture window's opponent difficulty/xG stats, plus each player's own
    team's xGC/90, to every row — the shared inputs the score and points model are built from."""
    summaries = {tid: _window_summary(tid, fixtures_by_team, team_short, rates) for tid in team_short}
    df = df.copy()
    for col in ("opponents", "difficulty", "opp_xgc_per90", "opp_xgf_per90", "num_fixtures"):
        df[col] = df["team"].map(lambda t, c=col: (summaries.get(t) or {}).get(c))
    df["own_xgc_per90"] = df["team"].map(rates.xgc)
    return df


def _zscore(s):
    std = s.std(ddof=0)
    if not std or pd.isna(std):
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def add_scores(df):
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


def project_points(row, fx, rates):
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
        atk_mult = (rates.xgc(opp) / rates.median_xgc90) if rates.median_xgc90 else 1.0
        def_mult = (rates.xgf(opp) / rates.median_xgf90) if rates.median_xgf90 else 1.0
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


def solve_squad(pool, squad_budget):
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


def solve_starting_xi(squad):
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


def optimal_gw_snapshot(players, gw_fixtures, team_short, rates, gw, budget, source="manual"):
    """The single-gameweek optimal squad the Squad Tracker follows, as a saveable dict, or None if no
    valid squad fits the budget. `players` must already carry the recent-stats columns."""
    snap_players = add_window_columns(players, gw_fixtures, team_short, rates)
    snap_base = snap_players[
        snap_players["opponents"].notna()
        & (snap_players["minutes"] >= 180)
        & (snap_players["mins_per_game"] >= 75)
        & (~snap_players["status"].isin(["i", "s", "u"]))
    ].copy()
    snap_base = add_scores(snap_base)
    snap_base["projected_pts"] = snap_base.apply(
        lambda r: project_points(r, gw_fixtures.get(r["team"], []), rates), axis=1
    )

    snap_candidates = pd.concat(
        [
            snap_base[snap_base["position"] == "GKP"].sort_values("score", ascending=False).head(10),
            snap_base[snap_base["position"] == "DEF"].sort_values("score", ascending=False).head(15),
            snap_base[snap_base["position"] == "MID"].sort_values("score", ascending=False).head(15),
            snap_base[snap_base["position"] == "FWD"].sort_values("score", ascending=False).head(15),
        ]
    )
    snap_squad = solve_squad(snap_candidates.set_index("id"), budget)
    if snap_squad is None:
        return None

    snap_squad, captain_id, vice_id = solve_starting_xi(snap_squad)
    return {
        "id": str(uuid.uuid4()),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "start_gw": int(gw),
        "budget": float(budget),
        "total_cost": float(snap_squad["price"].sum()),
        "source": source,
        "players": [
            {
                "id": int(idx),
                "name": row["web_name"],
                "team_short": row["team_short"],
                "position": row["position"],
                "is_starter": bool(row["is_starter"]),
                "is_captain": bool(idx == captain_id),
                "is_vice": bool(idx == vice_id),
            }
            for idx, row in snap_squad.iterrows()
        ],
    }
