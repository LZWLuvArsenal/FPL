import html
from collections import Counter

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from src.config import render_sidebar_settings, style_chart
from src.fpl_api import get_bootstrap_static, get_element_summary, get_fixtures
from src.player_dialog import player_dataframe
from src.team_model import project_match, team_rates, venue_factors
from src.understat import (
    current_season,
    find_player_id,
    format_season,
    get_league_fixtures,
    get_player_matches,
    matches_vs_opponent,
    seasons_since_earliest,
    team_h2h_matches,
    understat_team_name,
)
from src.utils import current_event, next_event, players_df

RECENT_MEETINGS = 6

st.set_page_config(page_title="Head-to-Head - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Head-to-Head: Player vs Upcoming Opponent")

bootstrap = get_bootstrap_static()
fixtures = get_fixtures()
players = players_df(bootstrap)
team_full = {t["id"]: t["name"] for t in bootstrap["teams"]}
team_short = {t["id"]: t["short_name"] for t in bootstrap["teams"]}

gw = next_event(bootstrap) or current_event(bootstrap)
gw_fixtures = [f for f in fixtures if f["event"] == gw]

if not gw_fixtures:
    st.info("No upcoming fixtures found.")
    st.stop()


def fixture_label(f):
    return f"{team_short[f['team_h']]} (H) vs {team_short[f['team_a']]} (A)"


chosen = st.selectbox(f"GW{gw} fixture", gw_fixtures, format_func=fixture_label)
home_id, away_id = chosen["team_h"], chosen["team_a"]

squad = players[players["team"].isin([home_id, away_id])]

st.subheader("Match projection")
finished_events = sorted(e["id"] for e in bootstrap["events"] if e["finished"])
form_window = 0
if finished_events:
    form_window = st.slider(
        "Recent form window (gameweeks)",
        1,
        len(finished_events),
        min(5, len(finished_events)),
        help="How many of the most recently completed gameweeks feed each team's xG and xGC per match.",
    )
xgf_rate, xgc_rate = team_rates(bootstrap, fixtures, finished_events, form_window)
if not (xgf_rate and xgc_rate):
    st.info("No completed gameweeks yet, so there's no xG/xGC data to project from.")
else:
    venue = venue_factors(fixtures)
    proj = project_match(home_id, away_id, xgf_rate, xgc_rate, venue)
    home_s, away_s = team_short[home_id], team_short[away_id]
    st.markdown(
        f'<div style="font-size:1.6rem; font-weight:700; margin:4px 0 8px;">'
        f"{home_s} {proj['xg_home']:.2f} – {proj['xg_away']:.2f} {away_s} "
        f'<span style="font-weight:400; opacity:0.7; font-size:1rem;">projected xG</span></div>',
        unsafe_allow_html=True,
    )
    outcome_fig = go.Figure()
    for label, prob, color in [
        (f"{home_s} win", proj["p_home"], "#e74c3c"),
        ("Draw", proj["p_draw"], "#95a5a6"),
        (f"{away_s} win", proj["p_away"], "#3498db"),
    ]:
        outcome_fig.add_trace(
            go.Bar(
                y=["Result"],
                x=[prob * 100],
                orientation="h",
                name=label,
                marker_color=color,
                text=[f"{prob:.0%}"],
                textposition="inside",
                textfont=dict(size=22, color="white"),
                hovertemplate=f"{label}: %{{x:.1f}}%<extra></extra>",
            )
        )
    outcome_fig.update_layout(
        barmode="stack",
        height=150,
        margin=dict(l=10, r=10, t=10, b=10),
        xaxis=dict(visible=False, range=[0, 100]),
        yaxis=dict(visible=False),
        legend=dict(orientation="h", traceorder="normal", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=14)),
    )
    style_chart(outcome_fig)
    st.plotly_chart(outcome_fig, width="stretch")

    col1, col2, col3 = st.columns(3)
    col1.metric(f"{home_s} clean sheet", f"{proj['home_cs']:.0%}")
    col2.metric(f"{away_s} clean sheet", f"{proj['away_cs']:.0%}")
    likely_h, likely_a = proj["likely_score"]
    col3.metric("Most likely scoreline", f"{home_s} {likely_h}-{likely_a} {away_s}")
    st.caption(
        "Each side's expected goals blends its own xG per match with the opponent's xG conceded per "
        "match (scaled against the league median, capped 0.6×–1.6×), then adjusted for venue using this "
        f"season's league-wide home/away goal split (home ×{venue[0]:.2f}, away ×{venue[1]:.2f}). Result "
        "odds treat each side's goals as an independent Poisson count — a simple model that tends to "
        "under-rate low-scoring draws a little — and are model estimates, not bookmaker odds."
    )

