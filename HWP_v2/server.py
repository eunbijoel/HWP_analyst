"""
HWP_v2 — Inline AI–style editor shell (local Ollama).

Run:
  python3 HWP_v2/server.py
  → http://127.0.0.1:8765
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request, send_file

_V2 = Path(__file__).resolve().parent
_ROOT = _V2.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

from hwp_core.hwpx_editor import HWPXEditor  # noqa: E402
from hwp_core.hwp_parser import parse_document  # noqa: E402
from hwp_core.llm_client import check_ollama_status, generate  # noqa: E402
from hwp_core.doc_agent.pipeline import DocFillPipeline  # noqa: E402
from hwp_core.shared.preview.plain import build_preview_from_text  # noqa: E402
from hwp_core.editing.preview_layer import build_preview_html  # noqa: E402
from convert_hwp import hwpx_to_hwp_bytes  # noqa: E402
from cell_ai import (  # noqa: E402
    build_cell_prompt,
    build_para_prompt,
    detect_cell_intent,
    shorten_locally,
)
from chat_route import (  # noqa: E402
    compute_label_total,
    decide_chat_route,
    resolve_search_edit,
)
from workspace_docs import DocSlot, load_doc_slot, slot_list_payload  # noqa: E402

app = Flask(
    __name__,
    template_folder=str(_V2 / "templates"),
    static_folder=str(_V2 / "static"),
)

DEFAULT_OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4")


@dataclass
class Session:
    id: str
    docs: dict[str, DocSlot] = field(default_factory=dict)
    active_id: str = ""
    filename: str = ""
    editor: Optional[HWPXEditor] = None
    chat: list[dict] = field(default_factory=list)
    selected_para: Optional[int] = None
    selected_cell: Optional[tuple[int, int, int]] = None  # t, r, c (마지막 클릭 · 호환)
    selected_paras: list[int] = field(default_factory=list)
    selected_cells: list[tuple[int, int, int]] = field(default_factory=list)
    source_was_hwp: bool = False
    original_hwp_name: str = ""
    original_hwp_bytes: Optional[bytes] = None
    convert_note: str = ""
    read_only: bool = False
    readonly_html: str = ""
    readonly_paras: list[str] = field(default_factory=list)
    ollama_url: str = DEFAULT_OLLAMA
    model: str = DEFAULT_MODEL
    fill_pipeline: Optional[DocFillPipeline] = None


SESSIONS: dict[str, Session] = {}


def _session(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if not s:
        raise KeyError("session")
    return s


def _active(sess: Session) -> Optional[DocSlot]:
    if sess.active_id and sess.active_id in sess.docs:
        return sess.docs[sess.active_id]
    if sess.docs:
        return next(iter(sess.docs.values()))
    return None


def _sync_active(sess: Session) -> None:
    """활성 슬롯 → 레거시 Session 필드 (편집/다운로드 경로 재사용)."""
    slot = _active(sess)
    if not slot:
        sess.filename = ""
        sess.editor = None
        sess.read_only = True
        sess.readonly_html = ""
        sess.readonly_paras = []
        return
    sess.active_id = slot.id
    sess.filename = slot.filename
    sess.editor = slot.editor
    sess.read_only = slot.read_only or slot.editor is None
    sess.source_was_hwp = slot.source_was_hwp
    sess.original_hwp_name = slot.original_hwp_name
    sess.original_hwp_bytes = slot.original_hwp_bytes
    sess.convert_note = slot.convert_note
    sess.readonly_html = slot.preview_html
    sess.readonly_paras = list(slot.paragraphs or [])
    # 활성 슬롯 미리보기 갱신
    if slot.editor is not None:
        from workspace_docs import _preview_from_editor
        slot.preview_html = _preview_from_editor(slot.editor, slot.filename)


def _add_slot(sess: Session, slot: DocSlot, *, make_active: bool = False) -> None:
    sess.docs[slot.id] = slot
    if make_active or not sess.active_id:
        # 편집 가능한 HWP(X)를 우선 활성으로
        if make_active or slot.is_editable or not sess.active_id:
            sess.active_id = slot.id
    # 아직 편집 문서가 없고 방금 추가한 게 편집 가능하면 활성
    cur = _active(sess)
    if cur and not cur.is_editable and slot.is_editable:
        sess.active_id = slot.id
    _sync_active(sess)


def _qa_documents(sess: Session) -> list[dict]:
    return [s.qa_payload() for s in sess.docs.values() if s.paragraphs or s.qa_tables]


def _doc_html(sess: Session) -> str:
    slot = _active(sess)
    if not slot:
        return ""
    if slot.editor is not None and not slot.read_only:
        from workspace_docs import _preview_from_editor
        return _preview_from_editor(slot.editor, slot.filename)
    return slot.preview_html or sess.readonly_html


def _clear_selection(sess: Session) -> None:
    sess.selected_para = None
    sess.selected_cell = None
    sess.selected_paras = []
    sess.selected_cells = []


def _selection_payload(sess: Session) -> dict:
    return {
        "selected_para": sess.selected_para,
        "selected_cell": (
            {"t": sess.selected_cell[0], "r": sess.selected_cell[1], "c": sess.selected_cell[2]}
            if sess.selected_cell else None
        ),
        "selected_paras": list(sess.selected_paras),
        "selected_cells": [
            {"t": t, "r": r, "c": c} for t, r, c in sess.selected_cells
        ],
    }


def _has_selection(sess: Session) -> bool:
    return bool(sess.selected_paras or sess.selected_cells) or (
        sess.selected_para is not None or sess.selected_cell is not None
    )


def _pending_summary(editor: Optional[HWPXEditor]) -> list[dict]:
    if editor is None:
        return []
    rows = []
    for ch in editor.get_pending_changes():
        rows.append({
            "id": ch.id,
            "type": ch.change_type,
            "location": ch.location,
            "old": (ch.old_text or "")[:120],
            "new": (ch.new_text or "")[:120],
        })
    return rows


def _state(sess: Session) -> dict:
    _sync_active(sess)
    pending_n = len(sess.editor.get_pending_changes()) if sess.editor else 0
    slots = list(sess.docs.values())
    return {
        "session_id": sess.id,
        "filename": sess.filename,
        "active_id": sess.active_id,
        "docs": slot_list_payload(slots, sess.active_id),
        "doc_count": len(slots),
        "pending_count": pending_n,
        "pending": _pending_summary(sess.editor),
        **_selection_payload(sess),
        "source_was_hwp": sess.source_was_hwp,
        "original_hwp_name": sess.original_hwp_name,
        "convert_note": sess.convert_note,
        "read_only": sess.read_only,
        "can_try_hwp_download": bool(sess.original_hwp_bytes) or (
            not sess.read_only and sess.editor is not None
        ),
        "chat": sess.chat[-40:],
        "html": _doc_html(sess),
        "ollama_url": sess.ollama_url,
        "model": sess.model,
    }


def _parse_json_rewrite(raw: str) -> tuple[str, str]:
    rewritten, summary = "", "제안 준비"
    try:
        parsed = json.loads(raw)
        rewritten = (parsed.get("rewritten") or "").strip()
        summary = (parsed.get("summary") or summary).strip()
        return rewritten, summary
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{.*\}', raw, re.S)
    if m:
        try:
            parsed = json.loads(m.group())
            rewritten = (parsed.get("rewritten") or "").strip()
            summary = (parsed.get("summary") or summary).strip()
            return rewritten, summary
        except json.JSONDecodeError:
            pass
    return raw.strip(), summary


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "product": "HWP_v2"})


@app.get("/api/ollama")
def ollama_status():
    url = request.args.get("url") or DEFAULT_OLLAMA
    return jsonify(check_ollama_status(url))


ALLOWED_EXT = (".hwp", ".hwpx")


def _collect_upload_files() -> list[tuple[str, bytes]]:
    """FormData: file / files 다중 지원."""
    out: list[tuple[str, bytes]] = []
    if request.files.getlist("files"):
        for f in request.files.getlist("files"):
            if f and f.filename:
                out.append((f.filename, f.read()))
    elif request.files.get("file"):
        f = request.files["file"]
        if f and f.filename:
            out.append((f.filename, f.read()))
    return out


@app.post("/api/upload")
def upload():
    """단일/다중 업로드. session_id가 있으면 워크스페이스에 추가."""
    items = _collect_upload_files()
    if not items:
        return jsonify({"error": "파일이 없습니다"}), 400

    for name, _ in items:
        if not name.lower().endswith(ALLOWED_EXT):
            return jsonify({
                "error": f"지원 형식: HWP / HWPX — {name}",
            }), 400

    sid = (request.form.get("session_id") or "").strip()
    ollama_url = request.form.get("ollama_url") or DEFAULT_OLLAMA
    model = request.form.get("model") or DEFAULT_MODEL

    if sid and sid in SESSIONS:
        sess = SESSIONS[sid]
    else:
        sid = uuid.uuid4().hex[:12]
        sess = Session(id=sid, ollama_url=ollama_url, model=model)
        SESSIONS[sid] = sess

    sess.ollama_url = ollama_url
    sess.model = model

    added: list[str] = []
    notes: list[str] = []
    for name, data in items:
        slot = load_doc_slot(data, name)
        # 파일명 중복이면 덮지 않고 새 슬롯
        _add_slot(sess, slot, make_active=slot.is_editable)
        added.append(slot.filename)
        if slot.errors:
            notes.append(f"{slot.filename}: {slot.errors[0]}")
        elif slot.source_was_hwp and slot.convert_note:
            notes.append(f"{slot.filename}: HWP→HWPX ({slot.convert_note})")

    names = ", ".join(added)
    n = len(sess.docs)
    tip = (
        f"워크스페이스 {n}개 · 방금 연 파일: {names}\n"
        "• 왼쪽 목록에서 활성 문서 전환\n"
        "• 다른 문서는 참고 · 「두 문서 보고 보완해줘」「사업비는?」\n"
        "• 문단 선택 후 짧은 수정 지시"
    )
    if notes:
        tip += "\n" + "\n".join(notes[:5])
    sess.chat.append({"role": "assistant", "content": tip})
    _clear_selection(sess)
    return jsonify(_state(sess))


@app.post("/api/set_active")
def set_active():
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    doc_id = body.get("doc_id") or ""
    if doc_id not in sess.docs:
        return jsonify({"error": "문서를 찾을 수 없습니다"}), 404
    sess.active_id = doc_id
    _clear_selection(sess)
    _sync_active(sess)
    return jsonify(_state(sess))


@app.post("/api/remove_doc")
def remove_doc():
    """워크스페이스에서 문서 슬롯 제거."""
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    doc_id = body.get("doc_id") or ""
    if doc_id not in sess.docs:
        return jsonify({"error": "문서를 찾을 수 없습니다"}), 404

    was_active = sess.active_id == doc_id
    del sess.docs[doc_id]
    _clear_selection(sess)
    if was_active:
        sess.active_id = ""
        editable = next(
            (s for s in sess.docs.values() if getattr(s, "is_editable", False)),
            None,
        )
        if editable:
            sess.active_id = editable.id
        elif sess.docs:
            sess.active_id = next(iter(sess.docs))
    elif sess.active_id not in sess.docs:
        sess.active_id = ""
    if not sess.docs:
        sess.active_id = ""
        sess.fill_pipeline = None
        sess.chat.append({
            "role": "assistant",
            "content": "열린 문서가 없습니다. 파일을 다시 추가해 주세요.",
        })
    _sync_active(sess)
    return jsonify(_state(sess))


def _run_workspace_fill(sess: Session, command: str) -> str:
    """활성 HWP(X) = target, 나머지 = 참고.

    Evidence Fill → Context Fill. 제안만 pending에 올리고 자동 적용하지 않음.
    """
    from hwp_core.doc_agent.edit_proposal_service import AI_DRAFT_MARKER, FILL_CONTEXT, FILL_EVIDENCE

    slots = list(sess.docs.values())
    targets = [s for s in slots if s.kind in ("hwp", "hwpx") and s.bytes_data]
    if not targets:
        return "채울 HWP/HWPX가 없습니다. 문서를 하나 더 열어 주세요."
    active = _active(sess)
    target = active if active and active.kind in ("hwp", "hwpx") else targets[0]
    refs = [s for s in slots if s.id != target.id]

    if target.editor is None and not target.read_only:
        # ensure editable slot has editor
        try:
            from hwp_core.hwpx_editor import HWPXEditor
            target.editor = HWPXEditor(target.bytes_data)
        except Exception:
            pass
    if target.editor is None:
        return (
            "편집 가능한 HWPX가 필요합니다. "
            "HWP만 있으면 변환된 HWPX를 활성으로 선택하세요."
        )

    pipe = DocFillPipeline()
    sess.fill_pipeline = pipe
    r = pipe.register_target(target.filename, target.bytes_data)
    err = r.get("error") or ""
    if err and ("HWPX" in err or "변환" in err or "불가" in err):
        return err
    for ref in refs:
        pipe.register_reference(ref.filename, ref.bytes_data)

    insp = pipe.run_inspect()
    if not insp.get("ok"):
        data = insp.get("data") or {}
        return insp.get("error") or data.get("error") or "문서를 읽지 못했습니다."

    out = pipe.run_propose(
        command, use_llm=True, model=sess.model, ollama_url=sess.ollama_url,
    )
    if not out.get("ok"):
        return out.get("error") or "초안을 만들지 못했습니다."

    proposals = (out.get("data") or {}).get("proposals") or []
    if not proposals:
        ref_note = (
            f"참고({', '.join(r.filename for r in refs)})와 "
            if refs else ""
        )
        return (
            f"「{target.filename}」에서 {ref_note}Evidence Fill과 Context Fill을 "
            "모두 시도했지만 반영할 초안을 만들지 못했습니다. "
            "빈칸·섹션 라벨을 확인하거나 참고 문서를 추가해 주세요."
        )

    # Sync active editor = target (propose/accept workflow)
    sess.active_id = target.id
    _sync_active(sess)
    editor = sess.editor
    if editor is None:
        return "편집기를 준비하지 못했습니다."

    n_ev = n_ctx = 0
    pushed = 0
    lines: list[str] = []

    for p in proposals:
        meta = p.get("meta") or {}
        fill_mode = meta.get("fill_mode") or FILL_EVIDENCE
        after = (p.get("after") or "").strip()
        if not after:
            continue
        action = p.get("action") or ""
        sources = p.get("sources") or []
        src_bits = []
        for s in sources[:3]:
            doc = s.get("document") or ""
            loc = s.get("location") or ""
            if doc or loc:
                src_bits.append(f"{doc} {loc}".strip())
        src_txt = " · ".join(src_bits) if src_bits else ""
        ch = None

        try:
            if action == "write_table_cell":
                t = int(meta["table_id"])
                r_i = int(meta["row"])
                c_i = int(meta["column"])
                ctx = p.get("label") or ""
                ch = editor.propose_cell_change(t, r_i, c_i, after, context=ctx)
                if fill_mode == FILL_CONTEXT and AI_DRAFT_MARKER not in (ch.location or ""):
                    ch.location = f"{AI_DRAFT_MARKER} · {ch.location}"
            elif action == "insert_after":
                anchor = (meta.get("anchor_label") or "").strip()
                if not anchor:
                    # location may include AI Draft prefix — strip for locate
                    raw_loc = (p.get("location") or "").replace(AI_DRAFT_MARKER, "").strip(" ·")
                    anchor = raw_loc
                if anchor:
                    ch = editor.propose_insert_after_anchor(anchor, after)
                else:
                    pid = meta.get("paragraph_id")
                    if pid is None:
                        continue
                    ch = editor.propose_paragraph_change(int(pid), after)
                if ch and fill_mode == FILL_CONTEXT and AI_DRAFT_MARKER not in (ch.location or ""):
                    ch.location = f"{AI_DRAFT_MARKER} · {ch.location}"
            elif action == "insert_table":
                # Pending insert as text after first paragraph (review before apply)
                paras = editor.get_paragraphs()
                anchor = paras[0]["text"] if paras else ""
                body = f"[표 삽입 제안]\n{after}"
                if anchor:
                    ch = editor.propose_insert_after_anchor(anchor, body)
                else:
                    continue
            else:
                # replace_paragraph
                pid = meta.get("paragraph_id")
                if pid is None:
                    continue
                ch = editor.propose_paragraph_change(int(pid), after)
                if fill_mode == FILL_CONTEXT and AI_DRAFT_MARKER not in (ch.location or ""):
                    ch.location = f"{AI_DRAFT_MARKER} · {ch.location}"
        except Exception as e:
            lines.append(f"· 제안 실패 ({p.get('label') or action}): {e}")
            continue

        if not ch:
            continue
        pushed += 1
        if fill_mode == FILL_CONTEXT:
            n_ctx += 1
            tag = AI_DRAFT_MARKER
        else:
            n_ev += 1
            tag = f"Evidence · {src_txt}" if src_txt else "Evidence Fill"
        show = after if len(after) <= 70 else after[:67] + "…"
        lines.append(f"· [{tag}] {ch.location}: 「{show}」")

    if not pushed:
        return (
            "초안은 만들었지만 문서 위치에 제안을 올리지 못했습니다. "
            "문단/표 좌표를 확인해 주세요."
        )

    editor._bump_preview()
    head = (
        f"채우기 제안 {pushed}건을 올렸습니다 "
        f"(Evidence {n_ev} · Context/AI Draft {n_ctx}).\n"
        "자동 반영하지 않았습니다. 항목별 수락/거절 또는 「전체 수락」「전체 거절」을 사용하세요."
    )
    if refs:
        head += f"\n참고 문서: {', '.join(r.filename for r in refs)}"
    else:
        head += "\n참고 문서 없음 → Context Fill만 사용했습니다."
    return head + "\n\n" + "\n".join(lines[:12])


def _explain_pending(sess: Session) -> str:
    """Narrow editing Q&A — explain proposed changes only."""
    if not sess.editor:
        return "활성 편집 문서가 없습니다. HWPX를 연 뒤 제안을 확인하세요."
    pending = sess.editor.get_pending_changes()
    if not pending:
        return (
            "대기 중인 변경 제안이 없습니다. "
            "문단을 선택한 뒤 수정 지시를 하거나, "
            "문서 분석·검토 Q&A는 Product A (Document Intelligence)를 이용하세요."
        )
    lines = [f"대기 변경 {len(pending)}건:"]
    for ch in pending[:20]:
        loc = getattr(ch, "location", "") or ch.change_type
        old = (ch.old_text or "")[:40]
        new = (ch.new_text or "")[:40]
        lines.append(f"• {loc}: 「{old}」 → 「{new}」 ({ch.id})")
    if len(pending) > 20:
        lines.append(f"… 외 {len(pending) - 20}건")
    lines.append(
        "\n적용하려면 「모두 적용」, 취소는 「모두 취소」를 사용하세요. "
        "전체 문서 검증·분석은 Product A로 이동하세요."
    )
    return "\n".join(lines)


def _analysis_redirect(question: str) -> str:
    return (
        "전체 문서 Q&A·검토·검증은 **HWP Document Intelligence** (Product A)에서 지원합니다.\n"
        "`streamlit run apps/intelligence/app.py` 또는 `streamlit run app.py`\n\n"
        "이 편집기(Product B)에서는:\n"
        "· 문단 선택 후 리라이트\n"
        "· 빈칸·참고 자료 채우기 (DocFill)\n"
        "· 제안 설명 (`변경 설명해줘`)\n"
        "만 지원합니다.\n\n"
        f"(질문: {question[:120]})"
    )


def _run_workspace_qa(sess: Session, question: str) -> str:
    """Product B: no full-document QA — explain edits or redirect to Product A."""
    q = (question or "").strip()
    if re.search(r"변경|제안|pending|설명|왜\s*바|달라", q, re.I):
        return _explain_pending(sess)
    return _analysis_redirect(q)


@app.get("/api/session/<sid>")
def get_session(sid: str):
    try:
        return jsonify(_state(_session(sid)))
    except KeyError:
        return jsonify({"error": "세션 없음 — 다시 업로드"}), 404


@app.post("/api/select")
def select_target():
    """문단/셀 선택. mode=replace(기본) | toggle(Ctrl+클릭 다중).

    문단과 셀은 동시에 고르지 않음.
    """
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404

    kind = body.get("kind") or "paragraph"
    mode = (body.get("mode") or "replace").lower()
    if mode not in ("replace", "toggle"):
        mode = "replace"

    if kind == "cell":
        key = (int(body["t"]), int(body["r"]), int(body["c"]))
        if mode == "toggle":
            sess.selected_paras = []
            sess.selected_para = None
            if key in sess.selected_cells:
                sess.selected_cells = [x for x in sess.selected_cells if x != key]
            else:
                sess.selected_cells = list(sess.selected_cells) + [key]
        else:
            sess.selected_paras = []
            sess.selected_para = None
            sess.selected_cells = [key]
        sess.selected_cell = sess.selected_cells[-1] if sess.selected_cells else None
    else:
        idx = body.get("paragraph_index")
        if idx is None:
            return jsonify({"error": "paragraph_index 필요"}), 400
        idx = int(idx)
        if mode == "toggle":
            sess.selected_cells = []
            sess.selected_cell = None
            if idx in sess.selected_paras:
                sess.selected_paras = [x for x in sess.selected_paras if x != idx]
            else:
                sess.selected_paras = list(sess.selected_paras) + [idx]
        else:
            sess.selected_cells = []
            sess.selected_cell = None
            sess.selected_paras = [idx]
        sess.selected_para = sess.selected_paras[-1] if sess.selected_paras else None

    return jsonify(_selection_payload(sess))


@app.post("/api/edit_paragraph")
def edit_paragraph():
    """v1 canvas와 동일: propose/accept 없이 XML에 즉시 반영 (track_changes=False)."""
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    if not sess.editor:
        return jsonify({"error": "읽기 전용 — 인라인 편집 불가"}), 400
    idx = int(body["paragraph_index"])
    text = (body.get("text") or "").strip()
    paras = sess.editor.get_paragraphs()
    if idx < 0 or idx >= len(paras):
        return jsonify({"error": f"문단 인덱스 범위 오류: {idx}"}), 400
    old = paras[idx].get("text") or ""
    if text == old:
        return jsonify({"ok": True, **_state(sess)})

    # 같은 문단의 pending AI 제안은 거절
    for ch in sess.editor.pending_changes:
        if ch.status == "pending" and ch.paragraph_index == idx:
            ch.status = "rejected"

    ok = sess.editor._set_paragraph_text(idx, text, track_changes=False)
    if not ok:
        return jsonify({"error": "문단 XML 반영 실패"}), 400
    sess.editor._bump_preview()
    sess.editor._invalidate_structure_cache()

    # HWP로 연 경우 — v1처럼 working HWP에도 즉시 미러
    _mirror_hwp_paragraph(sess, idx, text, old)

    # export가 실제로 반영됐는지 검증
    err = _verify_para_in_export(sess.editor, idx, text)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True, **_state(sess)})


@app.post("/api/edit_cell")
def edit_cell():
    """v1 canvas와 동일: 셀 XML 즉시 반영."""
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    if not sess.editor:
        return jsonify({"error": "읽기 전용 — 인라인 편집 불가"}), 400
    t, r, c = int(body["t"]), int(body["r"]), int(body["c"])
    text = body.get("text")
    if text is None:
        return jsonify({"error": "text 필요"}), 400
    text = str(text)
    if text.strip() in ("(비어 있음)",):
        text = ""

    rows = sess.editor.get_table_as_rows(t)
    old = ""
    if r < len(rows) and c < len(rows[r]):
        old = rows[r][c] or ""
    if text == old:
        return jsonify({"ok": True, **_state(sess)})

    for ch in sess.editor.pending_changes:
        if (
            ch.status == "pending"
            and ch.change_type == "cell"
            and ch.table_index == t
            and ch.row == r
            and ch.col == c
        ):
            ch.status = "rejected"

    ok = sess.editor.edit_table_cell(t, r, c, text)
    if not ok:
        return jsonify({"error": "셀 XML 반영 실패 — 위치/병합셀 확인"}), 400
    sess.editor._bump_preview()
    sess.editor._invalidate_structure_cache()

    _mirror_hwp_cell(sess, t, r, c, text)

    err = _verify_cell_in_export(sess.editor, t, r, c, text)
    if err:
        return jsonify({"error": err}), 400
    return jsonify({"ok": True, **_state(sess)})


def _mirror_hwp_paragraph(sess: Session, idx: int, text: str, old: str) -> None:
    if not sess.original_hwp_bytes:
        return
    from hwp_core.hwp_backends import apply_hwpilot_to_bytes, hwpilot_edit_paragraph

    name = sess.original_hwp_name or "doc.hwp"

    def _edit(path: str) -> tuple[bool, str]:
        return hwpilot_edit_paragraph(path, int(idx), text, old_text=old)

    new_b, _ = apply_hwpilot_to_bytes(sess.original_hwp_bytes, name, _edit)
    if new_b:
        sess.original_hwp_bytes = new_b
        slot = _active(sess)
        if slot:
            slot.original_hwp_bytes = new_b


def _mirror_hwp_cell(sess: Session, t: int, r: int, c: int, text: str) -> None:
    if not sess.original_hwp_bytes:
        return
    from hwp_core.hwp_backends import apply_hwpilot_to_bytes, hwpilot_edit_table_cell

    name = sess.original_hwp_name or "doc.hwp"
    ref = f"s0.t{t}.r{r}.c{c}"

    def _edit(path: str) -> tuple[bool, str]:
        return hwpilot_edit_table_cell(path, ref, text)

    new_b, _ = apply_hwpilot_to_bytes(sess.original_hwp_bytes, name, _edit)
    if new_b:
        sess.original_hwp_bytes = new_b
        slot = _active(sess)
        if slot:
            slot.original_hwp_bytes = new_b


def _verify_para_in_export(editor: HWPXEditor, idx: int, text: str) -> str:
    data = editor.get_export_bytes()
    ok, err = HWPXEditor.validate_hwpx_bytes(data)
    if not ok:
        return f"저장 검증 실패: {err}"
    check = HWPXEditor(data)
    paras = check.get_paragraphs()
    if idx >= len(paras):
        return "저장 검증 실패: 문단 인덱스 없음"
    got = (paras[idx].get("text") or "").strip()
    want = (text or "").strip()
    if got != want and want not in got:
        return f"저장 검증 실패: 문단에 「{want[:40]}」 미반영 (현재: 「{got[:40]}」)"
    return ""


def _verify_cell_in_export(editor: HWPXEditor, t: int, r: int, c: int, text: str) -> str:
    data = editor.get_export_bytes()
    ok, err = HWPXEditor.validate_hwpx_bytes(data)
    if not ok:
        return f"저장 검증 실패: {err}"
    check = HWPXEditor(data)
    rows = check.get_table_as_rows(t)
    got = ""
    if r < len(rows) and c < len(rows[r]):
        got = rows[r][c] or ""
    want = text or ""
    if got != want and want.strip() not in (got or ""):
        return f"저장 검증 실패: 셀에 「{want[:40]}」 미반영 (현재: 「{(got or '')[:40]}」)"
    return ""


def _row_hint(editor: HWPXEditor, t: int, r: int) -> str:
    try:
        rows = editor.get_table_as_rows(t)
        if not rows:
            return ""
        header = " | ".join((x or "")[:24] for x in rows[0][:12])
        cur = " | ".join((x or "")[:24] for x in rows[r][:12]) if r < len(rows) else ""
        return f"헤더: {header} / 현재행: {cur}"
    except Exception:
        return ""


def _propose_reply(loc: str, value: str, summary: str, *, n: int = 1) -> str:
    show = value if len(value) <= 80 else value[:77] + "…"
    if n > 1:
        return (
            f"{summary} · {n}곳 제안\n"
            f"마지막: {loc} → 「{show}」\n\n"
            "제안 목록에서 항목별 수락/거절하거나 「전체 수락」「전체 거절」을 쓰세요."
        )
    return (
        f"{summary}\n→ 「{show}」\n\n"
        f"{loc}에 제안을 올렸습니다. 항목별 수락/거절 또는 「전체 수락」「전체 거절」."
    )


def _rewrite_one_cell(sess: Session, user_msg: str, t: int, r: int, c: int) -> tuple[bool, str]:
    intent = detect_cell_intent(user_msg)
    rows = sess.editor.get_table_as_rows(t)
    old = ""
    if r < len(rows) and c < len(rows[r]):
        old = rows[r][c] or ""
    loc = f"{r + 1}행 {c + 1}열"

    prompt = build_cell_prompt(
        filename=sess.filename, t=t, r=r, c=c, old=old,
        user_msg=user_msg, intent=intent, row_hint=_row_hint(sess.editor, t, r),
    )
    result = generate(
        prompt, sess.model, sess.ollama_url,
        temperature=0.25, num_predict=900, format="json", timeout=180,
    )
    if result.get("error"):
        if intent == "shorten" and old.strip():
            det_short = shorten_locally(old, aggressive="더" in user_msg)
            sess.editor.propose_cell_change(t, r, c, det_short, context="축약")
            return True, f"{loc} 축약"
        return False, f"{loc}: Ollama 오류 ({result['error']})"

    rewritten, summary = _parse_json_rewrite(result.get("text") or "")
    if not (rewritten or "").strip():
        if intent == "shorten" and old.strip():
            rewritten = shorten_locally(old, aggressive="더" in user_msg)
            summary = "축약"
        else:
            return False, f"{loc}: 수정문을 만들지 못함"
    sess.editor.propose_cell_change(t, r, c, rewritten, context=summary)
    return True, f"{loc} {summary or '수정'}"


def _rewrite_one_para(sess: Session, user_msg: str, sel: int) -> tuple[bool, str]:
    intent = detect_cell_intent(user_msg)
    paras = sess.editor.get_paragraphs()
    if not (0 <= sel < len(paras)):
        return False, f"문단 {sel + 1}: 범위 오류"
    selected_text = paras[sel]["text"]
    loc = f"문단 {sel + 1}"
    prompt = build_para_prompt(old=selected_text, user_msg=user_msg, intent=intent)
    result = generate(
        prompt, sess.model, sess.ollama_url,
        temperature=0.3, num_predict=1500, format="json", timeout=180,
    )
    if result.get("error"):
        if intent == "shorten" and selected_text.strip():
            det_short = shorten_locally(selected_text, aggressive="더" in user_msg)
            sess.editor.propose_paragraph_change(sel, det_short)
            return True, f"{loc} 축약"
        return False, f"{loc}: Ollama 오류 ({result['error']})"
    rewritten, summary = _parse_json_rewrite(result.get("text") or "")
    if not (rewritten or "").strip():
        if intent == "shorten" and selected_text.strip():
            rewritten = shorten_locally(selected_text, aggressive="더" in user_msg)
            summary = "축약"
        else:
            return False, f"{loc}: 수정문을 만들지 못함"
    sess.editor.propose_paragraph_change(sel, rewritten)
    return True, f"{loc} {summary or '수정'}"


def _apply_selection_rewrite(sess: Session, user_msg: str) -> str:
    cells = list(sess.selected_cells)
    paras = list(sess.selected_paras)
    if not cells and sess.selected_cell is not None:
        cells = [sess.selected_cell]
    if not paras and sess.selected_para is not None:
        paras = [sess.selected_para]

    if cells and paras:
        return "문단과 셀을 동시에 선택할 수 없습니다. 한쪽만 Ctrl+클릭으로 고르세요."
    if not cells and not paras:
        return "선택된 문단이 없습니다. 대상을 선택한 뒤 다시 지시해 주세요."

    ok_msgs: list[str] = []
    err_msgs: list[str] = []
    last_value = ""
    last_loc = ""

    if cells:
        for t, r, c in cells:
            ok, msg = _rewrite_one_cell(sess, user_msg, t, r, c)
            (ok_msgs if ok else err_msgs).append(msg)
            if ok:
                last_loc = f"{r + 1}행 {c + 1}열"
                rows = sess.editor.get_table_as_rows(t) or []
                # new text is in pending; keep short summary
                last_value = msg
        n = len(ok_msgs)
        if n == 0:
            return "제안을 만들지 못했습니다.\n" + "\n".join(err_msgs)
        head = _propose_reply(last_loc or "선택", last_value or "제안", f"{n}곳 수정", n=n)
        detail = "\n".join(f"· {m}" for m in ok_msgs)
        extra = ("\n실패:\n" + "\n".join(f"· {m}" for m in err_msgs)) if err_msgs else ""
        return f"{head}\n\n{detail}{extra}"

    for sel in paras:
        ok, msg = _rewrite_one_para(sess, user_msg, sel)
        (ok_msgs if ok else err_msgs).append(msg)
        if ok:
            last_loc = f"문단 {sel + 1}"
            last_value = msg
    n = len(ok_msgs)
    if n == 0:
        return "제안을 만들지 못했습니다.\n" + "\n".join(err_msgs)
    head = _propose_reply(last_loc or "선택", last_value or "제안", f"{n}곳 수정", n=n)
    detail = "\n".join(f"· {m}" for m in ok_msgs)
    extra = ("\n실패:\n" + "\n".join(f"· {m}" for m in err_msgs)) if err_msgs else ""
    return f"{head}\n\n{detail}{extra}"


def _answer_selection_question(sess: Session, user_msg: str) -> str:
    """Selected location(s) + explanatory question → chat answer only."""
    parts: list[str] = []
    cells = list(sess.selected_cells) or (
        [sess.selected_cell] if sess.selected_cell is not None else []
    )
    paras = list(sess.selected_paras) or (
        [sess.selected_para] if sess.selected_para is not None else []
    )
    if sess.editor and cells:
        for t, r, c in cells[:8]:
            rows = sess.editor.get_table_as_rows(t) or []
            old = ""
            if r < len(rows) and c < len(rows[r]):
                old = rows[r][c] or ""
            parts.append(f"[{r + 1}행 {c + 1}열] {old[:400]}")
    elif sess.editor and paras:
        all_paras = sess.editor.get_paragraphs()
        for sel in paras[:8]:
            if 0 <= sel < len(all_paras):
                parts.append(f"[문단 {sel + 1}] {all_paras[sel]['text'][:400]}")

    context = "\n\n".join(parts)
    loc = f"{len(parts)}곳" if len(parts) > 1 else (parts[0][:40] if parts else "")
    if not context.strip():
        return "선택된 내용이 비어 있습니다. 다른 문단을 선택하거나 Product A에서 질문하세요."

    prompt = (
        "사용자는 문서 일부를 선택한 채 설명·정의·개념을 묻고 있습니다.\n"
        "문서를 수정하거나 rewritten을 만들지 마세요. 질문에 한국어로만 답하세요.\n"
        f"위치: {loc}\n"
        f"선택 내용:\n\"\"\"{context[:2400]}\"\"\"\n"
        f"질문: {user_msg}\n"
    )
    result = generate(
        prompt, sess.model, sess.ollama_url,
        temperature=0.2, num_predict=800, timeout=120,
    )
    if result.get("error"):
        return (
            f"질문에 바로 답하기 어렵습니다 ({result['error']}). "
            "전체 문서 Q&A는 Product A를 이용하세요."
        )
    return (result.get("text") or "").strip() or "답변을 만들지 못했습니다."


def _apply_propose_target(sess: Session, route) -> str:
    spec = route.spec
    tg = route.targets[0]
    new_text = (spec.new if spec else "") or ""
    if not new_text.strip():
        return "바꿀 새 문구가 없습니다. 예: *총사업비를 130억원으로 바꿔줘*"

    if tg.kind == "cell" and tg.table_index is not None:
        sess.editor.propose_cell_change(
            tg.table_index, tg.row, tg.col, new_text, context=tg.label,
        )
        return _propose_reply(tg.label, new_text, "치환 제안")

    if tg.kind == "paragraph" and tg.para_index is not None:
        # Replace whole paragraph or substitute needle
        old = tg.text
        if spec and spec.old and spec.old in old:
            rewritten = old.replace(spec.old, new_text, 1)
        else:
            rewritten = new_text
        sess.editor.propose_paragraph_change(tg.para_index, rewritten)
        return _propose_reply(tg.label, rewritten, "치환 제안")

    return "대상 위치에 제안을 올리지 못했습니다. 문단을 직접 선택해 주세요."


def _apply_compute_edit(sess: Session, route) -> str:
    spec = route.spec
    label = (spec.label if spec else "") or "합계"
    value, note = compute_label_total(sess.editor, label)
    if not value:
        return note

    # Prefer a cell whose row contains label and last column looks like total
    from chat_route import EditSpec, find_edit_targets

    targets = find_edit_targets(sess.editor, EditSpec(label=label, new=value))
    # Also look for rows with "합계"
    if not targets:
        targets = find_edit_targets(sess.editor, EditSpec(label="합계", new=value))

    if len(targets) == 1:
        tg = targets[0]
        if tg.kind == "cell":
            sess.editor.propose_cell_change(
                tg.table_index, tg.row, tg.col, value, context=note,
            )
            return _propose_reply(tg.label, value, note)
        sess.editor.propose_paragraph_change(tg.para_index, value)
        return _propose_reply(tg.label, value, note)
    if len(targets) > 1:
        resolved = resolve_search_edit(
            sess.editor, f"{label} 합계", EditSpec(label=label, new=value),
        )
        if resolved.action == "choose_targets":
            return f"{note}: {value}\n\n" + resolved.message
        if resolved.action == "propose_replace":
            route2 = resolved
            route2.spec = EditSpec(label=label, new=value)
            return _apply_propose_target(sess, route2)
    return (
        f"{note} 결과: {value}\n"
        "넣을 합계 셀을 찾지 못했습니다. 합계 셀을 선택한 뒤 "
        f"「{value}로 수정해줘」라고 지시해 주세요."
    )


@app.post("/api/chat")
def chat():
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404

    user_msg = (body.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "메시지를 입력하세요"}), 400

    if body.get("ollama_url"):
        sess.ollama_url = body["ollama_url"]
    if body.get("model"):
        sess.model = body["model"]

    sess.chat.append({"role": "user", "content": user_msg})
    _sync_active(sess)

    sel = sess.selected_para
    cell = sess.selected_cell
    has_selection = _has_selection(sess)

    decision = decide_chat_route(
        message=user_msg,
        has_selection=has_selection,
        has_editor=bool(sess.editor),
        has_docs=bool(sess.docs),
    )

    reply = ""
    if decision.action == "fill":
        reply = _run_workspace_fill(sess, user_msg)
    elif decision.action == "explain_pending":
        reply = _explain_pending(sess)
    elif decision.action == "answer_selection":
        reply = _answer_selection_question(sess, user_msg)
    elif decision.action == "rewrite_selection":
        if not sess.editor:
            reply = (
                "활성 문서는 읽기 전용입니다. "
                "편집은 HWPX 문서를 활성으로 선택하세요."
            )
        else:
            reply = _apply_selection_rewrite(sess, user_msg)
    elif decision.action == "search_edit":
        resolved = resolve_search_edit(sess.editor, user_msg, decision.spec)
        if resolved.action == "propose_replace":
            reply = _apply_propose_target(sess, resolved)
        elif resolved.action == "compute_edit":
            reply = _apply_compute_edit(sess, resolved)
        else:
            reply = resolved.message
    elif decision.action == "compute_edit":
        reply = _apply_compute_edit(sess, decision)
    elif decision.action == "redirect_a":
        reply = decision.message
    else:
        reply = decision.message or "문단을 선택한 뒤 다시 지시해 주세요."

    sess.chat.append({"role": "assistant", "content": reply})
    if reply.startswith("Ollama 오류:"):
        return jsonify({"error": reply, **_state(sess)}), 502
    return jsonify(_state(sess))


@app.post("/api/accept_all")
def accept_all():
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    if not sess.editor:
        return jsonify({"error": "읽기 전용"}), 400
    # 수락도 직접 수정과 같이 깨끗한 텍스트 치환 (strike/green 대신)
    pending = list(sess.editor.get_pending_changes())
    n = sess.editor.accept_all_pending(track_changes=False)
    for ch in pending:
        if ch.status != "accepted":
            continue
        if ch.change_type == "cell" and ch.table_index is not None:
            _mirror_hwp_cell(sess, ch.table_index, ch.row, ch.col, ch.new_text or "")
        elif ch.change_type == "paragraph" and ch.paragraph_index is not None:
            _mirror_hwp_paragraph(
                sess, ch.paragraph_index, ch.new_text or "", ch.old_text or "",
            )
    sess.chat.append({
        "role": "assistant",
        "content": f"✅ AI 제안 {n}건을 문서에 반영했습니다.",
    })
    return jsonify({"accepted": n, **_state(sess)})


@app.post("/api/accept_one")
def accept_one():
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    if not sess.editor:
        return jsonify({"error": "읽기 전용"}), 400
    change_id = (body.get("change_id") or "").strip()
    if not change_id:
        return jsonify({"error": "change_id 필요"}), 400
    ch = next((c for c in sess.editor.pending_changes if c.id == change_id), None)
    if ch is None or ch.status != "pending":
        return jsonify({"error": "제안을 찾을 수 없습니다"}), 404
    ok = sess.editor.accept_change(change_id, track_changes=False)
    if not ok:
        return jsonify({"error": "수락 반영 실패"}), 400
    if ch.change_type == "cell" and ch.table_index is not None:
        _mirror_hwp_cell(sess, ch.table_index, ch.row, ch.col, ch.new_text or "")
    elif ch.change_type == "paragraph" and ch.paragraph_index is not None:
        _mirror_hwp_paragraph(
            sess, ch.paragraph_index, ch.new_text or "", ch.old_text or "",
        )
    show = (ch.new_text or "")[:60]
    sess.chat.append({
        "role": "assistant",
        "content": f"✅ 수락 · {ch.location}: 「{show}」",
    })
    return jsonify({"accepted": 1, **_state(sess)})


@app.post("/api/reject_all")
def reject_all():
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    if not sess.editor:
        return jsonify({"error": "읽기 전용"}), 400
    n = sess.editor.reject_all_pending()
    sess.editor.pending_changes = [
        c for c in sess.editor.pending_changes if c.status == "pending"
    ]
    sess.editor._bump_preview()
    sess.chat.append({
        "role": "assistant",
        "content": f"❌ AI 제안 {n}건을 거절했습니다.",
    })
    return jsonify({"rejected": n, **_state(sess)})


@app.post("/api/reject_one")
def reject_one():
    body = request.get_json(force=True) or {}
    try:
        sess = _session(body["session_id"])
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    if not sess.editor:
        return jsonify({"error": "읽기 전용"}), 400
    change_id = (body.get("change_id") or "").strip()
    if not change_id:
        return jsonify({"error": "change_id 필요"}), 400
    ch = next((c for c in sess.editor.pending_changes if c.id == change_id), None)
    if ch is None or ch.status != "pending":
        return jsonify({"error": "제안을 찾을 수 없습니다"}), 404
    ch.status = "rejected"
    sess.editor.pending_changes = [
        c for c in sess.editor.pending_changes if c.status == "pending"
    ]
    sess.editor._bump_preview()
    sess.chat.append({
        "role": "assistant",
        "content": f"❌ 거절 · {ch.location}",
    })
    return jsonify({"rejected": 1, **_state(sess)})


def _export_hwp_via_hwpilot(sess: Session) -> tuple[Optional[bytes], str]:
    """v1과 동일: 편집 중 미러링된 working HWP bytes를 그대로 반환.

    (이전: HWPX highlight를 원본에 재적용 → 인덱스 불일치로 원본/실패)
    """
    if not sess.original_hwp_bytes:
        return None, "원본 HWP가 없습니다 (HWPX만 연 경우)"
    return bytes(sess.original_hwp_bytes), "working HWP"


def _ascii_filename(name: str, ext: str) -> str:
    """다운로드용 ASCII 파일명 (한글만 있으면 document)."""
    stem = Path(name).stem
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "document"
    return f"{safe}_edited.{ext.lstrip('.')}"


def _build_export(sess: Session, fmt: str) -> tuple[Optional[bytes], str, str]:
    """Returns (bytes|None, filename, error)."""
    stem_src = sess.original_hwp_name or sess.filename
    if fmt == "hwp":
        hwp_data, note = _export_hwp_via_hwpilot(sess)
        if not hwp_data and sess.editor:
            hwp_data, note2 = hwpx_to_hwp_bytes(
                sess.editor.get_export_bytes(), sess.filename,
            )
            note = f"{note}; {note2}"
        if not hwp_data:
            return None, "", (
                f"HWP 저장 실패 ({note}). "
                "대신 「저장 · HWPX」로 받은 뒤 한글에서 HWP로 저장하세요."
            )
        return hwp_data, _ascii_filename(stem_src, "hwp"), ""

    if sess.read_only or sess.editor is None:
        return None, "", "읽기 전용 — HWPX 편집본 없음. HWP 버튼으로 원본을 받으세요."

    data = sess.editor.get_export_bytes()
    ok, err = HWPXEditor.validate_hwpx_bytes(data)
    if not ok:
        return None, "", f"HWPX 손상: {err}"
    return data, _ascii_filename(stem_src, "hwpx"), ""


@app.get("/api/download/<sid>")
def download(sid: str):
    """바이너리 첨부 (octet-stream). 브라우저 미리보기 금지."""
    try:
        sess = _session(sid)
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    fmt = (request.args.get("fmt") or "hwpx").lower()
    data, filename, err = _build_export(sess, fmt)
    if err or not data:
        return jsonify({"error": err or "다운로드 실패"}), 400

    # Flask send_file 대신 헤더를 직접 고정 — Cursor/일부 프록시가 ZIP을 탭에 열던 문제 차단
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Length": str(len(data)),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store",
    }
    return Response(data, status=200, headers=headers, direct_passthrough=True)


@app.get("/api/export/<sid>")
def export_b64(sid: str):
    """다운로드용 JSON+base64 (브라우저에서 파일로 저장)."""
    try:
        sess = _session(sid)
    except KeyError:
        return jsonify({"error": "세션 없음"}), 404
    fmt = (request.args.get("fmt") or "hwpx").lower()
    data, filename, err = _build_export(sess, fmt)
    if err or not data:
        return jsonify({"error": err or "내보내기 실패"}), 400
    return jsonify({
        "filename": filename,
        "fmt": fmt,
        "size": len(data),
        "b64": base64.b64encode(data).decode("ascii"),
        "magic": "PK" if data[:2] == b"PK" else data[:4].hex(),
    })


if __name__ == "__main__":
    port = int(os.environ.get("HWP_V2_PORT", "8765"))
    try:
        from hwp_core.hwp_backends import get_backend_status
        st = get_backend_status()
        print(f"backends: {st.summary()}")
        for n in st.notes:
            print(f"  ! {n}")
        if not st.hwpilot:
            print("  → HWP 열기: cd hwpilot && npm install")
    except Exception as e:
        print("backend check failed:", e)
    print(f"HWP_v2 → http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
