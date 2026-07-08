"""
Canvas Edit Mode — HWP/HWPX 직접 편집
- 통합 편집: 본문을 하나(또는 표 사이 구간)의 text_area로 편집, 표는 data_editor
- 문단별 편집: 문단마다 text_area (기존 방식)
"""

import os
import html as html_mod
from functools import partial

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from hwp_core.hwpx_editor import HWPXEditor

PAGE_SIZE = 30
PARA_SEP = '\n\n'
PARA_SEP_HINT = '문단 구분은 빈 줄(Enter 두 번)로 유지해 주세요.'


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _rev(fname: str) -> int:
    return st.session_state.get(f"cvs_rev_{fname}", 0)


def _bump_rev(fname: str):
    st.session_state[f"cvs_rev_{fname}"] = _rev(fname) + 1


def _dirty(fname: str) -> dict[int, str]:
    key = f"cvs_dirty_{fname}"
    if key not in st.session_state:
        st.session_state[key] = {}
    return st.session_state[key]


def _applied(fname: str) -> set[int]:
    key = f"cvs_applied_{fname}"
    if key not in st.session_state:
        st.session_state[key] = set()
    return st.session_state[key]


def _dirty_tables(fname: str) -> dict[tuple[int, int, int], str]:
    key = f"cvs_dirty_tbl_{fname}"
    if key not in st.session_state:
        st.session_state[key] = {}
    return st.session_state[key]


def _editor_idx_for_block(editor: HWPXEditor, block_map: dict, block: dict) -> int | None:
    block_idx = block['paragraph_index']
    editor_idx = block_map.get(block_idx)
    if editor_idx is None:
        editor_idx = editor.editor_index_for_block(block_idx, block['text'])
    return editor_idx


def _build_ordered_items(
    blocks: list[dict],
    block_map: dict,
    editor: HWPXEditor,
) -> list[tuple]:
    """문서 순서: ('text', [editor_idx, ...]) | ('table', table_index)."""
    items: list[tuple] = []
    current_indices: list[int] = []
    for block in blocks:
        if block['type'] == 'paragraph':
            editor_idx = _editor_idx_for_block(editor, block_map, block)
            if editor_idx is not None:
                current_indices.append(editor_idx)
        elif block['type'] == 'table':
            if current_indices:
                items.append(('text', list(current_indices)))
                current_indices = []
            items.append(('table', block['table_index']))
    if current_indices:
        items.append(('text', list(current_indices)))
    return items


# ---------------------------------------------------------------------------
# on_change callbacks
# ---------------------------------------------------------------------------

def _on_para_change(fname: str, idx: int, rev: int):
    """text_area 변경 시 dirty dict에 저장."""
    key = f"cvs_{fname}_{idx}_{rev}"
    new_text = st.session_state.get(key, '')
    if new_text is not None:
        _dirty(fname)[idx] = new_text.strip()


def _on_body_change(fname: str, indices: list[int], rev: int):
    """통합 본문 text_area → 문단별 dirty."""
    if not indices:
        return
    key = f"cvs_body_{fname}_{indices[0]}_{rev}"
    combined = st.session_state.get(key, '')
    if combined is None:
        return
    parts = combined.split(PARA_SEP)
    dirty = _dirty(fname)
    for i, idx in enumerate(indices):
        if i < len(parts):
            dirty[idx] = parts[i].strip()