st.subheader("This season")

rows = []
with st.spinner(f"Checking {len(squad)} players' history against their GW{gw} opponent..."):
    for _, p in squad.iterrows():
        opponent_id = away_id if p["team"] == home_id else home_id
        summary = get_element_summary(int(p["id"]))
        history = [h for h in summary["history"] if h["opponent_team"] == opponent_id]
        if not history:
            continue
        goals = sum(h["goals_scored"] for h in history)
        assists = sum(h["assists"] for h in history)
        points = sum(h["total_points"] for h in history)
        rows.append(
            {
                "Player": p["web_name"],
                "Team": p["team_short"],
                "Opponent": team_short[opponent_id],
                "Meetings this season": len(history),
                "Goals": goals,
                "Assists": assists,
                "Points": points,
                "Avg Points": round(points / len(history), 1),
            }
        )

if rows:
    df = pd.DataFrame(rows).sort_values(["Goals", "Assists", "Points"], ascending=False)
    st.dataframe(df, hide_index=True, width="stretch")
else:
    st.info(f"None of the selected players have faced their GW{gw} opponent yet this season.")

st.caption(
    "Based only on meetings so far this season — element-summary only reports opponent per "
    "fixture for the current campaign, not prior seasons."
)

st.subheader("All-time record (via Understat)")
st.caption(
    "Understat.com is an unofficial, third-party stats site (not affiliated with the Premier "
    "League or FPL) covering every season it has tracked since 2014/15. Players are matched by "
    "name search, so double-check any surprising or missing row. The player table below only "
    "shows those with at least one goal or assist against the opponent — pure blanks are omitted."
)

