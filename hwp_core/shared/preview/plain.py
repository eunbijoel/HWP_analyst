"""
Read-only document HTML preview (no HWPXEditor, no mutation chrome).

Product A and shared callers use this. Issue-row highlights are analysis-safe.
Edit pending/applied overlays live in hwp_core.editing.preview_layer.
"""

from __future__ import annotations

import html
import re

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
tr.issue-row td, tr.issue-row th {
    background: #fff3cd !important;
    outline: 2px solid #ff9800;
}
.tbl-wrap.issue-table {
    outline: 3px solid #ff5722;
    border-radius: 4px;
    box-shadow: 0 0 14px rgba(255, 152, 0, .45);
}
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


def esc(text: str) -> str:
    return html.escape(str(text) if text else "")


def build_preview_from_text(
    paragraphs: list[str],
    tables: list[list[list[str]]],
    filename: str = "",
    applied_changes: list[dict] | None = None,
    highlight_table: int | None = None,
    highlight_row: int | None = None,
) -> str:
    """Plain HTML preview from parsed paragraphs/tables (read-only)."""
    applied_by_line: dict[int, dict] = {}
    for ch in applied_changes or []:
        ln = int(ch.get("line") or 0)
        if ln > 0:
            applied_by_line[ln] = ch

    parts = [PREVIEW_CSS, '<div class="doc-preview"><div class="doc-page">']
    parts.append('<div class="legend">')
    if applied_changes:
        parts.append(
            '<span><i class="dot" style="background:#cc0000"></i> 적용된 수정</span>'
        )
    parts.append(
        '<span><i class="dot" style="background:#ff9800"></i> 검토 이슈 위치</span>'
    )
    parts.append("</div>")
    if filename:
        parts.append(f'<div class="doc-title">📄 {esc(filename)}</div>')
    for i, text in enumerate(paragraphs[:200]):
        line_no = i + 1
        applied = applied_by_line.get(line_no)
        if applied:
            if applied.get("type") == "delete":
                parts.append(
                    f'<p class="para ch-applied" id="para-{i}">'
                    f'<span class="para-num">{line_no}</span>'
                    f'<span class="mod-applied-del">{esc(applied.get("old", text))}</span>'
                    f"</p>"
                )
            else:
                old = applied.get("old", "")
                body_parts = []
                if old and old.strip() and old != text:
                    body_parts.append(f'<span class="mod-applied-del">{esc(old)}</span>')
                body_parts.append(f'<span class="mod-applied">{esc(text)}</span>')
                parts.append(
                    f'<p class="para ch-applied" id="para-{i}">'
                    f'<span class="para-num">{line_no}</span>{"".join(body_parts)}</p>'
                )
        else:
            parts.append(
                f'<p class="para"><span class="para-num">{line_no}</span>{esc(text)}</p>'
            )
    for t_idx, rows in enumerate(tables[:20]):
        if not rows:
            continue
        wrap_cls = "tbl-wrap issue-table" if highlight_table == t_idx else "tbl-wrap"
        parts.append(
            f'<div class="{wrap_cls}" id="table-{t_idx}">'
            f'<div class="tbl-caption">표 {t_idx + 1}</div>'
        )
        parts.append('<table class="hwpx-tbl"><tbody>')
        for r_idx, row in enumerate(rows[:50]):
            highlight_raw = None
            if highlight_table == t_idx and highlight_row is not None:
                highlight_raw = highlight_row + 1 if len(rows) > 1 else highlight_row
            tr_cls = (
                ' class="issue-row"'
                if (highlight_raw is not None and r_idx == highlight_raw)
                else ""
            )
            tag = "th" if r_idx == 0 else "td"
            rid = f' id="issue-row-{t_idx}-{r_idx}"' if tr_cls else ""
            parts.append(f"<tr{tr_cls}{rid}>")
            for cell in row:
                parts.append(f"<{tag}>{esc(cell)}</{tag}>")
            parts.append("</tr>")
        parts.append("</tbody></table></div>")
    parts.append("</div></div>")
    if highlight_table is not None:
        scroll_row = ""
        if highlight_row is not None:
            raw = highlight_row + 1
            scroll_row = (
                f'var r=document.getElementById("issue-row-{highlight_table}-{raw}");'
                'if(r){r.scrollIntoView({behavior:"smooth",block:"center"});}'
            )
        parts.append(
            "<script>"
            f'var t=document.getElementById("table-{highlight_table}");'
            'if(t){t.scrollIntoView({behavior:"smooth",block:"center"});}'
            + scroll_row
            + "</script>"
        )
    return "\n".join(parts)
