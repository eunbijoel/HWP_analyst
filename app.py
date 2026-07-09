"""
HWP 문서 분석기 - 2분할 inline AI 스타일 UI
왼쪽: 문서 미리보기 (변경 색상 표시)  |  오른쪽: 채팅/명령
"""

import sys
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import time
import re
import hashlib
from typing import Optional
from types import SimpleNamespace
import streamlit as st
import streamlit.components.v1 as components

from hwp_core.hwp_parser import parse_document
from hwp_core.hwp_backends import get_backend_status, hwpilot_convert_to_hwpx
from hwp_core.table_extractor import extract_tables, detect_numbers_in_text, detect_numbers_in_tables
from hwp_core.qa_engine import QAEngine
from hwp_core.llm_client import check_ollama_status, answer_general_question
from hwp_core.hwpx_editor import HWPXEditor
from additional.reference_parser import parse_reference, build_reference_context
from additional.reference_parser import normalize_insert_body
from ui.document_preview import (
    build_preview_html, build_preview_from_text,
    format_pending_label,
)
from ui.command_router import classify_intent, execute_edit_command
from ui.canvas_editor import (
    render_canvas_editor, render_canvas_editor_hwp, render_hwpx_download,
)
from hwp_core.intel_pipeline import build_intelligence, build_workspace_intelligence
from ui.intel_panel import render_intel_review, render_workspace_intel
from ui.document_workspace import render_document_pane, render_excel_split_editor

st.set_page_config(page_title="HWP Analyzer", page_icon="📄", layout="wide")

VIEW_PREVIEW = "미리보기 + 채팅 편집"
VIEW_DIRECT = "직접 편집"

# 분할 패널 높이 (각 열 독립 스크롤)
DOC_PANE_HEIGHT = 720
DOC_IFRAME_HEIGHT = 700
CHAT_SCROLL_HEIGHT = 520


def render_scrollable_doc_preview(html: str, *, iframe_height: int = DOC_IFRAME_HEIGHT):
    """왼쪽 문서 미리보기 — 고정 높이 + 내부 스크롤."""
    with st.container(height=DOC_PANE_HEIGHT, border=False):
        components.html(html, height=iframe_height, scrolling=True)


def render_scrollable_pane(height: int = DOC_PANE_HEIGHT):
    """직접 편집 등 긴 콘텐츠용 스크롤 패널."""
    return st.container(height=height, border=False)


GENERAL_KNOWLEDGE_RE = re.compile(
    r'필요성|중요성|배경|기대효과|의미|정의|개념|bullet|불릿|장점|단점',
    re.I,
)


def _has_document_content(documents: list) -> bool:
    return any(
        d.get('paragraphs') or d.get('tables')
        for d in (documents or [])
    )


def _should_use_general_llm(question: str, documents: list) -> bool:
    """업로드된 문서가 있으면 일반 LLM(문서 없이 답변)으로 보내지 않음."""
    if _has_document_content(documents):
        return False
    if re.search(r'이\s*문서|이\s*자료|이\s*파일|문서\s*에서|올린|업로드', question, re.I):
        return False
    return bool(GENERAL_KNOWLEDGE_RE.search(question))


def get_reference_context() -> str:
    refs = st.session_state.get('reference_docs', [])
    return build_reference_context(refs) if refs else ''


def _cache_reference_summary(text: str):
    cleaned = normalize_insert_body(text)
    if len(cleaned) >= 80:
        st.session_state['ref_summary_cache'] = cleaned


def _apply_edit_result(fname: str, result: dict, editor=None, source_hwp: str = ''):
    if result.get('summary_text'):
        st.session_state['ref_summary_cache'] = result['summary_text']
    if result.get('new_file_bytes'):
        set_hwp_working_bytes(fname, result['new_file_bytes'])
    if editor is not None:
        if result.get('applied_direct') or result.get('changes', 0) > 0:
            _invalidate_hwpx_preview_cache(fname)
        if result.get('applied_direct'):
            sync_export_state(editor, fname, source_hwp=source_hwp)


def get_cached_ollama_status(url: str) -> dict:
    cache = st.session_state.get('ollama_cache', {})
    if cache.get('url') == url and time.time() - cache.get('ts', 0) < 30:
        return cache['status']
    status = check_ollama_status(url)
    st.session_state['ollama_cache'] = {'url': url, 'status': status, 'ts': time.time()}
    return status


def get_cached_qa_engine(all_documents: list, scope_key: str) -> QAEngine:
    sig_parts = []
    for d in all_documents:
        did = d.get('id', '')
        sig_parts.append(
            f"{did}:{len(d.get('paragraphs', []))}:{len(d.get('tables', []))}"
        )
    sig = hashlib.sha256('|'.join(sig_parts).encode('utf-8')).hexdigest()[:16]
    key = f"qa_engine_{scope_key}_{sig}"
    if key not in st.session_state:
        if len(all_documents) == 1:
            d0 = all_documents[0]
            st.session_state[key] = QAEngine(
                paragraphs=d0.get('paragraphs', []),
                table_summaries=d0.get('tables', []),
                text_numbers=d0.get('text_numbers', []),
                table_numbers=d0.get('table_numbers', []),
            )
        else:
            st.session_state[key] = QAEngine(documents=all_documents)
    return st.session_state[key]


