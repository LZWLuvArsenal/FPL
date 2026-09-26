"""Helpers for turning raw FPL API payloads into pandas DataFrames."""
from __future__ import annotations

import pandas as pd

# Official FPL fixture-difficulty palette as inline CSS (solid backgrounds with explicit text colours, so
# it stays legible on the dark theme). Key = difficulty 1 (easiest) to 5 (hardest).
FDR_STYLES = {
    1: "background-color: #257d5a; color: #ffffff",
    2: "background-color: #00ff86; color: #14001c",
    3: "background-color: #ebebe4; color: #14001c",
    4: "background-color: #ff005a; color: #ffffff",
    5: "background-color: #861d46; color: #ffffff",
}
FDR_STYLE_UNKNOWN = "background-color: #4a2266; color: #ffffff"


def players_df(bootstrap: dict) -> pd.DataFrame:
    df = pd.DataFrame(bootstrap["elements"])
    teams = {t["id"]: t["name"] for t in bootstrap["teams"]}
    team_shorts = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    positions = {p["id"]: p["singular_name_short"] for p in bootstrap["element_types"]}
    df["team_name"] = df["team"].map(teams)
    df["team_short"] = df["team"].map(team_shorts)
    df["position"] = df["element_type"].map(positions)
    df["price"] = df["now_cost"] / 10
    df["full_name"] = df["first_name"] + " " + df["second_name"]
    numeric_cols = (
        "form",
        "points_per_game",
        "selected_by_percent",
        "ict_index",
        "expected_goals",
        "expected_assists",
        "expected_goal_involvements",
        "expected_goals_conceded",
    )
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def teams_df(bootstrap: dict) -> pd.DataFrame:
    return pd.DataFrame(bootstrap["teams"])


def season_name(bootstrap: dict) -> str:
    """'2026-27' style label, from the year of the GW1 deadline."""
    year = int(bootstrap["events"][0]["deadline_time"][:4])
    return f"{year}-{(year + 1) % 100:02d}"


def current_event(bootstrap: dict) -> int:
    events = bootstrap["events"]
    for e in events:
        if e["is_current"]:
            return e["id"]
    for e in events:
        if e["is_next"]:
            return e["id"]
    return events[-1]["id"]


def next_event(bootstrap: dict) -> int | None:
    for e in bootstrap["events"]:
        if e["is_next"]:
            return e["id"]
    return None
