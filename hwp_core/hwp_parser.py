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

from hwp_core.table_grid import local_tag, parse_table_grid


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
    tag = local_tag(elem.tag)

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
        if local_tag(descendant.tag) == local_name:
            return descendant.tag
    return local_name


def _get_text_from_element(elem, skip_tables=False) -> str:
    texts = []
    if elem.text:
        texts.append(elem.text)
    for child in elem:
        if skip_tables and local_tag(child.tag) in ('tbl', 'table'):
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
    parsed = parse_table_grid(tbl_elem)
    return {'rows': parsed.rows, 'caption': '', 'unit': ''}


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
    """HWP 파싱 — hwpilot / pyhwp 우선, LibreOffice·자체 파서 fallback."""
    from hwp_core.hwp_backends import (
        parse_document_with_hwpilot,
        parse_hwp_with_pyhwp,
        hwpilot_convert_to_hwpx,
    )

    doc = ParsedDocument(filename=filename, file_type="hwp")

    # 1) hwpilot 구조화 read (.hwp 직접)
    hwpilot_doc = parse_document_with_hwpilot(file_bytes, filename)
    if hwpilot_doc:
        doc.paragraphs = hwpilot_doc['paragraphs']
        doc.tables_raw = hwpilot_doc['tables_raw']
        doc.full_text = hwpilot_doc['full_text']
        doc.file_type = f"hwp ({hwpilot_doc['parser_tag']})"
        return doc

    # 2) pyhwp HTML/txt (표 포함)
    pyhwp_doc = parse_hwp_with_pyhwp(file_bytes, filename)
    if pyhwp_doc:
        doc.paragraphs = pyhwp_doc['paragraphs']
        doc.tables_raw = pyhwp_doc['tables_raw']
        doc.full_text = pyhwp_doc['full_text']
        doc.file_type = f"hwp ({pyhwp_doc['parser_tag']})"
        return doc

    # 3) hwpilot HWP→HWPX 변환 후 XML 파싱
    hwpx_bytes = hwpilot_convert_to_hwpx(file_bytes, filename)
    if hwpx_bytes:
        converted = parse_hwpx(hwpx_bytes, filename)
        converted.file_type = "hwp (hwpilot→hwpx)"
        return converted

    # 4) LibreOffice 변환
    converted_doc = _try_hwp_to_hwpx_conversion(file_bytes, filename)
    if converted_doc is not None:
        return converted_doc

    # 5) olefile 자체 파서 fallback
    return _parse_hwp_olefile(file_bytes, filename, doc)


def _parse_hwp_olefile(file_bytes: bytes, filename: str, doc: ParsedDocument) -> ParsedDocument:
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
