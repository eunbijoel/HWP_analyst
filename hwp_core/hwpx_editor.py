"""
HWPX 문서 편집 모듈
- 원본 ZIP 내 section XML을 인플레이스 수정하여 서식 보존
- 찾기/바꾸기, 표 셀 편집, 합계 재계산 지원
- AI 편집용: 빈칸 감지, 변경 제안(diff), Track Changes 스타일, 선택 영역 편집
"""

import copy
import io
import re
import uuid
import zipfile
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional

from .table_extractor import (
    parse_table_grid, is_inside_table, local_tag, build_element_grid,
)
TOTAL_KEYWORDS = ['합계', '총계', '소계', '계', '합', '총', 'total', 'sum', '전체']
RED_COLOR = '#FF0000'
GREEN_COLOR = '#008800'
PLACEHOLDER_RE = re.compile(
    r'^[\s□○●◎◇◆▪▫·\-_=…\.…\(\)（）\[\]【】<>〈〉\'\"`~]*$'
    r'|^(입력|기재|작성|해당\s*없음|n/?a|tbd|미정|예시|ex\)|예\))[\s\.]*$',
    re.IGNORECASE,
)
PLACEHOLDER_SUBSTR = ('○○', '□□', '___', '...', '···', '　　', '  ')


@dataclass
class BlankField:
    field_type: str  # 'cell' | 'paragraph'
    location: str
    context: str
    current_text: str
    table_index: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    paragraph_index: Optional[int] = None
    section_file: Optional[str] = None


@dataclass
class PendingChange:
    id: str
    change_type: str  # 'cell' | 'paragraph' | 'replace' | 'append' | 'insert_after'
    location: str
    old_text: str
    new_text: str
    status: str = 'pending'  # pending | accepted | rejected
    table_index: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    paragraph_index: Optional[int] = None
    section_file: Optional[str] = None
    search_hint: str = ''


@dataclass
class AppliedHighlight:
    """미리보기용 — 화면에 반영된 수정 위치 기록."""
    change_type: str
    location: str
    old_text: str
    new_text: str
    table_index: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None
    paragraph_index: Optional[int] = None


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


def _normalize_value(s: str) -> str:
    return re.sub(r'[\s,]', '', str(s).strip())


def text_locatable_in(needle: str, haystack: str) -> bool:
    """공백·줄바꿈 차이를 무시하고 needle이 haystack에 있는지 확인."""
    if not needle or not haystack:
        return False
    if needle in haystack:
        return True
    n = re.sub(r'\s+', '', needle.strip())
    h = re.sub(r'\s+', '', haystack.strip())
    if not n:
        return False
    return n in h


def _cell_contains_value(cell: str, value: str) -> bool:
    cv = _normalize_value(cell)
    vv = _normalize_value(value)
    if not vv:
        return False
    return cv == vv or vv in cv


