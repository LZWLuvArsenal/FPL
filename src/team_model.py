"""Team-level attacking/defensive rates and per-fixture xG / clean-sheet / result projections.

Built from the same inputs as the expected-points model on the Recommendations page (each team's xG
for / xG conceded per match over a recent-form window, scaled by the specific opponent against the
league median, capped to 0.6x-1.6x), plus a league-wide home/away adjustment.
"""
from __future__ import annotations

import math

import pandas as pd

from src.fpl_api import get_event_live

MULT_MIN, MULT_MAX = 0.6, 1.6
# Premier League home teams score roughly 25% more goals than away teams over a long run. Early in
# a season the league's own split is noisy, so it's blended with this prior, weighted like this many games.
PRIOR_HOME_AWAY_RATIO = 1.25
PRIOR_WEIGHT_GAMES = 100
MAX_GOALS = 10


def team_rates(bootstrap: dict, fixtures: list[dict], finished_events: list[int], window: int):
    """Per-team xG for / xG conceded per match over the last `window` finished gameweeks.

    xG for is summed across every player who got minutes; xG conceded is read from the goalkeeper's
    own expected_goals_conceded (FPL only tracks it per player, and the keeper faces the whole match).
    Returns ({team_id: xgf_per_match}, {team_id: xgc_per_match}); teams with no games are absent.
    """
    id_to_team = {e["id"]: e["team"] for e in bootstrap["elements"]}
    pos_names = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}
    id_to_pos = {e["id"]: pos_names[e["element_type"]] for e in bootstrap["elements"]}
    team_ids = [t["id"] for t in bootstrap["teams"]]
    xgf_sum = {tid: 0.0 for tid in team_ids}
    xgc_sum = {tid: 0.0 for tid in team_ids}
    games = {tid: 0 for tid in team_ids}

    events = finished_events[-window:] if window > 0 else []
    for ev in events:
        for el in get_event_live(ev)["elements"]:
            stats = el["stats"]
            team = id_to_team.get(el["id"])
            if stats["minutes"] <= 0 or team is None:
                continue
            xgf_sum[team] += float(stats["expected_goals"])
            if id_to_pos[el["id"]] == "GKP":
                xgc_sum[team] += float(stats["expected_goals_conceded"])
        for f in fixtures:
            if f["event"] == ev and f["finished"]:
                games[f["team_h"]] += 1
                games[f["team_a"]] += 1

    xgf = {tid: xgf_sum[tid] / games[tid] for tid in team_ids if games[tid]}
    xgc = {tid: xgc_sum[tid] / games[tid] for tid in team_ids if games[tid]}
    return xgf, xgc


def venue_factors(fixtures: list[dict]) -> tuple[float, float]:
    """(home multiplier, away multiplier) applied to a team's expected goals.

    Taken from this season's league-wide home vs. away goals (all finished games, not team-by-team —
    a single club's home/away split is far too small a sample), shrunk toward the long-run prior.
    The pair is split symmetrically (sqrt each way) so the league-average team is unchanged overall.
    """
    played = [f for f in fixtures if f["finished"] and f["team_h_score"] is not None and f["team_a_score"] is not None]
    home_goals = sum(f["team_h_score"] for f in played)
    away_goals = sum(f["team_a_score"] for f in played)
    n = len(played)
    observed = (home_goals / away_goals) if away_goals else PRIOR_HOME_AWAY_RATIO
    ratio = (observed * n + PRIOR_HOME_AWAY_RATIO * PRIOR_WEIGHT_GAMES) / (n + PRIOR_WEIGHT_GAMES)
    half = math.sqrt(ratio)
    return half, 1 / half


def _median(mapping: dict) -> float:
    return float(pd.Series(mapping).median()) if mapping else float("nan")


def expected_goals_parts(scorer: int, conceder: int, xgf: dict, xgc: dict) -> dict:
    """The intermediate numbers behind `scorer`'s expected goals against `conceder` (before venue).

    Two estimates of the same number are produced: the scorer's own attack scaled by how leaky the
    conceder is, and the conceder's own xGC scaled by how dangerous the scorer is.
    """
    med_f, med_c = _median(xgf), _median(xgc)

    def get(mapping, key, median):
        val = mapping.get(key)
        return median if val is None or pd.isna(val) else val

    def clamp(mult):
        return min(max(mult, MULT_MIN), MULT_MAX)

    atk = get(xgf, scorer, med_f)
    leak = get(xgc, conceder, med_c)
    leak_mult = clamp(leak / med_c if med_c else 1.0)
    atk_mult = clamp(atk / med_f if med_f else 1.0)
    return {
        "attack": atk,
        "leakiness": leak,
        "leak_mult": leak_mult,
        "attack_mult": atk_mult,
        "from_attack": atk * leak_mult,
        "from_defence": leak * atk_mult,
    }


def _expected_goals(scorer: int, conceder: int, xgf: dict, xgc: dict, scorer_venue_mult: float) -> float:
    """Expected goals `scorer` scores against `conceder`, venue-adjusted.

    The attack-based and defence-based estimates are averaged. Using both means one side's xG is
    exactly the other side's xGA, so every screen agrees.
    """
    parts = expected_goals_parts(scorer, conceder, xgf, xgc)
    return (parts["from_attack"] + parts["from_defence"]) / 2 * scorer_venue_mult


def outcome_probs(lam_home: float, lam_away: float) -> dict:
    """Win/draw/loss chances and the modal scoreline from independent Poisson goal counts."""
    home_pmf = [math.exp(-lam_home) * lam_home**k / math.factorial(k) for k in range(MAX_GOALS + 1)]
    away_pmf = [math.exp(-lam_away) * lam_away**k / math.factorial(k) for k in range(MAX_GOALS + 1)]
    p_home = p_draw = p_away = 0.0
    best, best_p = (0, 0), -1.0
    for h, ph in enumerate(home_pmf):
        for a, pa in enumerate(away_pmf):
            p = ph * pa
            if h > a:
                p_home += p
            elif h == a:
                p_draw += p
            else:
                p_away += p
            if p > best_p:
                best, best_p = (h, a), p
    total = p_home + p_draw + p_away  # renormalise the (tiny) mass beyond MAX_GOALS
    return {
        "p_home": p_home / total,
        "p_draw": p_draw / total,
        "p_away": p_away / total,
        "likely_score": best,
        "home_cs": away_pmf[0],  # home keeps a clean sheet when the away side scores 0
        "away_cs": home_pmf[0],
    }


def project_match(home_id: int, away_id: int, xgf: dict, xgc: dict, venue: tuple[float, float]) -> dict:
    """Projected goals for each side, clean-sheet odds and win/draw/loss probabilities."""
    home_mult, away_mult = venue
    lam_home = _expected_goals(home_id, away_id, xgf, xgc, home_mult)
    lam_away = _expected_goals(away_id, home_id, xgf, xgc, away_mult)
    return {"xg_home": lam_home, "xg_away": lam_away, **outcome_probs(lam_home, lam_away)}


def project_fixture(team_id: int, opp_id: int, xgf: dict, xgc: dict, venue: tuple[float, float], is_home: bool) -> dict:
    """`team_id`'s view of a match: projected xG for/against and its clean-sheet probability
    (the Poisson chance the opponent scores zero, e^-xGA)."""
    if is_home:
        m = project_match(team_id, opp_id, xgf, xgc, venue)
        xg, xga = m["xg_home"], m["xg_away"]
    else:
        m = project_match(opp_id, team_id, xgf, xgc, venue)
        xg, xga = m["xg_away"], m["xg_home"]
    return {"xg": xg, "xga": xga, "cs_prob": math.exp(-xga)}