if st.checkbox("Load all-time head-to-head record", value=False):
    home_understat = understat_team_name(team_full[home_id])
    away_understat = understat_team_name(team_full[away_id])

    with st.expander("Understat team names used (edit if a club is misspelled/renamed)"):
        col1, col2 = st.columns(2)
        home_understat = col1.text_input(f"Name for {team_short[home_id]}", value=home_understat)
        away_understat = col2.text_input(f"Name for {team_short[away_id]}", value=away_understat)

    st.markdown("#### Team Record (All-Time)")
    season_list = seasons_since_earliest(current_season())
    team_matches = []
    with st.spinner(f"Checking {len(season_list)} seasons of {home_understat} vs {away_understat}..."):
        for s in season_list:
            try:
                season_fixtures = get_league_fixtures(s)
            except requests.RequestException:
                continue
            for m in team_h2h_matches(season_fixtures, home_understat, away_understat):
                team_matches.append({**m, "season": s})
    team_matches.sort(key=lambda m: m["datetime"])

    if not team_matches:
        st.info(f"No meetings found between {home_understat} and {away_understat} since {format_season(str(season_list[0]))}.")
    else:
        home_wins = draws = away_wins = 0
        match_rows = []
        perspective_scores = []
        for m in team_matches:
            h_goals, a_goals = int(m["goals"]["h"]), int(m["goals"]["a"])
            if m["h"]["title"] == home_understat:
                persp = (h_goals, a_goals)
            else:
                persp = (a_goals, h_goals)
            if persp[0] > persp[1]:
                home_wins += 1
            elif persp[0] < persp[1]:
                away_wins += 1
            else:
                draws += 1
            perspective_scores.append(persp)
            match_rows.append(
                {
                    "Season": format_season(m["season"]),
                    "Date": m["datetime"][:10],
                    "Result": f"{m['h']['title']} {h_goals}-{a_goals} {m['a']['title']}",
                }
            )

        st.markdown(
            f'<div style="font-size:1rem; margin-bottom:4px;">'
            f"{len(team_matches)} meetings since {format_season(season_list[0])}</div>",
            unsafe_allow_html=True,
        )
        record_fig = go.Figure()
        record_fig.add_trace(
            go.Bar(
                y=["Record"],
                x=[home_wins],
                orientation="h",
                name=f"{home_understat} wins",
                marker_color="#e74c3c",
                text=[str(home_wins)] if home_wins else [""],
                textposition="inside",
                textfont=dict(size=22, color="white"),
            )
        )
        record_fig.add_trace(
            go.Bar(
                y=["Record"],
                x=[draws],
                orientation="h",
                name="Draws",
                marker_color="#95a5a6",
                text=[str(draws)] if draws else [""],
                textposition="inside",
                textfont=dict(size=22, color="white"),
            )
        )
        record_fig.add_trace(
            go.Bar(
                y=["Record"],
                x=[away_wins],
                orientation="h",
                name=f"{away_understat} wins",
                marker_color="#3498db",
                text=[str(away_wins)] if away_wins else [""],
                textposition="inside",
                textfont=dict(size=22, color="white"),
            )
        )
        record_fig.update_layout(
            barmode="stack",
            height=150,
            margin=dict(l=10, r=10, t=10, b=10),
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            legend=dict(orientation="h", traceorder="normal", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=14)),
        )
        style_chart(record_fig)
        st.plotly_chart(record_fig, width="stretch")

        score_counts = Counter(perspective_scores)
        common_score, common_n = score_counts.most_common(1)[0]
        st.markdown(
            f'<div style="font-size:1.15rem; font-weight:600; margin:4px 0 12px;">'
            f"Most common scoreline: {home_understat} {common_score[0]}-{common_score[1]} {away_understat} "
            f'<span style="font-weight:400; opacity:0.75; font-size:1rem;">'
            f"({common_n} of {len(team_matches)} meetings)</span></div>",
            unsafe_allow_html=True,
        )

        # Recent form, newest last: the last few meetings, their split, and whatever streak is running.
        outcomes = ["H" if h > a else "A" if h < a else "D" for h, a in perspective_scores]
        outcome_color = {"H": "rgba(231, 76, 60, 0.85)", "D": "rgba(149, 165, 166, 0.85)", "A": "rgba(52, 152, 219, 0.85)"}
        recent_n = min(RECENT_MEETINGS, len(outcomes))
        recent = list(zip(outcomes, perspective_scores, match_rows))[-recent_n:]
        chips = "".join(
            f'<span title="{row["Date"]}: {html.escape(row["Result"])}" style="background:{outcome_color[o]}; '
            f'color:white; font-weight:700; padding:4px 10px; border-radius:6px; font-size:0.95rem;">{h}-{a}</span>'
            for o, (h, a), row in recent
        )
        split = Counter(o for o, _, _ in recent)

        latest = outcomes[-1]
        same = next((i for i, o in enumerate(reversed(outcomes)) if o != latest), len(outcomes))
        streaks = []
        if same >= 2:
            streaks.append(
                f"Last {same} meetings drawn" if latest == "D"
                else f"{home_understat if latest == 'H' else away_understat} won the last {same}"
            )
        for team, loss in ((home_understat, "A"), (away_understat, "H")):
            unbeaten = next((i for i, o in enumerate(reversed(outcomes)) if o == loss), len(outcomes))
            if unbeaten >= 3 and unbeaten > same:
                streaks.append(f"{team} unbeaten in the last {unbeaten}")
        streak = " · ".join(streaks)
        st.markdown(
            f'<div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin:0 0 6px;">'
            f'<span style="font-weight:600; margin-right:4px;">Last {recent_n} (oldest → newest):</span>{chips}</div>'
            f'<div style="opacity:0.85; margin-bottom:12px;">{home_understat} {split["H"]}W · {split["D"]}D · '
            f'{away_understat} {split["A"]}W{f" — <b>{html.escape(streak)}</b>" if streak else ""}</div>',
            unsafe_allow_html=True,
        )

        best_len, best_start, cur_len = 1, 0, 1
        for i in range(1, len(perspective_scores)):
            if perspective_scores[i] == perspective_scores[i - 1]:
                cur_len += 1
                if cur_len > best_len:
                    best_len, best_start = cur_len, i - cur_len + 1
            else:
                cur_len = 1
        if best_len >= 3:
            start_s = match_rows[best_start]["Season"]
            end_s = match_rows[best_start + best_len - 1]["Season"]
            score = perspective_scores[best_start]
            st.caption(
                f"📈 Trend spotted: {best_len} meetings in a row ended {home_understat} {score[0]}-{score[1]} "
                f"{away_understat}, from {start_s} to {end_s}."
            )

        def _color_outcome(row):
            persp = perspective_scores[row.name]
            if persp[0] > persp[1]:
                color = "background-color: rgba(231, 76, 60, 0.18)"
            elif persp[0] < persp[1]:
                color = "background-color: rgba(52, 152, 219, 0.18)"
            else:
                color = "background-color: rgba(149, 165, 166, 0.18)"
            return [color] * len(row)

        st.dataframe(
            pd.DataFrame(match_rows).style.apply(_color_outcome, axis=1), hide_index=True, width="stretch"
        )
        st.caption(
            f"🔴 {home_understat} win · ⚪ Draw · 🔵 {away_understat} win"
        )

    st.markdown("#### Player Records (All-Time)")
    hist_rows = []
    unmatched = []
    with st.spinner(f"Fetching all-time Understat history for {len(squad)} players..."):
        for _, p in squad.iterrows():
            own_team_understat = home_understat if p["team"] == home_id else away_understat
            opponent_name = away_understat if p["team"] == home_id else home_understat
            try:
                understat_id = find_player_id(p["full_name"], own_team_understat, web_name=p["web_name"])
                all_matches = get_player_matches(understat_id) if understat_id else []
            except requests.RequestException:
                unmatched.append(p["full_name"])
                continue
            if not understat_id:
                unmatched.append(p["full_name"])
                continue
            vs_opponent = matches_vs_opponent(all_matches, opponent_name)
            if not vs_opponent:
                continue
            goals = sum(int(m["goals"]) for m in vs_opponent)
            assists = sum(int(m["assists"]) for m in vs_opponent)
            if goals == 0 and assists == 0:
                continue
            xg = sum(float(m["xG"]) for m in vs_opponent)
            xa = sum(float(m["xA"]) for m in vs_opponent)
            seasons = sorted({format_season(m["season"]) for m in vs_opponent})
            hist_rows.append(
                {
                    "_id": p["id"],
                    "Player": p["web_name"],
                    "Team": p["team_short"],
                    "Opponent": team_short[away_id if p["team"] == home_id else home_id],
                    "Owned %": p["selected_by_percent"],
                    "Seasons faced": ", ".join(seasons),
                    "Matches": len(vs_opponent),
                    "Goals": goals,
                    "Assists": assists,
                    "xG": round(xg, 2),
                    "xA": round(xa, 2),
                }
            )

    if hist_rows:
        hist_df = pd.DataFrame(hist_rows).sort_values(["Goals", "xG"], ascending=False)
        player_dataframe(
            hist_df.drop(columns="_id"),
            hist_df["_id"],
            key="h2h_player_records",
            hide_index=True,
            width="stretch",
            column_config={
                "Team": st.column_config.TextColumn(width="small"),
                "Opponent": st.column_config.TextColumn(width="small"),
                "Owned %": st.column_config.NumberColumn(format="%.1f%%", width="small"),
                "Seasons faced": st.column_config.TextColumn(width="large"),
            },
        )
    else:
        st.info("No one in the squad has a goal or assist against this opponent to show.")

    if unmatched:
        with st.expander(f"{len(unmatched)} player(s) not matched on Understat"):
            st.write(", ".join(unmatched))
