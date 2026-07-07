"""
HWPX 문서 HTML 미리보기 — 변경사항 색상 하이라이트 (한글 Track Changes 스타일)
"""

import html
import re
from typing import Optional

from hwp_core.hwpx_editor import HWPXEditor, PendingChange, AppliedHighlight, text_locatable_in


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
/* 선택된 문단 (Canvas) */
.ch-selected { background: #e3f2fd; outline: 2px solid #2196f3; }
.para-clickable { cursor: pointer; }
.para-clickable:hover { background: #f5f9ff; }
.para-editable { outline: none; min-height: 1em; display: inline; }
.para-editable:focus { background: rgba(33, 150, 243, .08); border-radius: 2px; }
.para-hint {
    font-size: 10px; color: #999; margin-left: 6px; user-select: none;
}
.ch-focus {
    outline: 3px solid #ff5722 !important;
    box-shadow: 0 0 14px rgba(255, 152, 0, .55);
    animation: pulse-focus 0.9s ease-in-out 3;
}
@keyframes pulse-focus {
    0%, 100% { box-shadow: 0 0 8px rgba(255, 193, 7, .5); }
    50% { box-shadow: 0 0 18px rgba(255, 87, 34, .75); }
}
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
    if text_locatable_in(old, str(cell)):
        return True
    old_n = re.sub(r'[\s,]', '', old)
    cell_n = re.sub(r'[\s,]', '', str(cell))
    if not old_n:
        return False
    return old in str(cell) or cell_n == old_n or old_n in cell_n


def _map_replace_pending(
    ch: PendingChange,
    editor: HWPXEditor,
    pending_cells: dict,
    pending_paras: dict,
) -> None:
    """replace 유형 — 저장된 인덱스 또는 문서 스캔으로 위치 연결."""
    if ch.paragraph_index is not None:
        pending_paras[ch.paragraph_index] = ch
        return
    if ch.table_index is not None and ch.row is not None and ch.col is not None:
        pending_cells[_cell_key(ch.table_index, ch.row, ch.col)] = ch
        return
    if not ch.old_text:
        return
    for block in editor.get_document_blocks():
        if block['type'] == 'table':
            t_idx = block['table_index']
            for r_idx, row in enumerate(block['parsed'].rows):
                for c_idx, cell in enumerate(row):
                    if _values_match(ch.old_text, cell):
                        pending_cells[_cell_key(t_idx, r_idx, c_idx)] = ch
                        return
        elif block['type'] == 'paragraph':
            txt = block['text']
            editor_idx = editor.editor_index_for_block(
                block['paragraph_index'], txt,
            )
            if editor_idx is None:
                continue
            if text_locatable_in(ch.old_text, txt) or text_locatable_in(ch.new_text, txt):
                pending_paras[editor_idx] = ch
                return


def _build_preview_maps(editor: HWPXEditor):
    """pending / applied 위치 맵 구성."""
    pending_cells: dict[tuple, PendingChange] = {}
    pending_paras: dict[int, PendingChange] = {}
    pending_inserts: dict[int, list[PendingChange]] = {}
    applied_cells: dict[tuple, AppliedHighlight] = {}
    applied_paras: dict[int, AppliedHighlight] = {}

    for ch in editor.pending_changes:
        if ch.status != 'pending':
            continue
        if ch.change_type == 'cell' and ch.table_index is not None:
            pending_cells[_cell_key(ch.table_index, ch.row, ch.col)] = ch
        elif ch.change_type == 'paragraph' and ch.paragraph_index is not None:
            pending_paras[ch.paragraph_index] = ch
        elif ch.change_type == 'insert_after' and ch.paragraph_index is not None:
            pending_inserts.setdefault(ch.paragraph_index, []).append(ch)
        elif ch.change_type == 'replace':
            _map_replace_pending(ch, editor, pending_cells, pending_paras)

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

    return pending_cells, pending_paras, pending_inserts, applied_cells, applied_paras


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
    editor_idx: int,
    pending: Optional[PendingChange],
    applied: Optional[AppliedHighlight],
    *,
    canvas_mode: bool = False,
    selected: bool = False,
) -> str:
    extra_cls = ''
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
        if canvas_mode:
            body = f'<span class="para-editable">{_esc(live_text)}</span>'
        else:
            body = _esc(live_text)

    if canvas_mode and not pending:
        extra_cls = ' para-clickable'

    pid = f'pending-{pending.id}' if pending else f'para-{editor_idx}'
    data_attr = f' data-para-idx="{editor_idx}"' if canvas_mode else ''
    if canvas_mode and not pending:
        data_attr += f' data-para-orig="{_esc(live_text[:300])}"'
    hint = '' if canvas_mode else ''
    return (
        f'<p class="{cls}{extra_cls}" id="{pid}"{data_attr}>'
        f'<span class="para-num">{editor_idx + 1}</span>{body}{hint}</p>'
    )


def _render_table_block(
    t_idx: int,
    parsed,
    pending_cells: dict,
    applied_cells: dict,
    max_rows: int = 50,
) -> str:
    # TODO(canvas): 표 셀 클릭 선택·편집은 후속 단계에서 지원
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
                id_attr = f' id="pending-{pch.id}"'
            elif ach:
                cls, content = 'ch-applied', _render_applied_cell(str(cell), ach)
                id_attr = ''
            elif not cell or not str(cell).strip():
                cls, content = 'cell-empty', '<span class="cell-empty">(비어 있음)</span>'
                id_attr = ''
            else:
                cls, content = '', _esc(cell)
                id_attr = ''
            parts.append(f'<{tag} class="{cls}"{span_attr}{id_attr}>{content}</{tag}>')
        parts.append('</tr>')

    if len(parsed.rows) > max_rows:
        ncol = parsed.num_cols or 1
        parts.append(
            f'<tr><td colspan="{ncol}" style="text-align:center;color:#999">'
            f'... 외 {len(parsed.rows) - max_rows}행 ...</td></tr>'
        )
    parts.append('</tbody></table></div>')
    return '\n'.join(parts)


def format_pending_label(ch: PendingChange, max_len: int = 36) -> str:
    """대기 변경 네비게이션용 짧은 라벨."""
    if ch.old_text and ch.new_text:
        old = ch.old_text[:max_len] + ('…' if len(ch.old_text) > max_len else '')
        new = ch.new_text[:max_len] + ('…' if len(ch.new_text) > max_len else '')
        return f'"{old}" → "{new}"'
    if ch.location:
        return ch.location[: max_len * 2]
    return ch.change_type


def build_preview_html(
    editor: HWPXEditor,
    filename: str = '',
    max_paras: int = 200,
    max_tables: int = 20,
    max_rows_per_table: int = 50,
    scroll_to_change_id: str | None = None,
    canvas_mode: bool = False,
) -> str:
    pending_cells, pending_paras, pending_inserts, applied_cells, applied_paras = (
        _build_preview_maps(editor)
    )
    parts = [PREVIEW_CSS, f'<!-- rev:{editor.preview_revision} -->']
    parts.append('<div class="doc-preview"><div class="doc-page">')

    parts.append('<div class="legend">')
    parts.append('<span><i class="dot" style="background:#ffc107"></i> 대기 중 (AI 제안)</span>')
    parts.append('<span><i class="dot" style="background:#2196f3"></i> 선택 문단</span>')
    parts.append('<span><i class="dot" style="background:#cc0000"></i> 적용된 수정 (한글과 동일)</span>')
    parts.append('<span><i class="dot" style="background:#008800"></i> 제안된 새 내용</span>')
    parts.append('</div>')

    if filename:
        parts.append(f'<div class="doc-title">📄 {_esc(filename)}</div>')
    if canvas_mode:
        parts.append(
            '<div style="font-size:11px;color:#666;margin-bottom:10px">'
            '위 「편집할 문단」에서 선택 · 번호는 미리보기 왼쪽 숫자와 같음 · '
            '직접 수정은 선택 후 텍스트 상자 이용</div>'
        )

    para_shown = 0
    table_shown = 0
    block_map = editor.build_block_to_editor_paragraph_map() if canvas_mode else {}
    for block in editor.get_document_blocks():
        if block['type'] == 'paragraph':
            if para_shown >= max_paras:
                continue
            block_idx = block['paragraph_index']
            editor_idx = block_map.get(block_idx)
            if editor_idx is None:
                editor_idx = editor.editor_index_for_block(block_idx, block['text'])
            if editor_idx is None:
                continue
            parts.append(_render_paragraph(
                block['text'], editor_idx,
                pending_paras.get(editor_idx),
                applied_paras.get(editor_idx),
                canvas_mode=canvas_mode,
                selected=False,
            ))
            for ins in pending_inserts.get(editor_idx, []):
                for line in ins.new_text.split('\n'):
                    line = line.strip()
                    if line:
                        parts.append(
                            f'<p class="para ch-pending" id="pending-{ins.id}">'
                            f'<span class="ins">{_esc(line)}</span></p>'
                        )
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

    if scroll_to_change_id and not canvas_mode:
        cid = re.sub(r'[^\w\-]', '', scroll_to_change_id)
        parts.append(f"""<script>
(function() {{
  function focusPending() {{
    var el = document.getElementById("pending-{cid}");
    if (!el) return;
    el.scrollIntoView({{behavior: "smooth", block: "center"}});
    el.classList.add("ch-focus");
    setTimeout(function() {{ el.classList.remove("ch-focus"); }}, 2800);
  }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", focusPending);
  }} else {{
    focusPending();
  }}
}})();
</script>""")

    return '\n'.join(parts)


def append_viewer_scripts(
    html: str,
    *,
    selected_para_index: int | None = None,
    scroll_to_change_id: str | None = None,
    scroll_to_para_index: int | None = None,
) -> str:
    """캐시된 HTML에 선택/스크롤 스크립트만 추가 (캐시 무효화 없음)."""
    actions: list[str] = []
    if selected_para_index is not None:
        actions.append(
            f'var sel=document.getElementById("para-{selected_para_index}");'
            f'if(sel){{sel.classList.add("ch-selected");}}'
        )
    if scroll_to_change_id:
        cid = re.sub(r'[^\w\-]', '', scroll_to_change_id)
        actions.append(
            f'var pe=document.getElementById("pending-{cid}");'
            f'if(pe){{pe.scrollIntoView({{behavior:"smooth",block:"center"}});'
            f'pe.classList.add("ch-focus");'
            f'setTimeout(function(){{pe.classList.remove("ch-focus");}},2800);}}'
        )
    if scroll_to_para_index is not None:
        actions.append(
            f'var sp=document.getElementById("para-{scroll_to_para_index}");'
            f'if(sp){{sp.scrollIntoView({{behavior:"smooth",block:"center"}});'
            f'sp.classList.add("ch-selected");}}'
        )
    if not actions:
        return html
    body = ' '.join(actions)
    return html + f"""<script>
(function() {{
  function run() {{ {body} }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", run);
  }} else {{ run(); }}
}})();
</script>"""


def build_preview_from_text(
    paragraphs: list[str],
    tables: list[list[list[str]]],
    filename: str = '',
    applied_changes: list[dict] | None = None,
) -> str:
    applied_by_line: dict[int, dict] = {}
    for ch in applied_changes or []:
        ln = int(ch.get('line') or 0)
        if ln > 0:
            applied_by_line[ln] = ch

    parts = [PREVIEW_CSS, '<div class="doc-preview"><div class="doc-page">']
    parts.append('<div class="legend">')
    parts.append('<span><i class="dot" style="background:#cc0000"></i> 적용된 수정 (한글과 동일)</span>')
    parts.append('</div>')
    if filename:
        parts.append(f'<div class="doc-title">📄 {_esc(filename)}</div>')
    for i, text in enumerate(paragraphs[:200]):
        line_no = i + 1
        applied = applied_by_line.get(line_no)
        if applied:
            if applied.get('type') == 'delete':
                parts.append(
                    f'<p class="para ch-applied" id="para-{i}">'
                    f'<span class="para-num">{line_no}</span>'
                    f'<span class="mod-applied-del">{_esc(applied.get("old", text))}</span>'
                    f'</p>'
                )
            else:
                parts.append(_render_paragraph(
                    text, i, None, type('H', (), {
                        'old_text': applied.get('old', ''),
                        'new_text': applied.get('new', text),
                    })(),
                    canvas_mode=False,
                ))
        else:
            parts.append(f'<p class="para"><span class="para-num">{line_no}</span>{_esc(text)}</p>')
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
