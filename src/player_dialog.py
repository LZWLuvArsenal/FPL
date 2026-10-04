"""Click-through player details, like the FPL site's player modal: this season match by match, previous
seasons, and upcoming fixtures, in a dialog.

`player_dataframe` is a drop-in for st.dataframe that opens the dialog when a row is clicked;
`player_picker` does the same from a dropdown, for tables drawn as custom HTML that can't be clicked.
`gw_points_breakdown` gives each player's gameweek points by source, like the site's points breakdown.
"""
from __future__ import annotations

import html
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

from src.fpl_api import get_bootstrap_static, get_element_summary, get_event_live
from src.pitch import shirt_url

PHOTO_URL = "https://resources.premierleague.com/premierleague25/photos/players/110x140/{code}.png"
BADGE_URL = "https://resources.premierleague.com/premierleague/badges/70/t{code}.png"

# (source field, column label, is an expected stat shown to 2dp)
SEASON_STATS = [
    ("total_points", "Pts", False),
    ("starts", "ST", False),
    ("minutes", "MP", False),
    ("goals_scored", "GS", False),
    ("assists", "A", False),
    ("expected_goals", "xG", True),
    ("expected_assists", "xA", True),
    ("expected_goal_involvements", "xGI", True),
    ("clean_sheets", "CS", False),
    ("goals_conceded", "GC", False),
    ("expected_goals_conceded", "xGC", True),
    ("tackles", "T", False),
    ("clearances_blocks_interceptions", "CBI", False),
    ("recoveries", "R", False),
    ("defensive_contribution", "DC", False),
    ("own_goals", "OG", False),
    ("penalties_saved", "PS", False),
    ("penalties_missed", "PM", False),
    ("yellow_cards", "YC", False),
    ("red_cards", "RC", False),
    ("saves", "S", False),
    ("bonus", "B", False),
    ("bps", "BPS", False),
]
# FPL's points-breakdown identifiers, in the order the site lists them. Anything new falls back to a
# title-cased identifier at the end.
BREAKDOWN_LABELS = {
    "minutes": "Minutes played",
    "goals_scored": "Goals scored",
    "assists": "Assists",
    "clean_sheets": "Clean sheets",
    "goals_conceded": "Goals conceded",
    "saves": "Saves",
    "penalties_saved": "Penalties saved",
    "penalties_missed": "Penalties missed",
    "defensive_contribution": "Defensive contribution",
    "bonus": "Bonus",
    "yellow_cards": "Yellow cards",
    "red_cards": "Red cards",
    "own_goals": "Own goals",
}
# Per-90 rates only make sense for counting stats; points, starts and minutes are left as totals.
NO_PER_90 = {"total_points", "starts", "minutes"}
STAT_HELP = (
    "ST starts · MP minutes · GS goals · A assists · CS clean sheets · GC goals conceded · T tackles · "
    "CBI clearances, blocks & interceptions · R recoveries · DC defensive contribution · OG own goals · "
    "PS/PM penalties saved/missed · YC/RC cards · S saves · B bonus"
)


def _stats_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for field, _, _ in SEASON_STATS:
        df[field] = pd.to_numeric(df.get(field, 0), errors="coerce").fillna(0)
    return df


def _result(h: dict) -> str:
    if h["team_h_score"] is None or h["team_a_score"] is None:
        return ""
    ours, theirs = (h["team_h_score"], h["team_a_score"]) if h["was_home"] else (h["team_a_score"], h["team_h_score"])
    outcome = "W" if ours > theirs else "L" if ours < theirs else "D"
    return f"{outcome} {h['team_h_score']}-{h['team_a_score']}"


