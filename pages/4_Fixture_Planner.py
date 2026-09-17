import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_fixtures
from src.utils import current_event, next_event, teams_df

st.set_page_config(page_title="Fixture Planner - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Fixture Difficulty Planner")

bootstrap = get_bootstrap_static()
fixtures = get_fixtures()
teams = teams_df(bootstrap)
team_names = dict(zip(teams["id"], teams["short_name"]))

start_event = next_event(bootstrap) or current_event(bootstrap)
num_gw = st.slider("Number of gameweeks", min_value=3, max_value=10, value=6)
gw_range = range(start_event, start_event + num_gw)

rows = []
for team_id, short_name in team_names.items():
    row = {"Team": short_name}
    for gw in gw_range:
        gw_fixtures = [f for f in fixtures if f["event"] == gw and (f["team_h"] == team_id or f["team_a"] == team_id)]
        cells = []
        for f in gw_fixtures:
            is_home = f["team_h"] == team_id
            opponent = team_names.get(f["team_a"] if is_home else f["team_h"], "?")
            difficulty = f["team_h_difficulty"] if is_home else f["team_a_difficulty"]
            cells.append((f"{opponent} {'(H)' if is_home else '(A)'}", difficulty))
        if not cells:
            row[f"GW{gw}"] = "-"
            row[f"_diff_GW{gw}"] = 3
        else:
            row[f"GW{gw}"] = " + ".join(c[0] for c in cells)
            row[f"_diff_GW{gw}"] = sum(c[1] for c in cells) / len(cells)
    rows.append(row)

df = pd.DataFrame(rows)
gw_cols = [f"GW{gw}" for gw in gw_range]
diff_cols = [f"_diff_GW{gw}" for gw in gw_range]
df["Avg Difficulty"] = df[diff_cols].mean(axis=1).round(2)

sort_choice = st.selectbox("Sort by difficulty for", ["Average (all gameweeks shown)"] + gw_cols)
sort_col = "Avg Difficulty" if sort_choice.startswith("Average") else f"_diff_{sort_choice}"
df = df.sort_values(sort_col)


def color_by_difficulty(data):
    styles = pd.DataFrame("", index=data.index, columns=data.columns)
    for gw_col in gw_cols:
        diff_col = gw_col.replace("GW", "_diff_GW")
        for idx in data.index:
            d = df.loc[idx, diff_col]
            color = {
                1: "background-color: rgba(0, 166, 90, 0.5)",
                2: "background-color: rgba(0, 166, 90, 0.25)",
                3: "background-color: rgba(255, 193, 7, 0.2)",
                4: "background-color: rgba(220, 53, 69, 0.25)",
                5: "background-color: rgba(220, 53, 69, 0.5)",
            }.get(round(d), "")
            styles.loc[idx, gw_col] = color
    return styles


styled = (
    df[["Team", "Avg Difficulty"] + gw_cols]
    .style.apply(color_by_difficulty, axis=None)
    .format({"Avg Difficulty": "{:.2f}"})
    .hide(axis="index")
)
st.table(styled)
st.caption(
    "Green = easier fixtures, red = harder. Difficulty averaged across double gameweeks. "
    "Use the dropdown above to sort — clicking a column header here won't sort by difficulty "
    "since the cells show opponent names, not numbers."
)