def _cell_str(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ''
    return str(val)


def _sync_table_dirty(
    fname: str,
    state_key: str,
    t_idx: int,
    nrow: int,
    ncol: int,
    baseline: list[list[str]],
):
    """data_editor session state → 변경된 셀만 dirty (DataFrame / EditingState 모두 지원)."""
    edited = st.session_state.get(state_key)
    if edited is None:
        return

    dirty_tbl = _dirty_tables(fname)
    current: list[list[str]] = [list(row) for row in baseline]

    if isinstance(edited, pd.DataFrame):
        for r in range(min(nrow, len(edited.index))):
            for c in range(min(ncol, len(edited.columns))):
                current[r][c] = _cell_str(edited.iat[r, c])
    elif isinstance(edited, dict):
        if 'edited_rows' in edited:
            for row_id, col_changes in edited.get('edited_rows', {}).items():
                try:
                    r = int(row_id)
                except (TypeError, ValueError):
                    continue
                if r < 0 or r >= nrow:
                    continue
                for col_id, val in col_changes.items():
                    try:
                        c = int(col_id)
                    except (TypeError, ValueError):
                        continue
                    if c < 0 or c >= ncol:
                        continue
                    current[r][c] = _cell_str(val)
        else:
            try:
                df = pd.DataFrame(edited)
                for r in range(min(nrow, len(df.index))):
                    for c in range(min(ncol, len(df.columns))):
                        current[r][c] = _cell_str(df.iat[r, c])
            except (ValueError, TypeError):
                return
    else:
        return

    for r in range(nrow):
        for c in range(ncol):
            new_val = current[r][c]
            old_val = baseline[r][c] if r < len(baseline) and c < len(baseline[r]) else ''
            cell_key = (t_idx, r, c)
            if new_val != old_val:
                dirty_tbl[cell_key] = new_val
            elif cell_key in dirty_tbl:
                del dirty_tbl[cell_key]


def _on_table_change(fname: str, t_idx: int, rev: int, nrow: int, ncol: int, baseline: list[list[str]]):
    """레거시 on_change — Streamlit EditingState dict 호환."""
    _sync_table_dirty(
        fname, f"cvs_tbl_{fname}_{t_idx}_{rev}", t_idx, nrow, ncol, baseline,
    )


def _on_hwp_table_change(fname: str, t_idx: int, rev: int, nrow: int, ncol: int, baseline: list[list[str]]):
    _sync_table_dirty(
        fname, f"cvs_hwp_tbl_{fname}_{t_idx}_{rev}", t_idx, nrow, ncol, baseline,
    )


def _on_hwp_body_change(fname: str, rev: int, para_count: int):
    key = f"cvs_hwp_body_{fname}_{rev}"
    combined = st.session_state.get(key, '')
    if combined is None:
        return
    parts = combined.split(PARA_SEP)
    dirty = _dirty(fname)
    for i in range(min(len(parts), para_count)):
        dirty[i] = parts[i].strip()


# ---------------------------------------------------------------------------
# Commit dirty edits — HWPX
# ---------------------------------------------------------------------------

def _commit_dirty_edits(
    fname: str,
    editor: HWPXEditor,
    chat_key: str,
    source_hwp: str = '',
) -> int:
    """dirty dict를 드레인하여 HWPX XML에 즉시 반영."""
    from ui.session_store import sync_export_state

    dirty = _dirty(fname)
    if not dirty:
        return 0

    paras = editor.get_paragraphs()
    committed = 0
    for idx, new_text in list(dirty.items()):
        if idx >= len(paras):
            continue
        old_text = paras[idx]['text']
        if new_text == old_text:
            continue
        for ch in editor.pending_changes:
            if ch.status == 'pending' and ch.paragraph_index == idx:
                ch.status = 'rejected'
        ok = editor._set_paragraph_text(idx, new_text, track_changes=False)
        if ok:
            committed += 1
            _applied(fname).add(idx)

    if committed:
        editor._bump_preview()
        editor._invalidate_structure_cache()
        _bump_rev(fname)
        sync_export_state(editor, fname, source_hwp=source_hwp)
        if chat_key and chat_key in st.session_state:
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': f'✏️ {committed}건 문단 직접 수정 반영',
            })

    st.session_state[f"cvs_dirty_{fname}"] = {}
    return committed


def _commit_dirty_tables(
    fname: str,
    editor: HWPXEditor,
    chat_key: str,
    source_hwp: str = '',
) -> int:
    """dirty table cells → HWPX XML."""
    from ui.session_store import sync_export_state

    dirty = _dirty_tables(fname)
    if not dirty:
        return 0

    old_rows_cache: dict[int, list[list[str]]] = {}
    committed = 0
    for (t_idx, r, c), new_val in list(dirty.items()):
        if t_idx not in old_rows_cache:
            old_rows_cache[t_idx] = editor.get_table_as_rows(t_idx)
        old_rows = old_rows_cache[t_idx]
        old_val = ''
        if r < len(old_rows) and c < len(old_rows[r]):
            old_val = old_rows[r][c]
        if new_val == old_val:
            continue
        if editor.edit_table_cell(t_idx, r, c, new_val):
            committed += 1
            old_rows_cache[t_idx][r][c] = new_val

    if committed:
        editor._bump_preview()
        editor._invalidate_structure_cache()
        _bump_rev(fname)
        sync_export_state(editor, fname, source_hwp=source_hwp)
        if chat_key and chat_key in st.session_state:
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': f'📊 {committed}건 표 셀 직접 수정 반영',
            })

    st.session_state[f"cvs_dirty_tbl_{fname}"] = {}
    return committed