def render_workspace_qa_chat(
    all_documents: list,
    *,
    model_name: str,
    stage1_model: str,
    use_llm: bool,
    use_streaming: bool,
    ollama_url: str,
):
    """다중 파일 공통 Q&A 패널 (편집 명령 제외)."""
    def _norm_name(s: str) -> str:
        return re.sub(r'[\s_\-\.]+', '', (s or '').lower())

    def _doc_tokens(doc_id: str) -> list[str]:
        base = os.path.splitext(doc_id)[0]
        return [doc_id, base]

    def _pick_target_docs(question: str, docs: list[dict]) -> list[dict]:
        qn = _norm_name(question)
        picked = []
        for d in docs:
            did = d.get('id', '')
            matched = any(_norm_name(tok) in qn for tok in _doc_tokens(did))
            if matched:
                picked.append(d)
        return picked

    chat_key = "workspace_chat_multi"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    st.caption("공통 질의응답 — 여러 문서를 함께 분석합니다.")
    with st.container(height=CHAT_SCROLL_HEIGHT, border=False):
        for msg in st.session_state[chat_key]:
            with st.chat_message(msg['role']):
                st.write(msg['content'])
                if msg.get('chart_data') is not None:
                    st.bar_chart(msg['chart_data'])

    q = st.chat_input("여러 문서를 대상으로 질문하세요...", key="multi_chat_input")
    if q:
        st.session_state[chat_key].append({'role': 'user', 'content': q})
        target_docs = _pick_target_docs(q, all_documents)
        qa_docs = target_docs if target_docs else all_documents
        scope = "multi_all"
        if target_docs:
            scope = "multi_" + hashlib.sha256(
                '|'.join(d.get('id', '') for d in target_docs).encode('utf-8')
            ).hexdigest()[:12]
            st.caption("대상 파일: " + ", ".join(d.get('id', '') for d in target_docs))
        qa = get_cached_qa_engine(qa_docs, scope)
        hist = []
        msgs = st.session_state[chat_key][:-1]
        for i in range(0, len(msgs) - 1, 2):
            if i + 1 < len(msgs) and msgs[i]['role'] == 'user' and msgs[i + 1]['role'] == 'assistant':
                hist.append({'question': msgs[i]['content'], 'answer': msgs[i + 1]['content']})

        with st.spinner("분석 중..."):
            ans = qa.answer(
                question=q, use_llm=use_llm, model=model_name,
                ollama_url=ollama_url, stream=use_streaming,
                stage1_model=stage1_model, history=hist[-3:],
            )

        chart = ans.get('chart_data')
        if ans.get('answer_stream'):
            with st.chat_message("assistant"):
                reply_text = st.write_stream(ans['answer_stream'])
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': reply_text,
                'chart_data': chart.get('data') if chart else None,
            })
        else:
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': ans.get('answer', '답변 없음'),
                'chart_data': chart.get('data') if chart else None,
            })
        st.rerun()

    if st.session_state[chat_key] and st.button("공통 대화 초기화", key="clr_multi_chat"):
        st.session_state[chat_key] = []
        st.rerun()


def enrich_hwp_qa_from_hwpx(doc_payload: dict, hwpx_bytes: bytes, hwpx_name: str) -> dict:
    """.hwp 파싱이 빈약할 때 변환된 HWPX로 Q&A 데이터 보강."""
    if not hwpx_bytes:
        return doc_payload
    weak = len(doc_payload.get('paragraphs', [])) < 5 and len(doc_payload.get('tables', [])) < 1
    if not weak:
        return doc_payload
    doc2, tables2, tnums2, tblnums2 = process_document(hwpx_bytes, hwpx_name)
    if len(doc2.paragraphs) <= len(doc_payload.get('paragraphs', [])):
        return doc_payload
    return {
        **doc_payload,
        'paragraphs': doc2.paragraphs,
        'tables': tables2,
        'text_numbers': tnums2,
        'table_numbers': tblnums2,
        'doc': doc2,
        'parser_note': 'hwpx Q&A 보강',
    }


def process_document(file_bytes: bytes, filename: str):
    ext = os.path.splitext(filename)[1].lower()
    if ext in ('.xlsx', '.xls'):
        ref = parse_reference(file_bytes, filename)
        # XLSX는 참고 파서가 표 내용을 full_text/paragraphs에도 펼쳐 담으므로
        # 미리보기에서 문단+표가 중복 렌더링되지 않게 문단은 요약만 유지한다.
        sheet_count = len(ref.tables)
        summary_para = (
            f"엑셀 문서 {filename} · 시트/표 {sheet_count}개"
            if sheet_count > 0
            else f"엑셀 문서 {filename}"
        )
        doc = SimpleNamespace(
            filename=filename,
            file_type=ref.file_type,
            full_text=ref.full_text,
            paragraphs=[summary_para],
            tables_raw=[{'rows': rows, 'caption': '', 'unit': ''} for rows in ref.tables],
            errors=ref.errors,
        )
    else:
        doc = parse_document(file_bytes=file_bytes, filename=filename)
    tables = extract_tables(doc, document_id=filename)
    tnums = detect_numbers_in_text(doc.full_text, document_id=filename)
    tblnums = detect_numbers_in_tables(tables, document_id=filename)
    return doc, tables, tnums, tblnums


from ui.session_store import (
    sync_export_state,
    get_hwp_working_bytes,
    set_hwp_working_bytes,
    validate_hwp_bytes,
)


def append_hwp_highlights(fname: str, highlights: list[dict]):
    if not highlights:
        return
    key = f"hwp_highlights_{fname}"
    existing = st.session_state.get(key, [])
    st.session_state[key] = existing + highlights
    for k in list(st.session_state.keys()):
        if k.startswith(f"hwp_preview_{fname}_"):
            del st.session_state[k]


