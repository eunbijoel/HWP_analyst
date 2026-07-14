"""상용 AI 제품 톤의 Streamlit 테마 · 셸 헬퍼."""

from __future__ import annotations

import streamlit as st

PRODUCT_NAME = "HWP Analyst"
PRODUCT_TAGLINE = ""


APP_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');

:root {
  --bg: #f6f5f2;
  --surface: #ffffff;
  --ink: #1a1a1a;
  --muted: #6b6b6b;
  --line: #e8e6e1;
  --accent: #1f4b99;
  --accent-soft: #e8eef8;
  --ok: #1b7a4e;
  --ok-soft: #e7f5ee;
  --warn: #9a6700;
  --warn-soft: #fff6e0;
  --danger: #b42318;
  --radius: 16px;
  --shadow: 0 1px 2px rgba(26,26,26,.04), 0 8px 24px rgba(26,26,26,.06);
}

html, body, [class*="css"] {
  font-family: "IBM Plex Sans KR", "IBM Plex Sans", system-ui, sans-serif !important;
  color: var(--ink);
}

.stApp {
  background:
    radial-gradient(1200px 500px at 10% -10%, #e8eef8 0%, transparent 55%),
    radial-gradient(900px 400px at 100% 0%, #f0ebe3 0%, transparent 50%),
    var(--bg) !important;
}

/* Hide Streamlit chrome noise */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header[data-testid="stHeader"] {background: transparent;}
[data-testid="stToolbar"] {display: none;}

.block-container {
  padding-top: 1.4rem !important;
  padding-bottom: 4rem !important;
  max-width: 1180px !important;
}

[data-testid="stSidebar"] {
  background: #faf9f7 !important;
  border-right: 1px solid var(--line);
}
[data-testid="stSidebar"] .block-container {
  padding-top: 1.5rem !important;
}

.hx-hero {
  padding: 2.2rem 2rem 1.6rem;
  border-radius: 24px;
  background: linear-gradient(145deg, #ffffff 0%, #f3f6fb 100%);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  margin-bottom: 1.25rem;
}
.hx-hero h1 {
  font-size: 2rem;
  font-weight: 700;
  letter-spacing: -0.03em;
  margin: 0 0 .35rem 0;
  color: var(--ink);
}
.hx-hero p {
  margin: 0;
  color: var(--muted);
  font-size: 1.05rem;
  line-height: 1.55;
  max-width: 40rem;
}

.hx-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 1.25rem 1.35rem;
  box-shadow: var(--shadow);
  margin-bottom: 1rem;
}
.hx-card h3 {
  margin: 0 0 .35rem 0;
  font-size: 1.05rem;
  font-weight: 600;
}
.hx-muted { color: var(--muted); font-size: .92rem; line-height: 1.5; }

.hx-kpi-row {
  display: flex; gap: .75rem; flex-wrap: wrap; margin: 1rem 0 1.25rem;
}
.hx-kpi {
  flex: 1; min-width: 140px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 1rem 1.1rem;
  box-shadow: var(--shadow);
}
.hx-kpi .n {
  font-size: 1.75rem; font-weight: 700; letter-spacing: -0.03em; line-height: 1.1;
}
.hx-kpi .l { color: var(--muted); font-size: .85rem; margin-top: .25rem; }

.hx-step {
  display: flex; align-items: center; gap: .65rem;
  padding: .55rem 0; color: var(--muted); font-size: .95rem;
}
.hx-step.on { color: var(--accent); font-weight: 600; }
.hx-step.done { color: var(--ok); }
.hx-dot {
  width: 10px; height: 10px; border-radius: 50%;
  background: #d0cec8; flex-shrink: 0;
}
.hx-step.on .hx-dot { background: var(--accent); box-shadow: 0 0 0 4px var(--accent-soft); }
.hx-step.done .hx-dot { background: var(--ok); }

.hx-badge {
  display: inline-block;
  padding: .2rem .55rem;
  border-radius: 999px;
  font-size: .78rem;
  font-weight: 600;
  letter-spacing: .01em;
}
.hx-badge.ready { background: var(--ok-soft); color: var(--ok); }
.hx-badge.review { background: var(--warn-soft); color: var(--warn); }
.hx-badge.ref { background: var(--accent-soft); color: var(--accent); }
.hx-badge.skip { background: #f0eeea; color: #666; }

.hx-diff {
  display: grid; grid-template-columns: 1fr 1fr; gap: .75rem; margin-top: .75rem;
}
.hx-diff .box {
  border-radius: 12px; padding: .85rem 1rem; min-height: 4.5rem;
  font-size: .92rem; line-height: 1.55; white-space: pre-wrap;
}
.hx-diff .before { background: #f7f6f3; border: 1px solid var(--line); color: #444; }
.hx-diff .after { background: var(--ok-soft); border: 1px solid #c8e6d4; color: #143528; }

div[data-testid="stFileUploader"] section {
  border: 1.5px dashed #c9c5bc !important;
  border-radius: 16px !important;
  background: #fffcf8 !important;
  padding: 1rem !important;
}

.stButton > button {
  border-radius: 12px !important;
  font-weight: 600 !important;
  padding: .55rem 1rem !important;
  border: 1px solid var(--line) !important;
}
.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background: var(--accent) !important;
  color: white !important;
  border-color: var(--accent) !important;
}

[data-testid="stExpander"] {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
}

.hx-next {
  margin-top: 1rem;
  padding: .9rem 1.1rem;
  border-radius: 14px;
  background: var(--accent-soft);
  border: 1px solid #c9d7ef;
  color: #1a3470;
  font-weight: 500;
}
</style>
"""


def inject_theme() -> None:
  st.markdown(APP_CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str = "") -> None:
  sub = f"<p>{subtitle}</p>" if (subtitle or "").strip() else ""
  st.markdown(
    f'<div class="hx-hero"><h1>{title}</h1>{sub}</div>',
    unsafe_allow_html=True,
  )


def kpi_row(items: list[tuple[str, str]]) -> None:
  """items: [(number, label), ...]"""
  cells = "".join(
    f'<div class="hx-kpi"><div class="n">{n}</div><div class="l">{lab}</div></div>'
    for n, lab in items
  )
  st.markdown(f'<div class="hx-kpi-row">{cells}</div>', unsafe_allow_html=True)


def progress_steps(active: str) -> None:
  """active: idle|analyzing|ready|done"""
  steps = [
    ("analyzing", "문서를 읽는 중"),
    ("ready", "제안 준비 완료"),
    ("done", "반영 · 내보내기"),
  ]
  order = {"idle": -1, "analyzing": 0, "ready": 1, "done": 2}
  idx = order.get(active, -1)
  html = []
  for i, (key, label) in enumerate(steps):
    cls = "hx-step"
    if i < idx:
      cls += " done"
    elif i == idx:
      cls += " on"
    html.append(f'<div class="{cls}"><span class="hx-dot"></span>{label}</div>')
  st.markdown("".join(html), unsafe_allow_html=True)


def badge(text: str, kind: str = "ref") -> str:
  return f'<span class="hx-badge {kind}">{text}</span>'


def next_hint(text: str) -> None:
  st.markdown(f'<div class="hx-next">{text}</div>', unsafe_allow_html=True)
