import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_event_live
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

# Defensive contribution: 2pts for reaching the threshold in a match — clearances, blocks, interceptions
# and tackles for defenders; recoveries count too for midfielders and forwards. Keepers can't earn it.
DEFCON_THRESHOLD = {"DEF": 10, "MID": 12, "FWD": 12}


@st.cache_data(ttl=300, show_spinner="Loading match-by-match defensive stats...")
def defcon_by_player(event_ids):
    """Appearances and DEFCON hits per player, counted per fixture from FPL's points breakdown so double
    gameweeks count as two chances."""
    apps, hits = {}, {}
    for event_id in event_ids:
        for el in get_event_live(event_id)["elements"]:
            pid = el["id"]
            for fixture in el["explain"]:
                stats = {s["identifier"]: s for s in fixture["stats"]}
                if stats.get("minutes", {}).get("value", 0) <= 0:
                    continue
                apps[pid] = apps.get(pid, 0) + 1
                # The breakdown only lists defensive_contribution when it scored, so it can't give totals.
                if stats.get("defensive_contribution", {}).get("points", 0) > 0:
                    hits[pid] = hits.get(pid, 0) + 1
    return pd.DataFrame({"apps": apps, "defcon_hits": hits}).fillna(0)


st.subheader("Defensive Contributions (DEFCON)")
started = [e["id"] for e in bootstrap["events"] if e["finished"] or e["is_current"]]
if not started:
    st.info("No gameweeks played yet.")
else:
    dc = filtered[filtered["position"].isin(DEFCON_THRESHOLD)].join(defcon_by_player(tuple(started)), on="id")
    dc[["apps", "defcon_hits"]] = dc[["apps", "defcon_hits"]].fillna(0)
    dc = dc[dc["apps"] > 0]
    dc["hit_pct"] = dc["defcon_hits"] / dc["apps"] * 100
    dc["actions_per_app"] = dc["defensive_contribution"] / dc["apps"]
    dc["threshold"] = dc["position"].map(DEFCON_THRESHOLD)
    dc["defensive_contribution_per_90"] = pd.to_numeric(dc["defensive_contribution_per_90"], errors="coerce")

    dcol1, dcol2 = st.columns(2)
    max_apps = int(dc["apps"].max()) if not dc.empty else 1
    min_apps = dcol1.number_input("Minimum appearances", min_value=1, max_value=max_apps, value=min(3, max_apps))
    dc_sort = dcol2.selectbox(
        "Sort by", ["DEFCON Hit %", "DEFCON Hits", "Def. Actions / App", "Def. Contribution / 90"], key="dc_sort"
    )
    dc = dc[dc["apps"] >= min_apps]
    sort_map = {
        "DEFCON Hit %": ["hit_pct", "defcon_hits"],
        "DEFCON Hits": ["defcon_hits", "hit_pct"],
        "Def. Actions / App": ["actions_per_app"],
        "Def. Contribution / 90": ["defensive_contribution_per_90"],
    }
    dc = dc.sort_values(sort_map[dc_sort], ascending=False)
    st.caption(
        f"{len(dc)} outfield players. A DEFCON hit is a match where the player reached the threshold "
        "(10 clearances/blocks/interceptions/tackles for defenders, 12 including recoveries for midfielders "
        "and forwards) for 2pts. Hit % = hits ÷ appearances; every appearance counts, including sub cameos. "
        "Uses the Position / Team / Price / Search filters above."
    )
    st.dataframe(
        dc[
            [
                "web_name", "team_name", "position", "price", "apps", "defcon_hits", "hit_pct",
                "actions_per_app", "threshold", "defensive_contribution_per_90",
            ]
        ].rename(
            columns={
                "web_name": "Name",
                "team_name": "Team",
                "position": "Pos",
                "price": "Price",
                "apps": "Apps",
                "defcon_hits": "DEFCON Hits",
                "hit_pct": "DEFCON Hit %",
                "actions_per_app": "Def. Actions / App",
                "threshold": "Threshold",
                "defensive_contribution_per_90": "Def. Contribution / 90",
            }
        ),
        hide_index=True,
        width="stretch",
        height=500,
        column_config={
            "Price": st.column_config.NumberColumn(format="£%.1f"),
            "Apps": st.column_config.NumberColumn(format="%d"),
            "DEFCON Hits": st.column_config.NumberColumn(format="%d"),
            "DEFCON Hit %": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100),
            "Def. Actions / App": st.column_config.NumberColumn(format="%.1f"),
            "Def. Contribution / 90": st.column_config.NumberColumn(format="%.1f"),
        },
    )
