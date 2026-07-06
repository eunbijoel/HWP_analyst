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
import streamlit as st
import streamlit.components.v1 as components
import requests

from hwp_core.hwp_parser import parse_document
from hwp_core.hwp_backends import get_backend_status, hwpilot_convert_to_hwpx, hwpilot_read_structure
from hwp_core.table_extractor import extract_tables, detect_numbers_in_text, detect_numbers_in_tables
from hwp_core.qa_engine import QAEngine, check_ollama_status
from hwp_core.hwpx_editor import HWPXEditor
from additional.reference_parser import parse_reference, build_reference_context
from ui.document_preview import build_preview_html, build_preview_from_text
from ui.command_router import classify_intent, execute_edit_command


st.set_page_config(page_title="HWP 문서 분석기", page_icon="📄", layout="wide")
GENERAL_KNOWLEDGE_RE = re.compile(
    r'필요성|중요성|배경|목적|기대효과|의미|정의|개념|요약|정리|bullet|불릿|장점|단점',
    re.I,
)


def get_reference_context() -> str:
    refs = st.session_state.get('reference_docs', [])
    return build_reference_context(refs) if refs else ''


def get_cached_preview_html(editor: HWPXEditor, filename: str) -> str:
    key = f"preview_html_{filename}"
    entry = st.session_state.get(key)
    if entry and entry.get('rev') == editor.preview_revision:
        return entry['html']
    html = build_preview_html(editor, filename=filename)
    st.session_state[key] = {'rev': editor.preview_revision, 'html': html}
    return html


def get_cached_ollama_status(url: str) -> dict:
    cache = st.session_state.get('ollama_cache', {})
    if cache.get('url') == url and time.time() - cache.get('ts', 0) < 30:
        return cache['status']
    status = check_ollama_status(url)
    st.session_state['ollama_cache'] = {'url': url, 'status': status, 'ts': time.time()}
    return status


def get_cached_qa_engine(all_documents: list, filename: str) -> QAEngine:
    d = all_documents[0] if all_documents else {}
    sig = f"{len(d.get('paragraphs', []))}_{len(d.get('tables', []))}"
    key = f"qa_engine_{filename}_{sig}"
    if key not in st.session_state:
        if len(all_documents) == 1:
            st.session_state[key] = QAEngine(
                paragraphs=d['paragraphs'], table_summaries=d['tables'],
                text_numbers=d['text_numbers'], table_numbers=d['table_numbers'],
            )
        else:
            st.session_state[key] = QAEngine(documents=all_documents)
    return st.session_state[key]


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
    doc = parse_document(file_bytes=file_bytes, filename=filename)
    tables = extract_tables(doc, document_id=filename)
    tnums = detect_numbers_in_text(doc.full_text, document_id=filename)
    tblnums = detect_numbers_in_tables(tables, document_id=filename)
    return doc, tables, tnums, tblnums


def sync_export_state(editor: HWPXEditor, hwpx_name: str, source_hwp: str = ''):
    """편집 후 다운로드용 bytes를 세션에 동기화."""
    export = editor.get_export_bytes()
    st.session_state[f"export_bytes_{hwpx_name}"] = export
    if source_hwp:
        auto_key = f"auto_hwpx_{source_hwp}"
        if auto_key in st.session_state:
            st.session_state[auto_key]['bytes'] = export


def get_hwp_working_bytes(fname: str, initial: bytes) -> bytes:
    key = f"hwp_working_{fname}"
    if key not in st.session_state:
        st.session_state[key] = initial
    return st.session_state[key]


def set_hwp_working_bytes(fname: str, data: bytes):
    st.session_state[f"hwp_working_{fname}"] = data
    st.session_state[f"upload_bytes_{fname}"] = data
    for k in list(st.session_state.keys()):
        if k.startswith(f"parsed_{fname}_") or k.startswith(f"hwp_preview_{fname}_"):
            del st.session_state[k]


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


