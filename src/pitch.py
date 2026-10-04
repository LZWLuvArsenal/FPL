"""HTML pitch view of a 15-man squad: starters in formation rows on a striped pitch, bench underneath."""
from __future__ import annotations

import html

import pandas as pd

SHIRT_URL = "https://fantasy.premierleague.com/dist/img/shirts/standard/shirt_{code}{gk}-110.png"
POSITION_ROWS = ["GKP", "DEF", "MID", "FWD"]

def shirt_url(team_code: int, is_goalkeeper: bool) -> str:
    return SHIRT_URL.format(code=team_code, gk="_1" if is_goalkeeper else "")


_CSS = """<style>
.fpl-pitch{background:repeating-linear-gradient(180deg,#0e9f57 0,#0e9f57 64px,#13ad61 64px,#13ad61 128px);
border-radius:14px;padding:16px 6px 4px;border:2px solid rgba(255,255,255,.55);max-width:780px;margin:0 auto 14px;
box-shadow:0 8px 24px rgba(0,0,0,.4);}
.fpl-row{display:flex;justify-content:center;gap:clamp(6px,2.2vw,28px);margin:0 0 18px;}
.fpl-p{width:clamp(56px,14vw,104px);text-align:center;position:relative;}
.fpl-p[title]{cursor:help;}
.fpl-p img{width:78%;display:block;margin:0 auto -6px;position:relative;z-index:1;}
.fpl-name{background:#fff;color:#14001c;font-weight:700;font-size:clamp(.6rem,1.7vw,.86rem);padding:2px 2px;
border-radius:5px 5px 0 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;position:relative;z-index:2;}
.fpl-pts{font-weight:700;font-size:clamp(.62rem,1.7vw,.86rem);padding:2px 2px;border-radius:0 0 5px 5px;color:#fff;
background:#37003C;position:relative;z-index:2;}
.fpl-pts.low{background:linear-gradient(90deg,#e6198c,#ff5a2e);}
.fpl-pts.high{background:#00ff87;color:#14001c;}
.fpl-pts.fix{background:#ebebe4;color:#14001c;}
.fpl-badge{position:absolute;top:0;left:6%;width:1.35em;height:1.35em;line-height:1.35em;border-radius:50%;background:#37003C;
color:#fff;font-size:.68rem;font-weight:700;z-index:3;border:1px solid #fff;}
.fpl-bench{max-width:780px;margin:0 auto;background:rgba(255,255,255,.07);border:1px solid #4A2266;border-radius:14px;
padding:10px 6px 12px;}
.fpl-bench-title{text-align:center;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:1.15rem;margin-bottom:6px;}
.fpl-bench .fpl-row{margin:0;}
.fpl-slot{font-size:.7rem;font-weight:600;opacity:.75;margin-bottom:2px;}
</style>"""


def upcoming_fixture_labels(fixtures: list[dict], event: int, team_short: dict[int, str]) -> dict[int, str]:
    """team id -> 'OPP (H)' for teams whose fixtures in `event` haven't kicked off yet, so the pitch can
    show who a player faces instead of a meaningless 0 points."""
    games: dict[int, list[dict]] = {}
    for f in fixtures:
        if f["event"] != event:
            continue
        games.setdefault(f["team_h"], []).append({"opp": f["team_a"], "home": True, "started": bool(f["started"])})
        games.setdefault(f["team_a"], []).append({"opp": f["team_h"], "home": False, "started": bool(f["started"])})
    labels = {}
    for team_id, gs in games.items():
        if gs and not any(g["started"] for g in gs):
            labels[team_id] = " + ".join(f"{team_short.get(g['opp'], '?')} ({'H' if g['home'] else 'A'})" for g in gs)
    return labels


def _card(
    row: pd.Series, team_codes: dict[int, int], fixture_labels: dict[int, str], slot_label: str = "", tooltip: str = ""
) -> str:
    shirt = shirt_url(team_codes.get(int(row["team"]), 0), row["position"] == "GKP")
    badge = "C" if row["is_captain"] else "V" if row["is_vice_captain"] else ""
    label = fixture_labels.get(int(row["team"]))
    if label:
        pts_html = f'<div class="fpl-pts fix">{html.escape(label)}</div>'
    else:
        pts = int(row["event_points"])
        cls = "low" if pts <= 1 else "high" if pts >= 8 else ""
        pts_html = f'<div class="fpl-pts {cls}">{pts}</div>'
    slot_html = f'<div class="fpl-slot">{html.escape(slot_label)}</div>' if slot_label else ""
    # Newlines as entities: a raw newline inside the HTML ends Streamlit's markdown HTML block mid-card.
    title = f' title="{html.escape(tooltip).replace(chr(10), "&#10;")}"' if tooltip else ""
    return (
        f'<div class="fpl-p"{title}>{slot_html}'
        f'{f"<span class=fpl-badge>{badge}</span>" if badge else ""}'
        f'<img src="{shirt}" alt="">'
        f'<div class="fpl-name">{html.escape(row["web_name"])}</div>{pts_html}</div>'
    )


def render_pitch(
    picks: pd.DataFrame,
    team_codes: dict[int, int],
    fixture_labels: dict[int, str],
    tooltips: dict[int, str] | None = None,
) -> str:
    """picks needs: slot, position (GKP/DEF/MID/FWD), team (id), web_name, event_points, is_captain,
    is_vice_captain. Slots 1-11 are starters, 12-15 the bench (omitted if there are no bench players).
    `tooltips` (player id -> text, needs an `id` column) are shown on hover."""
    tooltips = tooltips or {}
    tip = lambda r: tooltips.get(int(r["id"]), "") if "id" in r else ""  # noqa: E731
    picks = picks.sort_values("slot")
    starters, bench = picks[picks["slot"] <= 11], picks[picks["slot"] > 11]

    rows = []
    for pos in POSITION_ROWS:
        cards = "".join(
            _card(r, team_codes, fixture_labels, tooltip=tip(r))
            for _, r in starters[starters["position"] == pos].iterrows()
        )
        rows.append(f'<div class="fpl-row">{cards}</div>')

    bench_html = ""
    if not bench.empty:
        bench_cards = "".join(
            _card(r, team_codes, fixture_labels, "GKP" if r["position"] == "GKP" else f"{i}. {r['position']}", tip(r))
            for i, (_, r) in enumerate(bench.iterrows())
        )
        bench_html = (
            f'<div class="fpl-bench"><div class="fpl-bench-title">Substitutes</div><div class="fpl-row">{bench_cards}</div></div>'
        )
    return f'{_CSS}<div class="fpl-pitch">{"".join(rows)}</div>{bench_html}'