# ---------------------------------------------------------------------------
# Commit dirty edits — HWP (hwpilot)
# ---------------------------------------------------------------------------

def _commit_hwp_dirty_edits(
    fname: str,
    file_bytes: bytes,
    paragraphs: list[str],
    chat_key: str,
) -> int:
    """dirty dict를 드레인하여 hwpilot으로 HWP 바이너리 수정."""
    from ui.session_store import get_hwp_working_bytes, set_hwp_working_bytes
    from hwp_core.hwp_backends import apply_hwpilot_to_bytes, hwpilot_edit_paragraph

    dirty = _dirty(fname)
    if not dirty:
        return 0

    hwp_bytes = get_hwp_working_bytes(fname, file_bytes)
    edits = {
        idx: text for idx, text in dirty.items()
        if idx < len(paragraphs) and text != paragraphs[idx]
    }
    if not edits:
        st.session_state[f"cvs_dirty_{fname}"] = {}
        return 0

    def _edit_all(path):
        count = 0
        for idx, new_text in sorted(edits.items()):
            old_text = paragraphs[idx] if idx < len(paragraphs) else ''
            ok, _ = hwpilot_edit_paragraph(path, idx, new_text, old_text=old_text)
            if ok:
                count += 1
        if count == 0:
            return False, '편집 실패'
        return True, f'{count}건 수정'

    new_bytes, msg = apply_hwpilot_to_bytes(hwp_bytes, fname, _edit_all)
    if new_bytes:
        set_hwp_working_bytes(fname, new_bytes)
        for idx in edits:
            _applied(fname).add(idx)
        _bump_rev(fname)
        if chat_key and chat_key in st.session_state:
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': f'✏️ {len(edits)}건 문단 직접 수정 반영 (HWP)',
            })

    st.session_state[f"cvs_dirty_{fname}"] = {}
    return len(edits)


def _commit_hwp_dirty_tables(
    fname: str,
    file_bytes: bytes,
    tables_raw: list[dict],
    chat_key: str,
) -> int:
    """dirty table cells → hwpilot table edit."""
    from ui.session_store import get_hwp_working_bytes, set_hwp_working_bytes
    from hwp_core.hwp_backends import apply_hwpilot_to_bytes, hwpilot_edit_table_cell

    dirty = _dirty_tables(fname)
    if not dirty:
        return 0

    edits = []
    for (t_idx, r, c), new_val in dirty.items():
        if t_idx >= len(tables_raw):
            continue
        rows = tables_raw[t_idx].get('rows', [])
        old_val = ''
        if r < len(rows) and c < len(rows[r]):
            old_val = rows[r][c]
        if new_val == old_val:
            continue
        edits.append((t_idx, r, c, new_val))

    if not edits:
        st.session_state[f"cvs_dirty_tbl_{fname}"] = {}
        return 0

    hwp_bytes = get_hwp_working_bytes(fname, file_bytes)

    def _edit_all(path):
        count = 0
        for t_idx, r, c, new_val in edits:
            ref = f"s0.t{t_idx}.r{r}.c{c}"
            ok, _ = hwpilot_edit_table_cell(path, ref, new_val)
            if ok:
                count += 1
        if count == 0:
            return False, '표 편집 실패'
        return True, f'{count}건 표 셀 수정'

    new_bytes, msg = apply_hwpilot_to_bytes(hwp_bytes, fname, _edit_all)
    if new_bytes:
        set_hwp_working_bytes(fname, new_bytes)
        _bump_rev(fname)
        if chat_key and chat_key in st.session_state:
            st.session_state[chat_key].append({
                'role': 'assistant',
                'content': f'📊 {len(edits)}건 표 셀 직접 수정 반영 (HWP)',
            })

    st.session_state[f"cvs_dirty_tbl_{fname}"] = {}
    return len(edits)


