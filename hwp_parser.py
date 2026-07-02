"""
HWP/HWPX 문서 파싱 모듈
- HWPX: ZIP(XML) 기반 파싱
- HWP: OLE 복합문서 기반 파싱 (제한적)
"""

import zipfile
import os
import io
import re
import struct
import zlib
import subprocess
import tempfile
from xml.etree import ElementTree as ET
from typing import Optional
from dataclasses import dataclass, field


UNIT_PATTERN = re.compile(r'[\(\（]\s*단위\s*[:：]\s*([^)\）]+)[\)\）]')


@dataclass
class ParsedDocument:
    filename: str = ""
    file_type: str = ""
    full_text: str = ""
    paragraphs: list = field(default_factory=list)
    tables_raw: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def parse_document(file_path: Optional[str] = None, file_bytes: Optional[bytes] = None,
                   filename: str = "") -> ParsedDocument:
    if file_path:
        filename = os.path.basename(file_path)
        with open(file_path, 'rb') as f:
            file_bytes = f.read()

    if not file_bytes:
        doc = ParsedDocument(filename=filename)
        doc.errors.append("파일 데이터가 없습니다.")
        return doc

    ext = os.path.splitext(filename)[1].lower()

    if ext == '.hwpx':
        return parse_hwpx(file_bytes, filename)
    elif ext == '.hwp':
        return parse_hwp(file_bytes, filename)
    else:
        doc = ParsedDocument(filename=filename)
        doc.errors.append(f"지원하지 않는 파일 형식: {ext}")
        return doc


def parse_hwpx(file_bytes: bytes, filename: str) -> ParsedDocument:
    doc = ParsedDocument(filename=filename, file_type="hwpx")

    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            section_files = sorted([
                f for f in zf.namelist()
                if 'section' in f.lower() and f.endswith('.xml')
            ])

            if not section_files:
                section_files = sorted([
                    f for f in zf.namelist()
                    if 'contents/' in f.lower() and f.endswith('.xml')
                ])

            if not section_files:
                section_files = [f for f in zf.namelist() if f.endswith('.xml')]

            for sf in section_files:
                try:
                    xml_data = zf.read(sf)
                    _parse_hwpx_section(xml_data, doc)
                except Exception as e:
                    doc.errors.append(f"섹션 파싱 오류 ({sf}): {str(e)}")

    except zipfile.BadZipFile:
        doc.errors.append("유효하지 않은 HWPX 파일입니다.")
    except Exception as e:
        doc.errors.append(f"HWPX 파싱 오류: {str(e)}")

    doc.full_text = "\n".join(doc.paragraphs)
    return doc


def _parse_hwpx_section(xml_data: bytes, doc: ParsedDocument):
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        xml_str = xml_data.decode('utf-8', errors='ignore')
        xml_str = re.sub(r'xmlns\s*=\s*"[^"]*"', '', xml_str, count=1)
        root = ET.fromstring(xml_str.encode('utf-8'))

    elements = _collect_elements_in_order(root)

    last_paragraphs = []
    for elem_type, elem in elements:
        if elem_type == 'paragraph':
            text = _get_text_from_element(elem, skip_tables=True).strip()
            if text:
                doc.paragraphs.append(text)
                last_paragraphs.append(text)
                if len(last_paragraphs) > 5:
                    last_paragraphs.pop(0)
        elif elem_type == 'table':
            table_data = _parse_table_element(elem)
            if table_data and table_data['rows']:
                caption, unit = _extract_caption_and_unit(last_paragraphs)
                table_data['caption'] = caption
                table_data['unit'] = unit

                unit_in_table = _detect_unit_in_table(table_data['rows'])
                if unit_in_table and not unit:
                    table_data['unit'] = unit_in_table

                doc.tables_raw.append(table_data)


def _collect_elements_in_order(root) -> list:
    results = []
    _walk_for_paragraphs_and_tables(root, results)
    return results


