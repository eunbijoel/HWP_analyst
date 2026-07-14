"""검토 모드 — 숫자 이슈를 사람 말로 보여주는 홈."""

from __future__ import annotations

import hashlib
from typing import Any

import streamlit as st

from ui.brand import kpi_row
from ui.issue_panel import queue_issue_chat, jump_to_issue

DISMISSED_KEY = "review_dismissed_issues"


def _issue_key(fname: str, issue: Any, idx: int) -> str:
  raw = f"{fname}|{getattr(issue, 'issue_type', '')}|{getattr(issue, 'message', '')}|{getattr(issue, 'source', '')}|{idx}"
  return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _dismissed() -> set[str]:
  raw = st.session_state.get(DISMISSED_KEY) or []
  return set(raw)


def _dismiss(key: str) -> None:
  cur = list(st.session_state.get(DISMISSED_KEY) or [])
  if key not in cur:
    cur.append(key)
  st.session_state[DISMISSED_KEY] = cur


def _collect_issues(file_entries: list[dict]) -> list[tuple[str, Any, int, str]]:
  rows = []
  for entry in file_entries:
    fname = entry["filename"]
    intel = entry["doc_payload"].get("intel")
    if not intel or not intel.issues:
      continue
    for idx, issue in enumerate(intel.issues):
      if getattr(issue, "severity", "") == "info" and getattr(issue, "issue_type", "") == "match":
        continue
      rows.append((fname, issue, idx, _issue_key(fname, issue, idx)))
  return rows


def _friendly_title(issue: Any) -> str:
  """디버거식 메시지를 조금 짧게."""
  msg = (getattr(issue, "message", "") or "").strip()
  # 이미 한글 문장이면 그대로
  return msg or "확인해 볼 숫자 차이가 있습니다"


def render_review_home(
  file_entries: list[dict],
  active_documents: list,
  *,
  render_workspace,  # callable for doc+chat workspace
) -> None:
  all_issues = _collect_issues(file_entries)
  dismissed = _dismissed()
  issues = [row for row in all_issues if row[3] not in dismissed]
  n_hidden = len(all_issues) - len(issues)

  n_warn = sum(1 for _, i, _, _ in issues if getattr(i, "severity", "") == "warning")
  n_err = sum(1 for _, i, _, _ in issues if getattr(i, "severity", "") == "error")

  kpi_row([
    (str(len(file_entries)), "열린 문서"),
    (str(len(issues)), "아직 볼 항목"),
    (str(n_err + n_warn), "주의 · 경고"),
  ])

  if n_hidden:
    c1, c2 = st.columns([3, 1])
    with c1:
      st.caption(f"가려 둔 항목 {n_hidden}개")
    with c2:
      if st.button("가린 항목 다시 보기", use_container_width=True, key="rev_undismiss_all"):
        st.session_state[DISMISSED_KEY] = []
        st.rerun()

  if not issues:
    if all_issues and n_hidden:
      st.success("지금은 보여줄 항목이 없습니다. (일부를 가려 두었습니다)")
    else:
      st.success("눈에 띄는 숫자 차이는 아직 없습니다.")
  else:
    st.markdown("##### 확인해 주세요")

    for fname, issue, idx, ikey in issues[:12]:
      sev = getattr(issue, "severity", "warning")
      tag = "경고" if sev == "warning" else ("오류" if sev == "error" else "참고")
      msg = _friendly_title(issue)
      src = getattr(issue, "source", "") or ""
      with st.container(border=True):
        head, dismiss_col = st.columns([8, 1])
        with head:
          st.markdown(f"**{msg}**")
          st.caption(f"{fname}" + (f" · {src}" if src else "") + f" · {tag}")
        with dismiss_col:
          if st.button("✕", key=f"rev_x_{ikey}", help="무시", use_container_width=True):
            _dismiss(ikey)
            st.rerun()
        a, b, c = st.columns(3)
        with a:
          if st.button("문서로 이동", key=f"rev_jump_{ikey}", use_container_width=True):
            jump_to_issue(fname, issue)
            st.rerun()
        with b:
          if st.button("AI에게 물어보기", key=f"rev_ask_{ikey}", use_container_width=True):
            queue_issue_chat(fname, issue)
            st.rerun()
        with c:
          if st.button("무시", key=f"rev_ignore_{ikey}", use_container_width=True):
            _dismiss(ikey)
            st.rerun()

  st.markdown("---")
  st.markdown("##### 문서와 대화")
  render_workspace()