# ---------------------------------------------------------------------------
# Table editor (editable)
# ---------------------------------------------------------------------------

def _pad_rows(rows: list[list[str]]) -> tuple[list[list[str]], int, int]:
    if not rows:
        return [], 0, 0
    ncol = max(len(r) for r in rows)
    padded = [list(r) + [''] * (ncol - len(r)) for r in rows]
    return padded, len(padded), ncol


def _render_table_editor_hwpx(
    fname: str,
    editor: HWPXEditor,
    t_idx: int,
    rev: int,
):
    rows = editor.get_table_as_rows(t_idx)
    padded, nrow, ncol = _pad_rows(rows)
    if nrow == 0:
        return
    df = pd.DataFrame(padded)
    st.markdown(f"**표 {t_idx + 1}**")
    state_key = f"cvs_tbl_{fname}_{t_idx}_{rev}"
    st.data_editor(
        df,
        key=state_key,
        use_container_width=True,
        num_rows='fixed',
        hide_index=True,
    )
    _sync_table_dirty(fname, state_key, t_idx, nrow, ncol, padded)


def _render_table_editor_hwp(
    fname: str,
    t_idx: int,
    rows: list[list[str]],
    rev: int,
):
    padded, nrow, ncol = _pad_rows(rows)
    if nrow == 0:
        return
    df = pd.DataFrame(padded)
    st.markdown(f"**표 {t_idx + 1}**")
    state_key = f"cvs_hwp_tbl_{fname}_{t_idx}_{rev}"
    st.data_editor(
        df,
        key=state_key,
        use_container_width=True,
        num_rows='fixed',
        hide_index=True,
    )
    _sync_table_dirty(fname, state_key, t_idx, nrow, ncol, padded)


# ---------------------------------------------------------------------------
# Static table rendering (문단별 모드 미리보기용)
# ---------------------------------------------------------------------------

_TABLE_CSS = """
<style>
.cvs-tbl-wrap { margin: 8px 0; overflow-x: auto; }
.cvs-tbl-caption { font-size: 12px; color: #666; margin-bottom: 4px; font-weight: 600; }
table.cvs-tbl {
    border-collapse: collapse; width: 100%; font-size: 13px;
}
table.cvs-tbl td, table.cvs-tbl th {
    border: 1px solid #bbb; padding: 5px 8px;
    vertical-align: top; min-width: 40px;
}
table.cvs-tbl th { background: #f0f0f0; font-weight: 600; }
</style>
"""


def _esc(text) -> str:
    return html_mod.escape(str(text) if text else '')


def _render_table_static(t_idx: int, parsed, max_rows: int = 40):
    """표를 정적 HTML로 렌더링."""
    parts = [_TABLE_CSS]
    parts.append(f'<div class="cvs-tbl-wrap">')
    parts.append(f'<div class="cvs-tbl-caption">표 {t_idx + 1}</div>')
    parts.append('<table class="cvs-tbl"><tbody>')

    for r_idx, row in enumerate(parsed.rows[:max_rows]):
        parts.append('<tr>')
        tag = 'th' if r_idx == 0 else 'td'
        for c_idx, cell in enumerate(row):
            if hasattr(parsed, 'covered') and (r_idx, c_idx) in parsed.covered:
                continue
            span_attr = ''
            if hasattr(parsed, 'get_merge_at'):
                merge = parsed.get_merge_at(r_idx, c_idx)
                if merge:
                    if merge.rowspan > 1:
                        span_attr += f' rowspan="{merge.rowspan}"'
                    if merge.colspan > 1:
                        span_attr += f' colspan="{merge.colspan}"'
            content = _esc(cell) if cell and str(cell).strip() else '&nbsp;'
            parts.append(f'<{tag}{span_attr}>{content}</{tag}>')
        parts.append('</tr>')

    if len(parsed.rows) > max_rows:
        ncol = getattr(parsed, 'num_cols', 1) or 1
        parts.append(
            f'<tr><td colspan="{ncol}" style="text-align:center;color:#999">'
            f'... 외 {len(parsed.rows) - max_rows}행 ...</td></tr>'
        )
    parts.append('</tbody></table></div>')
    components.html('\n'.join(parts), height=min(60 + 28 * min(len(parsed.rows), max_rows), 500), scrolling=True)


