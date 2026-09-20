import html

import pandas as pd
import plotly.express as px
import streamlit as st

from src.config import load_config, render_sidebar_settings, update_config
from src.fpl_api import get_bootstrap_static, get_entry, get_entry_picks, get_entry_transfers, get_league_standings
from src.utils import current_event, players_df

st.set_page_config(page_title="League Explorer - FPL Dashboard", page_icon="⚽", layout="wide")
render_sidebar_settings()
st.title("Mini-League Explorer")
st.caption("See any league member's squad, captain, and transfers — not just your own.")

team_id = st.session_state.get("team_id")
if not team_id:
    st.info("Enter your Team ID in the sidebar first — I'll look up which mini-leagues it belongs to.")
    st.stop()
try:
    team_id = int(team_id)
except ValueError:
    st.error("Team ID must be a number.")
    st.stop()

my_entry = get_entry(team_id)
private_leagues = [l for l in my_entry.get("leagues", {}).get("classic", []) if l["league_type"] == "x"]
if not private_leagues:
    st.warning(
        "No private mini-leagues found for this Team ID (only FPL's automatic leagues like "
        "'Overall' or your country league, which aren't shown here)."
    )
    st.stop()

league_options = {f"{l['name']} ({l['id']})": l["id"] for l in private_leagues}
labels = list(league_options.keys())
default_league_id = load_config().get("default_league_id")
default_idx = next((i for i, v in enumerate(league_options.values()) if v == default_league_id), 0)

select_col, save_col = st.columns([4, 1])
chosen_label = select_col.selectbox("Mini-League", labels, index=default_idx)
league_id = league_options[chosen_label]

save_col.markdown("<div style='margin-top:1.8rem'></div>", unsafe_allow_html=True)
if league_id == default_league_id:
    save_col.markdown("⭐ Default")
elif save_col.button("Set as default"):
    update_config(default_league_id=league_id)
    st.rerun()

bootstrap = get_bootstrap_static()
players = players_df(bootstrap).set_index("id")
gw_options = [e["id"] for e in bootstrap["events"] if e["finished"] or e["is_current"]]
default_gw = current_event(bootstrap)
gw = st.selectbox(
    "Gameweek", gw_options, index=gw_options.index(default_gw) if default_gw in gw_options else len(gw_options) - 1
)

standings_data = get_league_standings(league_id)
members = pd.DataFrame(standings_data["standings"]["results"])
st.subheader(standings_data["league"]["name"])


_CARD_STYLE = "display:grid; grid-template-columns:repeat(auto-fill, minmax(108px, 1fr)); gap:8px; margin-bottom:4px;"


_POS_LABELS = {"GKP": "GK", "DEF": "DF", "MID": "MF", "FWD": "FW"}


def _player_card(row, bench, ownership_pct, effective_ownership_pct=None):
    role = ""
    if row["is_captain"]:
        role = (
            ' <span style="background:#ffb300; color:#1a1a1a; font-size:0.62rem; font-weight:700; '
            'padding:1px 4px; border-radius:4px; margin-left:2px;">C</span>'
        )
    elif row["is_vice_captain"]:
        role = (
            ' <span style="background:rgba(128,128,128,0.35); color:inherit; font-size:0.62rem; font-weight:700; '
            'padding:1px 4px; border-radius:4px; margin-left:2px;">VC</span>'
        )
    bg = "rgba(128,128,128,0.16)" if bench else "rgba(128,128,128,0.04)"
    name = html.escape(row["web_name"])
    team = html.escape(row["team_short"])
    pos = _POS_LABELS.get(row["player_pos"], row["player_pos"])
    if ownership_pct is not None:
        if effective_ownership_pct is not None:
            own_line = (
                f'<div style="font-size:0.68rem; opacity:0.55; margin-top:2px;" '
                f'title="Effective ownership: (starters + captains) / total managers in league — '
                f'captaincy counts double. Raw ownership: {ownership_pct:.0f}%.">'
                f'{effective_ownership_pct:.0f}% EO</div>'
            )
        else:
            own_line = f'<div style="font-size:0.68rem; opacity:0.55; margin-top:2px;">{ownership_pct:.0f}% owned</div>'
    else:
        own_line = ""
    return (
        f'<div style="background:{bg}; border:1px solid rgba(128,128,128,0.25); border-radius:8px; '
        f'padding:6px 4px; text-align:center;">'
        f'<div style="font-size:0.82rem; font-weight:600; line-height:1.25;">{name}{role}</div>'
        f'<div style="font-size:0.72rem; opacity:0.65;">{team} ({pos})</div>'
        f'<div style="font-size:0.95rem; font-weight:700; margin-top:2px;">{row["event_points"]} pts</div>'
        f"{own_line}"
        f"</div>"
    )