def get_hwp_highlights(fname: str) -> list[dict]:
    return st.session_state.get(f"hwp_highlights_{fname}", [])


def get_cached_preview_html(
    editor: HWPXEditor,
    filename: str,
    *,
    scroll_to_change_id: str | None = None,
) -> str:
    """HWPX diff 미리보기 HTML (revision 기반 캐시)."""
    cache_key = f"hwpx_preview_{filename}"
    scroll_id = scroll_to_change_id or st.session_state.pop(f"scroll_pending_{filename}", None)
    if not scroll_id:
        entry = st.session_state.get(cache_key)
        if entry and entry.get('rev') == editor.preview_revision:
            return entry['html']
    html = build_preview_html(
        editor,
        filename=filename,
        scroll_to_change_id=scroll_id,
        canvas_mode=False,
    )
    if not scroll_id:
        st.session_state[cache_key] = {'rev': editor.preview_revision, 'html': html}
    return html


def _invalidate_hwpx_preview_cache(filename: str):
    for k in list(st.session_state.keys()):
        if k == f"hwpx_preview_{filename}" or k.startswith(f"hwp_preview_{filename}_"):
            del st.session_state[k]


def get_hwp_preview_html(fname: str, file_bytes: bytes) -> str:
    sig = hashlib.sha256(file_bytes).hexdigest()[:16]
    cache_key = f"hwp_preview_{fname}_{sig}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]
    doc, _, _, _ = process_document(file_bytes, filename=fname)
    tables_raw = [t.get('rows', []) for t in doc.tables_raw]
    html = build_preview_from_text(
        doc.paragraphs, tables_raw, filename=fname,
        applied_changes=get_hwp_highlights(fname),
    )
    st.session_state[cache_key] = html
    return html


def _selected_para_state(fname: str) -> dict | None:
    return st.session_state.get(f"selected_para_{fname}")


def _set_selected_para(fname: str, para_index: int, text: str):
    st.session_state[f"selected_para_{fname}"] = {
        'index': para_index,
        'text': text,
    }
    st.session_state[f"selection_{fname}"] = text


def render_chat_panel(
    fname: str,
    editor: HWPXEditor,
    all_documents: list,
    chat_key: str,
    model_name: str,
    stage1_model: str,
    use_llm: bool,
    use_streaming: bool,
    ollama_url: str = 'http://localhost:11434',
    source_hwp: str = '',
    canvas_mode: bool = False,
):
    st.caption("명령 / 질문 — 예: *빈칸 채워줘*, *초안 작성해줘*, *총 사업비는?*")

    sel_key = f"selection_{fname}"
    selected_para_key = f"selected_para_{fname}"
    if sel_key not in st.session_state:
        st.session_state[sel_key] = ''

    sel = st.session_state.get(selected_para_key)
    if sel:
        preview = (sel.get('text') or '')[:60]
        if len(sel.get('text', '')) > 60:
            preview += '…'
        c1, c2 = st.columns([4, 1])
        with c1:
            st.info(f"📌 **문단 {sel['index'] + 1}** 선택 · {preview}")
        with c2:
            if st.button('해제', key=f"clear_sel_{fname}", use_container_width=True):
                st.session_state.pop(selected_para_key, None)
                st.session_state[sel_key] = ''
                st.rerun()

    if not canvas_mode and st.session_state.get(f"show_para_{fname}"):
        paras = editor.get_paragraphs()
        if paras:
            para_labels = [f"{p['index']+1}. {p['preview']}" for p in paras[:30]]
            sel_idx = st.selectbox(
                "문단",
                options=list(range(min(len(paras), 30))),
                format_func=lambda i: para_labels[i],
                key=f"sel_{fname}",
            )
            st.session_state[sel_key] = paras[sel_idx]['text']
    elif not canvas_mode:
        if st.button("편집 대상 문단 선택", key=f"para_btn_{fname}", use_container_width=True):
            st.session_state[f"show_para_{fname}"] = True
            st.rerun()

    with st.container(height=CHAT_SCROLL_HEIGHT, border=False):
        for msg in st.session_state[chat_key]:
            if msg['role'] == 'user':
                with st.chat_message("user"):
                    st.write(msg['content'])
            else:
                with st.chat_message("assistant"):
                    st.markdown(msg['content'])
                    if msg.get('chart_data'):
                        st.bar_chart(msg['chart_data'])

    user_input = st.chat_input("명령 또는 질문을 입력하세요...", key=f"input_{fname}")

    if user_input:
        st.session_state[chat_key].append({'role': 'user', 'content': user_input})
        intent = classify_intent(user_input)
        ref_ctx = get_reference_context()
        selection = st.session_state.get(f"selection_{fname}", '')
        para_sel = _selected_para_state(fname)
        para_index = para_sel['index'] if para_sel else None

        if intent != 'qa' and (_edit_without_llm(intent) or use_llm):
            with st.spinner("AI 편집 중..."):
                result = execute_edit_command(
                    editor, user_input, ref_ctx,
                    model_name, ollama_url, selection_text=selection,
                    chat_history=st.session_state[chat_key][:-1],
                    source_filename=fname,
                    ref_summary_cache=st.session_state.get('ref_summary_cache', ''),
                    para_index=para_index,
                )
            reply = result.get('message', '완료')
            _apply_edit_result(fname, result, editor=editor, source_hwp=source_hwp)
            if result.get('applied_direct'):
                if result.get('intent') == 'delete':
                    reply += "\n\n👉 **hwpilot**으로 문서에서 삭제되었습니다. 왼쪽 미리보기를 확인하세요."
                else:
                    reply += "\n\n👉 **hwpilot**으로 문서에 바로 반영되었습니다. 왼쪽 미리보기를 확인하세요."
            elif result.get('new_file_bytes'):
                reply += "\n\n👉 문서에 반영되었습니다. 다운로드 버튼으로 저장하세요."
            elif result.get('changes', 0) > 0:
                reply += "\n\n👉 왼쪽 문서에서 **노란색(제안)** 으로 확인하세요. 맞으면 **「모두 적용」** → **빨간색**으로 확정됩니다."
            st.session_state[chat_key].append({'role': 'assistant', 'content': reply})
            st.rerun()
        else:
            general_mode = _should_use_general_llm(user_input, all_documents)
            if general_mode:
                with st.spinner("일반 답변 생성 중..."):
                    ans = answer_general_question(
                        question=user_input,
                        model=model_name,
                        ollama_url=ollama_url,
                        use_streaming=use_streaming,
                    )
            else:
                qa = get_cached_qa_engine(all_documents, fname)
                q = user_input
                if ref_ctx:
                    q = f"{user_input}\n\n[참고자료]\n{ref_ctx[:4000]}"
                hist = []
                msgs = st.session_state[chat_key][:-1]
                for i in range(0, len(msgs) - 1, 2):
                    if i + 1 < len(msgs) and msgs[i]['role'] == 'user' and msgs[i + 1]['role'] == 'assistant':
                        hist.append({'question': msgs[i]['content'], 'answer': msgs[i + 1]['content']})
                with st.spinner("분석 중..."):
                    ans = qa.answer(
                        question=q, use_llm=use_llm, model=model_name,
                        ollama_url=ollama_url, stream=use_streaming,
                        stage1_model=stage1_model, history=hist[-3:],
                    )
            chart = ans.get('chart_data')
            if ans.get('answer_stream'):
                with st.chat_message("assistant"):
                    reply_text = st.write_stream(ans['answer_stream'])
                st.session_state[chat_key].append({
                    'role': 'assistant',
                    'content': reply_text,
                    'chart_data': chart.get('data') if chart else None,
                })
                if ref_ctx and re.search(r'참고자료|요약', user_input, re.I):
                    _cache_reference_summary(reply_text)
            else:
                st.session_state[chat_key].append({
                    'role': 'assistant',
                    'content': ans.get('answer', '답변 없음'),
                    'chart_data': chart.get('data') if chart else None,
                })
                if ref_ctx and re.search(r'참고자료|요약', user_input, re.I):
                    _cache_reference_summary(ans.get('answer', ''))
            st.rerun()

    if st.session_state[chat_key] and st.button("대화 초기화", key=f"clr_{fname}"):
        st.session_state[chat_key] = []
        st.rerun()