def _totals_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Totals and per-90 rows, pre-formatted as text so integers and rates can share a column."""
    minutes = df["minutes"].sum()
    totals, per90 = {"": "Totals"}, {"": "Per 90"}
    for field, label, is_x in SEASON_STATS:
        total = df[field].sum()
        totals[label] = f"{total:.2f}" if is_x else f"{total:,.0f}"
        per90[label] = "-" if field in NO_PER_90 or minutes == 0 else f"{total / minutes * 90:.2f}"
    return pd.DataFrame([totals, per90])


@st.cache_data(ttl=86400, show_spinner=False)
def _photo_exists(url: str) -> bool:
    """New signings often have no photo yet; the CDN answers 403 for those."""
    try:
        return requests.head(url, timeout=5).status_code == 200
    except requests.RequestException:
        return False


def _header_html(player: dict, team: dict, position: str) -> str:
    photo = PHOTO_URL.format(code=player["photo"].split(".")[0])
    if not _photo_exists(photo):
        photo = shirt_url(team["code"], position == "GKP")
    stats = [
        ("Price", f"£{player['now_cost'] / 10:.1f}m"),
        ("Total Pts", player["total_points"]),
        ("Form", player["form"]),
        ("PPG", player["points_per_game"]),
        ("Selected", f"{player['selected_by_percent']}%"),
    ]
    chips = "".join(
        '<div style="background:rgba(128,128,128,0.14); border-radius:8px; padding:6px 12px; min-width:72px;">'
        f'<div style="font-size:0.7rem; opacity:0.7;">{label}</div>'
        f'<div style="font-size:1.05rem; font-weight:700;">{html.escape(str(value))}</div></div>'
        for label, value in stats
    )
    return (
        '<div style="display:flex; gap:16px; align-items:flex-end; flex-wrap:wrap; margin-bottom:8px;">'
        f'<img src="{photo}" alt="" style="height:120px; width:auto; border-radius:8px; '
        'background:linear-gradient(180deg, rgba(128,128,128,0.25), rgba(128,128,128,0.05));">'
        '<div style="flex:1; min-width:220px;">'
        '<div style="display:flex; align-items:center; gap:8px; margin-bottom:10px;">'
        f'<img src="{BADGE_URL.format(code=team["code"])}" alt="" style="height:28px;">'
        f'<span style="opacity:0.8;">{html.escape(team["name"])} · {position}</span></div>'
        f'<div style="display:flex; gap:8px; flex-wrap:wrap;">{chips}</div></div></div>'
    )


def show_player(player_id: int) -> None:
    """Opens the dialog, titled with the player's name (st.dialog's title is fixed when it's created,
    so it's created per call)."""
    player = next((e for e in get_bootstrap_static()["elements"] if e["id"] == player_id), None)
    title = f"{player['first_name']} {player['second_name']}" if player else "Player details"
    st.dialog(title, width="large")(_player_details)(player_id)