def validate_hwp_bytes(data: bytes) -> tuple[bool, str]:
    if not isinstance(data, (bytes, bytearray)) or len(data) < 8:
        return False, '파일 데이터가 비어 있습니다.'
    if data[:4] != b'\xd0\xcf\x11\xe0':
        return False, 'HWP(OLE) 형식이 아닙니다.'
    return True, ''


def answer_general_question(
    question: str,
    model: str,
    ollama_url: str,
    use_streaming: bool,
) -> dict:
    """문서 근거형 QA 대신 일반 LLM 답변."""
    prompt = f"""다음 질문에 한국어로 간결하고 실무적으로 답하세요.
요청이 bullet point 형식이면 해당 형식으로 답하세요.

질문:
{question}
"""
    try:
        if use_streaming:
            response = requests.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": True,
                    "options": {"temperature": 0.4, "num_predict": 1200, "num_ctx": 16384},
                },
                timeout=120,
                stream=True,
            )
            if response.status_code != 200:
                return {"answer": f"LLM 오류 (HTTP {response.status_code})"}

            def token_generator():
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = line.decode("utf-8", errors="ignore")
                        if chunk.startswith("{"):
                            import json
                            token = json.loads(chunk).get("response", "")
                            if token:
                                yield token
                    except Exception:
                        continue

            return {"answer_stream": token_generator()}

        response = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.4, "num_predict": 1200, "num_ctx": 16384},
            },
            timeout=120,
        )
        if response.status_code != 200:
            return {"answer": f"LLM 오류 (HTTP {response.status_code})"}
        text = response.json().get("response", "").strip()
        return {"answer": text or "답변 생성 실패"}
    except Exception as e:
        return {"answer": f"일반 답변 생성 실패: {e}"}