def _walk_for_paragraphs_and_tables(elem, results: list):
    tag = _local_tag(elem.tag)

    if tag in ('tbl', 'table'):
        results.append(('table', elem))
        return

    if tag in ('p', 'P', 'para'):
        tables_inside = elem.findall('.//' + _find_tag_with_ns(elem, 'tbl'))
        if not tables_inside:
            results.append(('paragraph', elem))
            return
        # p 안에 tbl이 있으면: 텍스트도 추출하고 tbl도 추출
        results.append(('paragraph', elem))
        for tbl in tables_inside:
            results.append(('table', tbl))
        return

    for child in elem:
        _walk_for_paragraphs_and_tables(child, results)


def _find_tag_with_ns(elem, local_name: str) -> str:
    """요소 트리에서 특정 로컬 태그명의 전체 태그(네임스페이스 포함) 찾기"""
    for descendant in elem.iter():
        if _local_tag(descendant.tag) == local_name:
            return descendant.tag
    return local_name


def _local_tag(tag: str) -> str:
    if '}' in tag:
        return tag.split('}')[-1]
    return tag


def _get_text_from_element(elem, skip_tables=False) -> str:
    texts = []
    if elem.text:
        texts.append(elem.text)
    for child in elem:
        if skip_tables and _local_tag(child.tag) in ('tbl', 'table'):
            if child.tail:
                texts.append(child.tail)
            continue
        child_text = _get_text_from_element(child, skip_tables=skip_tables)
        if child_text:
            texts.append(child_text)
        if child.tail:
            texts.append(child.tail)
    return "".join(texts)


def _extract_caption_and_unit(last_paragraphs: list) -> tuple:
    caption = ""
    unit = ""

    for p in reversed(last_paragraphs):
        m = UNIT_PATTERN.search(p)
        if m:
            unit = m.group(1).strip()
            caption_part = UNIT_PATTERN.sub('', p).strip()
            if caption_part:
                caption = caption_part
            break

    if not caption and last_paragraphs:
        candidate = last_paragraphs[-1]
        if len(candidate) < 100 and not candidate.endswith('.'):
            caption = candidate

    return caption, unit


def _detect_unit_in_table(rows: list) -> str:
    for row in rows[:3]:
        for cell in row:
            m = UNIT_PATTERN.search(cell)
            if m:
                return m.group(1).strip()
            if re.search(r'단위\s*[:：]\s*(\S+)', cell):
                m2 = re.search(r'단위\s*[:：]\s*(\S+)', cell)
                if m2:
                    return m2.group(1).strip()
    return ""