def _player_details(player_id: int) -> None:
    bootstrap = get_bootstrap_static()
    player = next((e for e in bootstrap["elements"] if e["id"] == player_id), None)
    if player is None:
        st.error("Player not found.")
        return
    teams = {t["id"]: t for t in bootstrap["teams"]}
    position = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}[player["element_type"]]
    summary = get_element_summary(player_id)

    st.markdown(_header_html(player, teams[player["team"]], position), unsafe_allow_html=True)
    if player.get("news"):
        st.warning(player["news"])

    st.markdown("#### This Season")
    history = summary["history"]
    if not history:
        st.caption("No appearances yet this season.")
    else:
        df = _stats_frame(history).sort_values(["round", "kickoff_time"], ascending=False)
        table = pd.DataFrame(
            {
                "GW": df["round"],
                "Opponent": [
                    f"{teams[h['opponent_team']]['short_name']} ({'H' if h['was_home'] else 'A'})"
                    for h in df.to_dict("records")
                ],
                "Result": [_result(h) for h in df.to_dict("records")],
                **{label: df[field] for field, label, _ in SEASON_STATS},
                "Price": df["value"] / 10,
            }
        )
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={
                **{label: st.column_config.NumberColumn(format="%.2f") for _, label, is_x in SEASON_STATS if is_x},
                **{label: st.column_config.NumberColumn(format="%d") for _, label, is_x in SEASON_STATS if not is_x},
                "Price": st.column_config.NumberColumn(format="£%.1f"),
            },
        )
        st.dataframe(_totals_rows(df), hide_index=True, width="stretch")

    upcoming = summary["fixtures"][:5]
    if upcoming:
        st.markdown("#### Upcoming Fixtures")
        st.dataframe(
            pd.DataFrame(
                {
                    "GW": [f["event"] for f in upcoming],
                    "Opponent": [
                        f"{teams[f['team_a'] if f['is_home'] else f['team_h']]['short_name']} ({'H' if f['is_home'] else 'A'})"
                        for f in upcoming
                    ],
                    "Kickoff": [
                        datetime.fromisoformat(f["kickoff_time"].replace("Z", "+00:00")).strftime("%a %d %b %H:%M UTC")
                        if f["kickoff_time"]
                        else "TBC"
                        for f in upcoming
                    ],
                    "Difficulty": [f["difficulty"] for f in upcoming],
                }
            ),
            hide_index=True,
            width="stretch",
        )

    past = summary["history_past"]
    if past:
        st.markdown("#### Previous Seasons")
        pdf = _stats_frame(past).iloc[::-1]
        st.dataframe(
            pd.DataFrame(
                {
                    "Season": pdf["season_name"],
                    **{label: pdf[field] for field, label, _ in SEASON_STATS},
                    "Start £": pdf["start_cost"] / 10,
                    "End £": pdf["end_cost"] / 10,
                }
            ),
            hide_index=True,
            width="stretch",
            column_config={
                **{label: st.column_config.NumberColumn(format="%.2f") for _, label, is_x in SEASON_STATS if is_x},
                **{label: st.column_config.NumberColumn(format="%d") for _, label, is_x in SEASON_STATS if not is_x},
                "Start £": st.column_config.NumberColumn(format="£%.1f"),
                "End £": st.column_config.NumberColumn(format="£%.1f"),
            },
        )
    st.caption(STAT_HELP)


def gw_points_breakdown(event_id: int) -> dict[int, list[tuple[str, int, int]]]:
    """player id -> [(label, value, points), ...] for the gameweek, summed across a double gameweek's
    fixtures. Only lists what scored (or lost) points, like the site."""
    out = {}
    for el in get_event_live(event_id)["elements"]:
        totals: dict[str, list[int]] = {}
        for fixture in el["explain"]:
            for stat in fixture["stats"]:
                value_points = totals.setdefault(stat["identifier"], [0, 0])
                value_points[0] += stat["value"]
                value_points[1] += stat["points"]
        order = list(BREAKDOWN_LABELS)
        ranked = sorted(totals.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order))
        out[el["id"]] = [
            (BREAKDOWN_LABELS.get(ident, ident.replace("_", " ").capitalize()), value, points)
            for ident, (value, points) in ranked
            if points != 0
        ]
    return out


def _open_on_change(key: str, selection, player_id) -> None:
    """Opens the dialog only when the selection changes, so it doesn't pop back up on every rerun
    (e.g. after closing it and touching another widget)."""
    state_key = f"_player_dialog_last_{key}"
    if selection is None:
        st.session_state.pop(state_key, None)
    elif st.session_state.get(state_key) != selection:
        st.session_state[state_key] = selection
        show_player(int(player_id))


def player_dataframe(data: pd.DataFrame, ids, key: str, **kwargs):
    """st.dataframe whose rows open the player dialog when clicked. `ids` are the FPL player ids in the
    same row order as `data` (e.g. the id column before it's dropped or renamed)."""
    ids = list(ids)
    event = st.dataframe(data, key=key, on_select="rerun", selection_mode="single-row", **kwargs)
    rows = event.selection.rows
    _open_on_change(key, tuple(rows) if rows else None, ids[rows[0]] if rows else None)
    return event


def player_picker(ids, names, key: str, label: str = "🔍 Player details") -> None:
    """A dropdown that opens the player dialog, for tables drawn as HTML that can't be clicked. `ids` and
    `names` are the table's player ids and display names, in the order to list them."""
    options = dict(zip((int(i) for i in ids), names))
    choice = st.selectbox(
        label, list(options), index=None, format_func=lambda i: options.get(i, ""), key=key, placeholder="Choose a player to see their details"
    )
    _open_on_change(key, choice, choice)