try:
    render_chat_panel = st.fragment(render_chat_panel)
except AttributeError:
    pass


def _hwp_edit_needs_llm(intent: str) -> bool:
    """hwpilot 직접 편집(insert/delete/replace/table_edit)은 LLM 없이 가능."""
    return intent in ('draft', 'fill', 'rewrite')


def _edit_without_llm(intent: str) -> bool:
    return intent in ('insert', 'delete', 'replace', 'table_edit', 'append_ref')


def _render_hwp_chat(
    fname: str, file_bytes: bytes, all_documents: list,
    chat_key: str, input_key: str,
    model_name: str, ollama_url: str, use_llm: bool,
    use_streaming: bool, stage1_model: str,
    *,
    on_new_bytes=None,
):
    """HWP/읽기전용 모드 공통 채팅 패널. hwpilot 편집 + Q&A."""
    st.caption("명령 / 질문 — 예: *A를 B로 바꿔줘*, *삭제해*, *총 사업비는?*")
    with st.container(height=CHAT_SCROLL_HEIGHT, border=False):
        for msg in st.session_state[chat_key]:
            with st.chat_message(msg['role']):
                st.write(msg['content'])
    q = st.chat_input("명령 또는 질문을 입력하세요...", key=input_key)
    if not q:
        return
    st.session_state[chat_key].append({'role': 'user', 'content': q})
    intent = classify_intent(q)
    ref_ctx = get_reference_context()
    if intent != 'qa' and get_backend_status().hwpilot and (
        _edit_without_llm(intent) or not _hwp_edit_needs_llm(intent) or use_llm
    ):
        with st.spinner("편집 중..."):
            result = execute_edit_command(
                None, q, ref_ctx, model_name, ollama_url,
                chat_history=st.session_state[chat_key][:-1],
                source_filename=fname,
                file_bytes=file_bytes,
                ref_summary_cache=st.session_state.get('ref_summary_cache', ''),
            )
        reply = result.get('message', '완료')
        if result.get('summary_text'):
            st.session_state['ref_summary_cache'] = result['summary_text']
        new_bytes = result.get('new_file_bytes')
        if new_bytes and on_new_bytes:
            on_new_bytes(new_bytes, result)
            reply += "\n\n👉 문서에 반영되었습니다. 왼쪽 미리보기·다운로드를 확인하세요."
        elif result.get('applied_direct'):
            reply += "\n\n👉 문서에 반영되었습니다."
        elif result.get('changes', 0) == 0 and _hwp_edit_needs_llm(intent) and not use_llm:
            reply += "\n\n⚠️ 초안/빈칸 채우기는 Ollama가 연결되어 있어야 합니다."
        elif result.get('changes', 0) > 0:
            reply += "\n\n👉 왼쪽 문서에서 확인하세요."
        st.session_state[chat_key].append({'role': 'assistant', 'content': reply})
        st.rerun()
    elif intent != 'qa' and _hwp_edit_needs_llm(intent) and not use_llm:
        st.session_state[chat_key].append({
            'role': 'assistant',
            'content': '초안·빈칸 채우기는 Ollama가 연결되어 있어야 합니다. '
                       '삽입/삭제는 LLM 없이도 됩니다. 예: *마지막에 (내용) 추가해줘*',
        })
        st.rerun()
    else:
        qa = get_cached_qa_engine(all_documents, fname)
        hist = []
        msgs = st.session_state[chat_key][:-1]
        for i in range(0, len(msgs) - 1, 2):
            if i + 1 < len(msgs) and msgs[i]['role'] == 'user' and msgs[i + 1]['role'] == 'assistant':
                hist.append({'question': msgs[i]['content'], 'answer': msgs[i + 1]['content']})
        with st.spinner("분석 중..."):
            ans = qa.answer(
                question=q, use_llm=use_llm, model=model_name,
                ollama_url=ollama_url, stream=use_streaming,
                stage1_model=stage1_model, history=hist[-3:],
            )
        if ans.get('answer_stream'):
            with st.chat_message("assistant"):
                reply_text = st.write_stream(ans['answer_stream'])
            st.session_state[chat_key].append({'role': 'assistant', 'content': reply_text})
        else:
            st.session_state[chat_key].append({
                'role': 'assistant', 'content': ans.get('answer', '답변 없음'),
            })
        st.rerun()