def _parse_table_element(tbl_elem) -> dict:
    # tbl 속성에서 행/열 수 가져오기
    row_cnt = int(tbl_elem.get('rowCnt', '0'))
    col_cnt = int(tbl_elem.get('colCnt', '0'))

    # cellAddr 기반 파싱 시도
    cells = []
    max_col = 0
    max_row = 0
    has_addr = False

    for tr_elem in tbl_elem:
        if _local_tag(tr_elem.tag) not in ('tr', 'row'):
            continue
        for tc_elem in tr_elem:
            if _local_tag(tc_elem.tag) not in ('tc', 'cell', 'td'):
                continue

            cell_text = _get_cell_text(tc_elem)

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
                col = int(addr_elem.get('colAddr', '0'))
                row = int(addr_elem.get('rowAddr', '0'))
                col_span = int(span_elem.get('colSpan', '1')) if span_elem is not None else 1
                row_span = int(span_elem.get('rowSpan', '1')) if span_elem is not None else 1
                cells.append((row, col, col_span, row_span, cell_text))
                max_col = max(max_col, col + col_span)
                max_row = max(max_row, row + row_span)

    if has_addr and cells:
        if col_cnt > 0:
            max_col = max(max_col, col_cnt)
        if row_cnt > 0:
            max_row = max(max_row, row_cnt)

        grid = [[''] * max_col for _ in range(max_row)]
        merge_owner = [[None] * max_col for _ in range(max_row)]
        for row_idx, col_idx, col_span, row_span, text in cells:
            if row_idx < max_row and col_idx < max_col:
                grid[row_idx][col_idx] = text
                for r in range(row_idx, min(row_idx + row_span, max_row)):
                    for c in range(col_idx, min(col_idx + col_span, max_col)):
                        merge_owner[r][c] = (row_idx, col_idx)
                        if r == row_idx and c == col_idx:
                            continue
                        if not grid[r][c]:
                            grid[r][c] = text

        rows = [row for row in grid if any(cell.strip() for cell in row)]
        return {'rows': rows, 'caption': '', 'unit': ''}

    # fallback: cellAddr가 없는 경우 — span 속성 활용 그리드 구축
    raw_cells = []
    fb_row_idx = 0
    for child in tbl_elem:
        tag = _local_tag(child.tag)
        if tag not in ('tr', 'row'):
            continue
        fb_col_idx = 0
        for cell_elem in child:
            cell_tag = _local_tag(cell_elem.tag)
            if cell_tag not in ('tc', 'cell', 'td'):
                continue
            cell_text = _get_cell_text(cell_elem)
            cs = int(cell_elem.get('colSpan', '1') or '1')
            rs = int(cell_elem.get('rowSpan', '1') or '1')
            raw_cells.append((fb_row_idx, fb_col_idx, cs, rs, cell_text))
            fb_col_idx += 1
        fb_row_idx += 1

    if not raw_cells:
        return {'rows': [], 'caption': '', 'unit': ''}

    has_span = any(cs > 1 or rs > 1 for _, _, cs, rs, _ in raw_cells)
    if has_span:
        fb_max_row = max(r + rs for r, _, _, rs, _ in raw_cells)
        fb_max_col = max(col_cnt, max(c + cs for _, c, cs, _, _ in raw_cells))
        if row_cnt > 0:
            fb_max_row = max(fb_max_row, row_cnt)
        grid = [[''] * fb_max_col for _ in range(fb_max_row)]
        occupied = [[False] * fb_max_col for _ in range(fb_max_row)]

        cur_row = 0
        row_cells = {}
        for r, c, cs, rs, text in raw_cells:
            row_cells.setdefault(r, []).append((c, cs, rs, text))

        for r_idx in sorted(row_cells.keys()):
            col_cursor = 0
            for _, cs, rs, text in row_cells[r_idx]:
                while col_cursor < fb_max_col and occupied[r_idx][col_cursor]:
                    col_cursor += 1
                if col_cursor >= fb_max_col:
                    break
                for dr in range(rs):
                    for dc in range(cs):
                        rr = r_idx + dr
                        cc = col_cursor + dc
                        if rr < fb_max_row and cc < fb_max_col:
                            occupied[rr][cc] = True
                            grid[rr][cc] = text
                col_cursor += cs

        rows = [row for row in grid if any(cell.strip() for cell in row)]
    else:
        rows = []
        for r_idx in sorted(set(r for r, _, _, _, _ in raw_cells)):
            row = [text for r, _, _, _, text in raw_cells if r == r_idx]
            if row:
                rows.append(row)

    return {'rows': rows, 'caption': '', 'unit': ''}


def _get_cell_text(tc_elem) -> str:
    """tc 요소에서 텍스트 추출 (subList > p > run > t 구조)"""
    texts = []
    for elem in tc_elem.iter():
        if _local_tag(elem.tag) == 't' and elem.text:
            texts.append(elem.text)
    result = ' '.join(texts).strip()
    result = re.sub(r'\s+', ' ', result)
    return result


def _try_hwp_to_hwpx_conversion(file_bytes: bytes, filename: str) -> Optional[ParsedDocument]:
    """LibreOffice를 사용하여 HWP를 HWPX로 변환 시도"""
    soffice_path = "/usr/bin/soffice"
    if not os.path.exists(soffice_path):
        return None

    try:
        with tempfile.TemporaryDirectory(prefix="hwp_convert_") as tmpdir:
            hwp_path = os.path.join(tmpdir, filename)
            with open(hwp_path, 'wb') as f:
                f.write(file_bytes)

            result = subprocess.run(
                [soffice_path, "--headless", "--convert-to", "hwpx",
                 "--outdir", tmpdir, hwp_path],
                capture_output=True, timeout=30, text=True,
            )

            if result.returncode != 0:
                return None

            base_name = os.path.splitext(filename)[0]
            hwpx_path = os.path.join(tmpdir, base_name + ".hwpx")

            if not os.path.exists(hwpx_path):
                hwpx_candidates = [f for f in os.listdir(tmpdir)
                                   if f.endswith('.hwpx') and f != filename]
                if not hwpx_candidates:
                    return None
                hwpx_path = os.path.join(tmpdir, hwpx_candidates[0])

            with open(hwpx_path, 'rb') as f:
                hwpx_bytes = f.read()

            doc = parse_hwpx(hwpx_bytes, filename)
            doc.file_type = "hwp (converted)"
            return doc
    except (subprocess.TimeoutExpired, OSError, Exception):
        return None


