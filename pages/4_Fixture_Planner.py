import html
from itertools import combinations

import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_fixtures
from src.team_model import project_fixture, team_rates, venue_factors
from src.utils import FDR_STYLES, current_event, next_event, teams_df

st.set_page_config(page_title="Fixture Planner - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Fixture Planner")

bootstrap = get_bootstrap_static()
fixtures = get_fixtures()
teams = teams_df(bootstrap)
team_names = dict(zip(teams["id"], teams["short_name"]))

start_event = next_event(bootstrap) or current_event(bootstrap)
num_gw = st.slider("Number of gameweeks", min_value=3, max_value=10, value=5)
gw_range = range(start_event, start_event + num_gw)
gw_cols = [f"GW{gw}" for gw in gw_range]

finished_events = sorted(e["id"] for e in bootstrap["events"] if e["finished"])
form_window = 0
if finished_events:
    form_window = st.slider(
        "Recent form window (gameweeks)",
        1,
        len(finished_events),
        min(5, len(finished_events)),
        help="How many of the most recently completed gameweeks feed each team's xG and xGC per match "
        "in the projection tabs. Turn it down to weight recent form over a flat season-to-date average.",
    )
xgf_rate, xgc_rate = team_rates(bootstrap, fixtures, finished_events, form_window)
venue = venue_factors(fixtures)
has_projections = bool(xgf_rate and xgc_rate)

# team_id -> gw -> [{"opp", "home", "diff", "xg", "xga", "cs"}, ...] — the one place fixtures and
# projections are computed, shared by every tab below.
team_games = {tid: {gw: [] for gw in gw_range} for tid in team_names}
for f in fixtures:
    if f["event"] not in gw_range:
        continue
    for team_id, opp_id, is_home, diff in [
        (f["team_h"], f["team_a"], True, f["team_h_difficulty"]),
        (f["team_a"], f["team_h"], False, f["team_a_difficulty"]),
    ]:
        game = {"opp": team_names.get(opp_id, "?"), "home": is_home, "diff": diff}
        if has_projections:
            proj = project_fixture(team_id, opp_id, xgf_rate, xgc_rate, venue, is_home)
            game.update(xg=proj["xg"], xga=proj["xga"], cs=proj["cs_prob"])
        team_games[team_id][f["event"]].append(game)

FDR_COLORS = FDR_STYLES


def fdr_color(value, _all_values):
    return FDR_COLORS.get(round(value), "")


# Best -> worst. A plain green-to-red gradient (brightest green = best), unlike the official difficulty
# palette above whose "1" is a darker green than its "2".
QUINTILE_COLORS = {
    1: "background-color: #00d97e; color: #14001c",
    2: "background-color: #a6ecc6; color: #14001c",
    3: "background-color: #ebebe4; color: #14001c",
    4: "background-color: #ff9db9; color: #14001c",
    5: "background-color: #ff005a; color: #ffffff",
}


def quintile_color(value, all_values):
    """Colours a cell by which fifth of the table's values it falls in (best fifth = band 1). Uses the
    same palette as the difficulty tab so 'green = good, red = bad' reads the same everywhere; ranking
    by percentile (not by raw range) keeps one double-gameweek outlier from washing out the rest."""
    share_at_or_below = (all_values <= value).mean()
    band = 1 if share_at_or_below > 0.8 else 2 if share_at_or_below > 0.6 else 3 if share_at_or_below > 0.4 else 4 if share_at_or_below > 0.2 else 5
    return QUINTILE_COLORS[band]


def _legend(palette: dict, labels: list[str]) -> None:
    chips = "".join(
        f'<span style="{palette[band]}; padding:3px 14px; font-size:0.78rem; font-weight:600;">{text}</span>'
        for band, text in enumerate(labels, start=1)
    )
    st.markdown(f'<div style="display:flex; gap:2px; margin:6px 0 2px; flex-wrap:wrap;">{chips}</div>', unsafe_allow_html=True)


def _venue(g):
    return f"{g['opp']} {'(H)' if g['home'] else '(A)'}"


