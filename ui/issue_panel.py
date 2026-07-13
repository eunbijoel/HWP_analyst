"""검토 이슈 카드 — 위치로 이동 / 채팅으로 설명."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

import streamlit as st

from ui.document_workspace import VIEW_DIRECT

ISSUE_JUMP_KEY = "issue_jump"
PENDING_CHAT_KEY = "pending_issue_chat"
FOCUS_DOC_KEY = "focus_doc"


def _issue_uid(filename: str, issue: Any, idx: int) -> str:
  raw = f"{filename}|{issue.issue_type}|{issue.message}|{issue.source}|{idx}"
  return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def resolve_issue_location(issue: Any) -> tuple[Optional[int], Optional[int]]:
  """(table_index 0-based, row_index 0-based). 필드 없으면 source 문자열에서 파싱."""
  t = getattr(issue, "table_index", None)
  r = getattr(issue, "row_index", None)
  if t is not None:
    return t, r
  src = getattr(issue, "source", "") or ""
  m = re.search(r"표\s*(\d+)", src)
  table_index = int(m.group(1)) - 1 if m else None
  row_index = None
  m2 = re.search(r"(\d+)\s*행", src)
  if m2:
    row_index = int(m2.group(1)) - 1
  elif "합계행" in src:
    row_index = None
  return table_index, row_index


def build_explain_question(filename: str, issue: Any) -> str:
  loc = issue.source or "(위치 미상)"
  return (
    f"다음 검토 이슈를 설명해 주세요. "
    f"숫자는 고치지 말고, 왜 발생했는지와 표에서 확인할 칸·수정 시 체크리스트만 알려 주세요.\n\n"
    f"파일: {filename}\n"
    f"이슈: {issue.message}\n"
    f"위치: {loc}"
  )


def jump_to_issue(filename: str, issue: Any) -> None:
  table_index, row_index = resolve_issue_location(issue)
  st.session_state[FOCUS_DOC_KEY] = filename
  st.session_state["active_file_chat_target"] = filename
  st.session_state[ISSUE_JUMP_KEY] = {
    "filename": filename,
    "table_index": table_index,
    "row_index": row_index,
    "source": getattr(issue, "source", "") or "",
    "message": getattr(issue, "message", "") or "",
  }
  # Excel은 표 행을 보기 위해 직접 편집 모드로
  view_key = f"doc_view_xlsx_{filename}"
  st.session_state[view_key] = VIEW_DIRECT
  # 일반 미리보기 파일도 직접 편집 쪽으로
  gen_key = f"doc_view_gen_{filename}"
  if gen_key in st.session_state or True:
    st.session_state[gen_key] = VIEW_DIRECT


def queue_issue_chat(filename: str, issue: Any) -> None:
  jump_to_issue(filename, issue)  # 설명 전에도 위치 보이게
  st.session_state[PENDING_CHAT_KEY] = {
    "filename": filename,
    "question": build_explain_question(filename, issue),
  }


def pop_pending_chat(filename: str) -> Optional[str]:
  pending = st.session_state.get(PENDING_CHAT_KEY)
  if not pending or pending.get("filename") != filename:
    return None
  del st.session_state[PENDING_CHAT_KEY]
  return pending.get("question") or None


def get_jump_for(filename: str) -> Optional[dict]:
  jump = st.session_state.get(ISSUE_JUMP_KEY)
  if not jump or jump.get("filename") != filename:
    return None
  return jump


def clear_jump_if(filename: str) -> None:
  jump = st.session_state.get(ISSUE_JUMP_KEY)
  if jump and jump.get("filename") == filename:
    # 한 번 보여준 뒤에도 하이라이트 유지하려면 지우지 않음.
    # 다른 이슈로 점프하면 덮어씀.
    pass


def render_issue_alerts(file_entries: list[dict], max_per_file: int = 5) -> None:
  """파일별 이슈 카드: [이 위치로] [채팅으로 설명]."""
  any_issue = False
  for entry in file_entries:
    fname = entry["filename"]
    intel = entry["doc_payload"].get("intel")
    if not intel or not intel.issues:
      continue
    for idx, issue in enumerate(intel.issues[:max_per_file]):
      any_issue = True
      uid = _issue_uid(fname, issue, idx)
      with st.container(border=True):
        st.markdown(f"**{fname}** — {issue.message}")
        if issue.source:
          st.caption(f"📍 {issue.source}")
        c1, c2 = st.columns(2)
        with c1:
          if st.button("이 위치로", key=f"jump_{uid}", use_container_width=True):
            jump_to_issue(fname, issue)
            st.rerun()
        with c2:
          if st.button("채팅으로 설명", key=f"chat_{uid}", use_container_width=True):
            queue_issue_chat(fname, issue)
            st.rerun()
  if not any_issue:
    return
