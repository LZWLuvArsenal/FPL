from datetime import timedelta

import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_entry_picks
from src.snapshots import previous_snapshot, uk_today
from src.utils import current_event, players_df, season_name

st.set_page_config(page_title="Price Changes - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Price Changes")
st.caption(
    "Prices update once daily, around 1:30am UK time. The FPL API only exposes the change since the "
    "current gameweek's deadline and since the season started, so day-by-day changes come from a "
    "snapshot of every player's price that this app archives each morning after the update."
)

bootstrap = get_bootstrap_static()
df = players_df(bootstrap)
df["change_gw"] = df["cost_change_event"] / 10
df["change_season"] = df["cost_change_start"] / 10

gw = current_event(bootstrap)
deadline = next((e["deadline_time"] for e in bootstrap["events"] if e["id"] == gw), None)
today = uk_today()
baseline = previous_snapshot(season_name(bootstrap))

if baseline:
    base_day, base = baseline
    base = base.set_index("id")
    df["change_today"] = ((df["now_cost"] - df["id"].map(base["now_cost"])) / 10).fillna(0)
    base_net = base["transfers_in"] - base["transfers_out"]
    df["net_transfers"] = (df["transfers_in"] - df["transfers_out"] - df["id"].map(base_net)).fillna(0).astype(int)
    since_label = "yesterday's" if base_day == today - timedelta(days=1) else f"the {base_day:%a %d %b}"
    transfers_label = f"net transfers since {since_label} snapshot"
    # FPL moves a player at most once a day, so anyone who changed today is done until tomorrow —
    # but only if the baseline really is yesterday, otherwise the change could be from an earlier day.
    if base_day == today - timedelta(days=1):
        can_move = df["change_today"] == 0
    else:
        can_move = pd.Series(True, index=df.index)
else:
    df["change_today"] = 0.0
    df["net_transfers"] = df["transfers_in_event"] - df["transfers_out_event"]
    transfers_label = "net transfers since the gameweek deadline"
    can_move = pd.Series(True, index=df.index)

display_cols_map = {
    "web_name": "Name",
    "team_name": "Team",
    "position": "Pos",
    "price": "Price",
    "selected_by_percent": "Selected %",
    "change_today": "Change Today",
    "change_gw": "Change This GW",
    "change_season": "Change This Season",
}


def show_table(data, change_col):
    cols = ["web_name", "team_name", "position", "price", "selected_by_percent", change_col]
    st.dataframe(
        data[cols].rename(columns=display_cols_map),
        hide_index=True,
        width="stretch",
        column_config={
            "Price": st.column_config.NumberColumn(format="£%.1f"),
            display_cols_map[change_col]: st.column_config.NumberColumn(format="£%+.1f"),
        },
    )


def show_movers(change_col):
    risers = df[df[change_col] > 0].sort_values(change_col, ascending=False)
    fallers = df[df[change_col] < 0].sort_values(change_col)
    st.subheader(f"Risers ({len(risers)})")
    show_table(risers, change_col)
    st.subheader(f"Fallers ({len(fallers)})")
    show_table(fallers, change_col)


tab_today, tab_gw, tab_season, tab_likely, tab_mine = st.tabs(
    ["Today", "This Gameweek", "Season-to-Date", "Likely to Rise/Fall", "My Team"]
)

with tab_today:
    if not baseline:
        st.info(
            "No earlier daily snapshot yet — today's movers will appear once there's one from a "
            "previous day to compare against. Until then, see This Gameweek."
        )
    else:
        if base_day == today - timedelta(days=1):
            st.caption(f"Live prices vs yesterday's snapshot ({base_day:%a %d %b}), taken after that day's update.")
        else:
            st.caption(
                f"No snapshot for yesterday, so this compares against the most recent one "
                f"({base_day:%a %d %b}) and may include more than one day's changes."
            )
        show_movers("change_today")

with tab_gw:
    if deadline:
        since = pd.Timestamp(deadline).strftime("%a %d %b")
        st.caption(f"Every price change since the GW{gw} deadline ({since}), not just today's.")
    show_movers("change_gw")

