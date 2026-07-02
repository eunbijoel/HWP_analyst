"""
HWP 문서 분석기 - 2분할 inline AI 스타일 UI
왼쪽: 문서 미리보기 (변경 색상 표시)  |  오른쪽: 채팅/명령
"""

import sys
import os
import time
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwp_parser import parse_document
from table_extractor import extract_tables, detect_numbers_in_text, detect_numbers_in_tables
from qa_engine import QAEngine, check_ollama_status
from hwpx_editor import HWPXEditor
from reference_parser import parse_reference, build_reference_context
from document_preview import build_preview_html, build_preview_from_text
from command_router import classify_intent, execute_edit_command


st.set_page_config(page_title="HWP 문서 분석기", page_icon="📄", layout="wide")


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
    key = f"qa_engine_{filename}"
    if key not in st.session_state:
        if len(all_documents) == 1:
            d = all_documents[0]
            st.session_state[key] = QAEngine(
                paragraphs=d['paragraphs'], table_summaries=d['tables'],
                text_numbers=d['text_numbers'], table_numbers=d['table_numbers'],
            )
        else:
            st.session_state[key] = QAEngine(documents=all_documents)
    return st.session_state[key]


def process_document(file_bytes: bytes, filename: str):
    doc = parse_document(file_bytes=file_bytes, filename=filename)
    tables = extract_tables(doc, document_id=filename)
    tnums = detect_numbers_in_text(doc.full_text, document_id=filename)
    tblnums = detect_numbers_in_tables(tables, document_id=filename)
    return doc, tables, tnums, tblnums


def render_chat_panel(
    fname: str,
    editor: HWPXEditor,
    all_documents: list,
    chat_key: str,
    model_name: str,
    stage1_model: str,
    use_llm: bool,
    use_streaming: bool,
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
                )
            reply = result.get('message', '완료')
            if result.get('changes', 0) > 0:
                reply += "\n\n👉 왼쪽 문서에서 **노란색(제안)** 으로 확인하세요. 맞으면 **「모두 적용」** → **빨간색**으로 확정됩니다."
            st.session_state[chat_key].append({'role': 'assistant', 'content': reply})
            st.rerun()
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


def render_split_editor(fname: str, file_bytes: bytes, all_documents: list):
    editor_key = f"editor_{fname}"
    if editor_key not in st.session_state:
        st.session_state[editor_key] = HWPXEditor(file_bytes)
    editor = st.session_state[editor_key]

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
            for t in range(editor.get_table_count()):
                editor.recalculate_totals(t)
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': f'✅ {n}건 변경을 문서에 적용했습니다. 왼쪽에서 **빨간색**으로 확인하세요.',
            })
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
        )

    with col_doc:
        st.caption("문서 미리보기 — 🟡 AI 제안  🔴 적용된 수정  🟢 새 내용")
        components.html(get_cached_preview_html(editor, fname), height=720, scrolling=True)
        base = os.path.splitext(fname)[0]
        dl_key = f"dl_{fname}"
        if st.session_state.get(dl_key):
            dl_data = editor.get_saved_bytes()
        else:
            dl_data = None
        if st.button("📥 다운로드 준비", key=f"prep_{fname}", use_container_width=True):
            st.session_state[dl_key] = True
            st.rerun()
        if dl_data:
            st.download_button(
                "📥 수정된 HWPX 다운로드",
                data=dl_data,
                file_name=f"{base}_edited.hwpx",
                mime="application/octet-stream",
                key=dl_key,
                use_container_width=True,
            )


def render_readonly_split(fname: str, doc_data: dict):
    chat_key = f"workspace_chat_{fname}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    tables_raw = [t.get('rows', []) for t in doc_data['doc'].tables_raw]
    preview_html = build_preview_from_text(doc_data['paragraphs'], tables_raw, filename=fname)

    col_doc, col_chat = st.columns([3, 2])
    with col_doc:
        st.caption("문서 미리보기 (읽기 전용)")
        components.html(preview_html, height=720, scrolling=True)
    with col_chat:
        st.caption("질문만 가능 (HWPX로 변환하면 편집 지원)")
        qa = get_cached_qa_engine([doc_data], fname)
        for msg in st.session_state[chat_key]:
            with st.chat_message(msg['role']):
                st.write(msg['content'])
        q = st.chat_input("질문하세요...", key=f"ro_input_{fname}")
        if q:
            st.session_state[chat_key].append({'role': 'user', 'content': q})
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
    if bytes_key not in st.session_state:
        st.session_state[bytes_key] = uf.read()
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
    all_documents = [{
        'id': filename,
        'paragraphs': cached['doc'].paragraphs,
        'tables': cached['tables'],
        'text_numbers': cached['text_numbers'],
        'table_numbers': cached['table_numbers'],
        'doc': cached['doc'],
    }]

    for err in cached['doc'].errors:
        st.warning(err)

    if filename.lower().endswith('.hwpx'):
        render_split_editor(filename, file_bytes, all_documents)
    else:
        render_readonly_split(filename, all_documents[0])

else:
    st.info("HWP/HWPX를 업로드하세요.")
