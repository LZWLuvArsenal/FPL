"""Pill bar of in-page links to a page's sections, pinned under the app header while scrolling.

Create a SectionNav right after st.title, then use nav.section(...) in place of st.subheader. Each section
adds its pill as it renders, so sections a page skips (no data yet, no Team ID) never show up in the bar."""
from __future__ import annotations

import html
import re

import streamlit as st

_CSS = """<style>
div[data-testid="stElementContainer"]:has(.fpl-secnav){position:sticky;top:3.75rem;z-index:99;}
.fpl-secnav{display:flex;gap:8px;overflow-x:auto;padding:8px 2px 10px;background:#170022;
border-bottom:1px solid #4A2266;scrollbar-width:thin;}
.fpl-secnav a{flex:none;white-space:nowrap;text-decoration:none !important;color:#F5F0F8 !important;
background:rgba(255,255,255,0.06);border:1px solid #4A2266;border-radius:999px;padding:5px 14px;
font-size:0.88rem;font-weight:600;}
.fpl-secnav a:hover{background:#00FF87;color:#14001c !important;border-color:#00FF87;}
h3[id]{scroll-margin-top:7.5rem;}
</style>"""


class SectionNav:
    def __init__(self):
        self._slot = st.empty()
        self._items: list[tuple[str, str]] = []

    def section(self, title: str, label: str | None = None) -> None:
        """Renders `title` as a subheader and adds a pill for it (`label` overrides the pill text)."""
        label = label or title
        anchor = "sec-" + (re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or str(len(self._items)))
        st.subheader(title, anchor=anchor)
        self._items.append((label, anchor))
        pills = "".join(f'<a href="#{a}">{html.escape(text)}</a>' for text, a in self._items)
        self._slot.markdown(f'{_CSS}<div class="fpl-secnav">{pills}</div>', unsafe_allow_html=True)