def _format_replacement(cell: str, old_val: str, new_val: str) -> str:
    """셀 텍스트에서 old→new 치환. 숫자면 콤마 서식 유지."""
    if _normalize_value(cell) == _normalize_value(old_val):
        ref = cell if _normalize_value(cell) else old_val
        if ',' in ref and re.fullmatch(r'[\d,\.]+', _normalize_value(new_val)):
            try:
                n = float(_normalize_value(new_val))
                if n == int(n):
                    return f"{int(n):,}"
            except ValueError:
                pass
        return new_val
    if old_val in cell:
        return cell.replace(old_val, new_val, 1)
    return new_val


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
        self.pending_changes: list[PendingChange] = []
        self.applied_highlights: list[AppliedHighlight] = []
        self.preview_revision: int = 0
        self._blocks_cache: Optional[list] = None
        self._paragraphs_cache: Optional[list] = None
        self._saved_bytes_cache: Optional[bytes] = None
        self._saved_bytes_rev: int = -1
        self._modified_runs: set = set()
        self._red_charpr_cache: dict[str, str] = {}
        self._green_charpr_cache: dict[str, str] = {}
        self._strike_charpr_cache: dict[str, str] = {}
        self._header_modified: bool = False
        self._header_file: Optional[str] = None
        self._header_tree: Optional[ET.Element] = None
        self._zip_order: list[str] = []
        self._dirty_sections: set[str] = set()
        self._native_modified: bool = False
        self._hwpilot_touched: bool = False
        self._source_filename: str = 'doc.hwpx'
        self._zip_infos: dict[str, zipfile.ZipInfo] = {}
        self._load()

    def _mark_section_dirty(self, section_file: Optional[str] = None):
        if section_file:
            self._dirty_sections.add(section_file)
        self._native_modified = True
        self._invalidate_structure_cache()

    def _load(self):
        if not zipfile.is_zipfile(io.BytesIO(self.original_bytes)):
            raise ValueError('유효한 HWPX(ZIP) 파일이 아닙니다.')
        self._zip_infos = {}
        with zipfile.ZipFile(io.BytesIO(self.original_bytes)) as zf:
            self._zip_order = list(zf.namelist())
            for info in zf.infolist():
                self._zip_infos[info.filename] = info
                self.zip_contents[info.filename] = zf.read(info.filename)

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
            if local_tag(elem.tag) == 'charProperties':
                return elem
        return None

    def _get_charpr_by_id(self, charpr_id: str) -> Optional[ET.Element]:
        if self._header_tree is None:
            return None
        for elem in self._header_tree.iter():
            if local_tag(elem.tag) == 'charPr' and elem.get('id') == charpr_id:
                return elem
        return None

    def _max_charpr_id(self) -> int:
        max_id = 0
        if self._header_tree is None:
            return 0
        for elem in self._header_tree.iter():
            if local_tag(elem.tag) == 'charPr':
                max_id = max(max_id, int(elem.get('id', '0')))
        return max_id

    def _get_or_create_styled_charpr(self, base_charpr_id: str, *,
                                     color: Optional[str] = None,
                                     strikeout: bool = False,
                                     cache: dict) -> Optional[str]:
        cache_key = f"{base_charpr_id}|{color}|{strikeout}"
        if cache_key in cache:
            return cache[cache_key]

        char_props_elem = self._char_properties_elem()
        base_cp = self._get_charpr_by_id(base_charpr_id)
        if char_props_elem is None or base_cp is None:
            return None

        new_id = self._max_charpr_id() + 1
        new_cp = copy.deepcopy(base_cp)
        new_cp.set('id', str(new_id))
        if color:
            new_cp.set('textColor', color)
        if strikeout:
            new_cp.set('strikeout', 'SOLID')
        char_props_elem.append(new_cp)

        item_cnt = char_props_elem.get('itemCnt')
        if item_cnt is not None:
            try:
                char_props_elem.set('itemCnt', str(int(item_cnt) + 1))
            except ValueError:
                pass

        cache[cache_key] = str(new_id)
        self._header_modified = True
        return str(new_id)

    def _get_or_create_red_charpr(self, base_charpr_id: str) -> Optional[str]:
        return self._get_or_create_styled_charpr(
            base_charpr_id, color=RED_COLOR, cache=self._red_charpr_cache)

    def _get_or_create_green_charpr(self, base_charpr_id: str) -> Optional[str]:
        return self._get_or_create_styled_charpr(
            base_charpr_id, color=GREEN_COLOR, cache=self._green_charpr_cache)

    def _get_or_create_strike_charpr(self, base_charpr_id: str) -> Optional[str]:
        return self._get_or_create_styled_charpr(
            base_charpr_id, color=RED_COLOR, strikeout=True, cache=self._strike_charpr_cache)

    def _mark_runs_style(self, container: ET.Element, style: str = 'red'):
        if self._header_tree is None:
            return
        for elem in container.iter():
            if local_tag(elem.tag) != 'run':
                continue
            base_id = elem.get('charPrIDRef', '0')
            if style == 'green':
                styled_id = self._get_or_create_green_charpr(base_id)
            elif style == 'strike':
                styled_id = self._get_or_create_strike_charpr(base_id)
            else:
                styled_id = self._get_or_create_red_charpr(base_id)
            if styled_id:
                elem.set('charPrIDRef', styled_id)
                self._modified_runs.add(id(elem))

    def _mark_runs_red(self, tc_elem: ET.Element):
        self._mark_runs_style(tc_elem, 'red')

    def _mark_runs_green(self, container: ET.Element):
        self._mark_runs_style(container, 'green')

    def _is_blank_text(self, text: str) -> bool:
        t = (text or '').strip()
        if not t:
            return True
        if PLACEHOLDER_RE.match(t):
            return True
        if len(t) <= 3 and all(c in '□○●-_·' for c in t):
            return True
        return any(p in t for p in PLACEHOLDER_SUBSTR) and len(t) < 20

    def _find_ancestor_tc(self, root: ET.Element, target: ET.Element) -> Optional[ET.Element]:
        """target 요소를 포함하는 tc 요소를 찾는다."""
        parent_map = {child: parent for parent in root.iter() for child in parent}
        current = target
        while current in parent_map:
            current = parent_map[current]
            if local_tag(current.tag) in ('tc', 'cell', 'td'):
                return current
        return None

    def _recalculate_all_totals(self):
        for t_idx in range(self.get_table_count()):
            self.recalculate_totals(t_idx)

    def _get_tables(self, root: ET.Element) -> list[ET.Element]:
        tables = []
        for elem in root.iter():
            if local_tag(elem.tag) in ('tbl', 'table'):
                tables.append(elem)
        return tables

    def _get_cell_at(self, tbl_elem: ET.Element, target_row: int, target_col: int) -> Optional[ET.Element]:
        for tr_elem in tbl_elem:
            if local_tag(tr_elem.tag) not in ('tr', 'row'):
                continue
            for tc_elem in tr_elem:
                if local_tag(tc_elem.tag) not in ('tc', 'cell', 'td'):
                    continue
                for sub in tc_elem:
                    if local_tag(sub.tag) == 'cellAddr':
                        r = int(sub.get('rowAddr', '-1'))
                        c = int(sub.get('colAddr', '-1'))
                        if r == target_row and c == target_col:
                            return tc_elem
        return None

    def _set_cell_text(self, tc_elem: ET.Element, new_text: str):
        t_elems = [e for e in tc_elem.iter() if local_tag(e.tag) == 't']
        if t_elems:
            t_elems[0].text = new_text
            for extra in t_elems[1:]:
                extra.text = ''
        else:
            for sub in tc_elem.iter():
                if local_tag(sub.tag) == 'run':
                    t_tag = sub.tag.replace(local_tag(sub.tag), 't')
                    t_elem = ET.SubElement(sub, t_tag)
                    t_elem.text = new_text
                    break

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

        self._set_cell_text(tc, new_value)
        self._mark_runs_red(tc)
        self._mark_section_dirty(section_name)
        return True

    def recalculate_totals(self, table_index: int) -> bool:
        all_tables = []
        for section_name, root in self.section_trees.items():
            for tbl in self._get_tables(root):
                all_tables.append((section_name, tbl))

        if table_index >= len(all_tables):
            return False

        section_name, tbl = all_tables[table_index]
        grid = build_element_grid(tbl)
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
            self._mark_section_dirty(section_name)
        return updated

    def save(self) -> bytes:
        """변경된 섹션/헤더만 재직렬화. ZIP 압축 방식·순서는 원본 유지."""
        order = self._zip_order or list(self.zip_contents.keys())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            for name in order:
                if name not in self.zip_contents:
                    continue
                if name == self._header_file and self._header_tree is not None and self._header_modified:
                    data = ET.tostring(
                        self._header_tree, encoding='utf-8', xml_declaration=True)
                elif name in self._dirty_sections and name in self.section_trees:
                    root = self.section_trees[name]
                    data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
                else:
                    data = self.zip_contents[name]

                orig = self._zip_infos.get(name)
                if orig is not None:
                    info = zipfile.ZipInfo(name)
                    info.compress_type = orig.compress_type
                    info.external_attr = orig.external_attr
                    info.date_time = orig.date_time
                    zf.writestr(info, data)
                else:
                    zf.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        result = buf.getvalue()
        self._commit_saved_zip(result)
        return result

    def _commit_saved_zip(self, data: bytes):
        """save() 결과를 에디터 상태에 반영."""
        self.original_bytes = data
        self._zip_infos = {}
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            self._zip_order = list(zf.namelist())
            self.zip_contents = {n: zf.read(n) for n in self._zip_order}
            for info in zf.infolist():
                self._zip_infos[info.filename] = info
        self._dirty_sections = set()
        self._native_modified = False
        self._header_modified = False
        self._saved_bytes_cache = data
        self._saved_bytes_rev = self.preview_revision

    def get_working_bytes(self) -> bytes:
        """hwpilot 등 외부 편집에 넘길 바이트 (불필요한 XML 재직렬화 방지)."""
        if not self._native_modified and not self._header_modified:
            return self.original_bytes
        return self.save()

    def reload_from_bytes(self, file_bytes: bytes, *, from_hwpilot: bool = False):
        """외부 편집(hwpilot 등) 결과로 에디터 상태를 갱신."""
        self.original_bytes = file_bytes
        self.zip_contents.clear()
        self.section_trees.clear()
        self.section_xml_bytes.clear()
        self.pending_changes.clear()
        self.applied_highlights.clear()
        self._blocks_cache = None
        self._paragraphs_cache = None
        self._saved_bytes_cache = None
        self._saved_bytes_rev = -1
        self._modified_runs.clear()
        self._red_charpr_cache.clear()
        self._green_charpr_cache.clear()
        self._strike_charpr_cache.clear()
        self._header_modified = False
        self._header_file = None
        self._header_tree = None
        self._zip_order = []
        self._dirty_sections = set()
        self._native_modified = False
        self._hwpilot_touched = from_hwpilot
        self._zip_infos = {}
        self._load()
        self._bump_preview()

    def get_export_bytes(self) -> bytes:
        """한글에서 열 HWPX bytes — hwpilot 편집본은 재저장 없이 그대로 반환."""
        if self._hwpilot_touched:
            return bytes(self.original_bytes)
        if not self._native_modified and not self._header_modified:
            return bytes(self.original_bytes)
        return bytes(self.save())

    @staticmethod
    def validate_hwpx_bytes(data: bytes) -> tuple[bool, str]:
        if not isinstance(data, (bytes, bytearray)):
            return False, '파일 데이터 형식 오류'
        if len(data) < 4 or data[:2] != b'PK':
            return False, 'HWPX(ZIP) 파일이 아닙니다'
        if not zipfile.is_zipfile(io.BytesIO(data)):
            return False, 'ZIP 구조가 손상되었습니다'
        return True, ''

    def apply_hwpilot_insert_after(self, anchor: str, body: str, filename: str = 'doc.hwpx') -> tuple[bool, str]:
        """hwpilot으로 앵커 아래 삽입 후 파일 bytes 갱신."""
        from hwp_core.hwp_backends import apply_hwpilot_to_bytes

        current = self.get_working_bytes()

        def _edit(path: str) -> tuple[bool, str]:
            from hwp_core.hwp_backends import hwpilot_insert_after_anchor
            return hwpilot_insert_after_anchor(path, anchor, body)

        new_bytes, msg = apply_hwpilot_to_bytes(current, filename, _edit)
        if new_bytes is None:
            return False, msg
        self.reload_from_bytes(new_bytes, from_hwpilot=True)
        self.applied_highlights.append(AppliedHighlight(
            change_type='insert_after',
            location=f'hwpilot: {anchor[:40]}',
            old_text='',
            new_text=body[:200],
        ))
        return True, msg

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

        parsed = parse_table_grid(all_tables[table_index])
        return parsed.rows

    # --- 빈칸 감지 / 문단 목록 ---

    def detect_blanks(self, meaningful_only: bool = True) -> list[BlankField]:
        blanks: list[BlankField] = []
        for t_idx in range(self.get_table_count()):
            rows = self.get_table_as_rows(t_idx)
            if not rows:
                continue
            header = rows[0] if rows else []
            for r_idx, row in enumerate(rows):
                for c_idx, cell in enumerate(row):
                    if not self._is_blank_text(cell):
                        continue
                    label_parts = []
                    if c_idx < len(header) and header[c_idx].strip():
                        label_parts.append(header[c_idx].strip())
                    if r_idx > 0 and r_idx < len(rows):
                        row_label = rows[r_idx][0] if rows[r_idx] else ''
                        if row_label.strip() and row_label.strip() not in label_parts:
                            label_parts.insert(0, row_label.strip())

                    if meaningful_only:
                        # 의미 없는 빈 셀(병합 패딩, 헤더행 빈칸) 제외
                        if r_idx == 0 and not label_parts:
                            continue
                        if not label_parts:
                            continue
                        # 같은 행에 데이터가 하나도 없으면 스킵
                        if r_idx > 0 and not any(
                            rows[r_idx][j].strip() for j in range(len(rows[r_idx])) if j != c_idx
                        ):
                            continue

                    context = ' / '.join(label_parts) if label_parts else f'열{c_idx+1}'
                    blanks.append(BlankField(
                        field_type='cell',
                        location=f'표{t_idx+1} ({r_idx},{c_idx})',
                        context=context,
                        current_text=cell,
                        table_index=t_idx,
                        row=r_idx,
                        col=c_idx,
                    ))

        for p_idx, para in enumerate(self.get_paragraphs()):
            if self._is_blank_text(para['text']):
                blanks.append(BlankField(
                    field_type='paragraph',
                    location=f'문단 {p_idx+1}',
                    context=para.get('preview', ''),
                    current_text=para['text'],
                    paragraph_index=p_idx,
                    section_file=para.get('section_file'),
                ))
        return blanks

    def get_paragraphs(self) -> list[dict]:
        if self._paragraphs_cache is not None:
            return self._paragraphs_cache
        paragraphs = []
        for section_name, root in self.section_trees.items():
            for elem in root.iter():
                if local_tag(elem.tag) != 'p':
                    continue
                if is_inside_table(elem, root):
                    continue
                texts = []
                for t_elem in elem.iter():
                    if local_tag(t_elem.tag) == 't' and t_elem.text:
                        texts.append(t_elem.text)
                text = ''.join(texts).strip()
                if not text:
                    continue
                preview = text[:80] + ('...' if len(text) > 80 else '')
                paragraphs.append({
                    'index': len(paragraphs),
                    'text': text,
                    'preview': preview,
                    'section_file': section_name,
                    'elem': elem,
                })
        self._paragraphs_cache = paragraphs
        return paragraphs

    def get_document_blocks(self) -> list[dict]:
        """문서 순서대로 문단/표 블록 반환 (미리보기용)."""
        if self._blocks_cache is not None:
            return self._blocks_cache
        from .hwp_parser import _get_text_from_element

        blocks: list[dict] = []
        para_counter = 0
        table_counter = 0

        def collect_ordered(root):
            ordered = []

            def walk(elem):
                tag = local_tag(elem.tag)
                if tag in ('tbl', 'table'):
                    ordered.append(('table', elem))
                    return
                if tag == 'p':
                    nested_tbls = [
                        x for x in elem.iter()
                        if x is not elem and local_tag(x.tag) in ('tbl', 'table')
                    ]
                    if nested_tbls:
                        text = _get_text_from_element(elem, skip_tables=True).strip()
                        if text:
                            ordered.append(('paragraph', text))
                        for tbl in nested_tbls:
                            ordered.append(('table', tbl))
                    else:
                        if is_inside_table(elem, root):
                            return
                        text = _get_text_from_element(elem).strip()
                        if text:
                            ordered.append(('paragraph', text))
                    return
                for child in elem:
                    walk(child)

            walk(root)
            return ordered

        for section_name, root in self.section_trees.items():
            for kind, payload in collect_ordered(root):
                if kind == 'paragraph':
                    blocks.append({
                        'type': 'paragraph',
                        'paragraph_index': para_counter,
                        'text': payload,
                        'section_file': section_name,
                    })
                    para_counter += 1
                else:
                    parsed = parse_table_grid(payload)
                    if parsed.rows:
                        blocks.append({
                            'type': 'table',
                            'table_index': table_counter,
                            'parsed': parsed,
                            'section_file': section_name,
                        })
                        table_counter += 1
        self._blocks_cache = blocks
        return blocks

    # --- 변경 제안 (diff) ---

    def propose_cell_change(self, table_index: int, row: int, col: int,
                            new_value: str, context: str = '') -> PendingChange:
        rows = self.get_table_as_rows(table_index)
        old = ''
        if row < len(rows) and col < len(rows[row]):
            old = rows[row][col]
        change = PendingChange(
            id=str(uuid.uuid4())[:8],
            change_type='cell',
            location=f'표{table_index+1} ({row},{col})' + (f' — {context}' if context else ''),
            old_text=old,
            new_text=new_value,
            table_index=table_index,
            row=row,
            col=col,
        )
        self.pending_changes.append(change)
        self._bump_preview()
        return change

    def _bump_preview(self):
        self.preview_revision += 1

    def _invalidate_structure_cache(self):
        self._blocks_cache = None
        self._paragraphs_cache = None
        self._saved_bytes_cache = None

    def get_saved_bytes(self) -> bytes:
        return self.get_export_bytes()

    def _record_applied_highlight(self, change: PendingChange):
        if change.change_type == 'replace':
            self._scan_replace_highlights(change.old_text, change.new_text)
        else:
            self.applied_highlights.append(AppliedHighlight(
                change_type=change.change_type,
                location=change.location,
                old_text=change.old_text,
                new_text=change.new_text,
                table_index=change.table_index,
                row=change.row,
                col=change.col,
                paragraph_index=change.paragraph_index,
            ))

    def _scan_replace_highlights(self, old_text: str, new_text: str):
        """replace 유형 — 문단/셀 위치를 스캔해 하이라이트 등록."""
        for p in self.get_paragraphs():
            txt = p['text']
            if text_locatable_in(new_text, txt) or text_locatable_in(old_text, txt):
                key = ('para', p['index'])
                if any(h.paragraph_index == p['index'] for h in self.applied_highlights):
                    continue
                self.applied_highlights.append(AppliedHighlight(
                    change_type='paragraph',
                    location=f"문단 {p['index']+1}",
                    old_text=old_text,
                    new_text=new_text if new_text in txt else txt,
                    paragraph_index=p['index'],
                ))
        for t_idx in range(self.get_table_count()):
            rows = self.get_table_as_rows(t_idx)
            for r_idx, row in enumerate(rows):
                for c_idx, cell in enumerate(row):
                    if new_text and (cell == new_text or new_text in cell):
                        if any(h.table_index == t_idx and h.row == r_idx and h.col == c_idx
                               for h in self.applied_highlights):
                            continue
                        self.applied_highlights.append(AppliedHighlight(
                            change_type='cell',
                            location=f'표{t_idx+1} ({r_idx},{c_idx})',
                            old_text=old_text,
                            new_text=cell,
                            table_index=t_idx, row=r_idx, col=c_idx,
                        ))

    def propose_paragraph_change(self, paragraph_index: int, new_text: str) -> PendingChange:
        paras = self.get_paragraphs()
        old = paras[paragraph_index]['text'] if paragraph_index < len(paras) else ''
        section_file = paras[paragraph_index].get('section_file') if paragraph_index < len(paras) else None
        change = PendingChange(
            id=str(uuid.uuid4())[:8],
            change_type='paragraph',
            location=f'문단 {paragraph_index+1}',
            old_text=old,
            new_text=new_text,
            paragraph_index=paragraph_index,
            section_file=section_file,
            search_hint=old[:120],
        )
        self.pending_changes.append(change)
        self._bump_preview()
        return change

    def find_paragraph_by_anchor(self, anchor: str) -> Optional[int]:
        """앵커 문자열이 포함된 문단 인덱스를 찾습니다."""
        anchor = (anchor or '').strip()
        if not anchor:
            return None
        paras = self.get_paragraphs()
        for p in paras:
            if anchor in p['text']:
                return p['index']
        a_norm = re.sub(r'\s+', '', anchor)
        for p in paras:
            if a_norm and a_norm in re.sub(r'\s+', '', p['text']):
                return p['index']
        tokens = [w for w in re.findall(r'[\w가-힣\-]+', anchor) if len(w) >= 2]
        if not tokens:
            return None
        best_idx, best_score = None, 0
        need = max(2, len(tokens) // 2)
        for p in paras:
            score = sum(1 for t in tokens if t in p['text'])
            if score > best_score and score >= need:
                best_score = score
                best_idx = p['index']
        return best_idx

    def propose_insert_after_anchor(self, anchor: str, body: str) -> Optional[PendingChange]:
        """앵커 문단 바로 아래에 본문 삽입을 제안합니다."""
        body = (body or '').strip()
        if not body:
            return None
        idx = self.find_paragraph_by_anchor(anchor)
        if idx is None:
            return None
        paras = self.get_paragraphs()
        loc_text = paras[idx]['text'][:50]
        change = PendingChange(
            id=str(uuid.uuid4())[:8],
            change_type='insert_after',
            location=f'"{loc_text}..." 아래',
            old_text='',
            new_text=body,
            paragraph_index=idx,
            section_file=paras[idx].get('section_file'),
            search_hint=anchor[:120],
        )
        self.pending_changes.append(change)
        self._bump_preview()
        return change

    def _insert_text_after_paragraph(
        self, paragraph_index: int, text: str, track_changes: bool = True,
    ) -> bool:
        paras = self.get_paragraphs()
        if paragraph_index >= len(paras):
            return False
        ref_info = paras[paragraph_index]
        ref_elem = ref_info['elem']
        root = self.section_trees.get(ref_info.get('section_file', ''))
        if root is None:
            return False
        parent_map = {child: parent for parent in root.iter() for child in parent}
        parent = parent_map.get(ref_elem)
        if parent is None:
            return False
        try:
            insert_at = list(parent).index(ref_elem) + 1
        except ValueError:
            insert_at = len(parent)
        blocks = [b.strip() for b in re.split(r'\n+', text.strip()) if b.strip()]
        if not blocks:
            return False
        for i, block in enumerate(blocks):
            new_p = copy.deepcopy(ref_elem)
            first = True
            for t in new_p.iter():
                if local_tag(t.tag) == 't':
                    if first:
                        t.text = block
                        first = False
                    else:
                        t.text = ''
            if track_changes:
                self._mark_runs_green(new_p)
            parent.insert(insert_at + i, new_p)
        self._mark_section_dirty(ref_info.get('section_file'))
        return True

    def find_table_cell_candidates(
        self, old_value: str, command: str = '',
    ) -> list[tuple[int, int, int, str, int]]:
        """(table_index, row, col, cell_text, score) — 점수 높을수록 적합."""
        table_index = None
        m = re.search(r'표\s*(\d+)', command)
        if m:
            table_index = int(m.group(1)) - 1

        row_num = None
        for pat in (r'(\d+)\s*행', r'(\d+)\s*번', r'(\d+)\s*전년도', r'(\d+)\s*줄'):
            m = re.search(pat, command)
            if m:
                row_num = int(m.group(1))
                break

        skip = {
            '표에서', '바꿔줘', '수정해줘', '수정해', '바꿔', '으로', '에서', '이에',
            '맞게', '소계도', '해주고', '해줘', '달라고', '안바뀌었는데', '여기서',
        }
        cmd_keywords = [
            w for w in re.findall(r'[가-힣a-zA-Z0-9]+', command)
            if len(w) >= 2 and w not in skip and not re.fullmatch(r'[\d,\.]+', w)
        ]

        candidates: list[tuple[int, int, int, str, int]] = []
        for t_idx in range(self.get_table_count()):
            if table_index is not None and t_idx != table_index:
                continue
            rows = self.get_table_as_rows(t_idx)
            if not rows:
                continue
            header_rows = rows[:min(3, len(rows))]

            for r_idx, row in enumerate(rows):
                row_text = ' '.join(str(c) for c in row)
                for c_idx, cell in enumerate(row):
                    if not _cell_contains_value(cell, old_value):
                        continue
                    score = 0
                    if table_index is not None:
                        score += 6
                    if row_num is not None:
                        if r_idx in (row_num, row_num - 1):
                            score += 10
                        elif str(row_num) in row_text[:30]:
                            score += 5
                    for kw in cmd_keywords:
                        if kw in row_text:
                            score += 3
                        for hdr_row in header_rows:
                            if c_idx < len(hdr_row) and kw in str(hdr_row[c_idx]):
                                score += 6
                    if _normalize_value(cell) == _normalize_value(old_value):
                        score += 2
                    candidates.append((t_idx, r_idx, c_idx, cell, score))
        return candidates

    def propose_table_value_replace(
        self, command: str, old_value: str, new_value: str,
    ) -> Optional[PendingChange]:
        """표 셀에서 old_value를 찾아 new_value로 변경 제안."""
        candidates = self.find_table_cell_candidates(old_value, command)
        if not candidates:
            return None

        candidates.sort(key=lambda x: x[4], reverse=True)
        best = candidates[0]
        min_score = 8 if len(_normalize_value(old_value)) <= 2 else 1

        strong = [c for c in candidates if c[4] >= min_score]
        if not strong:
            exact = [c for c in candidates if _normalize_value(c[3]) == _normalize_value(old_value)]
            if len(exact) == 1:
                strong = exact
            else:
                return None

        if len(strong) > 1 and strong[0][4] == strong[1][4] and strong[0][4] < 6:
            return None

        t_idx, r_idx, c_idx, cell, _ = strong[0]
        new_cell = _format_replacement(cell, old_value, new_value)
        location = f'표{t_idx + 1} ({r_idx + 1}행,{c_idx + 1}열)'
        return self.propose_cell_change(
            t_idx, r_idx, c_idx, new_cell,
            context=location,
        )

    def locate_replace_targets(self, old_text: str, new_text: str = '') -> list[dict]:
        """치환 대상 문단/셀 위치 탐색 (미리보기·제안용)."""
        targets: list[dict] = []
        for block in self.get_document_blocks():
            if block['type'] == 'paragraph':
                txt = block['text']
                if text_locatable_in(old_text, txt) or (
                    new_text and text_locatable_in(new_text, txt)
                ):
                    targets.append({
                        'kind': 'paragraph',
                        'paragraph_index': block['paragraph_index'],
                        'section_file': block.get('section_file'),
                    })
            elif block['type'] == 'table':
                t_idx = block['table_index']
                for r_idx, row in enumerate(block['parsed'].rows):
                    for c_idx, cell in enumerate(row):
                        if _cell_contains_value(str(cell), old_text):
                            targets.append({
                                'kind': 'cell',
                                'table_index': t_idx,
                                'row': r_idx,
                                'col': c_idx,
                            })
        return targets

    def propose_replace(self, old_text: str, new_text: str, location: str = '') -> PendingChange:
        targets = self.locate_replace_targets(old_text, new_text)
        paragraph_index = None
        table_index = row = col = None
        if targets:
            t = targets[0]
            if t['kind'] == 'paragraph':
                paragraph_index = t['paragraph_index']
                if not location:
                    location = f'문단 {paragraph_index + 1}'
            else:
                table_index = t['table_index']
                row, col = t['row'], t['col']
                if not location:
                    location = (
                        f'표{table_index + 1} ({row + 1}행,{col + 1}열)'
                    )
        if not location:
            location = '텍스트 치환 (미리보기에서 위치 미확인)'
        change = PendingChange(
            id=str(uuid.uuid4())[:8],
            change_type='replace',
            location=location,
            old_text=old_text,
            new_text=new_text,
            paragraph_index=paragraph_index,
            table_index=table_index,
            row=row,
            col=col,
            search_hint=old_text[:120],
        )
        self.pending_changes.append(change)
        self._bump_preview()
        return change

    def get_pending_changes(self) -> list[PendingChange]:
        return [c for c in self.pending_changes if c.status == 'pending']

    def accept_change(self, change_id: str, track_changes: bool = True) -> bool:
        change = next((c for c in self.pending_changes if c.id == change_id), None)
        if change is None or change.status != 'pending':
            return False
        ok = self._apply_change(change, track_changes=track_changes)
        if ok:
            change.status = 'accepted'
            self._record_applied_highlight(change)
            self._invalidate_structure_cache()
            self._bump_preview()
        return ok

    def accept_all_pending(self, track_changes: bool = True) -> int:
        count = 0
        for change in list(self.pending_changes):
            if change.status == 'pending' and self.accept_change(change.id, track_changes):
                count += 1
        return count

    def reject_all_pending(self) -> int:
        count = 0
        for change in self.pending_changes:
            if change.status == 'pending':
                change.status = 'rejected'
                count += 1
        return count

    def _apply_paragraph_via_hwpilot(
        self, paragraph_index: int, new_text: str, old_text: str = '',
    ) -> bool:
        """hwpilot edit text — XML 재직렬화 없이 문단 수정."""
        from hwp_core.hwp_backends import apply_hwpilot_to_bytes, hwpilot_edit_paragraph

        def _edit(path: str) -> tuple[bool, str]:
            return hwpilot_edit_paragraph(
                path, paragraph_index, new_text, old_text=old_text)

        new_bytes, msg = apply_hwpilot_to_bytes(
            self.original_bytes, self._source_filename, _edit)
        if new_bytes is None:
            return False
        self.reload_from_bytes(new_bytes, from_hwpilot=True)
        return True

    def _apply_change(self, change: PendingChange, track_changes: bool = True) -> bool:
        if change.change_type == 'paragraph' and self._hwpilot_touched:
            if change.paragraph_index is None:
                return False
            return self._apply_paragraph_via_hwpilot(
                change.paragraph_index, change.new_text, old_text=change.old_text)

        if change.change_type == 'cell':
            if change.table_index is None or change.row is None or change.col is None:
                return False
            ok = self.edit_table_cell(change.table_index, change.row, change.col, change.new_text)
            if ok and track_changes:
                all_tables = []
                for root in self.section_trees.values():
                    all_tables.extend(self._get_tables(root))
                if change.table_index < len(all_tables):
                    tc = self._get_cell_at(all_tables[change.table_index], change.row, change.col)
                    if tc is not None:
                        self._mark_runs_green(tc)
            return ok

        if change.change_type == 'paragraph':
            return self._set_paragraph_text(
                change.paragraph_index, change.new_text,
                old_text=change.old_text, track_changes=track_changes)

        if change.change_type == 'replace':
            if change.old_text:
                if self.replace_selection(change.old_text, change.new_text, track_changes):
                    return True
            return self.find_and_replace(
                change.old_text, change.new_text,
                track_changes=track_changes) > 0

        if change.change_type == 'append':
            title = change.search_hint or ''
            body = change.new_text
            if title or body:
                return self.append_section_text(title or '추가 내용', body)
            return False

        if change.change_type == 'insert_after':
            if change.paragraph_index is None:
                return False
            return self._insert_text_after_paragraph(
                change.paragraph_index, change.new_text, track_changes=track_changes)
        return False

    def _set_paragraph_text(self, paragraph_index: int, new_text: str,
                            old_text: str = '', track_changes: bool = True) -> bool:
        paras = self.get_paragraphs()
        if paragraph_index >= len(paras):
            return False
        elem = paras[paragraph_index]['elem']
        t_elems = [e for e in elem.iter() if local_tag(e.tag) == 't']
        if not t_elems:
            return False

        if track_changes and old_text and old_text != new_text:
            orig = t_elems[0].text or ''
            if old_text in orig:
                t_elems[0].text = old_text
                self._mark_runs_style(elem, 'strike')
                run_parent = None
                for sub in elem.iter():
                    if local_tag(sub.tag) == 'run':
                        run_parent = sub
                        break
                if run_parent is not None:
                    ns = run_parent.tag.rsplit('}', 1)[0] + '}' if '}' in run_parent.tag else ''
                    new_run = copy.deepcopy(run_parent)
                    for t in new_run.iter():
                        if local_tag(t.tag) == 't':
                            t.text = new_text
                    self._mark_runs_style(new_run, 'green')
                    parent = None
                    for p in elem.iter():
                        for child in p:
                            if child is run_parent:
                                parent = p
                                break
                    if parent is not None:
                        idx = list(parent).index(run_parent)
                        parent.insert(idx + 1, new_run)
                    else:
                        t_elems[0].text = new_text
                        self._mark_runs_green(elem)
                else:
                    t_elems[0].text = new_text
                    self._mark_runs_green(elem)
            else:
                t_elems[0].text = new_text
                for extra in t_elems[1:]:
                    extra.text = ''
                self._mark_runs_green(elem)
        else:
            t_elems[0].text = new_text
            for extra in t_elems[1:]:
                extra.text = ''
            if track_changes:
                self._mark_runs_green(elem)
        paras = self.get_paragraphs()
        if paragraph_index < len(paras):
            self._mark_section_dirty(paras[paragraph_index].get('section_file'))
        return True

    def _replace_in_table_cells(self, old_text: str, new_text: str,
                                track_changes: bool = True) -> int:
        """표 셀 전체 텍스트 기준 치환."""
        all_tables = []
        for root in self.section_trees.values():
            all_tables.extend(self._get_tables(root))

        count = 0
        for tbl in all_tables:
            grid = build_element_grid(tbl)
            for row in grid:
                for tc, cell_text in row:
                    if tc is None or not _cell_contains_value(cell_text, old_text):
                        continue
                    new_cell = _format_replacement(cell_text, old_text, new_text)
                    if track_changes and _normalize_value(cell_text) == _normalize_value(old_text):
                        self._mark_runs_style(tc, 'strike')
                    self._set_cell_text(tc, new_cell)
                    if track_changes:
                        self._mark_runs_green(tc)
                    count += 1
        return count

    def find_and_replace(self, old_text: str, new_text: str,
                         track_changes: bool = True) -> int:
        """텍스트 위치를 재탐색하여 치환 (에이전트형 편집)."""
        count = self._replace_in_table_cells(old_text, new_text, track_changes)
        modified_containers = []
        for root in self.section_trees.values():
            for elem in root.iter():
                if local_tag(elem.tag) != 't' or not elem.text or old_text not in elem.text:
                    continue
                if self._find_ancestor_tc(root, elem) is not None:
                    continue
                if track_changes and elem.text.strip() == old_text.strip():
                    container = self._find_ancestor_p(root, elem)
                    if container is not None:
                        self._mark_runs_style(container, 'strike')
                elem.text = elem.text.replace(old_text, new_text)
                count += 1
                container = self._find_ancestor_p(root, elem)
                if container is not None:
                    modified_containers.append(container)
        if count > 0:
            if track_changes:
                for c in modified_containers:
                    self._mark_runs_green(c)
            self._recalculate_all_totals()
            for sf in self.section_trees:
                self._mark_section_dirty(sf)
            return count
        if modified_containers:
            if track_changes:
                for c in modified_containers:
                    self._mark_runs_green(c)
            self._recalculate_all_totals()
            for sf in self.section_trees:
                self._mark_section_dirty(sf)
        return count

    def _find_ancestor_p(self, root: ET.Element, target: ET.Element) -> Optional[ET.Element]:
        parent_map = {child: parent for parent in root.iter() for child in parent}
        current = target
        while current in parent_map:
            current = parent_map[current]
            if local_tag(current.tag) == 'p':
                return current
        return None

    def replace_selection(self, selection_text: str, new_text: str,
                          track_changes: bool = True) -> bool:
        """선택 영역 텍스트를 찾아 치환 (위치 변경 시 재탐색)."""
        selection_text = selection_text.strip()
        if not selection_text:
            return False
        for section_name, root in self.section_trees.items():
            for elem in root.iter():
                if local_tag(elem.tag) != 'p':
                    continue
                full = self._get_element_text(elem)
                if not text_locatable_in(selection_text, full):
                    continue
                t_elems = [e for e in elem.iter() if local_tag(e.tag) == 't']
                if not t_elems:
                    continue
                combined = ''.join(t.text or '' for t in t_elems)
                if selection_text not in combined and not text_locatable_in(selection_text, combined):
                    continue
                new_combined = combined.replace(selection_text, new_text, 1)
                if new_combined == combined and text_locatable_in(selection_text, combined):
                    pattern = re.escape(selection_text.strip())
                    pattern = re.sub(r'\\ ', r'\\s+', pattern)
                    new_combined = re.sub(pattern, new_text, combined, count=1)
                if new_combined == combined:
                    continue
                if track_changes:
                    self._mark_runs_style(elem, 'strike')
                t_elems[0].text = new_combined
                for extra in t_elems[1:]:
                    extra.text = ''
                if track_changes:
                    self._mark_runs_green(elem)
                self._mark_section_dirty(section_name)
                return True
        return self.find_and_replace(selection_text, new_text, track_changes) > 0

    def _get_element_text(self, elem: ET.Element) -> str:
        return ''.join(t.text or '' for t in elem.iter() if local_tag(t.tag) == 't').strip()

    def append_section_text(self, title: str, body: str) -> bool:
        """문서 끝에 제목+본문 문단 추가 (초안 생성)."""
        if not self.section_trees:
            return False
        section_name = sorted(self.section_trees.keys())[-1]
        root = self.section_trees[section_name]
        paras = [e for e in root.iter() if local_tag(e.tag) == 'p']
        if not paras:
            return False
        ref = paras[-1]
        parent_map = {child: parent for parent in root.iter() for child in parent}
        parent = parent_map.get(ref)
        if parent is None:
            return False
        for text in [title, body]:
            new_p = copy.deepcopy(ref)
            for t in new_p.iter():
                if local_tag(t.tag) == 't':
                    t.text = text
            self._mark_runs_green(new_p)
            parent.append(new_p)
        self._mark_section_dirty(section_name)
        return True
