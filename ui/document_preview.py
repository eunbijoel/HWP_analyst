"""
HWPX 문서 HTML 미리보기 — 변경사항 색상 하이라이트 (한글 Track Changes 스타일)
"""

import html
import re
from typing import Optional

from main.hwpx_editor import HWPXEditor, PendingChange, AppliedHighlight


PREVIEW_CSS = """
<style>
.doc-preview {
    font-family: 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif;
    font-size: 14px;
    line-height: 1.7;
    color: #222;
    background: #f5f5f5;
    padding: 16px;
}
.doc-page {
    background: #fff;
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 32px 40px;
    max-width: 100%;
    box-shadow: 0 2px 8px rgba(0,0,0,.08);
}
.doc-title {
    font-size: 13px;
    color: #888;
    margin-bottom: 20px;
    padding-bottom: 8px;
    border-bottom: 1px solid #eee;
}
.para {
    margin: 0 0 10px 0;
    padding: 4px 6px;
    border-radius: 3px;
}
.para-num {
    color: #aaa;
    font-size: 11px;
    margin-right: 6px;
    user-select: none;
}
.tbl-wrap { margin: 16px 0; overflow-x: auto; }
.tbl-caption {
    font-size: 12px; color: #666; margin-bottom: 6px; font-weight: 600;
}
table.hwpx-tbl {
    border-collapse: collapse; width: 100%; font-size: 13px;
}
table.hwpx-tbl td, table.hwpx-tbl th {
    border: 1px solid #bbb; padding: 5px 8px;
    vertical-align: top; min-width: 40px;
}
table.hwpx-tbl th { background: #f0f0f0; font-weight: 600; }
.cell-empty { background: #fafafa; color: #ccc; }
/* 대기 중 (AI 제안) */
.ch-pending { background: #fff8e1; outline: 2px solid #ffc107; }
.ins { color: #008800; font-weight: 600; }
.old-pending { color: #cc0000; text-decoration: line-through; opacity: .75; }
/* 적용 완료 — 한글 파일과 동일한 빨간 수정 표시 */
.ch-applied { background: #fff5f5; }
.mod-applied { color: #cc0000; font-weight: 600; }
.mod-applied-del {
    display: block; color: #cc0000; text-decoration: line-through;
    font-size: 12px; opacity: .65; margin-bottom: 2px;
}
.legend {
    display: flex; gap: 16px; font-size: 11px; color: #666;
    margin-bottom: 12px; flex-wrap: wrap;
}
.legend span { display: flex; align-items: center; gap: 4px; }
.dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
</style>
"""


def _esc(text: str) -> str:
    return html.escape(str(text) if text else '')


def _cell_key(t_idx: int, r: int, c: int) -> tuple:
    return (t_idx, r, c)


def _values_match(old: str, cell: str) -> bool:
    old_n = re.sub(r'[\s,]', '', old)
    cell_n = re.sub(r'[\s,]', '', str(cell))
    if not old_n:
        return False
    return old in str(cell) or cell_n == old_n or old_n in cell_n