def _render_file_qa_chat(
    fname: str,
    doc_payload: dict,
    all_documents: list,
    chat_key: str,
    input_key: str,
    model_name: str,
    ollama_url: str,
    use_llm: bool,
    use_streaming: bool,
    stage1_model: str,
):
    """Excel 등 편집기 없는 파일 — Q&A 전용 채팅."""
    st.caption("질문 — 이 파일만 분석합니다.")
    with st.container(height=CHAT_SCROLL_HEIGHT, border=False):
        for msg in st.session_state[chat_key]:
            with st.chat_message(msg['role']):
                st.write(msg['content'])
                if msg.get('chart_data') is not None:
                    st.bar_chart(msg['chart_data'])

    q = st.chat_input("질문을 입력하세요...", key=input_key)
    if not q:
        return

    st.session_state[chat_key].append({'role': 'user', 'content': q})
    intent = classify_intent(q)
    if intent != 'qa':
        st.session_state[chat_key].append({
            'role': 'assistant',
            'content': (
                '이 파일 형식은 채팅 편집을 지원하지 않습니다. '
                '왼쪽 **직접 편집** 탭에서 수정하거나, 질문 형태로 요청해 주세요.'
            ),
        })
        st.rerun()

    ref_ctx = get_reference_context()
    qa = get_cached_qa_engine([doc_payload], fname)
    question = q
    if ref_ctx:
        question = f"{q}\n\n[참고자료]\n{ref_ctx[:4000]}"
    hist = []
    msgs = st.session_state[chat_key][:-1]
    for i in range(0, len(msgs) - 1, 2):
        if i + 1 < len(msgs) and msgs[i]['role'] == 'user' and msgs[i + 1]['role'] == 'assistant':
            hist.append({'question': msgs[i]['content'], 'answer': msgs[i + 1]['content']})
    with st.spinner("분석 중..."):
        ans = qa.answer(
            question=question, use_llm=use_llm, model=model_name,
            ollama_url=ollama_url, stream=use_streaming,
            stage1_model=stage1_model, history=hist[-3:],
        )
    chart = ans.get('chart_data')
    if ans.get('answer_stream'):
        with st.chat_message("assistant"):
            reply_text = st.write_stream(ans['answer_stream'])
        st.session_state[chat_key].append({
            'role': 'assistant', 'content': reply_text,
            'chart_data': chart.get('data') if chart else None,
        })
    else:
        st.session_state[chat_key].append({
            'role': 'assistant',
            'content': ans.get('answer', '답변 없음'),
            'chart_data': chart.get('data') if chart else None,
        })
    st.rerun()


def render_file_chat(
    entry: dict,
    all_documents: list,
    *,
    model_name: str,
    stage1_model: str,
    use_llm: bool,
    use_streaming: bool,
    ollama_url: str,
    input_suffix: str = '',
):
    """파일 전용 채팅 — HWPX/HWP/Excel 공통 진입."""
    fname = entry['filename']
    fbytes = entry['file_bytes']
    doc_payload = entry['doc_payload']
    chat_key = f"workspace_chat_{fname}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    ext = os.path.splitext(fname)[1].lower()
    input_key = f"file_chat_{fname}{input_suffix}"

    scoped_docs = [doc_payload]

    if ext == '.hwpx':
        content_hash = hashlib.sha256(fbytes).hexdigest()[:16]
        editor = st.session_state.get(f"editor_{fname}_{content_hash}")
        if editor:
            render_chat_panel(
                fname, editor, scoped_docs, chat_key,
                model_name, stage1_model, use_llm, use_streaming,
                ollama_url=ollama_url,
                source_hwp=st.session_state.get(f"hwp_source_{fname}", ''),
            )
            return

    if ext == '.hwp':
        _render_hwp_chat(
            fname, fbytes, scoped_docs,
            chat_key, input_key,
            model_name, ollama_url, use_llm, use_streaming, stage1_model,
            on_new_bytes=lambda nb, r: set_hwp_working_bytes(fname, nb),
        )
        return

    _render_file_qa_chat(
        fname, doc_payload, scoped_docs,
        chat_key, input_key,
        model_name, ollama_url, use_llm, use_streaming, stage1_model,
    )