def render_grid(key, cell_text, cell_value, color_fn, summaries, default_sort, number_formats):
    """One team x gameweek table.

    cell_text(game) -> a fixture's text; cell_value(games_in_gw) -> the number a gameweek is coloured
    and sorted by (None for blanks); summaries maps a summary column name to (fn(team_id) -> number,
    sort_ascending) — each becomes a column and a sort option.
    """
    text_rows, value_rows = [], []
    for team_id, short_name in team_names.items():
        text_row, value_row = {"Team": short_name}, {}
        for gw, col in zip(gw_range, gw_cols):
            games = team_games[team_id][gw]
            text_row[col] = "\n\n".join(cell_text(g) for g in games) if games else "-"
            value_row[col] = cell_value(games) if games else float("nan")
        for name, (fn, _asc) in summaries.items():
            text_row[name] = fn(team_id)
        text_rows.append(text_row)
        value_rows.append(value_row)
    df = pd.DataFrame(text_rows)
    values = pd.DataFrame(value_rows).astype(float)

    sort_options = {f"{name} ({'lowest' if asc else 'highest'} first)": (name, asc) for name, (_f, asc) in summaries.items()}
    for col in gw_cols:
        asc = summaries[default_sort][1]
        sort_options[f"{col} ({'lowest' if asc else 'highest'} first)"] = (col, asc)
    labels = list(sort_options)
    choice = st.selectbox("Sort by", labels, index=labels.index(next(l for l in labels if l.startswith(default_sort))), key=f"sort_{key}")
    sort_col, ascending = sort_options[choice]
    order = (values[sort_col] if sort_col in gw_cols else df[sort_col]).sort_values(ascending=ascending, na_position="last").index
    df, values = df.loc[order], values.loc[order]

    all_values = values.stack()

    def color(data):
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        for col in gw_cols:
            for idx in data.index:
                v = values.loc[idx, col]
                if not pd.isna(v):
                    styles.loc[idx, col] = color_fn(v, all_values)
        return styles

    styled = (
        df[["Team"] + list(summaries) + gw_cols]
        .style.apply(color, axis=None)
        .set_properties(subset=gw_cols, **{"white-space": "pre-line"})
        .format(number_formats, na_rep="-")
        .hide(axis="index")
    )
    st.table(styled)


def _mean(values):
    return sum(values) / len(values) if values else None


def all_games(team_id):
    return [g for gw in gw_range for g in team_games[team_id][gw]]


def render_rotation_pairs():
    """Ranks every pair of teams by how well they cover each other: each gameweek you'd start whichever
    of the two has the better projection, so the pair's value is the sum of the weekly best."""
    ctrl1, ctrl2, ctrl3, ctrl4 = st.columns([2, 2, 2, 1])
    mode = ctrl1.radio("Rotate for", ["Clean sheets (GKP / DEF)", "Goals (MID / FWD)"], key="rot_mode")
    stat, unit = ("cs", "Exp. clean sheets") if mode.startswith("Clean") else ("xg", "Proj xG")
    rank_by = ctrl2.radio("Rank by", ["Pair total", "Rotation gain (vs. best alone)"], key="rot_rank")
    by_name = {name: tid for tid, name in team_names.items()}
    must = ctrl3.selectbox("Must include", ["Any team"] + sorted(by_name), key="rot_team")
    top_n = int(ctrl4.number_input("Pairs shown", min_value=3, max_value=30, value=10, key="rot_n"))

    # Per team and gameweek: summed over the week's games (a double counts both, a blank is 0).
    value = {t: [sum(g[stat] for g in team_games[t][gw]) for gw in gw_range] for t in team_names}
    pairs = []
    for a, b in combinations(team_names, 2):
        if must != "Any team" and by_name[must] not in (a, b):
            continue
        weekly = [max(va, vb) for va, vb in zip(value[a], value[b])]
        total = sum(weekly)
        pairs.append({"a": a, "b": b, "total": total, "gain": total - max(sum(value[a]), sum(value[b]))})
    sort_key = "total" if rank_by == "Pair total" else "gain"
    pairs.sort(key=lambda p: (-p[sort_key], -p["total"]))

    all_values = pd.Series([v for t in team_names for v, gw in zip(value[t], gw_range) if team_games[t][gw]])
    cell_css = "padding:4px 6px; text-align:center; white-space:nowrap; font-size:0.78rem; font-weight:600;"
    head = "".join(f'<th style="{cell_css} opacity:0.7;">{c}</th>' for c in ["#", "Team", unit, "vs. best alone"] + gw_cols)
    rows = []
    for rank, p in enumerate(pairs[:top_n], start=1):
        a, b = p["a"], p["b"]
        for side, (team, other) in enumerate([(a, b), (b, a)]):
            cells = []
            for i, gw in enumerate(gw_range):
                games = team_games[team][gw]
                if not games:
                    cells.append(f'<td style="{cell_css} opacity:0.35;">-</td>')
                    continue
                picked = value[team][i] > value[other][i] or (value[team][i] == value[other][i] and team == a)
                style = quintile_color(value[team][i], all_values).replace("background-color", "background")
                text = "<br>".join(html.escape(_venue(g)) for g in games)
                look = "outline:2px solid rgba(0,0,0,0.55); outline-offset:-2px;" if picked else "opacity:0.3;"
                cells.append(f'<td style="{cell_css} {style}; {look}">{text}</td>')
            border = "border-top:6px solid transparent;" if side == 0 else ""
            lead = (
                f'<td rowspan="2" style="{cell_css} opacity:0.7;">{rank}</td>' if side == 0 else ""
            )
            summary = (
                f'<td rowspan="2" style="{cell_css}">{p["total"]:.1f}</td>'
                f'<td rowspan="2" style="{cell_css} opacity:0.75;">+{p["gain"]:.1f}</td>'
                if side == 0 else ""
            )
            rows.append(
                f'<tr style="{border}">{lead}<td style="{cell_css} text-align:left;">{html.escape(team_names[team])}</td>'
                f"{summary}{''.join(cells)}</tr>"
            )
    st.markdown(
        '<div style="overflow-x:auto;"><table style="border-collapse:separate; border-spacing:2px;">'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>",
        unsafe_allow_html=True,
    )
    _legend(QUINTILE_COLORS, ["Top fifth", "2nd", "Middle", "4th", "Bottom fifth"])
    st.caption(
        f"Every pair of teams is scored by starting whichever of the two has the better projection each "
        f"gameweek (outlined; the other is faded) and adding those up — **{unit}** is the pair's total over the "
        "gameweeks shown, doubles counting both games. **vs. best alone** is how much the rotation adds over "
        "just starting the better of the two every week — a small number means one team carries the pair "
        "and rotation isn't really needed. Rank by it to find true rotations (teams whose good runs "
        "alternate); rank by Pair total for the strongest pair outright. Colours rank each fixture among every fixture in the window, as "
        "in the other tabs. Use it for two keepers or two budget defenders; set 'Must include' to a team "
        "you already own to find its best partner."
    )


