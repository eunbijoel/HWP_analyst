"""
HWP Document Intelligence (Product A)

Understand · analyze · review · grounded Q&A.
Does NOT edit, fill, accept proposals, or export modified HWP/HWPX.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from apps.intelligence.ui import (
    render_intelligence_tab,
    render_knowledge_mode_picker,
)
from hwp_core.hwp_backends import get_backend_status
from hwp_core.llm_client import check_ollama_status
from ui.brand import LOGO_PATH, PRODUCT_NAME, inject_theme, sidebar_brand

st.set_page_config(
    page_title=f"{PRODUCT_NAME} · Intelligence",
    page_icon=str(LOGO_PATH) if LOGO_PATH.is_file() else "✦",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_theme()


@st.cache_data(ttl=30, show_spinner=False)
def get_cached_ollama_status(url: str):
    return check_ollama_status(url)


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

render_intelligence_tab(
    model_name=model_name,
    stage1_model=stage1_model,
    use_llm=use_llm,
    use_streaming=use_streaming,
    ollama_url=ollama_url,
    knowledge_mode=knowledge_mode,
    show_hero=True,
)