# ---------------------------------------------------------------------------
# Paragraph height estimation
# ---------------------------------------------------------------------------

def _estimate_height(text: str) -> int:
    lines = text.count('\n') + 1
    char_lines = max(1, len(text) // 60)
    return max(68, min(300, 24 + 22 * max(lines, char_lines)))


def _unified_body_height(text: str) -> int:
    lines = text.count('\n') + 1
    char_lines = max(1, len(text) // 70)
    return max(320, min(900, 80 + 20 * max(lines, char_lines)))


# ---------------------------------------------------------------------------
# Unified editor — HWPX
# ---------------------------------------------------------------------------

def _render_unified_editor_hwpx(
    fname: str,
    editor: HWPXEditor,
    blocks: list[dict],
    block_map: dict,
    rev: int,
    *,
    source_hwp: str = '',
    chat_key: str = '',
):
    items = _build_ordered_items(blocks, block_map, editor)
    text_sections = [x for x in items if x[0] == 'text']
    st.caption(
        f"통합 편집 — 본문 {len(text_sections)}구간 · 표 {sum(1 for x in items if x[0] == 'table')}개 · "
        f"{PARA_SEP_HINT}"
    )

    paras = editor.get_paragraphs()
    section_no = 0
    for item in items:
        if item[0] == 'text':
            indices = item[1]
            if not indices:
                continue
            section_no += 1
            combined = PARA_SEP.join(paras[i]['text'] for i in indices if i < len(paras))
            label = '본문'
            if len(text_sections) > 1:
                label = f'본문 (구간 {section_no})'
            first_idx = indices[0]
            st.text_area(
                label,
                value=combined,
                key=f"cvs_body_{fname}_{first_idx}_{rev}",
                height=_unified_body_height(combined),
                label_visibility='visible',
                on_change=partial(_on_body_change, fname, indices, rev),
            )
        else:
            _render_table_editor_hwpx(fname, editor, item[1], rev)

    _render_canvas_download(fname, editor, source_hwp)


# ---------------------------------------------------------------------------
# Paragraph-by-paragraph editor — HWPX
# ---------------------------------------------------------------------------

def _render_paragraph_editor_hwpx(
    fname: str,
    editor: HWPXEditor,
    blocks: list[dict],
    block_map: dict,
    rev: int,
    *,
    source_hwp: str = '',
):
    applied_set = _applied(fname)
    pending_paras = {
        ch.paragraph_index: ch
        for ch in editor.get_pending_changes()
        if ch.paragraph_index is not None
    }

    para_count = sum(1 for b in blocks if b['type'] == 'paragraph')
    total_pages = max(1, (para_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page_key = f"cvs_page_{fname}"
    page = st.session_state.get(page_key, 0)
    if page >= total_pages:
        page = 0

    if total_pages > 1:
        c1, c2, c3 = st.columns([1, 2, 1])
        with c1:
            if st.button("◀ 이전", key=f"cvs_prev_{fname}", disabled=page == 0):
                st.session_state[page_key] = page - 1
                st.rerun()
        with c2:
            st.caption(f"페이지 {page + 1} / {total_pages}")
        with c3:
            if st.button("다음 ▶", key=f"cvs_next_{fname}", disabled=page >= total_pages - 1):
                st.session_state[page_key] = page + 1
                st.rerun()

    para_seen = 0
    page_start = page * PAGE_SIZE
    page_end = page_start + PAGE_SIZE

    for block in blocks:
        if block['type'] == 'paragraph':
            if para_seen < page_start:
                para_seen += 1
                continue
            if para_seen >= page_end:
                para_seen += 1
                continue
            para_seen += 1

            editor_idx = _editor_idx_for_block(editor, block_map, block)
            if editor_idx is None:
                continue

            paras = editor.get_paragraphs()
            if editor_idx >= len(paras):
                continue
            current_text = paras[editor_idx]['text']

            if editor_idx in applied_set:
                st.markdown(
                    f'<div style="border-left:3px solid #008800;padding-left:8px;margin-bottom:2px">'
                    f'<span style="font-size:11px;color:#008800">✓ 문단 {editor_idx + 1}</span></div>',
                    unsafe_allow_html=True,
                )
            elif editor_idx in pending_paras:
                st.markdown(
                    f'<div style="border-left:3px solid #ffc107;padding-left:8px;margin-bottom:2px">'
                    f'<span style="font-size:11px;color:#b58900">⏳ 문단 {editor_idx + 1} · AI 제안 대기</span></div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption(f"문단 {editor_idx + 1}")

            ta_key = f"cvs_{fname}_{editor_idx}_{rev}"
            st.text_area(
                f"문단 {editor_idx + 1}",
                value=current_text,
                key=ta_key,
                height=_estimate_height(current_text),
                label_visibility="collapsed",
                on_change=partial(_on_para_change, fname, editor_idx, rev),
            )

        elif block['type'] == 'table':
            if para_seen < page_start and para_seen > 0:
                continue
            if para_seen > page_end:
                continue
            t_idx = block['table_index']
            _render_table_editor_hwpx(fname, editor, t_idx, rev)

    _render_canvas_download(fname, editor, source_hwp)


# ---------------------------------------------------------------------------
# Canvas editor — HWPX
# ---------------------------------------------------------------------------

def render_canvas_editor(
    fname: str,
    editor: HWPXEditor,
    *,
    source_hwp: str = '',
    chat_key: str = '',
):
    """HWPX Canvas 편집."""
    _commit_dirty_edits(fname, editor, chat_key, source_hwp)
    _commit_dirty_tables(fname, editor, chat_key, source_hwp)

    mode_key = f"cvs_mode_{fname}"
    mode = st.radio(
        "편집 방식",
        ["통합 편집", "문단별 편집"],
        horizontal=True,
        key=mode_key,
        help="통합: 본문을 한 편집기(또는 표 사이 구간)로 수정 · 표는 셀 단위 편집",
    )

    blocks = editor.get_document_blocks()
    block_map = editor.build_block_to_editor_paragraph_map()
    rev = _rev(fname)

    if mode == "통합 편집":
        _render_unified_editor_hwpx(
            fname, editor, blocks, block_map, rev,
            source_hwp=source_hwp, chat_key=chat_key,
        )
    else:
        st.caption("문단별 편집 — 각 문단을 개별 수정")
        _render_paragraph_editor_hwpx(
            fname, editor, blocks, block_map, rev, source_hwp=source_hwp,
        )


def render_hwpx_download(fname: str, editor: HWPXEditor, source_hwp: str = ''):
    """HWPX 다운로드 버튼 (미리보기·직접 편집 공통)."""
    _render_canvas_download(fname, editor, source_hwp)


def _render_canvas_download(fname: str, editor: HWPXEditor, source_hwp: str = ''):
    """HWPX 다운로드 버튼."""
    from ui.session_store import sync_export_state

    base = os.path.splitext(fname)[0]
    sync_export_state(editor, fname, source_hwp=source_hwp)
    dl_data = editor.get_export_bytes()
    ok, err = HWPXEditor.validate_hwpx_bytes(dl_data)

    if not ok:
        st.error(f"다운로드 파일 오류: {err}")
        return

    if source_hwp:
        st.info(
            f"원본 **{source_hwp}** → 편집본은 **HWPX** 형식입니다. "
            "한글에서 열어 .hwp로 다시 저장할 수 있습니다."
        )

    st.download_button(
        "📥 HWPX 다운로드",
        data=dl_data,
        file_name=f"{base}_edited.hwpx",
        mime="application/vnd.hancom.hwpx",
        key=f"dl_cvs_{fname}",
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# Canvas editor — HWP
# ---------------------------------------------------------------------------

def render_canvas_editor_hwp(
    fname: str,
    file_bytes: bytes,
    *,
    chat_key: str = '',
):
    """HWP Canvas 편집 — hwpilot으로 문단 직접 수정."""
    from ui.session_store import get_hwp_working_bytes, validate_hwp_bytes
    from hwp_core.hwp_parser import parse_document as _parse_doc

    hwp_bytes = get_hwp_working_bytes(fname, file_bytes)
    doc = _parse_doc(file_bytes=hwp_bytes, filename=fname)
    paragraphs = doc.paragraphs
    tables_raw = doc.tables_raw

    _commit_hwp_dirty_edits(fname, file_bytes, paragraphs, chat_key)
    _commit_hwp_dirty_tables(fname, file_bytes, tables_raw, chat_key)

    hwp_bytes = get_hwp_working_bytes(fname, file_bytes)
    doc = _parse_doc(file_bytes=hwp_bytes, filename=fname)
    paragraphs = doc.paragraphs
    tables_raw = doc.tables_raw

    st.markdown(f"### 📄 {fname}")

    mode_key = f"cvs_mode_{fname}"
    mode = st.radio(
        "편집 방식",
        ["통합 편집", "문단별 편집"],
        horizontal=True,
        key=mode_key,
    )

    rev = _rev(fname)

    if mode == "통합 편집":
        combined = PARA_SEP.join(paragraphs)
        st.caption(f"통합 편집 (HWP) · {PARA_SEP_HINT}")
        st.text_area(
            "본문",
            value=combined,
            key=f"cvs_hwp_body_{fname}_{rev}",
            height=_unified_body_height(combined or ' '),
            on_change=partial(_on_hwp_body_change, fname, rev, len(paragraphs)),
        )
        for t_idx, tbl in enumerate(tables_raw[:20]):
            rows = tbl.get('rows', [])
            if rows:
                _render_table_editor_hwp(fname, t_idx, rows, rev)
    else:
        st.caption("문단별 편집 (HWP)")
        applied_set = _applied(fname)
        para_count = len(paragraphs)
        total_pages = max(1, (para_count + PAGE_SIZE - 1) // PAGE_SIZE)
        page_key = f"cvs_page_{fname}"
        page = st.session_state.get(page_key, 0)
        if page >= total_pages:
            page = 0

        if total_pages > 1:
            c1, c2, c3 = st.columns([1, 2, 1])
            with c1:
                if st.button("◀ 이전", key=f"cvs_hwp_prev_{fname}", disabled=page == 0):
                    st.session_state[page_key] = page - 1
                    st.rerun()
            with c2:
                st.caption(f"페이지 {page + 1} / {total_pages}")
            with c3:
                if st.button("다음 ▶", key=f"cvs_hwp_next_{fname}", disabled=page >= total_pages - 1):
                    st.session_state[page_key] = page + 1
                    st.rerun()

        page_start = page * PAGE_SIZE
        page_end = min(page_start + PAGE_SIZE, para_count)

        for idx in range(page_start, page_end):
            text = paragraphs[idx]
            if not text.strip():
                continue
            if idx in applied_set:
                st.markdown(
                    f'<div style="border-left:3px solid #008800;padding-left:8px;margin-bottom:2px">'
                    f'<span style="font-size:11px;color:#008800">✓ 문단 {idx + 1}</span></div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption(f"문단 {idx + 1}")
            ta_key = f"cvs_{fname}_{idx}_{rev}"
            st.text_area(
                f"문단 {idx + 1}",
                value=text,
                key=ta_key,
                height=_estimate_height(text),
                label_visibility="collapsed",
                on_change=partial(_on_para_change, fname, idx, rev),
            )

        for t_idx, tbl in enumerate(tables_raw[:20]):
            rows = tbl.get('rows', [])
            if rows:
                _render_table_editor_hwp(fname, t_idx, rows, rev)

    _render_hwp_canvas_download(fname, file_bytes)


def _render_hwp_canvas_download(fname: str, file_bytes: bytes):
    """HWP 다운로드 버튼."""
    from ui.session_store import get_hwp_working_bytes, validate_hwp_bytes

    hwp_bytes = get_hwp_working_bytes(fname, file_bytes)
    ok, err = validate_hwp_bytes(hwp_bytes)
    base = os.path.splitext(fname)[0]

    if ok:
        st.caption(f"파일 크기: {len(hwp_bytes):,} bytes")
        st.download_button(
            "📥 수정본 HWP 다운로드",
            data=bytes(hwp_bytes),
            file_name=f"{base}_edited.hwp",
            mime="application/octet-stream",
            key=f"dl_hwp_cvs_{fname}",
            use_container_width=True,
        )
    else:
        st.error(f"다운로드 불가: {err}")
