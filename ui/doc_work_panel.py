"""문서 채우기 — 채팅 자연어로만 실행. 별도 모드/버튼 UI 없음."""

from __future__ import annotations

from typing import Optional

import streamlit as st

from hwp_core.doc_agent.pipeline import DocFillPipeline
from ui.brand import badge


SESSION_KEY = "doc_fill_pipeline"
STATE_KEY = "doc_fill_ux"


def _pipeline() -> DocFillPipeline:
  if SESSION_KEY not in st.session_state:
    st.session_state[SESSION_KEY] = DocFillPipeline()
  return st.session_state[SESSION_KEY]


def _empty() -> dict:
  return {
    "phase": "idle",  # idle | ready | done
    "target_name": "",
    "ref_names": [],
    "command": "",
    "fields": [],
    "proposals": [],
    "decisions": {},
    "verify": None,
    "edited_bytes": None,
    "export_name": "",
    "error": "",
    "notice": "",
  }


def _state() -> dict:
  if STATE_KEY not in st.session_state:
    st.session_state[STATE_KEY] = _empty()
  return st.session_state[STATE_KEY]


def _status_for(p: dict, decisions: dict) -> tuple[str, str]:
  pid = p["proposal_id"]
  d = decisions.get(pid)
  conf = float(p.get("confidence") or 0)
  if d == "skip":
    return "skip", "건너뜀"
  if d == "apply":
    return "ready", "적용 예정"
  if not (p.get("sources") or []):
    return "review", "근거 부족"
  if conf < 0.6:
    return "review", "검토 필요"
  return "ready", "준비됨"


def _escape(s: str) -> str:
  return (
    (s or "")
    .replace("&", "&amp;")
    .replace("<", "&lt;")
    .replace(">", "&gt;")
  )


def _proposal_html(p: dict, after_fallback: str) -> str:
  if p.get("action") == "insert_table":
    rows = (p.get("meta") or {}).get("table_rows") or []
    if rows:
      html = ['<table style="border-collapse:collapse;width:100%;font-size:12px">']
      for i, row in enumerate(rows[:12]):
        html.append("<tr>")
        tag = "th" if i == 0 else "td"
        for cell in row:
          html.append(
            f'<{tag} style="border:1px solid #ccc;padding:4px 6px;'
            f'{"background:#f5f5f5;" if i==0 else ""}">'
            f"{_escape(str(cell))}</{tag}>"
          )
        html.append("</tr>")
      html.append("</table>")
      if len(rows) > 12:
        html.append(f"<div>… (+{len(rows)-12}행)</div>")
      return "".join(html)
  return _escape(after_fallback).replace("\n", "<br/>")


def _is_doc_target(name: str) -> bool:
  n = (name or "").lower()
  return n.endswith(".hwpx") or n.endswith(".hwp")


def pick_fill_files(
  file_entries: list[dict],
  preferred_target: str = "",
) -> tuple[Optional[str], list[str], str]:
  """채울 문서·참고 자료 자동 선택. (target, refs, error)"""
  if not file_entries:
    return None, [], "문서를 먼저 업로드해 주세요."
  by_name = {e["filename"]: e for e in file_entries}
  names = list(by_name)
  docs = [n for n in names if _is_doc_target(n)]
  if not docs:
    return None, [], "채울 대상은 HWP/HWPX 파일이 필요합니다."

  target = preferred_target if preferred_target in docs else docs[0]
  refs = [n for n in names if n != target]
  if not refs:
    return target, [], (
      "참고할 자료(엑셀·다른 문서)를 하나 더 열어 주세요. "
      "채팅에서 「참고 자료로 채워줘」처럼 말씀하시면 됩니다."
    )
  return target, refs, ""


