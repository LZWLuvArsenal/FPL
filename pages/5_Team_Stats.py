import pandas as pd
import plotly.express as px
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_fixtures
from src.understat import (
    aggregate_team_season,
    current_season,
    format_season,
    get_league_team_data,
    understat_team_name,
)
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

tab1, tab2, tab3 = st.tabs(["Table", "xG For vs Against", "Advanced (Understat)"])

with tab1:
    SORT_OPTIONS = {
        "Points": "points",
        "Wins": "won",
        "Goals For": "goals_for",
        "xG For": "xg_for",
        "Finishing (G-xG)": "finishing",
        "Goals Against": "goals_against",
        "xG Against": "xg_against",
        "Def. Luck (xGA-GA)": "defensive_luck",
        "Goal Diff": "goal_diff",
        "xG Diff": "xg_diff",
    }
    sort_col1, sort_col2 = st.columns([3, 1])
    sort_label = sort_col1.selectbox("Sort by", list(SORT_OPTIONS.keys()))
    ascending = sort_col2.checkbox("Ascending", value=False)

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
    ranked = stats.sort_values(SORT_OPTIONS[sort_label], ascending=ascending).reset_index(drop=True)
    ranked.insert(0, "Rank", ranked.index + 1)
    st.dataframe(
        ranked[["Rank"] + display_cols].round(2).rename(
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
    st.caption("Top-right is best: high attacking xG, low xG conceded (the defence axis is flipped, so lower is higher).")

with tab3:
    st.caption(
        "From understat.com — an unofficial, third-party stats site (not affiliated with the "
        "Premier League or FPL) using its own shot-based xG model, which will differ slightly "
        "from the FPL API's own xG figures in the Table tab above."
    )
    try:
        season = current_season()
        team_data = get_league_team_data(season)
    except Exception as exc:
        st.error(f"Couldn't reach understat.com: {exc}")
        st.stop()

    understat_by_title = {t["title"].strip().lower(): t for t in team_data.values()}
    team_names = {t["id"]: t["name"] for t in bootstrap["teams"]}
    team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

    adv_rows = []
    unmatched = []
    for tid, fpl_name in team_names.items():
        u_team = understat_by_title.get(understat_team_name(fpl_name).strip().lower())
        if not u_team:
            unmatched.append(fpl_name)
            continue
        agg = aggregate_team_season(u_team["history"])
        matching_points = stats.loc[stats["team"] == team_short[tid], "points"]
        fpl_points = int(matching_points.iloc[0]) if not matching_points.empty else None
        adv_rows.append(
            {
                "Team": team_short[tid],
                "Matches": agg["matches"],
                "xG": round(agg["xg"], 2),
                "xGA": round(agg["xga"], 2),
                "npxG": round(agg["npxg"], 2),
                "npxGA": round(agg["npxga"], 2),
                "Deep": agg["deep"],
                "Deep Allowed": agg["deep_allowed"],
                "PPDA": round(agg["ppda"], 2) if agg["ppda"] else None,
                "xPTS": round(agg["xpts"], 1),
                "Points": fpl_points,
                "PTS vs xPTS": round(fpl_points - agg["xpts"], 1) if fpl_points is not None else None,
            }
        )

    adv_sort_col1, adv_sort_col2 = st.columns([3, 1])
    adv_sort_label = adv_sort_col1.selectbox(
        "Sort by",
        ["xPTS", "Points", "PTS vs xPTS", "xG", "xGA", "npxG", "npxGA", "Deep", "Deep Allowed", "PPDA"],
        key="adv_sort",
    )
    adv_ascending = adv_sort_col2.checkbox("Ascending", value=False, key="adv_ascending")

    adv_df = pd.DataFrame(adv_rows).sort_values(adv_sort_label, ascending=adv_ascending).reset_index(drop=True)
    adv_df.insert(0, "Rank", adv_df.index + 1)
    st.dataframe(adv_df, hide_index=True, width="stretch", height=750)
    st.caption(
        "PPDA (passes allowed per defensive action) — lower means more aggressive pressing. "
        "Deep completions — passes completed within ~20 yards of the opponent's goal, a proxy "
        "for territorial/build-up threat separate from shots. npxG/npxGA strip out penalties. "
        "PTS vs xPTS > 0 means over-performing their underlying numbers in the table; < 0 means "
        "they're actually playing better than their points suggest."
    )
    if unmatched:
        with st.expander(f"{len(unmatched)} team(s) not found on Understat"):
            st.write(", ".join(unmatched))
