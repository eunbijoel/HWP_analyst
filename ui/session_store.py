"""Streamlit session_state 헬퍼 — app.py 순환 import 방지용."""

import hashlib

import streamlit as st

from hwp_core.hwpx_editor import HWPXEditor


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


def validate_hwp_bytes(data: bytes) -> tuple[bool, str]:
    if not isinstance(data, (bytes, bytearray)) or len(data) < 8:
        return False, '파일 데이터가 비어 있습니다.'
    if data[:4] != b'\xd0\xcf\x11\xe0':
        return False, 'HWP(OLE) 형식이 아닙니다.'
    return True, ''