def render_unified_chat_column(
    file_entries: list,
    all_documents: list,
    *,
    model_name: str,
    stage1_model: str,
    use_llm: bool,
    use_streaming: bool,
    ollama_url: str,
):
    """오른쪽 통합 채팅 — 이 파일 / 전체."""
    tab_file, tab_all = st.tabs(["💬 이 파일", "💬 전체"])

    with tab_file:
        if len(file_entries) > 1:
            names = [e['filename'] for e in file_entries]
            chat_fname = st.selectbox(
                "채팅할 파일",
                names,
                key="active_file_chat_target",
            )
            entry = next(e for e in file_entries if e['filename'] == chat_fname)
        else:
            entry = file_entries[0]
        render_file_chat(
            entry, all_documents,
            model_name=model_name, stage1_model=stage1_model,
            use_llm=use_llm, use_streaming=use_streaming,
            ollama_url=ollama_url, input_suffix="_pane",
        )

    with tab_all:
        render_workspace_qa_chat(
            all_documents,
            model_name=model_name,
            stage1_model=stage1_model,
            use_llm=use_llm,
            use_streaming=use_streaming,
            ollama_url=ollama_url,
        )


def render_hwp_split_editor(
    fname: str, file_bytes: bytes, all_documents: list,
    model_name: str, ollama_url: str, use_llm: bool,
    use_streaming: bool, stage1_model: str,
    show_chat: bool = True,
):
    """HWP — 기본: diff 미리보기 + 채팅 편집 / 선택: 직접 편집."""
    chat_key = f"workspace_chat_{fname}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    if show_chat:
        col_doc, col_chat = st.columns([3, 2], gap="medium")
    else:
        col_doc = st.container()
        col_chat = None

    def _on_hwp_new_bytes(new_bytes, result):
        set_hwp_working_bytes(fname, new_bytes)
        append_hwp_highlights(fname, result.get('hwp_highlights') or [])

    with col_doc:
        view_mode = st.radio(
            "보기 방식",
            [VIEW_PREVIEW, VIEW_DIRECT],
            horizontal=True,
            key=f"doc_view_hwp_{fname}_{'chat' if show_chat else 'tab'}",
        )

        working_bytes = get_hwp_working_bytes(fname, file_bytes)

        if view_mode == VIEW_PREVIEW:
            st.caption("🟡 AI 제안 · 🔴 적용된 수정 · 🟢 새 내용")
            render_scrollable_doc_preview(get_hwp_preview_html(fname, working_bytes))
            ok, _ = validate_hwp_bytes(working_bytes)
            if ok:
                st.download_button(
                    "📥 수정된 HWP 다운로드",
                    data=working_bytes,
                    file_name=f"{os.path.splitext(fname)[0]}_edited.hwp",
                    mime="application/octet-stream",
                    key=f"dl_hwp_preview_{fname}",
                    use_container_width=True,
                )
        else:
            with render_scrollable_pane():
                render_canvas_editor_hwp(
                    fname, file_bytes,
                    chat_key=chat_key,
                )

    if show_chat and col_chat is not None:
        with col_chat:
            _render_hwp_chat(
                fname, get_hwp_working_bytes(fname, file_bytes), all_documents,
                chat_key, f"hwp_input_{fname}",
                model_name, ollama_url, use_llm, use_streaming, stage1_model,
                on_new_bytes=_on_hwp_new_bytes,
            )


