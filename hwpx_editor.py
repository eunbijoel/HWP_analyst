"""
HWPX 문서 편집 모듈
- 원본 ZIP 내 section XML을 인플레이스 수정하여 서식 보존
- 찾기/바꾸기, 표 셀 편집, 합계 재계산 지원
- 수정된 셀은 빨간색 텍스트로 표시
"""

import copy
import io
import re
import zipfile
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional


TOTAL_KEYWORDS = ['합계', '총계', '소계', '계', '합', '총', 'total', 'sum', '전체']
RED_COLOR = '#FF0000'  # HWPX RGBColorType: #RRGGBB (10진수 아님)


@dataclass
class FindResult:
    section_file: str
    element_path: str
    original_text: str
    context: str


@dataclass
class EditLog:
    action: str
    detail: str


def _local_tag(tag: str) -> str:
    if '}' in tag:
        return tag.split('}')[-1]
    return tag


def _register_namespaces(xml_bytes: bytes):
    xml_str = xml_bytes.decode('utf-8', errors='ignore')
    ns_pairs = re.findall(r'xmlns(?::(\w+))?="([^"]+)"', xml_str)
    for prefix, uri in ns_pairs:
        try:
            ET.register_namespace(prefix if prefix else '', uri)
        except ValueError:
            pass


def _parse_number(s: str) -> Optional[float]:
    s = s.replace(',', '').replace(' ', '').strip()
    s = re.sub(r'[원천만백억조%명개건호]', '', s)
    if s.startswith('(') and s.endswith(')'):
        inner = s[1:-1].strip()
        if inner and all(c in '0123456789.' for c in inner):
            s = '-' + inner
    if s.startswith('△') or s.startswith('▲'):
        s = '-' + s[1:]
    try:
        return float(s)
    except ValueError:
        return None


def _format_number(value: float, reference: str) -> str:
    has_comma = ',' in reference
    is_int = value == int(value)

    if is_int:
        if has_comma:
            result = f"{int(value):,}"
        else:
            result = str(int(value))
    else:
        decimal_places = 1
        if '.' in reference:
            decimal_places = len(reference.rstrip('0').split('.')[-1])
            decimal_places = max(decimal_places, 1)
        raw = f"{value:.{decimal_places}f}"
        if has_comma:
            int_part, dec_part = raw.split('.')
            int_part = f"{int(float(int_part)):,}"
            result = f"{int_part}.{dec_part}"
        else:
            result = raw

    return result