with tab_season:
    moved = df[df["change_season"] != 0].sort_values("change_season", ascending=False)
    st.caption(f"{len(moved)} players have moved in price since the season started")
    show_table(moved, "change_season")

with tab_likely:
    st.caption(
        "FPL doesn't publish its price-change algorithm or thresholds, so this is a heuristic, not a "
        f"guarantee: it ranks players by {transfers_label}, the same signal third-party FPL price "
        "trackers use. Players who already changed price today are left out — FPL moves a player at "
        "most once a day."
    )
    candidates = df[can_move]
    likely_rise = candidates[candidates["net_transfers"] > 0].sort_values("net_transfers", ascending=False).head(15)
    likely_fall = candidates[candidates["net_transfers"] < 0].sort_values("net_transfers").head(15)

    def show_transfer_table(data):
        st.dataframe(
            data[["web_name", "team_name", "position", "price", "selected_by_percent", "net_transfers"]].rename(
                columns={**display_cols_map, "net_transfers": "Net Transfers"}
            ),
            hide_index=True,
            width="stretch",
            column_config={
                "Price": st.column_config.NumberColumn(format="£%.1f"),
                "Net Transfers": st.column_config.NumberColumn(format="%+,d"),
            },
        )

    st.subheader("📈 Likely to Rise")
    show_transfer_table(likely_rise)
    st.subheader("📉 Likely to Fall")
    show_transfer_table(likely_fall)

with tab_mine:
    team_id = st.session_state.get("team_id")
    if not team_id:
        st.info("Enter your Team ID in the sidebar to see your squad's price risk at a glance.")
    else:
        try:
            team_id = int(team_id)
        except ValueError:
            st.error("Team ID must be a number.")
            st.stop()

        try:
            picks = get_entry_picks(team_id, gw)
        except Exception:
            picks = None

        if not picks:
            st.warning(f"Couldn't load your squad for GW{gw}.")
        else:
            st.caption(
                f"Rank is among all players league-wide who can still move today, by {transfers_label}. "
                "Top 30 bought/sold is a rough zone where a change becomes plausible — not a guarantee."
            )
            squad_ids = {p["element"] for p in picks["picks"]}
            squad_df = df[df["id"].isin(squad_ids)].copy()

            candidates = df[can_move]
            rise_rank = candidates.sort_values("net_transfers", ascending=False)["id"].reset_index(drop=True)
            rise_rank = pd.Series(rise_rank.index + 1, index=rise_rank.values)
            fall_rank = candidates.sort_values("net_transfers")["id"].reset_index(drop=True)
            fall_rank = pd.Series(fall_rank.index + 1, index=fall_rank.values)

            def classify(row):
                if row["change_today"] > 0:
                    return f"✅ Rose today (+£{row['change_today']:.1f})"
                if row["change_today"] < 0:
                    return f"⚠️ Fell today (£{row['change_today']:.1f})"
                r_rise = rise_rank.get(row["id"])
                r_fall = fall_rank.get(row["id"])
                if row["net_transfers"] > 0 and r_rise is not None and r_rise <= 30:
                    return f"📈 Rising soon (#{int(r_rise)} most bought)"
                if row["net_transfers"] < 0 and r_fall is not None and r_fall <= 30:
                    return f"📉 Falling soon (#{int(r_fall)} most sold)"
                return "➖ Stable"

            squad_df["Risk"] = squad_df.apply(classify, axis=1)
            squad_df = squad_df.sort_values("net_transfers")

            st.dataframe(
                squad_df[
                    ["web_name", "team_name", "position", "price", "change_gw", "change_season", "net_transfers", "Risk"]
                ].rename(columns=display_cols_map),
                hide_index=True,
                width="stretch",
                column_config={
                    "Price": st.column_config.NumberColumn(format="£%.1f"),
                    "Change This GW": st.column_config.NumberColumn(format="£%+.1f"),
                    "Change This Season": st.column_config.NumberColumn(format="£%+.1f"),
                    "net_transfers": st.column_config.NumberColumn("Net Transfers", format="%+,d"),
                },
            )
