"""
pyhwp / hwpilot 백엔드 래퍼

- pyhwp: .hwp 텍스트·HTML(표) 추출 (hwp5txt, hwp5html)
- hwpilot: .hwp/.hwpx 구조화 읽기, HWP→HWPX 변환, 문단 삽입·편집 (CLI)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HWPILOT_CLI_JS = os.path.join(_PROJECT_ROOT, 'hwpilot', 'dist', 'src', 'cli.js')


@dataclass
class BackendStatus:
    pyhwp_txt: bool = False
    pyhwp_html: bool = False
    hwpilot: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def pyhwp(self) -> bool:
        return self.pyhwp_txt or self.pyhwp_html

    def summary(self) -> str:
        parts = []
        parts.append(f"pyhwp({'ON' if self.pyhwp else 'OFF'})")
        parts.append(f"hwpilot({'ON' if self.hwpilot else 'OFF'})")
        return ', '.join(parts)


def get_backend_status() -> BackendStatus:
    status = BackendStatus(
        pyhwp_txt=bool(shutil.which('hwp5txt')),
        pyhwp_html=bool(shutil.which('hwp5html')),
        hwpilot=bool(_hwpilot_base()),
    )
    if not status.pyhwp:
        status.notes.append('pip install pyhwp 후 hwp5txt/hwp5html 사용 가능')
    if not status.hwpilot:
        status.notes.append(
            'hwpilot: cd "HWP analysis/hwpilot" && npm install && npm run build'
        )
    return status


def _hwpilot_executable() -> Optional[str]:
    path = shutil.which('hwpilot')
    if path:
        return path
    nvm_bins = os.path.expanduser('~/.nvm/versions/node/*/bin/hwpilot')
    import glob
    for candidate in glob.glob(nvm_bins):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run_cli(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    env = {**os.environ, 'HWPILOT_NO_DAEMON': '1'}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=env,
    )


def _with_temp_file(
    file_bytes: bytes,
    suffix: str,
    callback: Callable[[str], Optional[object]],
) -> Optional[object]:
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(file_bytes)
        return callback(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _with_temp_pair(
    file_bytes: bytes,
    in_suffix: str,
    out_suffix: str,
    callback: Callable[[str, str], Optional[object]],
    *,
    create_output: bool = False,
) -> Optional[object]:
    in_fd, in_path = tempfile.mkstemp(suffix=in_suffix)
    out_dir = tempfile.mkdtemp()
    out_path = os.path.join(out_dir, f'out{out_suffix}')
    if create_output:
        open(out_path, 'wb').close()
    try:
        with os.fdopen(in_fd, 'wb') as f:
            f.write(file_bytes)
        return callback(in_path, out_path)
    finally:
        try:
            os.unlink(in_path)
        except OSError:
            pass
        try:
            if os.path.isfile(out_path):
                os.unlink(out_path)
            os.rmdir(out_dir)
        except OSError:
            pass


# --- pyhwp ---


def pyhwp_extract_text(file_bytes: bytes, filename: str = 'doc.hwp') -> Optional[str]:
    """hwp5txt로 plain text 추출."""
    if not shutil.which('hwp5txt'):
        return None

    def _run(path: str) -> Optional[str]:
        out_fd, out_path = tempfile.mkstemp(suffix='.txt')
        os.close(out_fd)
        try:
            result = _run_cli(['hwp5txt', '--output', out_path, path], timeout=120)
            if result.returncode != 0:
                return None
            with open(out_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read().strip()
            return text or None
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    return _with_temp_file(file_bytes, '.hwp', _run)


def pyhwp_extract_html(file_bytes: bytes, filename: str = 'doc.hwp') -> Optional[str]:
    """hwp5html로 HTML 추출 (표 포함)."""
    if not shutil.which('hwp5html'):
        return None

    def _run(path: str) -> Optional[str]:
        out_fd, out_path = tempfile.mkstemp(suffix='.html')
        os.close(out_fd)
        try:
            result = _run_cli(['hwp5html', '--output', out_path, '--html', path], timeout=180)
            if result.returncode != 0:
                return None
            with open(out_path, 'r', encoding='utf-8', errors='ignore') as f:
                html = f.read()
            return html or None
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    return _with_temp_file(file_bytes, '.hwp', _run)


def html_to_paragraphs(html: str) -> list[str]:
    if not html:
        return []
    if HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style']):
            tag.decompose()
        text = soup.get_text('\n')
    else:
        text = re.sub(r'<[^>]+>', '\n', html)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines


def html_to_tables(html: str) -> list[dict]:
    """HTML 표 → tables_raw 형식."""
    if not html or not HAS_BS4:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    tables = []
    for tbl in soup.find_all('table'):
        rows = []
        for tr in tbl.find_all('tr'):
            cells = [td.get_text(' ', strip=True) for td in tr.find_all(['td', 'th'])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append({'rows': rows, 'caption': '', 'unit': ''})
    return tables


def parse_hwp_with_pyhwp(file_bytes: bytes, filename: str) -> Optional[dict]:
    """pyhwp로 paragraphs, tables_raw, full_text, parser_tag 반환."""
    html = pyhwp_extract_html(file_bytes, filename)
    if html:
        paragraphs = html_to_paragraphs(html)
        tables_raw = html_to_tables(html)
        if paragraphs or tables_raw:
            return {
                'paragraphs': paragraphs,
                'tables_raw': tables_raw,
                'full_text': '\n'.join(paragraphs),
                'parser_tag': 'pyhwp-html',
            }

    text = pyhwp_extract_text(file_bytes, filename)
    if text:
        paragraphs = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return {
            'paragraphs': paragraphs,
            'tables_raw': [],
            'full_text': text,
            'parser_tag': 'pyhwp-txt',
        }
    return None


# --- hwpilot ---


def _hwpilot_base() -> Optional[list[str]]:
    """node + 로컬 빌드 CLI 우선 (npm link의 bun shebang 회피)."""
    node = shutil.which('node')
    if node and os.path.isfile(_HWPILOT_CLI_JS):
        return [node, _HWPILOT_CLI_JS]
    exe = _hwpilot_executable()
    if exe:
        return [exe]
    return None


def hwpilot_run(args: list[str], timeout: int = 120) -> tuple[Optional[dict | list | str], str]:
    """hwpilot CLI 실행. JSON stdout이면 파싱."""
    base = _hwpilot_base()
    if not base:
        return None, 'hwpilot CLI가 설치되어 있지 않습니다.'
    result = _run_cli(base + args, timeout=timeout)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or '').strip()
        return None, err or f'hwpilot 실패 (code {result.returncode})'
    out = (result.stdout or '').strip()
    if not out:
        return {}, ''
    try:
        return json.loads(out), ''
    except json.JSONDecodeError:
        return out, ''


def hwpilot_read_file(file_path: str, limit: int = 500) -> Optional[dict]:
    data, err = hwpilot_run(['read', file_path, '--limit', str(limit)], timeout=180)
    if isinstance(data, dict):
        return data
    return None


def hwpilot_find_refs(file_path: str, query: str) -> list[dict]:
    data, _ = hwpilot_run(['find', file_path, query, '--json'], timeout=60)
    if isinstance(data, dict):
        matches = data.get('matches', [])
        if isinstance(matches, list):
            return [m for m in matches if isinstance(m, dict) and m.get('ref')]
    # fallback: plain text lines "s0.p1: text"
    data2, _ = hwpilot_run(['find', file_path, query], timeout=60)
    if isinstance(data2, str):
        matches = []
        for line in data2.splitlines():
            m = re.match(r'^(\S+):\s*(.*)$', line.strip())
            if m:
                matches.append({'ref': m.group(1), 'text': m.group(2)})
        return matches
    return []


def hwpilot_convert_to_hwpx(file_bytes: bytes, filename: str = 'doc.hwp') -> Optional[bytes]:
    """HWP → HWPX 변환."""
    if not _hwpilot_base():
        return None

    def _run(in_path: str, out_path: str) -> Optional[bytes]:
        data, err = hwpilot_run(['convert', in_path, out_path, '--force'], timeout=180)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            return None
        with open(out_path, 'rb') as f:
            return f.read()

    return _with_temp_pair(file_bytes, '.hwp', '.hwpx', _run, create_output=False)


def hwpilot_append_to_end(file_path: str, body: str) -> tuple[bool, str]:
    """문서 끝에 문단 추가 (HWP/HWPX 모두 hwpilot 직접 편집)."""
    from additional.reference_parser import normalize_insert_body

    section_ref = 's0'
    data = hwpilot_read_file(file_path, limit=5)
    if data and data.get('sections'):
        sections = data['sections']
        if sections:
            section_ref = f"s{sections[-1].get('index', len(sections) - 1)}"

    body = normalize_insert_body(body)
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return False, '삽입할 본문이 비어 있습니다.'

    added = 0
    for line in lines:
        _, err = hwpilot_run(
            ['paragraph', 'add', file_path, section_ref, '--position', 'end', '--', line],
            timeout=60,
        )
        if err:
            return added > 0, f'일부 추가 후 오류: {err}'
        added += 1
    return True, f'hwpilot으로 문서 끝에 {added}개 문단 추가'


def hwpilot_apply_content(
    file_bytes: bytes,
    filename: str,
    body: str,
    anchor: str = '',
) -> tuple[Optional[bytes], str]:
    """HWP/HWPX 파일에 본문 삽입 후 수정된 bytes 반환."""
    ext = os.path.splitext(filename)[1].lower() or '.hwp'
    end_markers = ('__END__', '', '마지막', '문서끝', '맨끝')

    def _edit(path: str) -> tuple[bool, str]:
        a = (anchor or '').strip()
        if a in end_markers or re.search(r'마지막|맨\s*끝|문서\s*끝', a, re.I):
            return hwpilot_append_to_end(path, body)
        return hwpilot_insert_after_anchor(path, a, body)

    return apply_hwpilot_to_bytes(file_bytes, filename, _edit)


def hwpilot_insert_after_anchor(file_path: str, anchor: str, body: str) -> tuple[bool, str]:
    """앵커 텍스트를 find한 뒤 문단을 after로 추가."""
    from additional.reference_parser import normalize_insert_body

    if not anchor.strip():
        return False, '앵커 텍스트가 필요합니다.'
    body = normalize_insert_body(body)
    matches = hwpilot_find_refs(file_path, anchor.strip())
    if not matches:
        short = anchor.strip()[:40]
        tokens = [t for t in re.findall(r'[\w가-힣\-]+', anchor) if len(t) >= 3]
        for tok in tokens[:5]:
            matches = hwpilot_find_refs(file_path, tok)
            if matches:
                break
    if not matches:
        return False, f'"{anchor[:50]}" 위치를 hwpilot find로 찾지 못했습니다.'

    ref = matches[0]['ref']
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return False, '삽입할 본문이 비어 있습니다.'

    added = 0
    for line in lines:
        _, err = hwpilot_run(
            ['paragraph', 'add', file_path, ref, '--position', 'after', '--', line],
            timeout=60,
        )
        if err:
            return added > 0, f'일부 삽입 후 오류: {err}'
        added += 1
    return True, f'hwpilot으로 {added}개 문단 삽입 ({ref} 아래)'


def get_hwp_preview_paragraphs(file_bytes: bytes, filename: str) -> list[str]:
    """미리보기 줄 번호와 동일한 문단 목록."""
    from hwp_core.hwp_parser import parse_document

    doc = parse_document(file_bytes=file_bytes, filename=filename)
    return [str(p) for p in doc.paragraphs]


def list_hwpilot_paragraph_refs(file_path: str) -> list[dict]:
    """hwpilot read 순서의 문단 ref·text 목록."""
    data = hwpilot_read_file(file_path, limit=2000)
    if not data:
        return []
    refs: list[dict] = []
    for section in data.get('sections', []):
        if not isinstance(section, dict):
            continue
        for p in section.get('paragraphs', []):
            if not isinstance(p, dict):
                continue
            ref = p.get('ref')
            if not ref:
                continue
            text = _paragraph_text_from_hwpilot(p)
            refs.append({'ref': str(ref), 'text': text})
    return refs


def resolve_hwp_line_ref(
    file_path: str,
    line_num_1based: int,
    preview_paragraphs: list[str],
) -> Optional[str]:
    """미리보기 N줄 → hwpilot ref (1-based 줄 번호)."""
    if line_num_1based < 1:
        return None
    refs = list_hwpilot_paragraph_refs(file_path)
    if not refs:
        return None
    if line_num_1based <= len(preview_paragraphs) and line_num_1based <= len(refs):
        target = preview_paragraphs[line_num_1based - 1].strip()
        candidate = refs[line_num_1based - 1]
        cand_text = candidate['text'].strip()
        if (
            target == cand_text
            or (target and target in cand_text)
            or (cand_text and cand_text in target)
        ):
            return candidate['ref']
    if line_num_1based <= len(preview_paragraphs):
        target = preview_paragraphs[line_num_1based - 1].strip()
        if len(target) >= 2:
            for item in refs:
                if item['text'].strip() == target:
                    return item['ref']
    if 0 < line_num_1based <= len(refs):
        return refs[line_num_1based - 1]['ref']
    return None


def hwpilot_edit_text(file_path: str, ref: str, text: str) -> tuple[bool, str]:
    """문단/셀 텍스트 편집: hwpilot edit text <file> <ref> <text>"""
    _, err = hwpilot_run(['edit', 'text', file_path, ref, text], timeout=60)
    if err:
        return False, err
    return True, f'문단 {ref} 수정'


def hwpilot_resolve_paragraph_ref(
    file_path: str,
    paragraph_index: Optional[int] = None,
    old_text: str = '',
) -> Optional[str]:
    """에디터 문단 인덱스 또는 old_text로 hwpilot ref(sN.pM)를 찾습니다."""
    data = hwpilot_read_file(file_path, limit=2000)
    if not data:
        return None

    non_empty: list[dict] = []
    for section in data.get('sections', []):
        if not isinstance(section, dict):
            continue
        for p in section.get('paragraphs', []):
            if not isinstance(p, dict):
                continue
            ref = p.get('ref')
            if not ref:
                continue
            text = _paragraph_text_from_hwpilot(p)
            non_empty.append({'ref': str(ref), 'text': text})

    old_norm = re.sub(r'\s+', '', (old_text or '').strip())
    if old_norm:
        for item in non_empty:
            item_norm = re.sub(r'\s+', '', item['text'])
            if old_norm in item_norm or item_norm in old_norm:
                return item['ref']
        for item in non_empty:
            if (old_text or '').strip() in item['text']:
                return item['ref']

    if paragraph_index is not None:
        with_text = [x for x in non_empty if x['text']]
        if 0 <= paragraph_index < len(with_text):
            return with_text[paragraph_index]['ref']
    return None


def hwpilot_edit_paragraph(
    file_path: str,
    paragraph_index: int,
    new_text: str,
    old_text: str = '',
) -> tuple[bool, str]:
    ref = hwpilot_resolve_paragraph_ref(file_path, paragraph_index, old_text)
    if not ref:
        return False, f'문단 {paragraph_index + 1}의 hwpilot ref를 찾지 못했습니다.'
    return hwpilot_edit_text(file_path, ref, new_text)


def hwpilot_edit_table_cell(file_path: str, ref: str, new_value: str) -> tuple[bool, str]:
    """표 셀 편집: hwpilot table edit <file> <ref> <value>"""
    _, err = hwpilot_run(['table', 'edit', file_path, ref, new_value], timeout=60)
    if err:
        return False, err
    return True, f'셀 {ref} 수정'


def _paragraph_text_from_hwpilot(p: dict) -> str:
    if not isinstance(p, dict):
        return ''
    if p.get('text'):
        return str(p['text']).strip()
    runs = p.get('runs', [])
    if isinstance(runs, list):
        return ''.join(str(r.get('text', '')) for r in runs if isinstance(r, dict)).strip()
    return ''


def _table_rows_from_hwpilot(t: dict) -> list[list[str]]:
    if not isinstance(t, dict):
        return []
    rows = t.get('rows')
    if isinstance(rows, list):
        out = []
        for row in rows:
            if isinstance(row, list):
                out.append([str(c.get('text', c) if isinstance(c, dict) else c) for c in row])
            elif isinstance(row, dict):
                cells = row.get('cells', [])
                out.append([str(c.get('text', c) if isinstance(c, dict) else c) for c in cells])
        return out
    cells = t.get('cells')
    if isinstance(cells, list):
        return [[str(c.get('text', c) if isinstance(c, dict) else c) for c in cells]]
    return []


def parse_document_with_hwpilot(file_bytes: bytes, filename: str) -> Optional[dict]:
    """hwpilot read JSON → paragraphs, tables_raw."""
    if not _hwpilot_base():
        return None
    ext = os.path.splitext(filename)[1].lower() or '.hwp'

    def _run(path: str) -> Optional[dict]:
        data = hwpilot_read_file(path)
        if not data:
            return None
        paragraphs: list[str] = []
        tables_raw: list[dict] = []
        for section in data.get('sections', []):
            if not isinstance(section, dict):
                continue
            for p in section.get('paragraphs', []):
                text = _paragraph_text_from_hwpilot(p)
                if text:
                    paragraphs.append(text)
            for t in section.get('tables', []):
                rows = _table_rows_from_hwpilot(t)
                if rows:
                    tables_raw.append({'rows': rows, 'caption': '', 'unit': ''})
        if not paragraphs and not tables_raw:
            return None
        return {
            'paragraphs': paragraphs,
            'tables_raw': tables_raw,
            'full_text': '\n'.join(paragraphs),
            'parser_tag': 'hwpilot-read',
            'hwpilot_raw': data,
        }

    return _with_temp_file(file_bytes, ext, _run)


def hwpilot_read_structure(file_bytes: bytes, filename: str = 'doc.hwpx', limit: int = 30) -> Optional[dict]:
    if not _hwpilot_base():
        return None
    ext = os.path.splitext(filename)[1].lower() or '.hwpx'

    def _run(path: str) -> Optional[dict]:
        return hwpilot_read_file(path, limit=limit)

    return _with_temp_file(file_bytes, ext, _run)


def apply_hwpilot_to_bytes(file_bytes: bytes, filename: str, fn: Callable[[str], tuple[bool, str]]) -> tuple[Optional[bytes], str]:
    """임시 파일에 hwpilot 편집 적용 후 수정된 bytes 반환."""
    ext = os.path.splitext(filename)[1].lower() or '.hwpx'

    def _run(path: str) -> tuple[Optional[bytes], str]:
        ok, msg = fn(path)
        if not ok:
            return None, msg
        with open(path, 'rb') as f:
            return f.read(), msg

    result = _with_temp_file(file_bytes, ext, _run)
    if result is None:
        return None, '임시 파일 처리 실패'
    return result