def parse_hwp(file_bytes: bytes, filename: str) -> ParsedDocument:
    converted_doc = _try_hwp_to_hwpx_conversion(file_bytes, filename)
    if converted_doc is not None:
        return converted_doc

    doc = ParsedDocument(filename=filename, file_type="hwp")

    try:
        import olefile
    except ImportError:
        doc.errors.append("HWP 파싱을 위해 olefile 패키지가 필요합니다: pip install olefile")
        return doc

    try:
        ole = olefile.OleFileIO(io.BytesIO(file_bytes))
        is_compressed = _check_hwp_compressed(ole)

        section_streams = [
            s for s in ole.listdir()
            if len(s) >= 2 and s[0] == 'BodyText'
        ]
        section_streams.sort(key=lambda x: x[-1])

        for stream_path in section_streams:
            try:
                stream_data = ole.openstream(stream_path).read()
                if is_compressed:
                    try:
                        stream_data = zlib.decompress(stream_data, -15)
                    except zlib.error:
                        try:
                            stream_data = zlib.decompress(stream_data)
                        except zlib.error:
                            doc.errors.append(f"스트림 압축 해제 실패: {'/'.join(stream_path)}")
                            continue

                text = _extract_hwp_text(stream_data)
                if text:
                    for line in text.split('\n'):
                        line = line.strip()
                        if line:
                            doc.paragraphs.append(line)
            except Exception as e:
                doc.errors.append(f"HWP 스트림 처리 오류 ({'/'.join(stream_path)}): {str(e)}")

        ole.close()

    except Exception as e:
        doc.errors.append(f"HWP 파싱 오류: {str(e)}")

    doc.full_text = "\n".join(doc.paragraphs)

    if not doc.paragraphs and not doc.errors:
        doc.errors.append("HWP 파일에서 텍스트를 추출하지 못했습니다. "
                          "HWP 바이너리 형식은 제한적으로 지원됩니다.")

    return doc


def _check_hwp_compressed(ole) -> bool:
    try:
        header = ole.openstream('FileHeader').read()
        if len(header) >= 40:
            flags = struct.unpack('<I', header[36:40])[0]
            return bool(flags & 0x01)
    except Exception:
        pass
    return True


def _extract_hwp_text(data: bytes) -> str:
    texts = []
    i = 0
    while i < len(data) - 4:
        try:
            header = struct.unpack('<I', data[i:i+4])[0]
            tag_id = header & 0x3FF
            size = (header >> 20) & 0xFFF

            if size == 0xFFF:
                if i + 8 <= len(data):
                    size = struct.unpack('<I', data[i+4:i+8])[0]
                    i += 8
                else:
                    break
            else:
                i += 4

            if i + size > len(data):
                break

            if tag_id == 67:
                text = _decode_hwp_para_text(data[i:i+size])
                if text:
                    texts.append(text)

            i += size

        except (struct.error, ValueError):
            i += 1

    return "\n".join(texts)


def _decode_hwp_para_text(data: bytes) -> str:
    chars = []
    i = 0
    while i < len(data) - 1:
        code = struct.unpack('<H', data[i:i+2])[0]
        i += 2

        if code == 0:
            break
        elif code < 32:
            if code in (1, 2, 3, 11, 12, 13, 14, 15, 16, 17, 18, 21, 22, 23):
                i += _get_hwp_control_size(code)
            elif code == 10:
                chars.append('\n')
            elif code == 13:
                break
            elif code == 9:
                chars.append('\t')
        else:
            chars.append(chr(code))

    return "".join(chars).strip()


def _get_hwp_control_size(code: int) -> int:
    extended_controls = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
    if code in extended_controls:
        return 12
    return 0
