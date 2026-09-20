import pandas as pd
import streamlit as st

from src.config import render_sidebar_settings
from src.fpl_api import get_bootstrap_static, get_entry_picks
from src.utils import current_event, players_df

st.set_page_config(page_title="Price Changes - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Price Changes")
st.caption(
    "The API only exposes the latest price movement and the season-to-date change — there's no "
    "day-by-day history to browse. Today's movers update once daily, typically around 1:30am UK time."
)

bootstrap = get_bootstrap_static()
df = players_df(bootstrap)
df["change_today"] = df["cost_change_event"] / 10
df["change_season"] = df["cost_change_start"] / 10
df["net_transfers"] = df["transfers_in_event"] - df["transfers_out_event"]

display_cols_map = {
    "web_name": "Name",
    "team_name": "Team",
    "position": "Pos",
    "price": "Price",
    "selected_by_percent": "Selected %",
    "change_today": "Change Today",
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


tab1, tab2, tab3, tab4 = st.tabs(["Today's Movers", "Season-to-Date", "Likely to Rise/Fall", "My Team"])

with tab1:
    risers = df[df["change_today"] > 0].sort_values("change_today", ascending=False)
    fallers = df[df["change_today"] < 0].sort_values("change_today")
    st.subheader(f"Risers ({len(risers)})")
    show_table(risers, "change_today")
    st.subheader(f"Fallers ({len(fallers)})")
    show_table(fallers, "change_today")

with tab2:
    moved = df[df["change_season"] != 0].sort_values("change_season", ascending=False)
    st.caption(f"{len(moved)} players have moved in price since the season started")
    show_table(moved, "change_season")

with tab3:
    st.caption(
        "FPL doesn't publish its price-change algorithm or thresholds, so this is a heuristic, not a "
        "guarantee: it ranks players by today's net transfers (in minus out), which is the same signal "
        "third-party FPL price trackers use. Players who already moved today are excluded since they've "
        "already triggered their change for this cycle."
    )
    unmoved = df[df["change_today"] == 0]
    likely_rise = unmoved[unmoved["net_transfers"] > 0].sort_values("net_transfers", ascending=False).head(15)
    likely_fall = unmoved[unmoved["net_transfers"] < 0].sort_values("net_transfers").head(15)

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

with tab4:
    team_id = st.session_state.get("team_id")
    if not team_id:
        st.info("Enter your Team ID in the sidebar to see your squad's price risk at a glance.")
    else:
        try:
            team_id = int(team_id)
        except ValueError:
            st.error("Team ID must be a number.")
            st.stop()

        gw = current_event(bootstrap)
        try:
            picks = get_entry_picks(team_id, gw)
        except Exception:
            picks = None

        if not picks:
            st.warning(f"Couldn't load your squad for GW{gw}.")
        else:
            st.caption(
                "Rank is among all not-yet-moved players league-wide, by today's net transfers. Top 30 "
                "bought/sold is a rough zone where a change becomes plausible — not a guarantee."
            )
            squad_ids = {p["element"] for p in picks["picks"]}
            squad_df = df[df["id"].isin(squad_ids)].copy()

            unmoved = df[df["change_today"] == 0]
            rise_rank = unmoved.sort_values("net_transfers", ascending=False)["id"].reset_index(drop=True)
            rise_rank = pd.Series(rise_rank.index + 1, index=rise_rank.values)
            fall_rank = unmoved.sort_values("net_transfers")["id"].reset_index(drop=True)
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
                    ["web_name", "team_name", "position", "price", "change_season", "net_transfers", "Risk"]
                ].rename(columns=display_cols_map),
                hide_index=True,
                width="stretch",
                column_config={
                    "Price": st.column_config.NumberColumn(format="£%.1f"),
                    "Change This Season": st.column_config.NumberColumn(format="£%+.1f"),
                    "net_transfers": st.column_config.NumberColumn("Net Transfers", format="%+,d"),
                },
            )