def render_squad_grid(picks_payload, own_df=None):
    df = pd.DataFrame(picks_payload["picks"]).merge(
        players.reset_index().rename(columns={"position": "player_pos"})[
            ["id", "web_name", "team_short", "event_points", "player_pos"]
        ],
        left_on="element",
        right_on="id",
    ).sort_values("position")

    starters = df[df["position"] <= 11]
    bench = df[df["position"] > 11]

    def ownership_for(element_id):
        if own_df is None or element_id not in own_df.index:
            return None, None
        row = own_df.loc[element_id]
        return row["ownership_pct"], row["effective_ownership_pct"]

    st.markdown(
        f'<div style="{_CARD_STYLE}">'
        + "".join(_player_card(r, False, *ownership_for(r["element"])) for _, r in starters.iterrows())
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption("Bench")
    st.markdown(
        f'<div style="{_CARD_STYLE}">'
        + "".join(_player_card(r, True, *ownership_for(r["element"])) for _, r in bench.iterrows())
        + "</div>",
        unsafe_allow_html=True,
    )


st.divider()
cache_key = f"league_picks_{league_id}_{gw}"
load_col1, load_col2 = st.columns([4, 1])
load_col1.caption(
    f"{len(members)} managers in this league. Loading full squads pulls one request per manager — needed "
    "for the squad breakdowns, captain/chip overview, and the Player Breakdown tab."
)
if standings_data["standings"]["has_next"]:
    load_col1.caption(
        "⚠️ This league has more than 50 members — standings only return the top 50 by rank, so "
        "ownership/captaincy stats below only reflect those managers. Your own squad is always "
        "included in the Rank Impact section even if you're outside the top 50."
    )
if load_col2.button("Load full league data", width="stretch"):
    loaded = {}
    progress = st.progress(0.0)
    for i, (_, m) in enumerate(members.iterrows()):
        try:
            loaded[int(m["entry"])] = get_entry_picks(int(m["entry"]), gw)
        except Exception:
            pass
        progress.progress((i + 1) / len(members))
    if team_id not in loaded:
        try:
            loaded[team_id] = get_entry_picks(team_id, gw)
        except Exception:
            pass
    progress.empty()
    st.session_state[cache_key] = loaded

league_picks = st.session_state.get(cache_key)

own_df = None
if league_picks:
    owner_counts = {}
    for picks in league_picks.values():
        for p in picks["picks"]:
            rec = owner_counts.setdefault(p["element"], {"owners": 0, "captains": 0, "vices": 0, "benched": 0})
            rec["owners"] += 1
            if p["is_captain"]:
                rec["captains"] += 1
            if p["is_vice_captain"]:
                rec["vices"] += 1
            if p["position"] > 11:
                rec["benched"] += 1
    own_df = pd.DataFrame.from_dict(owner_counts, orient="index")
    own_df["ownership_pct"] = own_df["owners"] / len(league_picks) * 100
    own_df["captain_pct"] = own_df["captains"] / len(league_picks) * 100
    own_df["vice_pct"] = own_df["vices"] / len(league_picks) * 100
    own_df["benched_pct"] = own_df["benched"] / len(league_picks) * 100
    # Effective ownership: captaincy counts double, so a player owned by everyone and captained
    # by half the league can carry more than 100% weight in the league's overall rank movement.
    # Denominator is the whole league, not just this player's owners (owning him but benching him
    # contributes 0, captaining him contributes 2x) — the standard FPL-community EO definition.
    own_df["starters"] = own_df["owners"] - own_df["benched"]
    own_df["effective_ownership_pct"] = (own_df["starters"] + own_df["captains"]) / len(league_picks) * 100
    own_df = own_df.join(players[["web_name", "team_name", "team_short", "position", "price"]])

tab1, tab2, tab3, tab4 = st.tabs(["League Overview", "Manager Detail", "Player Breakdown", "All Transfers"])

with tab1:
    if not league_picks:
        st.info("Click **Load full league data** above to see captains, chips, and squads for everyone.")
    else:
        st.subheader("Find a Player")
        search_col1, search_col2 = st.columns([1, 2])
        criterion = search_col1.selectbox("Criterion", ["Owns", "Captained"])
        player_lookup = {
            f"{r['web_name']} ({r['team_short']})": idx for idx, r in own_df.sort_values("web_name").iterrows()
        }
        selected_label = search_col2.selectbox("Player", ["-- none --"] + list(player_lookup.keys()))

        highlighted_entries = set()
        if selected_label != "-- none --":
            target_element = player_lookup[selected_label]
            for entry_id, picks in league_picks.items():
                for p in picks["picks"]:
                    if p["element"] != target_element:
                        continue
                    if criterion == "Owns" or p["is_captain"]:
                        highlighted_entries.add(entry_id)
                    break

        rows = []
        for _, m in members.iterrows():
            entry_id = int(m["entry"])
            picks = league_picks.get(entry_id)
            if not picks:
                continue
            cap = next((p for p in picks["picks"] if p["is_captain"]), None)
            vc = next((p for p in picks["picks"] if p["is_vice_captain"]), None)
            rows.append(
                {
                    "Rank": m["rank"],
                    "Manager": m["player_name"],
                    "Team": m["entry_name"],
                    "Captain": players.loc[cap["element"], "web_name"] if cap is not None else "-",
                    "Vice-Captain": players.loc[vc["element"], "web_name"] if vc is not None else "-",
                    "Chip": picks["active_chip"] or "-",
                    "GW Points": picks["entry_history"]["points"],
                    "Bench Points": picks["entry_history"]["points_on_bench"],
                    "Overall Points": m["total"],
                    "_entry_id": entry_id,
                }
            )
        overview_df = pd.DataFrame(rows).sort_values("GW Points", ascending=False)
        overview_df["_match"] = overview_df["_entry_id"].isin(highlighted_entries)
        match_lookup = overview_df["_match"]

        def _highlight_match(row):
            color = "background-color: rgba(46, 204, 113, 0.35)" if match_lookup.loc[row.name] else ""
            return [color] * len(row)

        st.dataframe(
            overview_df.drop(columns=["_entry_id", "_match"]).style.apply(_highlight_match, axis=1),
            hide_index=True,
            width="stretch",
        )
        if selected_label != "-- none --":
            verb = "own" if criterion == "Owns" else "captained"
            st.caption(f"{len(highlighted_entries)} manager(s) {verb} {selected_label} this gameweek.")

        st.subheader("Squads")
        for _, row in overview_df.iterrows():
            marker = "🟢 " if row["_match"] else ""
            with st.expander(f"{marker}{row['Manager']} — {row['Team']}  ·  {row['GW Points']} pts"):
                render_squad_grid(league_picks[row["_entry_id"]], own_df)

with tab2:
    manager_options = {f"{r['player_name']} — {r['entry_name']}": int(r["entry"]) for _, r in members.iterrows()}
    default_manager_idx = next((i for i, v in enumerate(manager_options.values()) if v == team_id), 0)
    chosen_manager = st.selectbox("Manager", list(manager_options.keys()), index=default_manager_idx)
    manager_id = manager_options[chosen_manager]

    entry = get_entry(manager_id)
    picks = (league_picks or {}).get(manager_id) or get_entry_picks(manager_id, gw)
    history = picks["entry_history"]

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Overall Points", entry["summary_overall_points"])
    col2.metric("GW Points", history["points"])
    col3.metric("Team Value", f"£{history['value'] / 10:.1f}m")
    col4.metric("Chip Used", picks["active_chip"] or "None")

    st.subheader("Squad")
    render_squad_grid(picks, own_df)

    st.subheader("Transfers")
    transfers = get_entry_transfers(manager_id)
    if transfers:
        t_df = pd.DataFrame(transfers)
        t_df["Player In"] = t_df["element_in"].map(players["web_name"])
        t_df["Player Out"] = t_df["element_out"].map(players["web_name"])
        t_df["Cost In"] = t_df["element_in_cost"] / 10
        t_df["Cost Out"] = t_df["element_out_cost"] / 10
        this_gw_only = st.checkbox(f"Only show GW{gw} transfers", value=True)
        if this_gw_only:
            t_df = t_df[t_df["event"] == gw]
        st.dataframe(
            t_df.rename(columns={"event": "GW"})[["GW", "Player In", "Cost In", "Player Out", "Cost Out"]],
            hide_index=True,
            width="stretch",
            column_config={
                "Cost In": st.column_config.NumberColumn(format="£%.1f"),
                "Cost Out": st.column_config.NumberColumn(format="£%.1f"),
            },
        )
        if t_df.empty:
            st.caption("No transfers for this filter.")
    else:
        st.caption("No transfers made all season.")

with tab3:
    if not league_picks:
        st.info("Click **Load full league data** above to see ownership, captaincy, and rank-threat breakdowns.")
    else:
        num_managers = len(league_picks)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.subheader("Most Owned in League")
            top_owned = own_df.sort_values("owners", ascending=False).head(15)
            st.dataframe(
                top_owned[["web_name", "team_name", "position", "owners", "ownership_pct"]].round(0).rename(
                    columns={
                        "web_name": "Player",
                        "team_name": "Team",
                        "position": "Pos",
                        "owners": "Owned By",
                        "ownership_pct": "Ownership %",
                    }
                ),
                hide_index=True,
                width="stretch",
            )
        with col2:
            st.subheader("Most Captained in League")
            top_captained = own_df[own_df["captains"] > 0].sort_values("captains", ascending=False).head(15)
            st.dataframe(
                top_captained[["web_name", "team_name", "position", "captains", "captain_pct"]].round(0).rename(
                    columns={
                        "web_name": "Player",
                        "team_name": "Team",
                        "position": "Pos",
                        "captains": "Captained By",
                        "captain_pct": "Captain %",
                    }
                ),
                hide_index=True,
                width="stretch",
            )
        with col3:
            st.subheader("Most Benched in League")
            top_benched = own_df[own_df["benched"] > 0].sort_values("benched", ascending=False).head(15)
            st.dataframe(
                top_benched[["web_name", "team_name", "position", "benched", "benched_pct"]].round(0).rename(
                    columns={
                        "web_name": "Player",
                        "team_name": "Team",
                        "position": "Pos",
                        "benched": "Benched By",
                        "benched_pct": "Benched %",
                    }
                ),
                hide_index=True,
                width="stretch",
            )
            st.caption("Owned but left out of the starting XI this gameweek.")

        st.subheader("Highest Effective Ownership in League")
        st.caption(
            "Effective ownership = (managers starting the player + managers captaining the player) "
            "÷ total managers in the league. Captaincy doubles a player's weight on the league's rank "
            "movement, so this can exceed 100% — e.g. in a 13-manager league, 5 start a player and 2 "
            "of them also captain him: (5 + 2) ÷ 13 = 54%; a near-universal captain pick like Haaland "
            "can run much higher, e.g. (12 + 7) ÷ 13 = 146%."
        )
        top_eo = own_df[own_df["owners"] > 0].sort_values("effective_ownership_pct", ascending=False).head(15)
        st.dataframe(
            top_eo[
                ["web_name", "team_name", "position", "owners", "captains", "effective_ownership_pct"]
            ].round(0).rename(
                columns={
                    "web_name": "Player",
                    "team_name": "Team",
                    "position": "Pos",
                    "owners": "Owned By",
                    "captains": "Captained By",
                    "effective_ownership_pct": "Effective Ownership %",
                }
            ),
            hide_index=True,
            width="stretch",
        )

        def _pie_with_others(df, count_col, pct_col, title):
            slice_df = df[df[count_col] > 0][["web_name", pct_col]].rename(columns={"web_name": "label"})
            main = slice_df[slice_df[pct_col] >= 5]
            others_pct = slice_df.loc[slice_df[pct_col] < 5, pct_col].sum()
            if others_pct > 0:
                main = pd.concat([main, pd.DataFrame([{"label": "Others", pct_col: others_pct}])])
            if main.empty:
                st.caption("No data at this threshold.")
                return
            fig = px.pie(main, names="label", values=pct_col, title=title, hole=0.4)
            fig.update_traces(textinfo="label+percent")
            st.plotly_chart(fig, width="stretch")

        pie_col1, pie_col2 = st.columns(2)
        with pie_col1:
            _pie_with_others(own_df, "captains", "captain_pct", "Captaincy Share")
        with pie_col2:
            _pie_with_others(own_df, "vices", "vice_pct", "Vice-Captaincy Share")
        st.caption("Slices under 5% of the league are grouped into 'Others'.")

        st.divider()
        st.subheader("Rank Impact vs. Your Squad")
        my_picks = league_picks.get(team_id)
        if not my_picks:
            st.warning("Your Team ID's picks weren't loaded for this gameweek — try reloading above.")
        else:
            my_element_ids = {p["element"] for p in my_picks["picks"]}
            rival_denominator = max(num_managers - 1, 1)
            own_df["mine"] = own_df.index.to_series().isin(my_element_ids)
            own_df["rival_owners"] = own_df["owners"] - own_df["mine"].astype(int)
            own_df["rival_ownership_pct"] = own_df["rival_owners"] / rival_denominator * 100

            sleeper_threshold = st.slider("High-ownership threshold (%)", 30, 100, 60, step=5)
            diff_threshold = st.slider("Differential threshold — max rival ownership (%)", 0, 50, 15, step=5)

            threats = own_df[(~own_df["mine"]) & (own_df["rival_ownership_pct"] >= sleeper_threshold)].sort_values(
                "rival_ownership_pct", ascending=False
            )
            sleepers = own_df[own_df["ownership_pct"] >= sleeper_threshold]
            my_diffs = own_df[own_df["mine"] & (own_df["rival_ownership_pct"] <= diff_threshold)].sort_values(
                "rival_ownership_pct"
            )

            def show(df, cols_pct, label):
                if df.empty:
                    st.caption("None at this threshold.")
                    return
                st.dataframe(
                    df[["web_name", "team_name", "position", "price"] + cols_pct].round(0).rename(
                        columns={
                            "web_name": "Player",
                            "team_name": "Team",
                            "position": "Pos",
                            "price": "Price",
                            **{c: label for c in cols_pct},
                        }
                    ),
                    hide_index=True,
                    width="stretch",
                    column_config={"Price": st.column_config.NumberColumn(format="£%.1f")},
                )

            st.markdown("**🎯 Threats to your rank** — rivals own these, you don't. If they haul, you fall behind.")
            show(threats, ["rival_ownership_pct"], "Rival Ownership %")

            st.markdown(
                "**😴 Sleepers** — near-universal ownership across the league. Whether you own these barely "
                "moves your rank relative to rivals, since almost everyone has them."
            )
            show(sleepers, ["ownership_pct"], "League Ownership %")

            st.markdown(
                "**💎 Your differentials** — you own these, few rivals do. If they haul, you gain rank on the field."
            )
            show(my_diffs, ["rival_ownership_pct"], "Rival Ownership %")

with tab4:
    st.caption(f"{len(members)} managers in this league. Loading transfers pulls one request per manager.")
    transfer_cache_key = f"league_transfers_{league_id}"
    if st.button("Load all transfers", width="stretch"):
        loaded_transfers = {}
        progress = st.progress(0.0)
        for i, (_, m) in enumerate(members.iterrows()):
            try:
                loaded_transfers[int(m["entry"])] = get_entry_transfers(int(m["entry"]))
            except Exception:
                pass
            progress.progress((i + 1) / len(members))
        progress.empty()
        st.session_state[transfer_cache_key] = loaded_transfers

    all_transfers = st.session_state.get(transfer_cache_key)
    if not all_transfers:
        st.info("Click **Load all transfers** above to see every transfer made in this league this season.")
    else:
        manager_names = dict(zip(members["entry"].astype(int), members["player_name"]))
        team_names_map = dict(zip(members["entry"].astype(int), members["entry_name"]))
        players_by_id = players.set_index("id")["web_name"]

        rows = []
        for entry_id, transfers in all_transfers.items():
            for t in transfers:
                rows.append(
                    {
                        "GW": t["event"],
                        "Manager": manager_names.get(entry_id, entry_id),
                        "Team": team_names_map.get(entry_id, ""),
                        "Player In": players_by_id.get(t["element_in"], "?"),
                        "Cost In": t["element_in_cost"] / 10,
                        "Player Out": players_by_id.get(t["element_out"], "?"),
                        "Cost Out": t["element_out_cost"] / 10,
                        "_time": t["time"],
                    }
                )

        if not rows:
            st.info("No transfers made by anyone in this league yet.")
        else:
            tdf = pd.DataFrame(rows).sort_values("_time", ascending=False)
            gw_filter = st.multiselect("Filter by GW", sorted(tdf["GW"].unique()), default=[])
            if gw_filter:
                tdf = tdf[tdf["GW"].isin(gw_filter)]
            st.caption(f"{len(tdf)} transfer(s) shown.")
            st.dataframe(
                tdf.drop(columns="_time"),
                hide_index=True,
                width="stretch",
                column_config={
                    "Cost In": st.column_config.NumberColumn(format="£%.1f"),
                    "Cost Out": st.column_config.NumberColumn(format="£%.1f"),
                },
            )