def run_doc_fill_from_chat(
  file_entries: list[dict],
  *,
  command: str,
  preferred_target: str = "",
  model_name: str = "gemma4",
  ollama_url: str = "http://localhost:11434",
  use_llm: bool = False,
) -> str:
  """inspect→propose 실행 후 session 상태 갱신. 채팅용 한 줄 답변 반환."""
  state = _state()
  pipe = _pipeline()
  by_name = {e["filename"]: e for e in file_entries}

  target_name, ref_names, pick_err = pick_fill_files(file_entries, preferred_target)
  if pick_err and (not target_name or not ref_names):
    state.update(_empty())
    state["error"] = pick_err
    return pick_err

  assert target_name and ref_names
  cmd = (command or "").strip() or "참고 자료 내용을 문서에 반영해 주세요"

  try:
    pipe.reset()
    r = pipe.register_target(by_name[target_name]["filename"], by_name[target_name]["file_bytes"])
    err = r.get("error") or ""
    if err and ("HWPX" in err or "변환" in err or "불가" in err):
      state.update(_empty())
      state["error"] = err
      return err
    for rn in ref_names:
      e = by_name[rn]
      pipe.register_reference(e["filename"], e["file_bytes"])

    insp = pipe.run_inspect()
    data = insp.get("data") or {}
    if not insp.get("ok"):
      msg = insp.get("error") or data.get("error") or "문서를 읽지 못했습니다."
      state.update(_empty())
      state["error"] = msg
      return msg

    out = pipe.run_propose(
      cmd, use_llm=use_llm, model=model_name, ollama_url=ollama_url,
    )
    if not out.get("ok"):
      msg = out.get("error") or "초안을 만들지 못했습니다."
      state.update(_empty())
      state["error"] = msg
      return msg

    fields = data.get("fields") or pipe.tools.last_fields or []
    proposals = (out.get("data") or {}).get("proposals") or []
    state["phase"] = "ready"
    state["target_name"] = target_name
    state["ref_names"] = ref_names
    state["command"] = cmd
    state["fields"] = fields
    state["proposals"] = proposals
    state["decisions"] = {p["proposal_id"]: "apply" for p in proposals}
    state["edited_bytes"] = None
    state["verify"] = None
    state["error"] = ""
    state["export_name"] = ""

    if not proposals:
      state["notice"] = (
        f"문서「{target_name}」와 참고 자료({', '.join(ref_names)})를 봤지만 "
        "반영할 초안을 찾지 못했습니다. 문서에 비어 있는 항목이 있는지, "
        "참고 자료가 맞는지 확인해 주세요."
      )
      return state["notice"]

    state["notice"] = ""
    n_table = sum(1 for p in proposals if p.get("action") == "insert_table")
    n_cell = sum(1 for p in proposals if p.get("action") == "write_table_cell")
    extra = ""
    for p in proposals:
      um = (p.get("meta") or {}).get("unmatched_form_labels") or []
      if um:
        extra = (
          f" 서식 빈칸({', '.join(um[:5])}{(', …' if len(um)>5 else '')})은 "
          "참고자료에 같은 항목이 없어 비워 두었습니다."
        )
        break
    kind = []
    if n_table:
      kind.append("표 삽입")
    if n_cell:
      kind.append(f"칸 채우기 {n_cell}")
    kind_s = " · ".join(kind) if kind else "제안"
    return (
      f"「{target_name}」에 대해 {len(proposals)}건({kind_s})을 만들었습니다. "
      f"아래 카드를 확인한 뒤 적용해 주세요. (참고: {', '.join(ref_names)})"
      f"{extra}"
    )
  except Exception as e:
    state.update(_empty())
    state["error"] = str(e)
    return f"처리 중 오류: {e}"


def _resync_pipe(by_name: dict, state: dict) -> Optional[str]:
  pipe = _pipeline()
  pipe.reset()
  target = state.get("target_name") or ""
  if target not in by_name:
    return "대상 문서를 찾을 수 없습니다."
  r = pipe.register_target(by_name[target]["filename"], by_name[target]["file_bytes"])
  err = r.get("error") or ""
  if err and ("HWPX" in err or "변환" in err or "불가" in err):
    return err
  for rn in state.get("ref_names") or []:
    if rn in by_name:
      e = by_name[rn]
      pipe.register_reference(e["filename"], e["file_bytes"])
  return None


