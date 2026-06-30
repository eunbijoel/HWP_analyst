"""
HWP 문서 분석기 - Streamlit UI
파일 업로드 → 바로 질의응답 (다중 문서 지원, 2-Stage LLM, 스트리밍)
"""

import sys
import os
import time
import streamlit as st
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hwp_parser import parse_document
from table_extractor import extract_tables, detect_numbers_in_text, detect_numbers_in_tables
from qa_engine import QAEngine, check_ollama_status


st.set_page_config(page_title="HWP 문서 분석기", page_icon="📄", layout="wide")
st.title("한글 문서 분석기")
st.caption("HWP/HWPX 파일을 업로드하고 바로 질문하세요 (여러 파일 동시 지원)")


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
            model_name = st.selectbox("Stage 2 모델 (해석)", sorted_models, index=0)
        else:
            model_name = st.text_input("모델 이름", value="gemma4")
            st.warning("설치된 모델이 없습니다. `ollama pull gemma4`로 설치하세요.")
    else:
        st.warning("Ollama 미연결. Rule-based 분석만 사용합니다.")
        model_name = "gemma4"
        available_models = []

    use_llm = st.checkbox(
        "LLM 사용 (Ollama)",
        value=ollama_status['status'] == 'running',
        disabled=ollama_status['status'] != 'running',
    )

    use_streaming = st.checkbox(
        "스트리밍 응답",
        value=True,
        disabled=not use_llm,
        help="LLM 응답을 토큰 단위로 실시간 표시합니다",
    )

    # Stage 1 모델 선택
    if use_llm and available_models:
        small_model_tags = ['gemma3', 'qwen2.5:7b', 'phi4', 'gemma2:2b']
        small_models = [m for m in available_models
                        if any(tag in m for tag in small_model_tags)]
        if not small_models:
            small_models = available_models
        stage1_model = st.selectbox(
            "Stage 1 모델 (의도 분석)",
            small_models,
            index=0,
            help="질문 의도 분석용 소형 모델 (빠른 모델 추천)",
        )
    else:
        stage1_model = "gemma3:4b"

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
uploaded_files = st.file_uploader(
    "한글 문서를 업로드하세요 (여러 파일 가능)",
    type=["hwp", "hwpx"],
    accept_multiple_files=True,
)


def process_document(file_bytes: bytes, filename: str):
    with st.spinner(f"문서 분석 중: {filename}"):
        doc = parse_document(file_bytes=file_bytes, filename=filename)
        document_id = filename
        table_summaries = extract_tables(doc, document_id=document_id)
        text_numbers = detect_numbers_in_text(doc.full_text, document_id=document_id)
        table_numbers = detect_numbers_in_tables(table_summaries, document_id=document_id)
    return doc, table_summaries, text_numbers, table_numbers


if uploaded_files:
    all_documents = []
    all_errors = []

    for uploaded_file in uploaded_files:
        file_bytes = uploaded_file.read()
        filename = uploaded_file.name

        cache_key = f"parsed_{filename}_{len(file_bytes)}"
        if cache_key not in st.session_state:
            doc, table_summaries, text_numbers, table_numbers = process_document(
                file_bytes, filename)
            st.session_state[cache_key] = {
                'doc': doc, 'tables': table_summaries,
                'text_numbers': text_numbers, 'table_numbers': table_numbers,
            }
            if doc.errors:
                all_errors.extend([(filename, err) for err in doc.errors])

        cached = st.session_state[cache_key]
        all_documents.append({
            'id': filename,
            'paragraphs': cached['doc'].paragraphs,
            'tables': cached['tables'],
            'text_numbers': cached['text_numbers'],
            'table_numbers': cached['table_numbers'],
            'doc': cached['doc'],
        })

    for filename, err in all_errors:
        st.warning(f"[{filename}] 파싱 경고: {err}")

    # --- 문서 요약 ---
    total_paragraphs = sum(len(d['paragraphs']) for d in all_documents)
    total_tables = sum(len(d['tables']) for d in all_documents)
    total_numbers = sum(len(d['text_numbers']) + len(d['table_numbers']) for d in all_documents)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("문서 수", len(all_documents))
    with col2:
        st.metric("문단 수", total_paragraphs)
    with col3:
        st.metric("표 수", total_tables)
    with col4:
        st.metric("탐지된 숫자", total_numbers)

    # 추출된 표 미리보기
    if total_tables > 0:
        with st.expander(f"추출된 표 미리보기 ({total_tables}개)", expanded=False):
            for doc_data in all_documents:
                if len(all_documents) > 1:
                    st.subheader(f"{doc_data['id']}")
                for ts in doc_data['tables']:
                    unit_info = f" [단위: {ts.unit}]" if ts.unit else ""
                    caption_info = f" - {ts.caption}" if ts.caption else ""
                    st.markdown(f"**표 {ts.index+1}{caption_info}{unit_info}** ({ts.num_rows}행 x {ts.num_cols}열)")
                    if ts.dataframe is not None:
                        st.dataframe(ts.dataframe, use_container_width=True, height=min(200, 35 * (ts.num_rows + 1)))
                    st.divider()

    # --- 채팅 ---
    st.divider()

    # QAEngine 생성
    if len(all_documents) == 1:
        d = all_documents[0]
        qa_engine = QAEngine(
            paragraphs=d['paragraphs'],
            table_summaries=d['tables'],
            text_numbers=d['text_numbers'],
            table_numbers=d['table_numbers'],
        )
    else:
        qa_engine = QAEngine(documents=all_documents)

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
            should_stream = use_llm and use_streaming

            if use_llm and not should_stream:
                with st.spinner(f"{model_name} 분석 중..."):
                    result = qa_engine.answer(
                        question=question,
                        use_llm=use_llm,
                        model=model_name,
                        ollama_url=ollama_url,
                        stream=False,
                        stage1_model=stage1_model,
                    )
            else:
                if not use_llm:
                    with st.spinner("분석 중..."):
                        result = qa_engine.answer(
                            question=question,
                            use_llm=False,
                            model=model_name,
                            ollama_url=ollama_url,
                            stream=False,
                            stage1_model=stage1_model,
                        )
                else:
                    result = qa_engine.answer(
                        question=question,
                        use_llm=use_llm,
                        model=model_name,
                        ollama_url=ollama_url,
                        stream=True,
                        stage1_model=stage1_model,
                    )

            if 'answer_stream' in result:
                full_response = st.write_stream(result['answer_stream'])
                result['answer'] = full_response
                elapsed = time.time() - result.get('start_time', time.time())
                result['elapsed'] = round(elapsed, 1)
            else:
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
    st.info("한글 문서(.hwp 또는 .hwpx)를 업로드하면 바로 질문할 수 있습니다. 여러 파일을 동시에 업로드할 수 있습니다.")
    st.markdown("""
    ### 지원 형식
    - **HWPX**: 완전 지원 (텍스트 + 표 추출)
    - **HWP**: LibreOffice 변환 후 표 추출 지원, 미변환 시 텍스트 추출 위주

    ### 아키텍처
    - **Stage 1**: 소형 모델(gemma3:4b)로 질문 의도/엔티티 추출
    - **Pre-compute**: DataFrame 기반 정확한 수치 계산
    - **Stage 2**: 대형 모델(gemma4)로 해석 및 자연어 답변 (스트리밍 지원)
    """)