def _build_preview_maps(editor: HWPXEditor):
    """pending / applied 위치 맵 구성."""
    pending_cells: dict[tuple, PendingChange] = {}
    pending_paras: dict[int, PendingChange] = {}
    applied_cells: dict[tuple, AppliedHighlight] = {}
    applied_paras: dict[int, AppliedHighlight] = {}

    for ch in editor.pending_changes:
        if ch.status != 'pending':
            continue
        if ch.change_type == 'cell' and ch.table_index is not None:
            pending_cells[_cell_key(ch.table_index, ch.row, ch.col)] = ch
        elif ch.change_type == 'paragraph' and ch.paragraph_index is not None:
            pending_paras[ch.paragraph_index] = ch
        elif ch.change_type == 'replace' and ch.old_text:
            for block in editor.get_document_blocks():
                if block['type'] == 'table':
                    t_idx = block['table_index']
                    for r_idx, row in enumerate(block['parsed'].rows):
                        for c_idx, cell in enumerate(row):
                            if _values_match(ch.old_text, cell):
                                pending_cells[_cell_key(t_idx, r_idx, c_idx)] = ch
                elif block['type'] == 'paragraph':
                    txt = block['text']
                    if ch.old_text in txt or ch.new_text in txt:
                        pending_paras[block['paragraph_index']] = ch

    for h in editor.applied_highlights:
        if h.change_type == 'cell' and h.table_index is not None:
            applied_cells[_cell_key(h.table_index, h.row, h.col)] = h
        elif h.change_type == 'paragraph' and h.paragraph_index is not None:
            applied_paras[h.paragraph_index] = h

    # accepted pending도 applied 맵에 병합
    for ch in editor.pending_changes:
        if ch.status != 'accepted':
            continue
        if ch.change_type == 'cell' and ch.table_index is not None:
            k = _cell_key(ch.table_index, ch.row, ch.col)
            if k not in applied_cells:
                applied_cells[k] = AppliedHighlight(
                    'cell', ch.location, ch.old_text, ch.new_text,
                    ch.table_index, ch.row, ch.col)
        elif ch.change_type == 'paragraph' and ch.paragraph_index is not None:
            if ch.paragraph_index not in applied_paras:
                applied_paras[ch.paragraph_index] = AppliedHighlight(
                    'paragraph', ch.location, ch.old_text, ch.new_text,
                    paragraph_index=ch.paragraph_index)

    return pending_cells, pending_paras, applied_cells, applied_paras


def _render_applied_cell(live_text: str, highlight: AppliedHighlight) -> str:
    """적용된 셀 — 현재 값을 빨간색으로 (한글 파일과 동일)."""
    parts = []
    if highlight.old_text and highlight.old_text.strip() and highlight.old_text != live_text:
        parts.append(f'<span class="mod-applied-del">{_esc(highlight.old_text)}</span>')
    display = live_text if live_text.strip() else highlight.new_text
    if not display.strip():
        return '<span class="cell-empty">(비어 있음)</span>'
    parts.append(f'<span class="mod-applied">{_esc(display)}</span>')
    return ''.join(parts)


def _render_pending_cell(highlight: PendingChange) -> str:
    old = _esc(highlight.old_text) if highlight.old_text else '(비어 있음)'
    new = _esc(highlight.new_text)
    return f'<span class="old-pending">{old}</span> <span class="ins">{new}</span>'


def _render_paragraph(
    live_text: str,
    idx: int,
    pending: Optional[PendingChange],
    applied: Optional[AppliedHighlight],
) -> str:
    if pending:
        cls = 'para ch-pending'
        if pending.change_type == 'replace':
            body = (
                f'<span class="old-pending">{_esc(pending.old_text)}</span> '
                f'<span class="ins">{_esc(pending.new_text)}</span>'
            )
        else:
            body = (
                f'<span class="old-pending">{_esc(pending.old_text)}</span> '
                f'<span class="ins">{_esc(pending.new_text)}</span>'
            )
    elif applied:
        cls = 'para ch-applied'
        parts = []
        if applied.old_text and applied.old_text.strip() and applied.old_text != live_text:
            parts.append(f'<span class="mod-applied-del">{_esc(applied.old_text)}</span>')
        parts.append(f'<span class="mod-applied">{_esc(live_text)}</span>')
        body = ''.join(parts)
    else:
        cls = 'para'
        body = _esc(live_text)
    return f'<p class="{cls}" id="para-{idx}"><span class="para-num">{idx+1}</span>{body}</p>'