tab_names = ["Fixture difficulty"] + (["Projected xG", "Clean sheet odds", "Rotation pairs"] if has_projections else [])
tabs = st.tabs(tab_names)

with tabs[0]:
    render_grid(
        "fdr",
        cell_text=_venue,
        cell_value=lambda games: _mean([g["diff"] for g in games]),
        color_fn=fdr_color,
        summaries={"Avg Difficulty": (lambda t: _mean([g["diff"] for g in all_games(t)]), True)},
        default_sort="Avg Difficulty",
        number_formats={"Avg Difficulty": "{:.2f}"},
    )
    _legend(FDR_COLORS, ["1 Easiest", "2", "3", "4", "5 Hardest"])
    st.caption(
        "FPL's own fixture difficulty rating (1 easiest – 5 hardest). Green = easier fixtures, red = harder. "
        "Averaged across double gameweeks. Use the dropdown to sort — clicking a column header here won't "
        "sort by difficulty since the cells show opponent names, not numbers."
    )

if has_projections:
    with tabs[1]:
        render_grid(
            "xg",
            cell_text=lambda g: f"{_venue(g)}\nxG {g['xg']:.1f}",
            cell_value=lambda games: sum(g["xg"] for g in games),
            color_fn=quintile_color,
            summaries={"Proj xG": (lambda t: sum(g["xg"] for g in all_games(t)), False)},
            default_sort="Proj xG",
            number_formats={"Proj xG": "{:.1f}"},
        )
        _legend(QUINTILE_COLORS, ["Top fifth", "2nd", "Middle", "4th", "Bottom fifth"])
        st.caption(
            "**xG** is the goals a team is projected to score in that game. **Proj xG** is the total across "
            "the gameweeks shown (doubles count twice). Each cell is coloured by how its xG ranks among every "
            "cell in the table: the top fifth is green, the bottom fifth red."
        )
    with tabs[2]:
        render_grid(
            "cs",
            cell_text=lambda g: f"{_venue(g)}\nCS {g['cs']:.0%}",
            cell_value=lambda games: _mean([g["cs"] for g in games]),
            color_fn=quintile_color,
            summaries={
                "Avg CS %": (lambda t: (_mean([g["cs"] for g in all_games(t)]) or 0) * 100, False),
                "Exp. clean sheets": (lambda t: sum(g["cs"] for g in all_games(t)), False),
            },
            default_sort="Avg CS %",
            number_formats={"Avg CS %": "{:.0f}%", "Exp. clean sheets": "{:.1f}"},
        )
        _legend(QUINTILE_COLORS, ["Top fifth", "2nd", "Middle", "4th", "Bottom fifth"])
        st.caption(
            "**CS** is the chance a team keeps a clean sheet in that game. **Avg CS %** is the average per "
            "game and **Exp. clean sheets** the sum across the gameweeks shown (doubles count both games). "
            "Each cell is coloured by how its chance ranks among every cell in the table: the top fifth is "
            "green, the bottom fifth red."
        )
    with tabs[3]:
        render_rotation_pairs()
    st.caption(
        "**How the projections work:** built like the Recommendations page's points model — each side's own "
        "xG scored and xG conceded per match over the recent-form window, scaled by how the specific "
        "opponent compares to the league-median defence/attack (capped to 0.6×–1.6× so one odd sample "
        "can't dominate). Each side's expected goals averages two views — its own attack vs. the "
        "opponent's leakiness, and the opponent's xGC vs. its attacking threat — so one team's xG is "
        "always exactly the other's xGA. It's then adjusted for venue using this season's league-wide "
        f"home/away goal split (home ×{venue[0]:.2f}, away ×{venue[1]:.2f}, shrunk toward the long-run norm "
        "early in the season). Clean sheet odds are the Poisson chance of conceding zero, e^-xGA. Fixture "
        "difficulty is FPL's own rating and isn't an input to these numbers. Model estimates from "
        "expected-goal data, not bookmaker odds. Full detail on the How It Works page."
    )
else:
    st.caption("No completed gameweeks yet, so there's no xG/xGC data for the projection tabs.")
