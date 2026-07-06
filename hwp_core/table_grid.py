"""
HWPX 표 그리드 파싱 — 병합 셀(cellAddr/cellSpan) 복원
hwp_parser / hwpx_editor / document_preview 공통 사용
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


def local_tag(tag: str) -> str:
    if '}' in tag:
        return tag.split('}')[-1]
    return tag


def get_cell_text(tc_elem: ET.Element) -> str:
    texts = []
    for elem in tc_elem.iter():
        if local_tag(elem.tag) == 't' and elem.text:
            texts.append(elem.text)
    result = ' '.join(texts).strip()
    return re.sub(r'\s+', ' ', result)


@dataclass
class CellMerge:
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1


@dataclass
class ParsedTableGrid:
    rows: list[list[str]] = field(default_factory=list)
    merges: list[CellMerge] = field(default_factory=list)
    covered: set[tuple[int, int]] = field(default_factory=set)

    @property
    def num_rows(self) -> int:
        return len(self.rows)

    @property
    def num_cols(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    def get_merge_at(self, row: int, col: int) -> CellMerge | None:
        for m in self.merges:
            if m.row == row and m.col == col:
                return m
        return None


def _collect_addressed_cells(
    tbl_elem: ET.Element,
) -> tuple[bool, list[tuple[int, int, int, int, ET.Element, str]], int, int]:
    """cellAddr 기반 셀 목록과 그리드 크기 반환."""
    row_cnt = int(tbl_elem.get('rowCnt', '0') or '0')
    col_cnt = int(tbl_elem.get('colCnt', '0') or '0')

    cells: list[tuple[int, int, int, int, ET.Element, str]] = []
    max_col = 0
    max_row = 0
    has_addr = False

    for tr_elem in tbl_elem:
        if local_tag(tr_elem.tag) not in ('tr', 'row'):
            continue
        for tc_elem in tr_elem:
            if local_tag(tc_elem.tag) not in ('tc', 'cell', 'td'):
                continue
            cell_text = get_cell_text(tc_elem)
            addr_elem = None
            span_elem = None
            for sub in tc_elem:
                st = local_tag(sub.tag)
                if st == 'cellAddr':
                    addr_elem = sub
                elif st == 'cellSpan':
                    span_elem = sub

            if addr_elem is not None:
                has_addr = True
                col = int(addr_elem.get('colAddr', '0'))
                row = int(addr_elem.get('rowAddr', '0'))
                cs = int(span_elem.get('colSpan', '1')) if span_elem is not None else 1
                rs = int(span_elem.get('rowSpan', '1')) if span_elem is not None else 1
                cells.append((row, col, cs, rs, tc_elem, cell_text))
                max_col = max(max_col, col + cs)
                max_row = max(max_row, row + rs)

    if has_addr and cells:
        if col_cnt > 0:
            max_col = max(max_col, col_cnt)
        if row_cnt > 0:
            max_row = max(max_row, row_cnt)

    return has_addr, cells, max_row, max_col


def build_element_grid(tbl_elem: ET.Element) -> list[list[tuple[ET.Element | None, str]]]:
    """편집용 — 각 셀의 (tc Element, 텍스트) 그리드. 병합 셀은 좌상단만 Element."""
    has_addr, cells, max_row, max_col = _collect_addressed_cells(tbl_elem)
    if not has_addr or not cells:
        return []

    grid: list[list[tuple[ET.Element | None, str]]] = [
        [(None, '') for _ in range(max_col)] for _ in range(max_row)
    ]
    for row_idx, col_idx, _cs, _rs, tc_elem, text in cells:
        if row_idx < max_row and col_idx < max_col:
            grid[row_idx][col_idx] = (tc_elem, text)
    return grid


def parse_table_grid(tbl_elem: ET.Element) -> ParsedTableGrid:
    row_cnt = int(tbl_elem.get('rowCnt', '0') or '0')
    col_cnt = int(tbl_elem.get('colCnt', '0') or '0')

    has_addr, cells, max_row, max_col = _collect_addressed_cells(tbl_elem)

    if has_addr and cells:
        grid = [[''] * max_col for _ in range(max_row)]
        merges: list[CellMerge] = []
        covered: set[tuple[int, int]] = set()

        for row_idx, col_idx, cs, rs, _tc, text in cells:
            if row_idx >= max_row or col_idx >= max_col:
                continue
            grid[row_idx][col_idx] = text
            if cs > 1 or rs > 1:
                merges.append(CellMerge(row_idx, col_idx, rs, cs))
            for r in range(row_idx, min(row_idx + rs, max_row)):
                for c in range(col_idx, min(col_idx + cs, max_col)):
                    if r == row_idx and c == col_idx:
                        continue
                    covered.add((r, c))
                    if not grid[r][c]:
                        grid[r][c] = text

        return ParsedTableGrid(rows=grid, merges=merges, covered=covered)

    return _parse_table_fallback(tbl_elem, row_cnt, col_cnt)


def _parse_table_fallback(tbl_elem: ET.Element, row_cnt: int, col_cnt: int) -> ParsedTableGrid:
    raw_cells: list[tuple[int, int, int, int, str]] = []
    fb_row_idx = 0
    for child in tbl_elem:
        if local_tag(child.tag) not in ('tr', 'row'):
            continue
        fb_col_idx = 0
        for cell_elem in child:
            if local_tag(cell_elem.tag) not in ('tc', 'cell', 'td'):
                continue
            text = get_cell_text(cell_elem)
            cs = int(cell_elem.get('colSpan', '1') or '1')
            rs = int(cell_elem.get('rowSpan', '1') or '1')
            raw_cells.append((fb_row_idx, fb_col_idx, cs, rs, text))
            fb_col_idx += cs
        fb_row_idx += 1

    if not raw_cells:
        return ParsedTableGrid()

    fb_max_row = max(r + rs for r, _, _, rs, _ in raw_cells)
    fb_max_col = max(col_cnt, max(c + cs for _, c, cs, _, _ in raw_cells))
    if row_cnt > 0:
        fb_max_row = max(fb_max_row, row_cnt)

    grid = [[''] * fb_max_col for _ in range(fb_max_row)]
    occupied = [[False] * fb_max_col for _ in range(fb_max_row)]
    merges: list[CellMerge] = []
    covered: set[tuple[int, int]] = set()

    row_cells: dict[int, list] = {}
    for r, c, cs, rs, text in raw_cells:
        row_cells.setdefault(r, []).append((c, cs, rs, text))

    for r_idx in sorted(row_cells.keys()):
        col_cursor = 0
        for _, cs, rs, text in row_cells[r_idx]:
            while col_cursor < fb_max_col and occupied[r_idx][col_cursor]:
                col_cursor += 1
            if col_cursor >= fb_max_col:
                break
            grid[r_idx][col_cursor] = text
            if cs > 1 or rs > 1:
                merges.append(CellMerge(r_idx, col_cursor, rs, cs))
            for dr in range(rs):
                for dc in range(cs):
                    rr, cc = r_idx + dr, col_cursor + dc
                    if rr < fb_max_row and cc < fb_max_col:
                        occupied[rr][cc] = True
                        if not (dr == 0 and dc == 0):
                            covered.add((rr, cc))
                        if not grid[rr][cc]:
                            grid[rr][cc] = text
            col_cursor += cs

    rows = [row for row in grid if any(str(cell).strip() for cell in row)]
    return ParsedTableGrid(rows=grid if grid else rows, merges=merges, covered=covered)


def is_inside_table(elem: ET.Element, root: ET.Element) -> bool:
    parent_map = {child: parent for parent in root.iter() for child in parent}
    current = elem
    while current in parent_map:
        current = parent_map[current]
        if local_tag(current.tag) in ('tbl', 'table'):
            return True
    return False
