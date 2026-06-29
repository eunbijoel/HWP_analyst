"""
HWP 문서 분석기 - Streamlit UI
파일 업로드 → 바로 질의응답
"""

import sys
import os
import streamlit as st
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwp_parser import parse_document
from table_extractor import extract_tables, detect_numbers_in_text, detect_numbers_in_tables
from qa_engine import QAEngine, check_ollama_status


st.set_page_config(page_title="HWP 문서 분석기", page_icon="📄", layout="wide")
st.title("한글 문서 분석기")
st.caption("HWP/HWPX 파일을 업로드하고 바로 질문하세요")


# --- 사이드바 ---
with st.sidebar:
    st.header("설정")

    ollama_url = st.text_input("Ollama URL", value="http://localhost:11434")
    ollama_status = check_ollama_status(ollama_url)

    if ollama_status['status'] == 'running':
        st.success("Ollama 연결됨")
        available_models = ollama_status['models']
        if available_models:
            gemma4_models = [m for m in available_models if 'gemma4' in m]
            sorted_models = gemma4_models + [m for m in available_models if m not in gemma4_models]
            model_name = st.selectbox("LLM 모델", sorted_models, index=0)
        else:
            model_name = st.text_input("모델 이름", value="gemma4")
            st.warning("설치된 모델이 없습니다. `ollama pull gemma4`로 설치하세요.")
    else:
        st.warning("Ollama 미연결. Rule-based 분석만 사용합니다.")
        model_name = "gemma4"

    use_llm = st.checkbox(
        "LLM 사용 (Ollama)",
        value=ollama_status['status'] == 'running',
        disabled=ollama_status['status'] != 'running',
    )

    st.divider()
    st.markdown("**예시 질문**")
    examples = [
        "이 문서에서 예산 관련 표만 찾아줘",
        "총 사업비가 얼마야?",
        "연차별 예산을 표로 정리해줘",
        "기관별 예산 합계를 알려줘",
        "가장 큰 금액이 들어간 항목은 뭐야?",
        "2026년 예산만 뽑아줘",
        "표 안의 숫자 합계를 계산해줘",
        "비율이나 퍼센트가 들어간 항목을 찾아줘",
    ]
    for q in examples:
        st.code(q, language=None)


# --- 파일 업로드 ---
uploaded_file = st.file_uploader("한글 문서를 업로드하세요", type=["hwp", "hwpx"])


def process_document(file_bytes: bytes, filename: str):
    with st.spinner("문서 분석 중..."):
        doc = parse_document(file_bytes=file_bytes, filename=filename)
        table_summaries = extract_tables(doc)
        text_numbers = detect_numbers_in_text(doc.full_text)
        table_numbers = detect_numbers_in_tables(table_summaries)
    return doc, table_summaries, text_numbers, table_numbers


if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    filename = uploaded_file.name

    cache_key = f"parsed_{filename}_{len(file_bytes)}"
    if cache_key not in st.session_state:
        doc, table_summaries, text_numbers, table_numbers = process_document(file_bytes, filename)
        st.session_state[cache_key] = {
            'doc': doc, 'tables': table_summaries,
            'text_numbers': text_numbers, 'table_numbers': table_numbers,
        }
        if doc.errors:
            for err in doc.errors:
                st.warning(f"파싱 경고: {err}")

    cached = st.session_state[cache_key]
    doc = cached['doc']
    table_summaries = cached['tables']
    text_numbers = cached['text_numbers']
    table_numbers = cached['table_numbers']

    # --- 문서 요약 ---
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("파일 형식", doc.file_type.upper())
    with col2:
        st.metric("문단 수", len(doc.paragraphs))
    with col3:
        st.metric("표 수", len(table_summaries))
    with col4:
        st.metric("탐지된 숫자", len(text_numbers) + len(table_numbers))

    # 추출된 표 미리보기 (접을 수 있는 영역)
    if table_summaries:
        with st.expander(f"추출된 표 미리보기 ({len(table_summaries)}개)", expanded=False):
            for ts in table_summaries:
                unit_info = f" [단위: {ts.unit}]" if ts.unit else ""
                caption_info = f" - {ts.caption}" if ts.caption else ""
                st.markdown(f"**표 {ts.index+1}{caption_info}{unit_info}** ({ts.num_rows}행 x {ts.num_cols}열)")
                if ts.dataframe is not None:
                    st.dataframe(ts.dataframe, use_container_width=True, height=min(200, 35 * (ts.num_rows + 1)))
                st.divider()

    # --- 채팅 ---
    st.divider()

    qa_engine = QAEngine(
        paragraphs=doc.paragraphs,
        table_summaries=table_summaries,
        text_numbers=text_numbers,
        table_numbers=table_numbers,
    )

    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []

    for chat in st.session_state.chat_history:
        with st.chat_message("user"):
            st.write(chat['question'])
        with st.chat_message("assistant"):
            st.markdown(chat['answer'])
            meta_parts = []
            if chat.get('source'):
                meta_parts.append(chat['source'])
            if chat.get('elapsed'):
                meta_parts.append(f"{chat['elapsed']}s")
            if chat.get('prompt_tokens') or chat.get('completion_tokens'):
                meta_parts.append(f"in:{chat.get('prompt_tokens',0)} out:{chat.get('completion_tokens',0)}")
            if meta_parts:
                st.caption(' | '.join(meta_parts))

    question = st.chat_input("질문을 입력하세요 (예: 총 사업비가 얼마야?)")

    if question:
        with st.chat_message("user"):
            st.write(question)

        with st.chat_message("assistant"):
            spinner_text = f"{model_name} 분석 중..." if use_llm else "분석 중..."
            with st.spinner(spinner_text):
                result = qa_engine.answer(
                    question=question,
                    use_llm=use_llm,
                    model=model_name,
                    ollama_url=ollama_url,
                )

            st.markdown(result.get('answer', '답변을 생성하지 못했습니다.'))

            meta_parts = []
            if result.get('source'):
                meta_parts.append(result['source'])
            if result.get('elapsed'):
                meta_parts.append(f"{result['elapsed']}s")
            if result.get('prompt_tokens') or result.get('completion_tokens'):
                meta_parts.append(f"in:{result.get('prompt_tokens',0)} out:{result.get('completion_tokens',0)}")
            if meta_parts:
                st.caption(' | '.join(meta_parts))
            if result.get('error'):
                st.error(result['error'])

        st.session_state.chat_history.append({
            'question': question,
            'answer': result.get('answer', ''),
            'source': result.get('source', ''),
            'elapsed': result.get('elapsed'),
            'prompt_tokens': result.get('prompt_tokens'),
            'completion_tokens': result.get('completion_tokens'),
        })

    if st.session_state.chat_history:
        if st.button("대화 초기화"):
            st.session_state.chat_history = []
            st.rerun()

else:
    st.info("한글 문서(.hwp 또는 .hwpx)를 업로드하면 바로 질문할 수 있습니다.")
    st.markdown("""
    ### 지원 형식
    - **HWPX**: 완전 지원 (텍스트 + 표 추출)
    - **HWP**: 제한적 지원 (텍스트 추출 위주)
    """)
