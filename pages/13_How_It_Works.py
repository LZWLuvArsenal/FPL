import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_fixtures
from src.team_model import (
    MAX_GOALS,
    MULT_MAX,
    MULT_MIN,
    PRIOR_HOME_AWAY_RATIO,
    PRIOR_WEIGHT_GAMES,
    expected_goals_parts,
    project_match,
    team_rates,
    venue_factors,
)
from src.utils import current_event, next_event

st.set_page_config(page_title="How It Works - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("How the Projections Work")
st.caption(
    "Every projected number in this dashboard — expected points, expected goals, clean sheet odds, "
    "win/draw/loss chances — is a model estimate built from public FPL expected-goal data. This page "
    "explains each one in full, and the worked example at the end runs a real fixture through every step."
)

tabs = st.tabs(
    [
        "Overview",
        "Team xG & xGA",
        "Home / away",
        "Clean sheets",
        "Win / draw / loss",
        "Player points",
        "Score & squad builder",
        "Limits",
        "Worked example",
    ]
)

with tabs[0]:
    st.markdown(
        f"""
### The big picture

Everything is built from one idea: **expected goals (xG)** measures the quality of chances a team
creates or allows, which predicts future scoring better than actual goals do (goals are lumpy —
one deflection or one wonder-strike can swing a result without changing how well a team is playing).

| Where you see it | What it is | Built from |
|---|---|---|
| Fixture Planner — **Projected xG** and **Clean sheet odds** tabs | Projected goals scored, and clean sheet odds, for one team in one game | Team xG model + home/away + Poisson |
| Fixture Planner — **Proj xG**, **Avg CS %**, **Exp. clean sheets** | Those numbers totalled / averaged over the gameweeks shown | Same |
| Head-to-Head — **Match projection** | Both sides' expected goals, win/draw/loss %, clean sheet %, likeliest score | Same, plus a scoreline grid |
| Recommendations — **Next GW**, **Proj Pts** | Expected FPL points for one player | Player per-90 rates + team model + FPL scoring rules |
| Recommendations — **Score** | A standardised ranking blend (not in points) | z-scores of xGI, xGC, FDR |
| Recommendations — **Recommended 15** | Best affordable squad | Integer optimisation over Proj Pts |

**Two team models, one difference.** The Fixture Planner and Head-to-Head pages use the *symmetric*
team model with a home/away adjustment (so one team's xG is exactly the other's xGA). The
Recommendations points model is the older, one-sided version without a venue adjustment. They agree
closely but won't match to the decimal. All of it is estimates — not bookmaker odds.
"""
    )

with tabs[1]:
    st.markdown(
        f"""
### Team xG and xGA

**Step 1 — each team's rates.** Over the *recent-form window* (the slider on the pages; default the
whole season so far), each team gets:

- **xG for per match** — the sum of every player's `expected_goals` who got minutes, ÷ the team's
  finished matches in the window.
- **xG conceded (xGC) per match** — the goalkeeper's own `expected_goals_conceded` (FPL only tracks it per
  player, and the keeper faces the whole game), ÷ matches.

A shorter window weights recent form; a long one is steadier but slower to notice a team improving or
collapsing.

**Step 2 — scale for the specific opponent.** A team's average is measured against an average
opponent, so for a given game it's scaled by how the opponent compares to the *league median*:

- *Attack view:* team's xG for × (opponent's xGC ÷ league-median xGC). Facing a leakier-than-median
  defence pushes it up.
- *Defence view:* opponent's xGC × (team's xG for ÷ league-median xG for). This is the same quantity
  (goals the team scores) estimated from the other side.

Each multiplier is **capped to {MULT_MIN}×–{MULT_MAX}×** so a tiny sample or one freak game can't
dominate.

**Step 3 — average the two views**, then apply the venue adjustment (next tab). The result is the
team's projected goals in that game. The opponent's projected goals come from the same procedure with
roles swapped, so **one side's xG is always exactly the other's xGA** — every screen agrees with itself.

**What the columns mean.** *xG* = chances the team should create. *xGA* = chances it should concede.
*Proj xG* on the Fixture Planner sums xG across all games in the window, so doubles count twice and
blanks add nothing.
"""
    )

with tabs[2]:
    home_m, away_m = venue_factors(get_fixtures())
    st.markdown(
        f"""
### Home / away adjustment

Home sides score more and concede less — across a long run of Premier League seasons home teams
score about {(PRIOR_HOME_AWAY_RATIO - 1) * 100:.0f}% more goals than visitors.

- **How it's measured:** the league-wide home ÷ away goal ratio over *all* finished games this season.
  It's deliberately league-wide — an individual club's home/away split is only a handful of games, far
  too noisy to trust.
- **Early-season shrinkage:** with few games played, the observed ratio is blended with the long-run
  prior ({PRIOR_HOME_AWAY_RATIO}), weighted like {PRIOR_WEIGHT_GAMES} games of evidence. As the season
  fills in, the observed figure takes over.
- **How it's applied:** the ratio is split symmetrically (square root each way) so a league-average
  team is unchanged overall. Right now that means **home team's goals × {home_m:.3f}**, **away team's
  goals × {away_m:.3f}**. Because a side's xGA is the opponent's xG, this automatically also *lowers*
  a home team's xGA and raises an away team's.
- **What it isn't:** FPL's difficulty ratings (FDR) already bake in some home/away, but FDR is used only
  for the colours on the planner. It is not an input to the xG numbers.
"""
    )

with tabs[3]:
    st.markdown(
        """
### Clean sheet odds

**CS % = e^(−xGA)**, where xGA is the team's projected goals conceded in that game.

This is the **Poisson probability of exactly zero goals**. If the opponent is expected to score 0.8,
the chance they score none is e^−0.8 ≈ 45%. It treats goals as independent random events arriving at
a steady average rate — a standard, reasonable model for football.

Reading the numbers: xGA 0.5 → 61%, 1.0 → 37%, 1.5 → 22%, 2.0 → 14%.

- **Fixture Planner:** *Avg CS %* averages this across the games in the window (per game, not per
  gameweek, so doubles are weighted evenly).
- **Head-to-Head:** each side's clean sheet is the Poisson chance the *other* side scores 0.
- **Recommendations:** the player model uses the same e^−xGA logic (see Player points) and multiplies
  it by the clean sheet points on offer.
"""
    )

with tabs[4]:
    st.markdown(
        f"""
### Win / draw / loss and the likeliest score

Given each side's expected goals (λ home and λ away), the number of goals each side scores is modelled
as an independent **Poisson** count. For every scoreline from 0–0 up to {MAX_GOALS}–{MAX_GOALS} the
probability is P(home scores h) × P(away scores a), where P(k) = e^−λ · λ^k ÷ k!.

- **Home win** = the sum over all scorelines where h > a; **draw** = h = a; **away win** = h < a.
  (The sliver of probability beyond {MAX_GOALS} goals a side is renormalised away.)
- **Likeliest scoreline** = the single scoreline with the highest probability. It can be 1-0 even when
  the *expected* goals are 1.7 – 0.8, because a whole number is more likely than the average is — and it
  has a low absolute probability (often ~10–15%), so treat it as a flavour, not a prediction.

**Known weakness:** independent Poisson slightly *under-rates* low-scoring draws (0-0, 1-1), since real
teams' goals aren't quite independent. Proper fixes (e.g. Dixon–Coles) exist but need match-level data
this dashboard doesn't have. Expect draws to be a touch higher in reality than shown.
"""
    )

with tabs[5]:
    st.markdown(
        """
### Player expected points (Next GW and Proj Pts)

On the Recommendations page, each player's points for a fixture are estimated as the sum of:

| Component | How it's estimated |
|---|---|
| **Appearance +2** | Treated as certain — the pool is already limited to players averaging 75+ mins/game |
| **Goals** | Player's `expected_goals_per_90` × opponent multiplier (leakiness of the opponent's defence vs. the league median, capped 0.6×–1.6×) × points per goal: **6** GKP/DEF, **5** MID, **4** FWD |
| **Assists** | `expected_assists_per_90` × the same multiplier × **3** |
| **Clean sheet** | Team's xGC/90 × the opponent's attacking-threat multiplier → **e^(−that)** = clean sheet probability × **4** GKP/DEF, **1** MID, **0** FWD |
| **Goals conceded** (GKP/DEF) | −0.5 × the same expected goals against (FPL docks 1 pt per 2 goals conceded — this is its continuous expectation) |
| **Saves** (GKP) | Recent saves per match × the opponent-attack multiplier, ÷ 3 (FPL gives 1 pt per 3 saves) |
| **Bonus, cards, penalties, own goals** | Each player's own per-match average over the recent-form window (from gameweek-by-gameweek live data), used directly rather than modelled |
| **Defensive contribution** | How often the player has hit the threshold (**10** actions DEF, **12** MID/FWD) × the **2** points on offer; not applied to keepers |

- **Next GW** sums this over the upcoming gameweek only — this is what each position list is ranked by.
- **Proj Pts** sums it over the whole look-ahead window (the slider). Doubles count twice, blanks zero.

**Who's in the pool:** at least 180 minutes played, at least 75 minutes per game, and not injured,
suspended or otherwise unavailable. Doubtful players stay in, flagged with their chance of playing.

**Not modelled:** home/away (this model predates the venue adjustment), penalty-taker status beyond
what shows up in xG, likelihood of rotation, and team news after the last data pull.
"""
    )

with tabs[6]:
    st.markdown(
        """
### Score, and the squad builder

**Score** is *not* in points. It's a separate, equal-weight blend of standardised (z-scored) stats,
measured against the eligible pool:

- **Midfielders / Forwards:** own xGI/90 (goals + assists involvement) + the upcoming opponents' xGC/90
  (leakier = better) − fixture difficulty (easier = better), ÷ 3.
- **Goalkeepers / Defenders:** −(fixture difficulty + own team's xGC/90 + opponents' xG/90) ÷ 3 — all
  three point the same way (lower is better).

A z-score says how many standard deviations above/below the pool average something is, so different
units (xG, difficulty ratings) can be blended fairly. Team-mates on identical fixtures can tie.

**Recommended 15** is solved as an integer optimisation problem (PuLP): choose **2 GK, 5 DEF, 5 MID,
3 FWD, max 3 per club**, within your budget, maximising total **Proj Pts**. It draws from each position's
top players by *both* Next GW and Proj Pts, so a strong multi-week run isn't excluded just because next
week is tough. The **starting XI** is a second optimisation (1 GK, 3–5 DEF, 2–5 MID, 1–3 FWD); the
captain is the highest-projected starter, vice-captain the next best. Bench order: outfield subs by
projection, keeper last.

The **Track squad** button saves a squad optimised for the *next gameweek only* (a fair single-week
snapshot) so the Squad Tracker can compare it with real results afterwards.
"""
    )

with tabs[7]:
    st.markdown(
        """
### What these models can't do

- **They are estimates, not odds.** No bookmaker prices, injuries reported after the last data pull,
  lineups, rotation, motivation, weather or referees.
- **Small samples.** Early in a season xG averages rest on a few games; the 0.6×–1.6× caps and the
  home/away shrinkage limit the damage but don't remove it. A shorter form window is *more* reactive
  and *noisier*.
- **xG is a proxy.** It's the closest public measure of chance quality, but it ignores finishing skill,
  set-piece routines and game state. Corner counts, flank-by-flank chance creation and other
  Opta/StatsBomb-tier data aren't in FPL's public API.
- **Independence assumptions.** Poisson goals ignore that teams change tactics when leading or trailing.
- **Nothing here is validated against outcomes yet.** The Squad Tracker is the intended way to check
  how the points model performs over time.
"""
    )

with tabs[8]:
    st.markdown("### Worked example — one fixture, every step")
    bootstrap = get_bootstrap_static()
    fixtures = get_fixtures()
    team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    gw = next_event(bootstrap) or current_event(bootstrap)
    gw_fixtures = [f for f in fixtures if f["event"] == gw]
    finished_events = sorted(e["id"] for e in bootstrap["events"] if e["finished"])

    if not gw_fixtures or not finished_events:
        st.info("Needs an upcoming fixture and at least one completed gameweek.")
    else:
        chosen = st.selectbox(
            f"GW{gw} fixture",
            gw_fixtures,
            format_func=lambda f: f"{team_short[f['team_h']]} (H) vs {team_short[f['team_a']]} (A)",
            key="worked_fixture",
        )
        window = st.slider("Recent form window (gameweeks)", 1, len(finished_events), len(finished_events), key="worked_window")
        xgf, xgc = team_rates(bootstrap, fixtures, finished_events, window)
        venue = venue_factors(fixtures)
        h_id, a_id = chosen["team_h"], chosen["team_a"]
        h, a = team_short[h_id], team_short[a_id]
        med_f, med_c = pd.Series(xgf).median(), pd.Series(xgc).median()
        proj = project_match(h_id, a_id, xgf, xgc, venue)
        h_parts = expected_goals_parts(h_id, a_id, xgf, xgc)
        a_parts = expected_goals_parts(a_id, h_id, xgf, xgc)

        st.markdown(f"**1. Team rates** (last {window} completed GW{'s' if window > 1 else ''}; league medians: xG {med_f:.2f}, xGC {med_c:.2f})")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Team": h, "xG for / match": xgf.get(h_id), "xGC / match": xgc.get(h_id)},
                    {"Team": a, "xG for / match": xgf.get(a_id), "xGC / match": xgc.get(a_id)},
                ]
            ),
            hide_index=True,
            column_config={
                "xG for / match": st.column_config.NumberColumn(format="%.2f"),
                "xGC / match": st.column_config.NumberColumn(format="%.2f"),
            },
        )

        st.markdown(f"**2. Expected goals before venue** (multipliers capped {MULT_MIN}×–{MULT_MAX}×)")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Scorer": h,
                        "Attack view": h_parts["from_attack"],
                        "Attack view = xG × opp. leak mult": f"{h_parts['attack']:.2f} × {h_parts['leak_mult']:.2f}",
                        "Defence view": h_parts["from_defence"],
                        "Defence view = opp. xGC × atk mult": f"{h_parts['leakiness']:.2f} × {h_parts['attack_mult']:.2f}",
                        "Average": (h_parts["from_attack"] + h_parts["from_defence"]) / 2,
                    },
                    {
                        "Scorer": a,
                        "Attack view": a_parts["from_attack"],
                        "Attack view = xG × opp. leak mult": f"{a_parts['attack']:.2f} × {a_parts['leak_mult']:.2f}",
                        "Defence view": a_parts["from_defence"],
                        "Defence view = opp. xGC × atk mult": f"{a_parts['leakiness']:.2f} × {a_parts['attack_mult']:.2f}",
                        "Average": (a_parts["from_attack"] + a_parts["from_defence"]) / 2,
                    },
                ]
            ),
            hide_index=True,
            column_config={
                "Attack view": st.column_config.NumberColumn(format="%.2f"),
                "Defence view": st.column_config.NumberColumn(format="%.2f"),
                "Average": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        st.markdown(
            f"**3. Venue adjustment:** {h} × {venue[0]:.3f} (home), {a} × {venue[1]:.3f} (away) → "
            f"**{h} {proj['xg_home']:.2f} – {proj['xg_away']:.2f} {a}** projected xG."
        )
        st.markdown(
            f"**4. Clean sheets:** {h} = e^−{proj['xg_away']:.2f} = **{proj['home_cs']:.0%}**, "
            f"{a} = e^−{proj['xg_home']:.2f} = **{proj['away_cs']:.0%}**."
        )
        likely_h, likely_a = proj["likely_score"]
        st.markdown(
            f"**5. Result odds** (Poisson scoreline grid): {h} win **{proj['p_home']:.0%}**, draw "
            f"**{proj['p_draw']:.0%}**, {a} win **{proj['p_away']:.0%}**; likeliest scoreline "
            f"{h} {likely_h}-{likely_a} {a}."
        )
