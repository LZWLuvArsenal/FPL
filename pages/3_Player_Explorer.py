import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static
from src.utils import players_df

st.set_page_config(page_title="Player Explorer - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Player Explorer")

bootstrap = get_bootstrap_static()
df = players_df(bootstrap)

col1, col2, col3, col4 = st.columns(4)
positions = col1.multiselect("Position", sorted(df["position"].unique()), default=[])
teams = col2.multiselect("Team", sorted(df["team_name"].unique()), default=[])
price_range = col3.slider(
    "Price (£m)", float(df["price"].min()), float(df["price"].max()), (float(df["price"].min()), float(df["price"].max()))
)
search = col4.text_input("Search name")

filtered = df.copy()
if positions:
    filtered = filtered[filtered["position"].isin(positions)]
if teams:
    filtered = filtered[filtered["team_name"].isin(teams)]
filtered = filtered[filtered["price"].between(*price_range)]
if search:
    filtered = filtered[
        filtered["full_name"].str.contains(search, case=False, na=False)
        | filtered["web_name"].str.contains(search, case=False, na=False)
    ]

sort_col = st.selectbox(
    "Sort by",
    [
        "total_points",
        "form",
        "points_per_game",
        "selected_by_percent",
        "price",
        "ict_index",
        "goals_scored",
        "assists",
        "expected_goals",
        "expected_assists",
        "expected_goal_involvements",
        "expected_goals_conceded",
    ],
    index=0,
)
filtered = filtered.sort_values(sort_col, ascending=False)

display_cols = [
    "web_name",
    "team_name",
    "position",
    "price",
    "total_points",
    "form",
    "points_per_game",
    "selected_by_percent",
    "goals_scored",
    "assists",
    "clean_sheets",
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "ict_index",
]
st.caption(f"{len(filtered)} players — xG/xA/xGC are season totals from FPL's underlying stats provider")
st.dataframe(
    filtered[display_cols].rename(
        columns={
            "web_name": "Name",
            "team_name": "Team",
            "position": "Pos",
            "price": "Price",
            "total_points": "Points",
            "form": "Form",
            "points_per_game": "PPG",
            "selected_by_percent": "Selected %",
            "goals_scored": "Goals",
            "assists": "Assists",
            "clean_sheets": "Clean Sheets",
            "expected_goals": "xG",
            "expected_assists": "xA",
            "expected_goal_involvements": "xGI",
            "expected_goals_conceded": "xGC",
            "ict_index": "ICT",
        }
    ),
    hide_index=True,
    width="stretch",
    height=650,
    column_config={"Price": st.column_config.NumberColumn(format="£%.1f")},
)