def render_split_editor(
    fname: str,
    file_bytes: bytes,
    all_documents: list,
    model_name: str,
    ollama_url: str,
    use_llm: bool,
    use_streaming: bool,
    stage1_model: str,
    source_hwp: str = '',
    show_chat: bool = True,
):
    content_hash = hashlib.sha256(file_bytes).hexdigest()[:16]
    editor_key = f"editor_{fname}_{content_hash}"
    if editor_key not in st.session_state:
        st.session_state[editor_key] = HWPXEditor(file_bytes)
    editor = st.session_state[editor_key]
    editor._source_filename = fname

    chat_key = f"workspace_chat_{fname}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    pending_list = editor.get_pending_changes()
    pending_n = len(pending_list)
    scroll_key = f"scroll_pending_{fname}"

    bar1, bar2, bar3, bar4 = st.columns([2, 1, 1, 1])
    with bar1:
        st.markdown(f"### 📄 {fname}")
    with bar2:
        st.caption("대기 변경")
        if pending_n == 0:
            st.markdown("**0**")
        elif pending_n == 1:
            ch = pending_list[0]
            if st.button(
                f"**{pending_n}** · 위치로",
                key=f"goto_pending_{fname}_{ch.id}",
                help=format_pending_label(ch),
                use_container_width=True,
            ):
                st.session_state[scroll_key] = ch.id
                st.rerun()
        else:
            st.markdown(f"**{pending_n}**")
            with st.popover("목록 · 위치로", use_container_width=True):
                for ch in pending_list:
                    if st.button(
                        format_pending_label(ch),
                        key=f"goto_{ch.id}_{fname}",
                        use_container_width=True,
                    ):
                        st.session_state[scroll_key] = ch.id
                        st.rerun()
    with bar3:
        if pending_n and st.button("✅ 모두 적용", key=f"apply_{fname}", type="primary", use_container_width=True):
            pending_before = len(editor.get_pending_changes())
            n = editor.accept_all_pending(track_changes=True)
            if not getattr(editor, '_hwpilot_touched', False):
                for t in range(editor.get_table_count()):
                    editor.recalculate_totals(t)
            if n == 0 and pending_before:
                apply_msg = (
                    '⚠️ 변경 적용에 실패했습니다. 표 셀 위치를 확인하거나 '
                    '「표1 8행 5열을 …으로」처럼 좌표로 지정해 보세요.'
                )
            else:
                apply_msg = f'✅ {n}건 변경을 문서에 적용했습니다.'
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': apply_msg,
            })
            sync_export_state(editor, fname, source_hwp=source_hwp)
            _invalidate_hwpx_preview_cache(fname)
            st.session_state[f"cvs_rev_{fname}"] = st.session_state.get(f"cvs_rev_{fname}", 0) + 1
            st.rerun()
    with bar4:
        if pending_n and st.button("❌ 모두 취소", key=f"cancel_{fname}", use_container_width=True):
            editor.reject_all_pending()
            st.session_state[chat_key].append({
                'role': 'assistant', 'content': '변경 제안을 모두 취소했습니다.',
            })
            _invalidate_hwpx_preview_cache(fname)
            st.rerun()

    view_mode = st.radio(
        "보기 방식",
        [VIEW_PREVIEW, VIEW_DIRECT],
        horizontal=True,
        key=f"doc_view_{fname}_{'chat' if show_chat else 'tab'}",
    )

    if show_chat:
        col_doc, col_chat = st.columns([3, 2], gap="medium")
    else:
        col_doc = st.container()
        col_chat = None

    with col_doc:
        if view_mode == VIEW_PREVIEW:
            st.caption("🟡 AI 제안 · 🔴 적용된 수정 · 🟢 새 내용")
            preview_html = get_cached_preview_html(editor, fname)
            render_scrollable_doc_preview(preview_html)
            render_hwpx_download(fname, editor, source_hwp=source_hwp)
        else:
            with render_scrollable_pane():
                render_canvas_editor(
                    fname, editor,
                    source_hwp=source_hwp,
                    chat_key=chat_key,
                )

    if show_chat and col_chat is not None:
        with col_chat:
            render_chat_panel(
                fname, editor, all_documents, chat_key,
                model_name, stage1_model, use_llm, use_streaming,
                ollama_url=ollama_url,
                source_hwp=source_hwp,
                canvas_mode=(view_mode == VIEW_DIRECT),
            )


def try_open_hwp_for_editing(filename: str, file_bytes: bytes) -> tuple[Optional[bytes], Optional[str], str]:
    """.hwp → 편집 가능한 hwpx bytes. (bytes, display_name, note)"""
    if get_backend_status().hwpilot:
        hwpx = hwpilot_convert_to_hwpx(file_bytes, filename)
        if hwpx:
            base = os.path.splitext(filename)[0]
            return hwpx, f"{base}.hwpx", "hwpilot 변환"
    return None, None, "HWPX 변환 실패 — hwpilot build 확인 (cd hwpilot && npm run build)"


def render_readonly_split(
    fname: str, doc_data: dict, file_bytes: bytes,
    model_name: str, ollama_url: str, use_llm: bool,
    use_streaming: bool, stage1_model: str,
    show_chat: bool = True,
):
    chat_key = f"workspace_chat_{fname}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    tables_raw = [t.get('rows', []) for t in doc_data['doc'].tables_raw]
    preview_html = build_preview_from_text(doc_data['paragraphs'], tables_raw, filename=fname)

    if show_chat:
        col_doc, col_chat = st.columns([3, 2])
    else:
        col_doc = st.container()
        col_chat = None
    with col_doc:
        st.caption("문서 미리보기 (읽기 전용)")
        render_scrollable_doc_preview(preview_html)
        status = get_backend_status()
        if fname.lower().endswith('.hwp'):
            st.info(f"파서: {doc_data['doc'].file_type or 'hwp'} | 백엔드: {status.summary()}")
            if status.hwpilot:
                if st.button("HWPX로 변환하여 편집 시작", key=f"conv_{fname}", use_container_width=True):
                    hwpx_bytes, new_name, note = try_open_hwp_for_editing(fname, file_bytes)
                    if hwpx_bytes and new_name:
                        st.session_state[f"upload_bytes_{new_name}"] = hwpx_bytes
                        st.session_state[f"hwp_source_{new_name}"] = fname
                        st.session_state.pop(f"parsed_{fname}_{len(file_bytes)}", None)
                        st.success(f"{note} 완료 — {new_name}")
                        st.rerun()
                    else:
                        st.error(note)
            else:
                st.caption("편집: `npm install -g hwpilot` 후 HWPX 변환 가능")

    def _on_ro_new_bytes(new_bytes, result):
        bytes_key = f"upload_bytes_{fname}"
        st.session_state[bytes_key] = new_bytes
        for k in list(st.session_state.keys()):
            if k.startswith(f"parsed_{fname}_") or k.startswith(f"qa_engine_{fname}"):
                del st.session_state[k]

    if show_chat and col_chat is not None:
        with col_chat:
            _render_hwp_chat(
                fname, file_bytes, [doc_data],
                chat_key, f"ro_input_{fname}",
                model_name, ollama_url, use_llm, use_streaming, stage1_model,
                on_new_bytes=_on_ro_new_bytes,
            )
            bytes_key = f"upload_bytes_{fname}"
            current_bytes = st.session_state.get(bytes_key, file_bytes)
            if current_bytes is not file_bytes:
                st.download_button(
                    "📥 수정된 HWP 다운로드",
                    data=current_bytes,
                    file_name=f"{os.path.splitext(fname)[0]}_edited.hwp",
                    mime="application/octet-stream",
                    key=f"dl_hwp_{fname}",
                    use_container_width=True,
                )


