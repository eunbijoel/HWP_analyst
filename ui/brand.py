"""Product A theme — B visual tokens only (no workflow redesign)."""

from __future__ import annotations

import base64
from pathlib import Path

import streamlit as st

PRODUCT_NAME = "HWP Document Intelligence (A)"
PRODUCT_TAGLINE = "문서 이해 · 검토 · 질문"
LOGO_PATH = Path(__file__).resolve().parent / "logo.png"

_logo_data_uri_cache: str | None = None


def _logo_data_uri() -> str:
  global _logo_data_uri_cache
  if _logo_data_uri_cache is None:
    if LOGO_PATH.is_file():
      encoded = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
      _logo_data_uri_cache = f"data:image/png;base64,{encoded}"
    else:
      _logo_data_uri_cache = ""
  return _logo_data_uri_cache


# Visual tokens aligned with HWP_v2/static/css/app.css — layout/UX stays Streamlit A.
APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,0,0&display=swap');

:root {
  --bg: #e8eaef;
  --paper: #ffffff;
  --ink: #1a1a1a;
  --muted: #6a6f7a;
  --line: #d5d8e0;
  --accent: #1f4b99;
  --accent-soft: #eef3fb;
  --ok: #1b7a4e;
  --ok-soft: #e7f5ee;
  --warn: #9a6700;
  --warn-soft: #fff6e0;
  --danger: #b42318;
  --radius: 10px;
  --shadow: 0 1px 2px rgba(0,0,0,.05), 0 12px 32px rgba(20,30,50,.08);
}

html, body, .stApp {
  font-family: "IBM Plex Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif;
  color: var(--ink);
  background: var(--bg) !important;
}
.stMarkdown, .stMarkdown p, .stCaption, label,
[data-testid="stWidgetLabel"],
[data-testid="stChatMessageContent"],
.stButton > button {
  font-family: "IBM Plex Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif !important;
}

span[data-testid="stIconMaterial"],
.material-symbols-rounded {
  font-family: "Material Symbols Rounded" !important;
  font-weight: 400 !important;
  font-style: normal !important;
  font-variation-settings: "FILL" 0, "wght" 400, "GRAD" 0, "opsz" 24 !important;
  letter-spacing: normal !important;
  text-transform: none !important;
  display: inline-block !important;
  line-height: 1 !important;
}

#MainMenu, footer { visibility: hidden; }
.stAppDeployButton, [data-testid="stToolbar"],
div[data-testid="stStatusWidget"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent !important; }

[data-testid="stAppViewContainer"],
[data-testid="stMain"],
section.main,
[data-testid="stMainBlockContainer"] {
  background: var(--bg) !important;
}

section.main .block-container,
[data-testid="stMainBlockContainer"] {
  padding-top: 0.75rem !important;
  padding-bottom: 2rem !important;
  max-width: 100% !important;
}

/* Top brand bar (B look, no mode switch) */
.hx-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: .55rem 1rem;
  background: #f7f8fa;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  margin: 0 0 .85rem 0;
}
.hx-brand { display: flex; gap: .7rem; align-items: center; }
.hx-brand-mark {
  width: 2rem; height: 2rem; border-radius: 8px;
  background: var(--accent); color: #fff;
  display: grid; place-items: center; font-weight: 800; font-size: .72rem;
}
.hx-brand strong { display: block; font-size: .92rem; line-height: 1.2; }
.hx-brand small { color: var(--muted); font-size: .75rem; }

/* Sidebar — B docs-pane colors; keep Streamlit widgets */
[data-testid="stSidebar"] {
  background: #f3f4f7 !important;
  border-right: 1px solid var(--line);
}
[data-testid="stSidebar"] > div:first-child { background: #f3f4f7 !important; }
[data-testid="stSidebar"] .block-container {
  padding-top: .75rem !important;
}
[data-testid="stSidebar"] .stCheckbox {
  background: #fff;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: .35rem .5rem;
  margin-bottom: .3rem;
}
[data-testid="stSidebar"] .stCheckbox:hover { border-color: #c5cddc; }
[data-testid="stSidebar"] .stExpander {
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #fff;
}
[data-testid="stExpander"] summary [data-testid="stIconMaterial"] {
  /* keep expand icon working */
  font-family: "Material Symbols Rounded" !important;
}

div[data-testid="stFileUploaderDropzone"] {
  border: 1px dashed var(--line) !important;
  border-radius: 10px !important;
  background: #fff !important;
}
div[data-testid="stFileUploaderDropzone"] [data-testid="stIconMaterial"] {
  font-family: "Material Symbols Rounded" !important;
}

.stButton > button {
  border-radius: 10px !important;
  font-weight: 600 !important;
  border: 1px solid var(--line) !important;
}
.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background: var(--accent) !important;
  color: #fff !important;
  border-color: var(--accent) !important;
}

.hx-kpi-row {
  display: flex; gap: .45rem; flex-wrap: wrap; margin: .5rem 0 .75rem;
}
.hx-kpi {
  flex: 1; min-width: 100px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: .65rem .75rem;
}
.hx-kpi .n { font-size: 1.35rem; font-weight: 700; line-height: 1.1; }
.hx-kpi .l { color: var(--muted); font-size: .75rem; margin-top: .2rem; }

.hx-badge {
  display: inline-block;
  padding: .12rem .5rem;
  border-radius: 999px;
  font-size: .72rem;
  font-weight: 700;
}
.hx-badge.ready { background: var(--ok-soft); color: var(--ok); }
.hx-badge.review { background: var(--warn-soft); color: var(--warn); }
.hx-badge.ref { background: var(--accent-soft); color: var(--accent); }
.hx-badge.skip { background: #f0f2f6; color: #666; }

.hx-next {
  margin-top: .65rem;
  padding: .65rem .85rem;
  border-radius: 10px;
  background: var(--accent-soft);
  border: 1px solid #c9d7ef;
  color: #1a3470;
  font-weight: 500;
  font-size: .88rem;
}

.hx-hero { display: none !important; }
</style>
"""


def inject_theme() -> None:
  st.markdown(APP_CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str = "") -> None:
  """B-like top brand bar (no Understand/Modify)."""
  sub = subtitle or PRODUCT_TAGLINE
  st.markdown(
    f"""
<div class="hx-topbar">
  <div class="hx-brand">
    <span class="hx-brand-mark">HWP</span>
    <div>
      <strong>{title or PRODUCT_NAME}</strong>
      <small>{sub}</small>
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
  )


def sidebar_brand(*, caption: str = "로컬 처리 · 원본 보존") -> None:
  uri = _logo_data_uri()
  if uri:
    st.markdown(
      f'<img src="{uri}" alt="" style="height:28px;margin-bottom:.35rem;" />',
      unsafe_allow_html=True,
    )
  st.markdown(f"**{PRODUCT_NAME}**")
  if caption:
    st.caption(caption)


def kpi_row(items: list[tuple[str, str]]) -> None:
  cells = "".join(
    f'<div class="hx-kpi"><div class="n">{n}</div><div class="l">{lab}</div></div>'
    for n, lab in items
  )
  st.markdown(f'<div class="hx-kpi-row">{cells}</div>', unsafe_allow_html=True)


def progress_steps(active: str) -> None:
  return None


def badge(text: str, kind: str = "ref") -> str:
  return f'<span class="hx-badge {kind}">{text}</span>'


def next_hint(text: str) -> None:
  st.markdown(f'<div class="hx-next">{text}</div>', unsafe_allow_html=True)