class HWPXEditor:
    def __init__(self, file_bytes: bytes):
        self.original_bytes = file_bytes
        self.zip_contents: dict[str, bytes] = {}
        self.section_trees: dict[str, ET.Element] = {}
        self.section_xml_bytes: dict[str, bytes] = {}
        self.edit_log: list[EditLog] = []
        self._modified_runs: set = set()
        self._red_charpr_cache: dict[str, str] = {}  # base charPr id → red charPr id
        self._header_modified: bool = False
        self._header_file: Optional[str] = None
        self._header_tree: Optional[ET.Element] = None
        self._load()

    def _load(self):
        with zipfile.ZipFile(io.BytesIO(self.original_bytes)) as zf:
            for name in zf.namelist():
                self.zip_contents[name] = zf.read(name)

            section_files = sorted([
                f for f in zf.namelist()
                if 'section' in f.lower() and f.endswith('.xml')
            ])
            if not section_files:
                section_files = sorted([
                    f for f in zf.namelist()
                    if 'contents/' in f.lower() and f.endswith('.xml')
                ])

            for sf in section_files:
                xml_bytes = self.zip_contents[sf]
                self.section_xml_bytes[sf] = xml_bytes
                _register_namespaces(xml_bytes)
                try:
                    root = ET.fromstring(xml_bytes)
                except ET.ParseError:
                    xml_str = xml_bytes.decode('utf-8', errors='ignore')
                    xml_str = re.sub(r'xmlns\s*=\s*"[^"]*"', '', xml_str, count=1)
                    root = ET.fromstring(xml_str.encode('utf-8'))
                self.section_trees[sf] = root

            self._load_header()

    def _load_header(self):
        for name in self.zip_contents:
            if 'header' in name.lower() and name.endswith('.xml'):
                self._header_file = name
                break
        if not self._header_file:
            return
        xml_bytes = self.zip_contents[self._header_file]
        _register_namespaces(xml_bytes)
        try:
            self._header_tree = ET.fromstring(xml_bytes)
        except ET.ParseError:
            xml_str = xml_bytes.decode('utf-8', errors='ignore')
            xml_str = re.sub(r'xmlns\s*=\s*"[^"]*"', '', xml_str, count=1)
            try:
                self._header_tree = ET.fromstring(xml_str.encode('utf-8'))
            except ET.ParseError:
                self._header_tree = None

    def _char_properties_elem(self) -> Optional[ET.Element]:
        if self._header_tree is None:
            return None
        for elem in self._header_tree.iter():
            if _local_tag(elem.tag) == 'charProperties':
                return elem
        return None

    def _get_charpr_by_id(self, charpr_id: str) -> Optional[ET.Element]:
        if self._header_tree is None:
            return None
        for elem in self._header_tree.iter():
            if _local_tag(elem.tag) == 'charPr' and elem.get('id') == charpr_id:
                return elem
        return None

    def _max_charpr_id(self) -> int:
        max_id = 0
        if self._header_tree is None:
            return 0
        for elem in self._header_tree.iter():
            if _local_tag(elem.tag) == 'charPr':
                max_id = max(max_id, int(elem.get('id', '0')))
        return max_id

    def _get_or_create_red_charpr(self, base_charpr_id: str) -> Optional[str]:
        """원본 charPr를 복사해 textColor만 빨간색으로 — 폰트 등 나머지 서식 유지."""
        if base_charpr_id in self._red_charpr_cache:
            return self._red_charpr_cache[base_charpr_id]

        char_props_elem = self._char_properties_elem()
        base_cp = self._get_charpr_by_id(base_charpr_id)
        if char_props_elem is None or base_cp is None:
            return None

        new_id = self._max_charpr_id() + 1
        new_cp = copy.deepcopy(base_cp)
        new_cp.set('id', str(new_id))
        new_cp.set('textColor', RED_COLOR)
        char_props_elem.append(new_cp)

        item_cnt = char_props_elem.get('itemCnt')
        if item_cnt is not None:
            try:
                char_props_elem.set('itemCnt', str(int(item_cnt) + 1))
            except ValueError:
                pass

        self._red_charpr_cache[base_charpr_id] = str(new_id)
        self._header_modified = True
        return str(new_id)

    def _mark_runs_red(self, tc_elem: ET.Element):
        """수정된 셀의 run — 원래 charPr 기반으로 빨간색 charPr 참조."""
        if self._header_tree is None:
            return
        for elem in tc_elem.iter():
            if _local_tag(elem.tag) != 'run':
                continue
            base_id = elem.get('charPrIDRef', '0')
            red_id = self._get_or_create_red_charpr(base_id)
            if red_id:
                elem.set('charPrIDRef', red_id)
                self._modified_runs.add(id(elem))

    def find_all(self, search_text: str) -> list[FindResult]:
        results = []
        for section_name, root in self.section_trees.items():
            for elem in root.iter():
                if _local_tag(elem.tag) == 't' and elem.text and search_text in elem.text:
                    results.append(FindResult(
                        section_file=section_name,
                        element_path=_local_tag(elem.tag),
                        original_text=elem.text,
                        context=elem.text,
                    ))
        return results

    def replace_all(self, old_text: str, new_text: str) -> int:
        count = 0
        modified_tcs = []
        for section_name, root in self.section_trees.items():
            for elem in root.iter():
                if _local_tag(elem.tag) == 't' and elem.text and old_text in elem.text:
                    elem.text = elem.text.replace(old_text, new_text)
                    count += 1
                    # t의 상위 tc를 찾아서 색상 표시
                    tc = self._find_ancestor_tc(root, elem)
                    if tc is not None:
                        modified_tcs.append(tc)
        if count > 0:
            for tc in modified_tcs:
                self._mark_runs_red(tc)
            self.edit_log.append(EditLog(
                action="찾기/바꾸기",
                detail=f"'{old_text}' → '{new_text}' ({count}건)",
            ))
            self._recalculate_all_totals()
        return count

    def _find_ancestor_tc(self, root: ET.Element, target: ET.Element) -> Optional[ET.Element]:
        """target 요소를 포함하는 tc 요소를 찾는다."""
        parent_map = {child: parent for parent in root.iter() for child in parent}
        current = target
        while current in parent_map:
            current = parent_map[current]
            if _local_tag(current.tag) in ('tc', 'cell', 'td'):
                return current
        return None

    def _recalculate_all_totals(self):
        for t_idx in range(self.get_table_count()):
            self.recalculate_totals(t_idx)

    def _get_tables(self, root: ET.Element) -> list[ET.Element]:
        tables = []
        for elem in root.iter():
            if _local_tag(elem.tag) in ('tbl', 'table'):
                tables.append(elem)
        return tables

    def _get_cell_at(self, tbl_elem: ET.Element, target_row: int, target_col: int) -> Optional[ET.Element]:
        for tr_elem in tbl_elem:
            if _local_tag(tr_elem.tag) not in ('tr', 'row'):
                continue
            for tc_elem in tr_elem:
                if _local_tag(tc_elem.tag) not in ('tc', 'cell', 'td'):
                    continue
                for sub in tc_elem:
                    if _local_tag(sub.tag) == 'cellAddr':
                        r = int(sub.get('rowAddr', '-1'))
                        c = int(sub.get('colAddr', '-1'))
                        if r == target_row and c == target_col:
                            return tc_elem
        return None

    def _set_cell_text(self, tc_elem: ET.Element, new_text: str):
        t_elems = [e for e in tc_elem.iter() if _local_tag(e.tag) == 't']
        if t_elems:
            t_elems[0].text = new_text
            for extra in t_elems[1:]:
                extra.text = ''
        else:
            for sub in tc_elem.iter():
                if _local_tag(sub.tag) == 'run':
                    t_tag = sub.tag.replace(_local_tag(sub.tag), 't')
                    t_elem = ET.SubElement(sub, t_tag)
                    t_elem.text = new_text
                    break

    def _get_cell_text(self, tc_elem: ET.Element) -> str:
        texts = []
        for elem in tc_elem.iter():
            if _local_tag(elem.tag) == 't' and elem.text:
                texts.append(elem.text)
        return ' '.join(texts).strip()

    def edit_table_cell(self, table_index: int, row: int, col: int, new_value: str) -> bool:
        all_tables = []
        for section_name, root in self.section_trees.items():
            for tbl in self._get_tables(root):
                all_tables.append((section_name, tbl))

        if table_index >= len(all_tables):
            return False

        section_name, tbl = all_tables[table_index]
        tc = self._get_cell_at(tbl, row, col)
        if tc is None:
            return False

        old_text = self._get_cell_text(tc)
        self._set_cell_text(tc, new_value)
        self._mark_runs_red(tc)
        self.edit_log.append(EditLog(
            action="셀 수정",
            detail=f"표{table_index+1} ({row},{col}): '{old_text}' → '{new_value}'",
        ))
        return True

    def _build_table_grid(self, tbl_elem: ET.Element) -> list[list[tuple[ET.Element, str]]]:
        cells_info = []
        max_row = 0
        max_col = 0
        has_addr = False

        for tr_elem in tbl_elem:
            if _local_tag(tr_elem.tag) not in ('tr', 'row'):
                continue
            for tc_elem in tr_elem:
                if _local_tag(tc_elem.tag) not in ('tc', 'cell', 'td'):
                    continue
                addr_elem = None
                span_elem = None
                for sub in tc_elem:
                    sub_tag = _local_tag(sub.tag)
                    if sub_tag == 'cellAddr':
                        addr_elem = sub
                    elif sub_tag == 'cellSpan':
                        span_elem = sub

                if addr_elem is not None:
                    has_addr = True
                    r = int(addr_elem.get('rowAddr', '0'))
                    c = int(addr_elem.get('colAddr', '0'))
                    rs = int(span_elem.get('rowSpan', '1')) if span_elem else 1
                    cs = int(span_elem.get('colSpan', '1')) if span_elem else 1
                    text = self._get_cell_text(tc_elem)
                    cells_info.append((r, c, rs, cs, tc_elem, text))
                    max_row = max(max_row, r + rs)
                    max_col = max(max_col, c + cs)

        if not has_addr or not cells_info:
            return []

        row_cnt_attr = int(tbl_elem.get('rowCnt', '0'))
        col_cnt_attr = int(tbl_elem.get('colCnt', '0'))
        if row_cnt_attr > 0:
            max_row = max(max_row, row_cnt_attr)
        if col_cnt_attr > 0:
            max_col = max(max_col, col_cnt_attr)

        grid = [[(None, '') for _ in range(max_col)] for _ in range(max_row)]
        for r, c, rs, cs, tc_elem, text in cells_info:
            if r < max_row and c < max_col:
                grid[r][c] = (tc_elem, text)

        return grid

    def recalculate_totals(self, table_index: int) -> bool:
        all_tables = []
        for section_name, root in self.section_trees.items():
            for tbl in self._get_tables(root):
                all_tables.append((section_name, tbl))

        if table_index >= len(all_tables):
            return False

        section_name, tbl = all_tables[table_index]
        grid = self._build_table_grid(tbl)
        if not grid:
            return False

        num_rows = len(grid)
        num_cols = len(grid[0]) if grid else 0

        total_rows = []
        for r_idx in range(num_rows):
            for c_idx in range(num_cols):
                tc, text = grid[r_idx][c_idx]
                if text.strip().lower() in TOTAL_KEYWORDS:
                    total_rows.append(r_idx)
                    break

        if not total_rows:
            return False

        updated = False
        for total_row in total_rows:
            data_rows_above = [r for r in range(total_row) if r not in total_rows]
            if not data_rows_above:
                continue

            for c_idx in range(num_cols):
                total_tc, total_text = grid[total_row][c_idx]
                if total_tc is None or total_text.strip().lower() in TOTAL_KEYWORDS:
                    continue

                total_num = _parse_number(total_text)
                if total_num is None:
                    continue

                col_sum = 0.0
                count = 0
                for r in data_rows_above:
                    _, cell_text = grid[r][c_idx]
                    if not cell_text.strip():
                        continue
                    val = _parse_number(cell_text)
                    if val is not None:
                        col_sum += val
                        count += 1

                if count > 0 and col_sum != total_num:
                    new_text = _format_number(col_sum, total_text)
                    self._set_cell_text(total_tc, new_text)
                    self._mark_runs_red(total_tc)
                    updated = True

        if updated:
            self.edit_log.append(EditLog(
                action="합계 재계산",
                detail=f"표{table_index+1} 합계/소계 행 업데이트",
            ))
        return updated

    def save(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name in self.zip_contents:
                if name in self.section_trees:
                    root = self.section_trees[name]
                    xml_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
                    zf.writestr(name, xml_bytes)
                elif name == self._header_file and self._header_tree is not None and self._header_modified:
                    xml_bytes = ET.tostring(self._header_tree, encoding='utf-8', xml_declaration=True)
                    zf.writestr(name, xml_bytes)
                else:
                    zf.writestr(name, self.zip_contents[name])
        return buf.getvalue()

    def get_table_count(self) -> int:
        count = 0
        for root in self.section_trees.values():
            count += len(self._get_tables(root))
        return count

    def get_table_as_rows(self, table_index: int) -> list[list[str]]:
        all_tables = []
        for section_name, root in self.section_trees.items():
            for tbl in self._get_tables(root):
                all_tables.append(tbl)

        if table_index >= len(all_tables):
            return []

        grid = self._build_table_grid(all_tables[table_index])
        return [[text for _, text in row] for row in grid]