def _render_table_block(
    t_idx: int,
    parsed,
    pending_cells: dict,
    applied_cells: dict,
    max_rows: int = 50,
) -> str:
    parts = [f'<div class="tbl-wrap" id="table-{t_idx}">']
    parts.append(f'<div class="tbl-caption">표 {t_idx + 1}</div>')
    parts.append('<table class="hwpx-tbl"><tbody>')

    for r_idx, row in enumerate(parsed.rows[:max_rows]):
        parts.append('<tr>')
        for c_idx, cell in enumerate(row):
            if (r_idx, c_idx) in parsed.covered:
                continue
            merge = parsed.get_merge_at(r_idx, c_idx)
            span_attr = ''
            if merge:
                if merge.rowspan > 1:
                    span_attr += f' rowspan="{merge.rowspan}"'
                if merge.colspan > 1:
                    span_attr += f' colspan="{merge.colspan}"'

            tag = 'th' if r_idx == 0 else 'td'
            key = _cell_key(t_idx, r_idx, c_idx)
            pch = pending_cells.get(key)
            ach = applied_cells.get(key)
            if pch:
                cls, content = 'ch-pending', _render_pending_cell(pch)
            elif ach:
                cls, content = 'ch-applied', _render_applied_cell(str(cell), ach)
            elif not cell or not str(cell).strip():
                cls, content = 'cell-empty', '<span class="cell-empty">(비어 있음)</span>'
            else:
                cls, content = '', _esc(cell)
            parts.append(f'<{tag} class="{cls}"{span_attr}>{content}</{tag}>')
        parts.append('</tr>')

    if len(parsed.rows) > max_rows:
        ncol = parsed.num_cols or 1
        parts.append(
            f'<tr><td colspan="{ncol}" style="text-align:center;color:#999">'
            f'... 외 {len(parsed.rows) - max_rows}행 ...</td></tr>'
        )
    parts.append('</tbody></table></div>')
    return '\n'.join(parts)


def build_preview_html(
    editor: HWPXEditor,
    filename: str = '',
    max_paras: int = 200,
    max_tables: int = 20,
    max_rows_per_table: int = 50,
) -> str:
    pending_cells, pending_paras, applied_cells, applied_paras = _build_preview_maps(editor)
    parts = [PREVIEW_CSS, f'<!-- rev:{editor.preview_revision} -->']
    parts.append('<div class="doc-preview"><div class="doc-page">')

    parts.append('<div class="legend">')
    parts.append('<span><i class="dot" style="background:#ffc107"></i> 대기 중 (AI 제안)</span>')
    parts.append('<span><i class="dot" style="background:#cc0000"></i> 적용된 수정 (한글과 동일)</span>')
    parts.append('<span><i class="dot" style="background:#008800"></i> 제안된 새 내용</span>')
    parts.append('</div>')

    if filename:
        parts.append(f'<div class="doc-title">📄 {_esc(filename)}</div>')

    para_shown = 0
    table_shown = 0
    for block in editor.get_document_blocks():
        if block['type'] == 'paragraph':
            if para_shown >= max_paras:
                continue
            idx = block['paragraph_index']
            parts.append(_render_paragraph(
                block['text'], idx,
                pending_paras.get(idx),
                applied_paras.get(idx),
            ))
            para_shown += 1
        elif block['type'] == 'table':
            if table_shown >= max_tables:
                continue
            t_idx = block['table_index']
            parts.append(_render_table_block(
                t_idx, block['parsed'],
                pending_cells, applied_cells,
                max_rows=max_rows_per_table,
            ))
            table_shown += 1

    parts.append('</div></div>')
    return '\n'.join(parts)


def build_preview_from_text(
    paragraphs: list[str],
    tables: list[list[list[str]]],
    filename: str = '',
) -> str:
    parts = [PREVIEW_CSS, '<div class="doc-preview"><div class="doc-page">']
    if filename:
        parts.append(f'<div class="doc-title">📄 {_esc(filename)}</div>')
    for i, text in enumerate(paragraphs[:200]):
        parts.append(f'<p class="para"><span class="para-num">{i+1}</span>{_esc(text)}</p>')
    for t_idx, rows in enumerate(tables[:20]):
        if not rows:
            continue
        parts.append(f'<div class="tbl-wrap"><div class="tbl-caption">표 {t_idx+1}</div>')
        parts.append('<table class="hwpx-tbl"><tbody>')
        for r_idx, row in enumerate(rows[:30]):
            tag = 'th' if r_idx == 0 else 'td'
            parts.append('<tr>')
            for cell in row:
                parts.append(f'<{tag}>{_esc(cell)}</{tag}>')
            parts.append('</tr>')
        parts.append('</tbody></table></div>')
    parts.append('</div></div>')
    return '\n'.join(parts)
