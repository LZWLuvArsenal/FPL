import pandas as pd
import plotly.express as px
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_fixtures
from src.utils import players_df, teams_df

st.set_page_config(page_title="Team Stats - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Team Stats")
st.caption(
    "xG For is summed across a team's players — every shot belongs to exactly one player. "
    "xG Against isn't a per-player stat, so it's proxied by summing expected_goals_conceded "
    "across every goalkeeper the team has used, covering keeper rotations. Played/Won/Points "
    "are computed from finished fixtures — the API's team records never populate those fields directly."
)

bootstrap = get_bootstrap_static()
fixtures = get_fixtures()
players = players_df(bootstrap)
teams = teams_df(bootstrap)

for col in ("goals_scored", "goals_conceded", "minutes"):
    players[col] = pd.to_numeric(players[col], errors="coerce")

xg_for = players.groupby("team")["expected_goals"].sum().rename("xg_for")

# Sum across all goalkeepers a team has used, not just the #1 — a mid-season change of gloves
# (injury, loss of form, new signing) would otherwise silently drop whatever the backup conceded.
xg_against = players[players["position"] == "GKP"].groupby("team")["expected_goals_conceded"].sum().rename(
    "xg_against"
)

records = {
    tid: {"played": 0, "won": 0, "drawn": 0, "lost": 0, "points": 0, "goals_for": 0, "goals_against": 0}
    for tid in teams["id"]
}
for f in fixtures:
    if not f["finished"]:
        continue
    for tid, gf, ga in ((f["team_h"], f["team_h_score"], f["team_a_score"]), (f["team_a"], f["team_a_score"], f["team_h_score"])):
        r = records[tid]
        r["played"] += 1
        r["goals_for"] += gf
        r["goals_against"] += ga
        if gf > ga:
            r["won"] += 1
            r["points"] += 3
        elif gf == ga:
            r["drawn"] += 1
            r["points"] += 1
        else:
            r["lost"] += 1

standings = pd.DataFrame.from_dict(records, orient="index")

stats = teams.set_index("id")[["short_name"]].join(standings).join(xg_for).join(xg_against)
stats["goal_diff"] = stats["goals_for"] - stats["goals_against"]
stats["xg_diff"] = stats["xg_for"] - stats["xg_against"]
stats["finishing"] = stats["goals_for"] - stats["xg_for"]
stats["defensive_luck"] = stats["xg_against"] - stats["goals_against"]
stats = stats.reset_index(drop=True).rename(columns={"short_name": "team"})
stats = stats.sort_values(["points", "goal_diff"], ascending=False)

tab1, tab2 = st.tabs(["Table", "xG For vs Against"])

with tab1:
    display_cols = [
        "team",
        "played",
        "won",
        "drawn",
        "lost",
        "points",
        "goals_for",
        "xg_for",
        "finishing",
        "goals_against",
        "xg_against",
        "defensive_luck",
        "goal_diff",
        "xg_diff",
    ]
    st.dataframe(
        stats[display_cols].round(2).rename(
            columns={
                "team": "Team",
                "played": "Played",
                "won": "W",
                "drawn": "D",
                "lost": "L",
                "points": "Points",
                "goals_for": "Goals For",
                "xg_for": "xG For",
                "finishing": "Finishing (G-xG)",
                "goals_against": "Goals Against",
                "xg_against": "xG Against",
                "defensive_luck": "Def. Luck (xGA-GA)",
                "goal_diff": "Goal Diff",
                "xg_diff": "xG Diff",
            }
        ),
        hide_index=True,
        width="stretch",
        height=750,
    )
    st.caption(
        "Finishing > 0 = scoring more than their chances deserve. Def. Luck > 0 = conceding fewer "
        "goals than their defense/keeper is facing (could be goalkeeping form or luck)."
    )

with tab2:
    fig = px.scatter(
        stats,
        x="xg_for",
        y="xg_against",
        text="team",
        color="points",
        color_continuous_scale="Viridis",
        labels={"xg_for": "xG For (attack)", "xg_against": "xG Against (defense)", "points": "Points"},
    )
    fig.update_traces(textposition="top center")
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, width="stretch")
    st.caption("Bottom-right is best: high attacking xG, low xG conceded.")