def render_chat_panel(
    fname: str,
    editor: HWPXEditor,
    all_documents: list,
    chat_key: str,
    model_name: str,
    stage1_model: str,
    use_llm: bool,
    use_streaming: bool,
    source_hwp: str = '',
):
    st.caption("명령 / 질문 — 예: *빈칸 채워줘*, *초안 작성해줘*, *총 사업비는?*")

    sel_key = f"selection_{fname}"
    if sel_key not in st.session_state:
        st.session_state[sel_key] = ''
    if st.session_state.get(f"show_para_{fname}"):
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
    else:
        if st.button("편집 대상 문단 선택", key=f"para_btn_{fname}", use_container_width=True):
            st.session_state[f"show_para_{fname}"] = True
            st.rerun()

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

        if intent != 'qa' and use_llm:
            with st.spinner("AI 편집 중..."):
                result = execute_edit_command(
                    editor, user_input, ref_ctx,
                    model_name, ollama_url, selection_text=selection,
                    chat_history=st.session_state[chat_key][:-1],
                    source_filename=fname,
                )
            reply = result.get('message', '완료')
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
            if result.get('applied_direct') or result.get('changes', 0) > 0:
                sync_export_state(editor, fname, source_hwp=source_hwp)
            st.rerun()
        else:
            general_mode = use_llm and bool(GENERAL_KNOWLEDGE_RE.search(user_input))
            if general_mode:
                with st.spinner("일반 답변 생성 중..."):
                    ans = answer_general_question(
                        question=user_input,
                        model=model_name,
                        ollama_url=ollama_url,
                        use_streaming=use_streaming and use_llm,
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
                        ollama_url=ollama_url, stream=use_streaming and use_llm,
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

    if st.session_state[chat_key] and st.button("대화 초기화", key=f"clr_{fname}"):
        st.session_state[chat_key] = []
        st.rerun()


try:
    render_chat_panel = st.fragment(render_chat_panel)
except AttributeError:
    pass


def _hwp_edit_needs_llm(intent: str) -> bool:
    """hwpilot 직접 편집(insert/delete/replace)은 LLM 없이 가능."""
    return intent in ('draft', 'fill', 'rewrite')


def render_hwp_split_editor(fname: str, file_bytes: bytes, all_documents: list):
    """HWP 원본을 hwpilot으로 직접 편집·다운로드 (HWPX 변환 없음 — 한글 호환)."""
    hwp_bytes = get_hwp_working_bytes(fname, file_bytes)
    chat_key = f"workspace_chat_{fname}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    col_doc, col_chat = st.columns([3, 2], gap="medium")

    with col_chat:
        st.caption("명령 / 질문 — 예: *9줄 A를 B로 바꿔줘*, *14줄,15줄 삭제해*, *총 사업비는?*")
        for msg in st.session_state[chat_key]:
            with st.chat_message(msg['role']):
                st.write(msg['content'])
        q = st.chat_input("명령 또는 질문을 입력하세요...", key=f"hwp_input_{fname}")
        if q:
            st.session_state[chat_key].append({'role': 'user', 'content': q})
            intent = classify_intent(q)
            ref_ctx = get_reference_context()
            if intent != 'qa' and get_backend_status().hwpilot and (not _hwp_edit_needs_llm(intent) or use_llm):
                with st.spinner("HWP 편집 중..."):
                    result = execute_edit_command(
                        None, q, ref_ctx, model_name, ollama_url,
                        chat_history=st.session_state[chat_key][:-1],
                        source_filename=fname,
                        file_bytes=hwp_bytes,
                    )
                reply = result.get('message', '완료')
                new_bytes = result.get('new_file_bytes')
                if new_bytes:
                    set_hwp_working_bytes(fname, new_bytes)
                    append_hwp_highlights(fname, result.get('hwp_highlights') or [])
                    hwp_bytes = new_bytes
                    reply += "\n\n👉 **hwpilot**으로 HWP에 반영되었습니다. 왼쪽 **빨간색** 수정 표시·다운로드를 확인하세요."
                elif result.get('applied_direct'):
                    reply += "\n\n👉 문서에 반영되었습니다."
                elif result.get('changes', 0) == 0 and _hwp_edit_needs_llm(intent) and not use_llm:
                    reply += "\n\n⚠️ 초안/빈칸 채우기는 사이드바 **LLM 사용**을 켜야 합니다."
                st.session_state[chat_key].append({'role': 'assistant', 'content': reply})
                st.rerun()
            elif intent != 'qa' and _hwp_edit_needs_llm(intent) and not use_llm:
                st.session_state[chat_key].append({
                    'role': 'assistant',
                    'content': '초안·빈칸 채우기는 Ollama 연결 후 **LLM 사용**을 켜 주세요. '
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
                        ollama_url=ollama_url, stream=use_streaming and use_llm,
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

    with col_doc:
        st.markdown(f"### 📄 {fname}")
        st.caption("HWP 직접 편집 모드 — 🔴 적용된 수정 표시 · 다운로드 **.hwp**")
        hwp_bytes = get_hwp_working_bytes(fname, file_bytes)
        components.html(get_hwp_preview_html(fname, hwp_bytes), height=720, scrolling=True)
        base = os.path.splitext(fname)[0]
        ok, err = validate_hwp_bytes(hwp_bytes)
        if ok:
            st.caption(f"파일 크기: {len(hwp_bytes):,} bytes")
            st.download_button(
                "📥 수정본 HWP 다운로드",
                data=bytes(hwp_bytes),
                file_name=f"{base}_edited.hwp",
                mime="application/octet-stream",
                key=f"dl_hwp_btn_{fname}",
                use_container_width=True,
            )
        else:
            st.error(f"다운로드 불가: {err}")


def render_split_editor(
    fname: str,
    file_bytes: bytes,
    all_documents: list,
    source_hwp: str = '',
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

    pending_n = len(editor.get_pending_changes())

    bar1, bar2, bar3, bar4 = st.columns([2, 1, 1, 1])
    with bar1:
        st.markdown(f"### 📄 {fname}")
    with bar2:
        st.metric("대기 변경", pending_n)
    with bar3:
        if pending_n and st.button("✅ 모두 적용", key=f"apply_{fname}", type="primary", use_container_width=True):
            n = editor.accept_all_pending(track_changes=True)
            if not getattr(editor, '_hwpilot_touched', False):
                for t in range(editor.get_table_count()):
                    editor.recalculate_totals(t)
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': f'✅ {n}건 변경을 문서에 적용했습니다. 왼쪽에서 **빨간색**으로 확인하세요.',
            })
            sync_export_state(editor, fname, source_hwp=source_hwp)
            st.rerun()
    with bar4:
        if pending_n and st.button("❌ 모두 취소", key=f"cancel_{fname}", use_container_width=True):
            editor.reject_all_pending()
            st.session_state[chat_key].append({
                'role': 'assistant', 'content': '변경 제안을 모두 취소했습니다.',
            })
            st.rerun()

    col_doc, col_chat = st.columns([3, 2], gap="medium")

    # 채팅 패널을 먼저 렌더링 — 미리보기 HTML/ZIP 생성이 채팅 표시를 막지 않도록
    with col_chat:
        render_chat_panel(
            fname, editor, all_documents, chat_key,
            model_name, stage1_model, use_llm, use_streaming,
            source_hwp=source_hwp,
        )

    with col_doc:
        st.caption("문서 미리보기 — 🟡 AI 제안  🔴 적용된 수정  🟢 새 내용")
        components.html(get_cached_preview_html(editor, fname), height=720, scrolling=True)
        base = os.path.splitext(fname)[0]
        dl_ready_key = f"dl_ready_{fname}"
        if st.button("📥 다운로드 준비", key=f"prep_{fname}", use_container_width=True):
            sync_export_state(editor, fname, source_hwp=source_hwp)
            export = editor.get_export_bytes()
            ok, err = HWPXEditor.validate_hwpx_bytes(export)
            if ok:
                st.session_state[dl_ready_key] = True
            else:
                st.error(f"다운로드 준비 실패: {err}")
            st.rerun()
        if st.session_state.get(dl_ready_key):
            dl_data = editor.get_export_bytes()
            ok, err = HWPXEditor.validate_hwpx_bytes(dl_data)
            if not ok:
                st.error(f"다운로드 파일 오류: {err}. 문서를 다시 업로드해 주세요.")
                dl_data = None
        else:
            dl_data = None
        if dl_data:
            if source_hwp:
                st.info(
                    f"원본 **{source_hwp}** → 편집본은 **HWPX** 형식입니다. "
                    "한글에서 **파일 열기**로 여세요. 확장자를 `.hwp`로 바꾸지 마세요."
                )
            st.caption(f"파일 크기: {len(dl_data):,} bytes")
            st.download_button(
                "📥 수정된 HWPX 다운로드",
                data=bytes(dl_data),
                file_name=f"{base}_edited.hwpx",
                mime="application/vnd.hancom.hwpx",
                key=f"dl_btn_{fname}",
                use_container_width=True,
            )


def try_open_hwp_for_editing(filename: str, file_bytes: bytes) -> tuple[Optional[bytes], Optional[str], str]:
    """.hwp → 편집 가능한 hwpx bytes. (bytes, display_name, note)"""
    if get_backend_status().hwpilot:
        hwpx = hwpilot_convert_to_hwpx(file_bytes, filename)
        if hwpx:
            base = os.path.splitext(filename)[0]
            return hwpx, f"{base}.hwpx", "hwpilot 변환"
    return None, None, "HWPX 변환 실패 — hwpilot build 확인 (cd hwpilot && npm run build)"


def render_readonly_split(fname: str, doc_data: dict, file_bytes: bytes):
    chat_key = f"workspace_chat_{fname}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    tables_raw = [t.get('rows', []) for t in doc_data['doc'].tables_raw]
    preview_html = build_preview_from_text(doc_data['paragraphs'], tables_raw, filename=fname)

    col_doc, col_chat = st.columns([3, 2])
    with col_doc:
        st.caption("문서 미리보기 (읽기 전용)")
        components.html(preview_html, height=720, scrolling=True)
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
    with col_chat:
        st.caption("질문 · 편집: 「…마지막에 추가해줘」 (hwpilot)")
        qa = get_cached_qa_engine([doc_data], fname)
        for msg in st.session_state[chat_key]:
            with st.chat_message(msg['role']):
                st.write(msg['content'])
        q = st.chat_input("질문 또는 편집 명령...", key=f"ro_input_{fname}")
        if q:
            st.session_state[chat_key].append({'role': 'user', 'content': q})
            intent = classify_intent(q)
            ref_ctx = get_reference_context()
            if intent != 'qa' and get_backend_status().hwpilot and (intent in ('insert', 'delete', 'replace') or use_llm):
                with st.spinner("문서 편집 중..."):
                    result = execute_edit_command(
                        None, q, ref_ctx, model_name, ollama_url,
                        chat_history=st.session_state[chat_key][:-1],
                        source_filename=fname,
                        file_bytes=file_bytes,
                    )
                reply = result.get('message', '완료')
                new_bytes = result.get('new_file_bytes')
                if new_bytes:
                    bytes_key = f"upload_bytes_{fname}"
                    st.session_state[bytes_key] = new_bytes
                    for k in list(st.session_state.keys()):
                        if k.startswith(f"parsed_{fname}_") or k.startswith(f"qa_engine_{fname}"):
                            del st.session_state[k]
                    reply += "\n\n👉 왼쪽 미리보기 갱신 · 아래 **수정 HWP 다운로드**"
                st.session_state[chat_key].append({'role': 'assistant', 'content': reply})
                st.rerun()
            else:
                ans = qa.answer(
                    q, use_llm=use_llm, model=model_name,
                    ollama_url=ollama_url, stage1_model=stage1_model,
                    stream=use_streaming and use_llm,
                )
                if ans.get('answer_stream'):
                    with st.chat_message("assistant"):
                        reply_text = st.write_stream(ans['answer_stream'])
                    st.session_state[chat_key].append({
                        'role': 'assistant', 'content': reply_text,
                    })
                else:
                    st.session_state[chat_key].append({
                        'role': 'assistant', 'content': ans.get('answer', ''),
                    })
                st.rerun()

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
    ollama_url = st.text_input("Ollama URL", value="http://localhost:11434")
    ollama_status = get_cached_ollama_status(ollama_url)

    if ollama_status['status'] == 'running':
        st.success("Ollama 연결됨")
        available_models = ollama_status['models']
        if available_models:
            gemma4_models = [m for m in available_models if 'gemma4' in m]
            sorted_models = gemma4_models + [m for m in available_models if m not in gemma4_models]
            model_name = st.selectbox("모델", sorted_models, index=0)
        else:
            model_name = st.text_input("모델", value="gemma4")
    else:
        st.warning("Ollama 미연결")
        model_name = "gemma4"
        available_models = []

    use_llm = st.checkbox("LLM 사용", value=ollama_status['status'] == 'running',
                          disabled=ollama_status['status'] != 'running')
    use_streaming = st.checkbox("스트리밍", value=True, disabled=not use_llm)

    if use_llm and available_models:
        small_models = [m for m in available_models
                        if any(t in m for t in ['gemma3', 'qwen', 'phi4', 'gemma2'])] or available_models
        stage1_model = st.selectbox("의도 분석", small_models, index=0)
    else:
        stage1_model = "gemma3:4b"

    st.divider()
    st.caption("참고자료: PDF·DOCX·XLSX·TXT")
    ref_files = st.file_uploader(
        "참고 자료", type=["pdf", "docx", "xlsx", "txt", "hwp", "hwpx"],
        accept_multiple_files=True, key="ref_uploader", label_visibility="collapsed",
    )
    if ref_files:
        if 'reference_docs' not in st.session_state:
            st.session_state.reference_docs = []
        for rf in ref_files:
            rkey = f"ref_{rf.name}_{rf.size}"
            if rkey not in st.session_state:
                st.session_state.reference_docs.append(parse_reference(rf.read(), rf.name))
                st.session_state[rkey] = True
        st.caption(f"참고자료 {len(st.session_state.reference_docs)}개 로드됨")


# --- 메인 ---
st.title("한글 문서 분석기")

uploaded_files = st.file_uploader(
    "한글 문서를 업로드하세요",
    type=["hwp", "hwpx"],
    accept_multiple_files=False,
)

if uploaded_files:
    uf = uploaded_files[0] if isinstance(uploaded_files, list) else uploaded_files
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
            if k in (f"dl_ready_{filename}", f"export_bytes_{filename}", f"hwp_working_{filename}", f"hwp_highlights_{filename}"):
                del st.session_state[k]
    else:
        file_bytes = st.session_state[bytes_key]

    cache_key = f"parsed_{filename}_{len(file_bytes)}"
    if cache_key not in st.session_state:
        with st.spinner("문서 분석 중..."):
            doc, tables, tnums, tblnums = process_document(file_bytes, filename)
        st.session_state[cache_key] = {
            'doc': doc, 'tables': tables,
            'text_numbers': tnums, 'table_numbers': tblnums,
        }

    cached = st.session_state[cache_key]
    doc_payload = {
        'id': filename,
        'paragraphs': cached['doc'].paragraphs,
        'tables': cached['tables'],
        'text_numbers': cached['text_numbers'],
        'table_numbers': cached['table_numbers'],
        'doc': cached['doc'],
    }

    edit_name = filename
    edit_bytes = file_bytes

    all_documents = [doc_payload]

    for err in cached['doc'].errors:
        st.warning(err)

    parser_tag = cached['doc'].file_type or 'unknown'
    note = doc_payload.get('parser_note', '')
    st.caption(f"파서: {parser_tag}{(' · ' + note) if note else ''} · {get_backend_status().summary()}")
    st.caption(f"추출: 문단 {len(doc_payload['paragraphs'])}개, 표 {len(doc_payload['tables'])}개")

    if not doc_payload['paragraphs'] and not doc_payload['tables']:
        st.error("문서에서 문단·표를 추출하지 못했습니다. pyhwp/hwpilot 설치 및 파일 형식을 확인하세요.")

    if filename.lower().endswith('.hwp') and get_backend_status().hwpilot:
        render_hwp_split_editor(filename, file_bytes, all_documents)
    elif edit_name.lower().endswith('.hwpx'):
        with st.expander("📋 hwpilot 문서 구조 (표·문단)", expanded=False):
            if get_backend_status().hwpilot:
                struct = hwpilot_read_structure(edit_bytes, edit_name)
                if struct:
                    for si, sec in enumerate(struct.get('sections', [])[:2]):
                        st.markdown(f"**섹션 {si}** — 문단 {len(sec.get('paragraphs', []))}개, 표 {len(sec.get('tables', []))}개")
                        for p in sec.get('paragraphs', [])[:8]:
                            txt = p.get('text') or ''.join(
                                r.get('text', '') for r in p.get('runs', []) if isinstance(r, dict))
                            if txt.strip():
                                st.text(f"  · {txt[:120]}")
                else:
                    st.caption("hwpilot read 실패")
            else:
                st.caption("hwpilot 미설치")
        render_split_editor(edit_name, edit_bytes, all_documents)
    else:
        render_readonly_split(filename, all_documents[0], file_bytes)

else:
    st.info("HWP/HWPX를 업로드하세요.")