def render_doc_fill_chat_result(
  file_entries: list[dict],
  *,
  on_applied=None,
  key_prefix: str = "dw",
) -> None:
  """채팅 아래 — 제안 카드 / 반영 / 다운로드 (파이프라인 KPI 숨김)."""
  state = _state()
  if state.get("error") and state["phase"] == "idle" and not state.get("proposals"):
    # 방금 실패 메시지는 채팅 본문에 이미 있음
    return
  if state["phase"] not in ("ready", "done"):
    return
  if not state.get("proposals") and not state.get("edited_bytes"):
    return

  kp = key_prefix or "dw"
  by_name = {e["filename"]: e for e in file_entries}
  pipe = _pipeline()
  decisions = state["decisions"]

  if state["proposals"] and state["phase"] == "ready":
    st.markdown("##### 제안")
    for p in state["proposals"]:
      pid = p["proposal_id"]
      title = p.get("label") or p.get("location") or "항목"
      kind, label = _status_for(p, decisions)
      before = (p.get("before") or "").strip() or "(비어 있음)"
      after = (p.get("after") or "").strip() or "(내용 없음)"
      srcs = p.get("sources") or []

      with st.container(border=True):
        top = st.columns([4, 1.2, 1.2])
        with top[0]:
          st.markdown(
            f"**{title}** &nbsp; {badge(label, kind)}",
            unsafe_allow_html=True,
          )
          if srcs:
            st.caption(
              "근거 · " + " · ".join(f"{s.get('document', '')}" for s in srcs[:2])
            )
        with top[1]:
          if st.button("적용", key=f"{kp}_ap_{pid}", use_container_width=True):
            decisions[pid] = "apply"
            st.rerun()
        with top[2]:
          if st.button("건너뛰기", key=f"{kp}_sk_{pid}", use_container_width=True):
            decisions[pid] = "skip"
            st.rerun()

        st.markdown(
          f"""
          <div class="hx-diff">
            <div class="box before"><b>지금</b><br/>{before}</div>
            <div class="box after"><b>제안</b><br/>{_proposal_html(p, after)}</div>
          </div>
          """,
          unsafe_allow_html=True,
        )

    approved = [pid for pid, d in decisions.items() if d == "apply"]
    st.caption(
      f"적용 예정 {len(approved)} · 건너뜀 "
      f"{sum(1 for d in decisions.values() if d == 'skip')}"
    )

    if st.button("선택한 내용 반영", type="primary", use_container_width=True, key=f"{kp}_apply"):
      if not approved:
        st.warning("적용할 항목이 없습니다.")
      else:
        try:
          with st.status("반영 중…", expanded=False) as status:
            err = _resync_pipe(by_name, state)
            if err:
              st.error(err)
              status.update(label="실패", state="error")
            else:
              for p in state["proposals"]:
                p["status"] = (
                  "approved" if decisions.get(p["proposal_id"]) == "apply" else "rejected"
                )
              pipe.tools.last_fields = state["fields"]
              pipe.tools.last_proposals = state["proposals"]
              res = pipe.run_apply(approved)
              data = res.get("data") or {}
              applied = (data.get("log") or {}).get("applied") or []
              if not applied:
                status.update(label="반영 실패", state="error")
                st.error(res.get("error") or "반영 실패")
              else:
                state["edited_bytes"] = data.get("edited_bytes")
                state["proposals"] = data.get("proposals") or state["proposals"]
                v = pipe.run_verify()
                state["verify"] = v.get("data")
                exp = pipe.run_export()
                if exp.get("ok"):
                  ed = exp.get("data") or {}
                  state["edited_bytes"] = ed.get("bytes") or state["edited_bytes"]
                  state["export_name"] = ed.get("filename") or "document_filled.hwpx"
                state["phase"] = "done"
                if on_applied and state.get("edited_bytes"):
                  on_applied(state["target_name"], state["edited_bytes"])
                status.update(label="완료", state="complete")
                st.rerun()
        except Exception as e:
          st.error(str(e))

  if state["phase"] == "done" and state.get("edited_bytes"):
    st.success("문서에 반영했습니다. 아래 파일로 내려받으세요.")
    st.download_button(
      "수정본 다운로드",
      data=state["edited_bytes"],
      file_name=state.get("export_name") or "document_filled.hwpx",
      mime="application/hwp+zip",
      type="primary",
      use_container_width=True,
      key=f"{kp}_dl",
    )
    if st.button("제안 지우기", use_container_width=True, key=f"{kp}_clear"):
      st.session_state[STATE_KEY] = _empty()
      _pipeline().reset()
      st.rerun()
