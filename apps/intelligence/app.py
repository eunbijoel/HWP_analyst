"""
HWP Document Intelligence (Product A)

Understand · analyze · review · grounded Q&A.
Does NOT edit, fill, accept proposals, or export modified HWP/HWPX.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from additional.reference_parser import build_reference_context
from hwp_core.analysis.intent_route import (
    analysis_chat_reply_for_edit_intent,
    route_analysis_intent,
)
from hwp_core.hwp_backends import get_backend_status
from hwp_core.hwp_parser import parse_document
from hwp_core.intel_pipeline import build_intelligence, build_workspace_intelligence
from hwp_core.llm_client import check_ollama_status
from hwp_core.knowledge_mode import (
    DEFAULT_KNOWLEDGE_MODE,
    MODE_HELP_KO,
    MODE_LABELS_KO,
    KnowledgeMode,
    normalize_knowledge_mode,
)
from hwp_core.qa_engine import QAEngine
from hwp_core.shared.preview.plain import build_preview_from_text
from hwp_core.table_extractor import (
    detect_numbers_in_tables,
    detect_numbers_in_text,
    extract_tables,
)
from ui.brand import LOGO_PATH, PRODUCT_NAME, hero, inject_theme, next_hint, sidebar_brand
from ui.issue_panel import (
    FOCUS_DOC_KEY,
    ISSUE_JUMP_KEY,
    get_jump_for,
    pop_pending_chat,
    render_issue_alerts,
)
from ui.review_home import render_review_home

st.set_page_config(
    page_title=f"{PRODUCT_NAME} · Intelligence",
    page_icon=str(LOGO_PATH) if LOGO_PATH.is_file() else "✦",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_theme()

DOC_PANE_HEIGHT = 720
DOC_IFRAME_HEIGHT = 700
CHAT_SCROLL_HEIGHT = 520


def render_scrollable_doc_preview(html: str, *, iframe_height: int = DOC_IFRAME_HEIGHT):
    with st.container(height=DOC_PANE_HEIGHT, border=False):
        components.html(html, height=iframe_height, scrolling=True)


@st.cache_data(ttl=30, show_spinner=False)
def get_cached_ollama_status(url: str):
    return check_ollama_status(url)


def _has_document_content(documents: list) -> bool:
    return any(d.get("paragraphs") or d.get("tables") for d in (documents or []))


def render_knowledge_mode_picker(*, key: str = "knowledge_mode") -> KnowledgeMode:
    """Product A: document vs general knowledge — default document + general."""
    options: list[KnowledgeMode] = [
        "document_only",
        "document_plus_general",
        "general_only",
    ]
    if key not in st.session_state:
        st.session_state[key] = DEFAULT_KNOWLEDGE_MODE
    mode = st.radio(
        "답변 근거",
        options=options,
        format_func=lambda m: MODE_LABELS_KO[m],
        horizontal=True,
        key=key,
        help="문서 근거와 일반 지식을 섞지 않습니다. 기본은 문서 답변 후 「문서 외」보충입니다.",
    )
    st.caption(MODE_HELP_KO[normalize_knowledge_mode(mode)])
    return normalize_knowledge_mode(mode)


def process_document(file_bytes: bytes, filename: str):
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".xlsx", ".xls"):
        raise ValueError("지금은 HWP / HWPX만 지원합니다.")
    doc = parse_document(file_bytes=file_bytes, filename=filename)
    tables = extract_tables(doc, document_id=filename)
    tnums = detect_numbers_in_text(doc.full_text, document_id=filename)
    tblnums = detect_numbers_in_tables(tables, document_id=filename)
    return doc, tables, tnums, tblnums


def get_cached_qa_engine(documents: list, fname: str) -> QAEngine:
    # Bump suffix when QAEngine.answer signature changes so Streamlit
    # does not keep pre-reload instances missing new kwargs (e.g. knowledge_mode).
    key = f"qa_engine_v2_{fname}_{len(documents)}"
    stale = [
        k for k in list(st.session_state.keys())
        if k.startswith("qa_engine_") and not k.startswith("qa_engine_v2_")
    ]
    for k in stale:
        del st.session_state[k]
    if key not in st.session_state:
        st.session_state[key] = QAEngine(documents=documents)
    return st.session_state[key]


def get_reference_context() -> str:
    refs = st.session_state.get("reference_docs", [])
    return build_reference_context(refs) if refs else ""


def _file_active_key(filename: str) -> str:
    return f"file_active_{filename}"


def render_sidebar_file_checkboxes(filenames: list[str]) -> list[str]:
    with st.sidebar:
        st.markdown("---")
        st.markdown("##### 문서")
        st.caption("분석에 포함할 파일을 선택하세요.")
        known = set(filenames)
        for k in list(st.session_state.keys()):
            if k.startswith("file_active_") and k[len("file_active_") :] not in known:
                del st.session_state[k]
        if not filenames:
            st.caption("아직 열린 문서가 없습니다.")
            return []
        c1, c2 = st.columns(2)
        with c1:
            if st.button("모두", use_container_width=True, key="file_select_all"):
                for name in filenames:
                    st.session_state[_file_active_key(name)] = True
                st.rerun()
        with c2:
            if st.button("없음", use_container_width=True, key="file_select_none"):
                for name in filenames:
                    st.session_state[_file_active_key(name)] = False
                st.rerun()
        selected: list[str] = []
        for name in filenames:
            key = _file_active_key(name)
            if key not in st.session_state:
                st.session_state[key] = True
            if st.checkbox(name, key=key):
                selected.append(name)
        st.caption(f"{len(selected)}개 사용 중")
        return selected


def render_analysis_preview(entry: dict):
    """Read-only document preview with optional issue highlight."""
    fname = entry["filename"]
    dp = entry["doc_payload"]
    doc = dp.get("doc")
    paragraphs = dp.get("paragraphs", [])
    tables_raw = [t.get("rows", []) for t in getattr(doc, "tables_raw", [])]
    jump = get_jump_for(fname)
    jump_t = jump.get("table_index") if jump else None
    jump_r = jump.get("row_index") if jump else None
    st.caption(
        f"파서: {getattr(doc, 'file_type', None) or 'unknown'} · "
        f"문단 {len(paragraphs)}"
    )
    st.markdown(f"### 📄 {fname}")
    if jump:
        st.info(
            f"검토 이슈 위치 · {jump.get('source') or '문서'}"
            + (f" — {jump.get('message')}" if jump.get("message") else "")
        )
    html = build_preview_from_text(
        paragraphs,
        tables_raw,
        filename=fname,
        highlight_table=jump_t,
        highlight_row=jump_r,
    )
    render_scrollable_doc_preview(html)
    st.caption("문서 수정은 HWP Editing Assistant (B) · http://127.0.0.1:8765 에서 하세요.")


def render_analysis_chat(
    entry: dict,
    all_documents: list,
    *,
    model_name: str,
    stage1_model: str,
    use_llm: bool,
    use_streaming: bool,
    ollama_url: str,
    knowledge_mode: KnowledgeMode = DEFAULT_KNOWLEDGE_MODE,
    active_filenames: list[str] | None = None,
):
    fname = entry["filename"]
    doc_payload = entry["doc_payload"]
    chat_key = f"workspace_chat_{fname}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    active = active_filenames or [fname]
    st.caption(
        f"질문하세요 — 예: *총 사업비는?*, *이 이슈가 왜 생겼어?*  ·  "
        f"활성 문서: {', '.join(active)}"
    )
    with st.container(height=CHAT_SCROLL_HEIGHT, border=False):
        for msg in st.session_state[chat_key]:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
                if msg.get("chart_data") is not None:
                    st.bar_chart(msg["chart_data"])

    pending_payload = None
    raw_pending = st.session_state.get("pending_issue_chat")
    if raw_pending and raw_pending.get("filename") == fname:
        pending_payload = pop_pending_chat(fname)
    pending_q = (pending_payload or {}).get("question") if pending_payload else None
    pending_issues = None
    if pending_payload and pending_payload.get("issue"):
        pending_issues = [pending_payload["issue"]]

    q_input = st.chat_input("질문을 입력하세요...", key=f"intel_chat_{fname}")
    q = pending_q or q_input
    if not q:
        return

    st.session_state[chat_key].append({"role": "user", "content": q})
    route = route_analysis_intent(q)
    if route != "qa" and not pending_q:
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": analysis_chat_reply_for_edit_intent(route),
        })
        st.rerun()
        return

    ref_ctx = get_reference_context()
    documents = [doc_payload] if len(all_documents) <= 1 else all_documents

    qa = get_cached_qa_engine(documents, fname)
    question = q
    if ref_ctx and knowledge_mode != "general_only":
        question = f"{q}\n\n[참고자료]\n{ref_ctx[:4000]}"
    hist = []
    msgs = st.session_state[chat_key][:-1]
    for i in range(0, len(msgs) - 1, 2):
        if (
            i + 1 < len(msgs)
            and msgs[i]["role"] == "user"
            and msgs[i + 1]["role"] == "assistant"
        ):
            hist.append({
                "question": msgs[i]["content"],
                "answer": msgs[i + 1]["content"],
            })
    # Labeled split answers need full text (no stream) except pure document_only
    do_stream = bool(use_streaming and knowledge_mode == "document_only")
    with st.spinner("분석 중..." if knowledge_mode != "general_only" else "일반 설명 생성 중..."):
        ans = qa.answer(
            question=question,
            use_llm=use_llm,
            model=model_name,
            ollama_url=ollama_url,
            stream=do_stream,
            stage1_model=stage1_model,
            history=hist[-3:],
            issues=pending_issues,
            knowledge_mode=knowledge_mode,
        )

    chart = ans.get("chart_data")
    if ans.get("answer_stream"):
        with st.chat_message("assistant"):
            reply_text = st.write_stream(ans["answer_stream"])
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": reply_text,
            "chart_data": chart.get("data") if chart else None,
        })
    else:
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": ans.get("answer", "답변 없음"),
            "chart_data": chart.get("data") if chart else None,
        })
    st.rerun()


def render_workspace_qa_chat(
    all_documents: list,
    *,
    model_name: str,
    stage1_model: str,
    use_llm: bool,
    use_streaming: bool,
    ollama_url: str,
    knowledge_mode: KnowledgeMode = DEFAULT_KNOWLEDGE_MODE,
    active_filenames: list[str] | None = None,
):
    chat_key = "workspace_chat_ALL"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []
    names = active_filenames or [
        d.get("filename") or d.get("name") or "?" for d in (all_documents or [])
    ]
    st.caption(
        f"여러 문서를 비교·질문합니다 (읽기 전용).  ·  활성 문서: {', '.join(str(n) for n in names)}"
    )
    with st.container(height=CHAT_SCROLL_HEIGHT, border=False):
        for msg in st.session_state[chat_key]:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
    q = st.chat_input("전체 문서에 질문…", key="intel_chat_all")
    if not q:
        return
    st.session_state[chat_key].append({"role": "user", "content": q})
    route = route_analysis_intent(q)
    if route != "qa":
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": analysis_chat_reply_for_edit_intent(route),
        })
        st.rerun()
        return
    qa = get_cached_qa_engine(all_documents, "ALL")
    do_stream = bool(use_streaming and knowledge_mode == "document_only")
    with st.spinner("분석 중..." if knowledge_mode != "general_only" else "일반 설명 생성 중..."):
        ans = qa.answer(
            question=q,
            use_llm=use_llm,
            model=model_name,
            ollama_url=ollama_url,
            stream=do_stream,
            stage1_model=stage1_model,
            knowledge_mode=knowledge_mode,
        )
    chart = ans.get("chart_data")
    if ans.get("answer_stream"):
        with st.chat_message("assistant"):
            reply_text = st.write_stream(ans["answer_stream"])
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": reply_text or "답변 없음",
            "chart_data": chart.get("data") if chart else None,
        })
    else:
        st.session_state[chat_key].append({
            "role": "assistant",
            "content": ans.get("answer") or "답변 없음",
            "chart_data": chart.get("data") if chart else None,
        })
    st.rerun()


# --- Sidebar ---
with st.sidebar:
    sidebar_brand()
    st.caption("분석 · 검토 · Q&A (편집 없음)")
    st.markdown("---")
    with st.expander("연결 · 모델", expanded=False):
        ollama_url = st.text_input(
            "Ollama URL", value="http://localhost:11434", key="sidebar_ollama_url"
        )
        ollama_status = get_cached_ollama_status(ollama_url)
        if ollama_status["status"] == "running":
            st.caption("AI 연결됨")
            available_models = ollama_status["models"]
            if available_models:
                gemma4_models = [m for m in available_models if "gemma4" in m]
                sorted_models = gemma4_models + [
                    m for m in available_models if m not in gemma4_models
                ]
                model_name = st.selectbox("답변 모델", sorted_models, index=0)
            else:
                model_name = st.text_input(
                    "답변 모델", value="gemma4", key="sidebar_model_name"
                )
                available_models = []
        else:
            st.caption("AI 미연결 · 규칙 검토만 가능")
            model_name = "gemma4"
            available_models = []
        use_llm = ollama_status["status"] == "running"
        use_streaming = use_llm
        if use_llm and available_models:
            small_models = [
                m
                for m in available_models
                if any(t in m for t in ["gemma3", "qwen", "phi4", "gemma2"])
            ] or available_models
            stage1_model = st.selectbox("질문 이해 모델", small_models, index=0)
        else:
            stage1_model = "gemma3:4b"
    st.markdown("---")
    knowledge_mode = render_knowledge_mode_picker(key="knowledge_mode")
    st.markdown("---")
    st.caption(f"백엔드: {get_backend_status().summary()}")

hero(PRODUCT_NAME)
st.caption("분석 · 검토 · Q&A — 문서를 추가해 시작하세요")

uploaded_files = st.file_uploader(
    "문서 추가",
    type=["hwp", "hwpx"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)

if not uploaded_files:
    render_sidebar_file_checkboxes([])
    render_issue_alerts([])
else:
    uploaded_list = (
        uploaded_files if isinstance(uploaded_files, list) else [uploaded_files]
    )
    file_entries = []
    all_documents = []

    for uf in uploaded_list:
        filename = uf.name
        bytes_key = f"upload_bytes_{filename}"
        hash_key = f"upload_hash_{filename}"
        file_bytes = uf.read()
        file_hash = hashlib.sha256(file_bytes).hexdigest()
        if st.session_state.get(hash_key) != file_hash:
            st.session_state[bytes_key] = file_bytes
            st.session_state[hash_key] = file_hash
            for k in list(st.session_state.keys()):
                if k.startswith(f"parsed_{filename}_") or k.startswith(
                    f"qa_engine_{filename}"
                ):
                    del st.session_state[k]
        else:
            file_bytes = st.session_state[bytes_key]

        cache_key = f"parsed_{filename}_{len(file_bytes)}"
        if cache_key not in st.session_state:
            with st.spinner("문서를 준비하는 중…"):
                doc, tables, tnums, tblnums = process_document(file_bytes, filename)
                intel = build_intelligence(
                    paragraphs=doc.paragraphs,
                    tables=tables,
                    text_numbers=tnums,
                    table_numbers=tblnums,
                    document_id=filename,
                )
            st.session_state[cache_key] = {
                "doc": doc,
                "tables": tables,
                "text_numbers": tnums,
                "table_numbers": tblnums,
                "intel": intel,
            }

        cached = st.session_state[cache_key]
        doc_payload = {
            "id": filename,
            "paragraphs": cached["doc"].paragraphs,
            "tables": cached["tables"],
            "text_numbers": cached["text_numbers"],
            "table_numbers": cached["table_numbers"],
            "doc": cached["doc"],
            "intel": cached.get("intel"),
        }
        all_documents.append(doc_payload)
        file_entries.append({
            "filename": filename,
            "file_bytes": file_bytes,
            "doc_payload": doc_payload,
        })
        for err in cached["doc"].errors:
            st.warning(f"{filename}: {err}")

    uploaded_names = [e["filename"] for e in file_entries]
    active_names = set(render_sidebar_file_checkboxes(uploaded_names))
    active_entries = [e for e in file_entries if e["filename"] in active_names]
    active_documents = [e["doc_payload"] for e in active_entries]

    if not active_entries:
        next_hint("왼쪽에서 작업할 문서를 하나 이상 선택하세요.")
    else:
        if len(active_documents) >= 2:
            build_workspace_intelligence(active_documents)

        def _render_workspace():
            names = [e["filename"] for e in active_entries]
            if st.session_state.get(FOCUS_DOC_KEY) not in names:
                st.session_state[FOCUS_DOC_KEY] = names[0]
            col_doc, col_chat = st.columns([5, 3], gap="large")
            with col_doc:
                if len(active_entries) == 1:
                    render_analysis_preview(active_entries[0])
                else:
                    jump = st.session_state.get(ISSUE_JUMP_KEY) or {}
                    jump_name = jump.get("filename")
                    if jump_name in names:
                        ordered = (
                            [e for e in active_entries if e["filename"] == jump_name]
                            + [e for e in active_entries if e["filename"] != jump_name]
                        )
                    else:
                        ordered = active_entries
                    tabs = st.tabs([e["filename"] for e in ordered])
                    for tab, entry in zip(tabs, ordered):
                        with tab:
                            render_analysis_preview(entry)
            with col_chat:
                tab_file, tab_all = st.tabs(["💬 이 파일", "💬 전체"])
                with tab_file:
                    if len(active_entries) > 1:
                        names = [e["filename"] for e in active_entries]
                        prev = st.session_state.get("active_file_chat_target")
                        if prev not in names:
                            st.session_state["active_file_chat_target"] = names[0]
                        chat_fname = st.selectbox(
                            "채팅할 파일", names, key="active_file_chat_target"
                        )
                        entry = next(
                            e for e in active_entries if e["filename"] == chat_fname
                        )
                    else:
                        entry = active_entries[0]
                    render_analysis_chat(
                        entry,
                        active_documents,
                        model_name=model_name,
                        stage1_model=stage1_model,
                        use_llm=use_llm,
                        use_streaming=use_streaming,
                        ollama_url=ollama_url,
                        knowledge_mode=knowledge_mode,
                        active_filenames=[e["filename"] for e in active_entries],
                    )
                with tab_all:
                    render_workspace_qa_chat(
                        active_documents,
                        model_name=model_name,
                        stage1_model=stage1_model,
                        use_llm=use_llm,
                        use_streaming=use_streaming,
                        ollama_url=ollama_url,
                        knowledge_mode=knowledge_mode,
                        active_filenames=[e["filename"] for e in active_entries],
                    )

        render_review_home(
            active_entries,
            active_documents,
            render_workspace=_render_workspace,
        )