# --- 사이드바 ---
with st.sidebar:
    st.header("⚙️ 설정")
    ollama_url = st.text_input("Ollama URL", value="http://localhost:11434", key="sidebar_ollama_url")
    ollama_status = get_cached_ollama_status(ollama_url)

    if ollama_status['status'] == 'running':
        st.success("Ollama 연결됨")
        available_models = ollama_status['models']
        if available_models:
            gemma4_models = [m for m in available_models if 'gemma4' in m]
            sorted_models = gemma4_models + [m for m in available_models if m not in gemma4_models]
            model_name = st.selectbox("모델", sorted_models, index=0)
        else:
            model_name = st.text_input("모델", value="gemma4", key="sidebar_model_name")
    else:
        st.warning("Ollama 미연결")
        model_name = "gemma4"
        available_models = []

    use_llm = ollama_status['status'] == 'running'
    use_streaming = use_llm

    if use_llm and available_models:
        small_models = [m for m in available_models
                        if any(t in m for t in ['gemma3', 'qwen', 'phi4', 'gemma2'])] or available_models
        stage1_model = st.selectbox("의도 분석", small_models, index=0)
    else:
        stage1_model = "gemma3:4b"

# --- 메인 ---
st.title("HWP Analyzer")

uploaded_files = st.file_uploader(
    "문서를 업로드하세요 (HWP/HWPX/XLSX)",
    type=["hwp", "hwpx", "xlsx", "xls"],
    accept_multiple_files=True,
)

if uploaded_files:
    uploaded_list = uploaded_files if isinstance(uploaded_files, list) else [uploaded_files]
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
                if k.startswith(f"parsed_{filename}_") or k.startswith(f"editor_{filename}_"):
                    del st.session_state[k]
                if k in (
                    f"dl_ready_{filename}", f"export_bytes_{filename}",
                    f"hwp_working_{filename}", f"hwp_highlights_{filename}",
                ):
                    del st.session_state[k]
        else:
            file_bytes = st.session_state[bytes_key]

        cache_key = f"parsed_{filename}_{len(file_bytes)}"
        if cache_key not in st.session_state:
            with st.spinner(f"{filename} 분석 중..."):
                doc, tables, tnums, tblnums = process_document(file_bytes, filename)
                intel = build_intelligence(
                    paragraphs=doc.paragraphs,
                    tables=tables,
                    text_numbers=tnums,
                    table_numbers=tblnums,
                    document_id=filename,
                )
            st.session_state[cache_key] = {
                'doc': doc, 'tables': tables,
                'text_numbers': tnums, 'table_numbers': tblnums,
                'intel': intel,
            }

        cached = st.session_state[cache_key]
        doc_payload = {
            'id': filename,
            'paragraphs': cached['doc'].paragraphs,
            'tables': cached['tables'],
            'text_numbers': cached['text_numbers'],
            'table_numbers': cached['table_numbers'],
            'doc': cached['doc'],
            'intel': cached.get('intel'),
        }
        all_documents.append(doc_payload)
        file_entries.append({
            'filename': filename,
            'file_bytes': file_bytes,
            'doc_payload': doc_payload,
        })

        for err in cached['doc'].errors:
            st.warning(f"[{filename}] {err}")

    workspace_intel = build_workspace_intelligence(all_documents)
    render_workspace_intel(workspace_intel)

    n_files = len(file_entries)
    st.caption(
        f"{'단일' if n_files == 1 else '다중'} 파일 · {n_files}개 · {get_backend_status().summary()}"
    )

    for entry in file_entries:
        dp = entry['doc_payload']
        if not dp['paragraphs'] and not dp['tables']:
            st.warning(f"[{entry['filename']}] 문단·표를 추출하지 못했습니다.")

    col_doc, col_chat = st.columns([3, 2], gap="medium")

    with col_doc:
        if n_files == 1:
            entry = file_entries[0]
            if entry['doc_payload'].get('intel'):
                render_intel_review(entry['doc_payload']['intel'])
            render_document_pane(
                entry, all_documents,
                model_name=model_name, ollama_url=ollama_url,
                use_llm=use_llm, use_streaming=use_streaming,
                stage1_model=stage1_model,
                render_hwp_split_editor=render_hwp_split_editor,
                render_split_editor=render_split_editor,
                render_excel_split_editor_fn=render_excel_split_editor,
                render_scrollable_doc_preview=render_scrollable_doc_preview,
                render_scrollable_pane=render_scrollable_pane,
            )
        else:
            tabs = st.tabs([e['filename'] for e in file_entries])
            for tab, entry in zip(tabs, file_entries):
                with tab:
                    render_document_pane(
                        entry, all_documents,
                        model_name=model_name, ollama_url=ollama_url,
                        use_llm=use_llm, use_streaming=use_streaming,
                        stage1_model=stage1_model,
                        render_hwp_split_editor=render_hwp_split_editor,
                        render_split_editor=render_split_editor,
                        render_excel_split_editor_fn=render_excel_split_editor,
                        render_scrollable_doc_preview=render_scrollable_doc_preview,
                        render_scrollable_pane=render_scrollable_pane,
                    )

    with col_chat:
        render_unified_chat_column(
            file_entries, all_documents,
            model_name=model_name, stage1_model=stage1_model,
            use_llm=use_llm, use_streaming=use_streaming,
            ollama_url=ollama_url,
        )

else:
    st.info("HWP/HWPX/XLSX 파일을 업로드하세요.")
