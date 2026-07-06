"""
AI 기반 HWPX 편집 엔진
- 빈칸/표 자동 채우기
- 양식 문서 초안 생성
- 선택 영역 리라이트
"""

import json
import re
import time
from typing import Optional

from hwp_core.hwpx_editor import HWPXEditor, BlankField, PendingChange
from hwp_core.llm_client import generate_json as _call_ollama_json


def _document_outline(editor: HWPXEditor, max_paras: int = 30) -> str:
    paras = editor.get_paragraphs()[:max_paras]
    lines = [f'- 문단{p["index"]+1}: {p["text"][:100]}' for p in paras]
    table_lines = []
    for t_idx in range(min(editor.get_table_count(), 5)):
        rows = editor.get_table_as_rows(t_idx)
        if rows:
            table_lines.append(f'[표{t_idx+1}] 헤더: {rows[0][:6]}')
    return '\n'.join(lines + table_lines)


def _suggest_fill_targets(editor: HWPXEditor, max_items: int = 6) -> list[dict]:
    targets = []
    blanks = editor.detect_blanks(meaningful_only=True)[:max_items]
    for b in blanks:
        if b.field_type == 'cell':
            targets.append({
                'kind': 'cell',
                'table_index': b.table_index,
                'row': b.row,
                'col': b.col,
                'context': b.context,
                'current': b.current_text,
            })
        else:
            targets.append({
                'kind': 'paragraph',
                'paragraph_index': b.paragraph_index,
                'context': b.context,
                'current': b.current_text,
            })

    if targets:
        return targets

    paras = editor.get_paragraphs()[:max_items]
    for p in paras:
        targets.append({
            'kind': 'paragraph',
            'paragraph_index': p['index'],
            'context': p['preview'],
            'current': p['text'][:120],
        })
    return targets


def generate_blank_fills(
    editor: HWPXEditor,
    instruction: str,
    reference_context: str = '',
    model: str = 'gemma4',
    ollama_url: str = 'http://localhost:11434',
    max_blanks: int = 40,
) -> tuple[list[PendingChange], str, float]:
    """빈 셀·플레이스홀더를 감지하고 LLM으로 값 생성 → PendingChange 목록."""
    blanks = editor.detect_blanks()[:max_blanks]
    if not blanks:
        return [], '채울 빈칸이 없습니다.', 0.0

    blank_specs = []
    for i, b in enumerate(blanks):
        spec = {
            'id': i,
            'type': b.field_type,
            'location': b.location,
            'context': b.context,
            'current': b.current_text,
        }
        if b.field_type == 'cell':
            spec.update({'table_index': b.table_index, 'row': b.row, 'col': b.col})
        else:
            spec.update({'paragraph_index': b.paragraph_index})
        blank_specs.append(spec)

    ref_section = f'\n## 참고 자료:\n{reference_context}\n' if reference_context else ''
    prompt = f"""당신은 한글 공문/사업계획서 작성 전문가입니다.
아래 HWPX 문서의 빈칸을 사용자 지시에 맞게 채우세요.

## 사용자 지시:
{instruction}
{ref_section}
## 문서 개요:
{_document_outline(editor)}

## 채울 빈칸 목록:
{json.dumps(blank_specs, ensure_ascii=False, indent=2)}

## 출력 형식 (JSON만):
{{"fills": [{{"id": 0, "value": "채울 내용"}}]}}

규칙:
- id는 위 목록의 id와 일치
- 숫자·날짜·기관명은 한국 공문 양식에 맞게
- 모르는 정보는 "해당없음" 또는 합리적 예시값
- 표 셀은 짧게, 문단은 1~3문장"""

    start = time.time()
    data, err = _call_ollama_json(prompt, model, ollama_url)
    elapsed = round(time.time() - start, 1)
    if data is None:
        return [], f'LLM 오류: {err}', elapsed

    fills = data.get('fills', []) if isinstance(data, dict) else data
    changes: list[PendingChange] = []
    blank_by_id = {i: b for i, b in enumerate(blanks)}

    for item in fills:
        if not isinstance(item, dict):
            continue
        bid = item.get('id')
        value = str(item.get('value', '')).strip()
        if bid is None or not value or bid not in blank_by_id:
            continue
        b = blank_by_id[bid]
        if b.field_type == 'cell':
            ch = editor.propose_cell_change(
                b.table_index, b.row, b.col, value, context=b.context)
        else:
            ch = editor.propose_paragraph_change(b.paragraph_index, value)
        changes.append(ch)

    return changes, f'{len(changes)}건 생성', elapsed


def generate_fill_fallback(
    editor: HWPXEditor,
    instruction: str,
    reference_context: str = '',
    model: str = 'gemma4',
    ollama_url: str = 'http://localhost:11434',
) -> tuple[list[PendingChange], str, float]:
    """채우기 대상이 모호할 때 추천 대상 기준으로 생성."""
    targets = _suggest_fill_targets(editor)
    if not targets:
        return [], '추천할 문단/표 대상이 없습니다.', 0.0

    ref_section = f'\n## 참고 자료:\n{reference_context}\n' if reference_context else ''
    prompt = f"""사용자 지시에 맞춰 문서의 추천 대상을 채워주세요.

## 사용자 지시:
{instruction}
{ref_section}
## 문서 개요:
{_document_outline(editor)}

## 추천 대상:
{json.dumps(targets, ensure_ascii=False, indent=2)}

## 출력 형식 (JSON만):
{{
  "actions": [
    {{"kind":"cell","table_index":0,"row":1,"col":2,"value":"내용"}},
    {{"kind":"paragraph","paragraph_index":3,"value":"보강 문장"}}
  ],
  "summary":"작업 요약"
}}

규칙:
- actions는 최대 6개
- 추천 대상에서만 작성
- 숫자/고유명사는 문맥에 맞게 보수적으로
- 값이 불명확하면 생성하지 않음"""

    start = time.time()
    data, err = _call_ollama_json(prompt, model, ollama_url, timeout=180)
    elapsed = round(time.time() - start, 1)
    if data is None:
        return [], f'LLM 오류: {err}', elapsed

    actions = data.get('actions', []) if isinstance(data, dict) else []
    summary = data.get('summary', '') if isinstance(data, dict) else ''
    changes: list[PendingChange] = []

    for action in actions[:6]:
        if not isinstance(action, dict):
            continue
        kind = action.get('kind', '')
        value = str(action.get('value', '')).strip()
        if not value:
            continue
        try:
            if kind == 'cell':
                ch = editor.propose_cell_change(
                    int(action['table_index']),
                    int(action['row']),
                    int(action['col']),
                    value,
                    context='추천 대상 자동 생성',
                )
                changes.append(ch)
            elif kind == 'paragraph':
                ch = editor.propose_paragraph_change(
                    int(action['paragraph_index']),
                    value,
                )
                changes.append(ch)
        except (KeyError, ValueError, TypeError):
            continue

    if changes:
        return changes, summary or f'추천 대상 기반 {len(changes)}건 생성', elapsed
    return [], summary or '추천 대상 기반 생성 결과가 없어 적용하지 못했습니다.', elapsed


def generate_document_draft(
    editor: HWPXEditor,
    instruction: str,
    reference_context: str = '',
    model: str = 'gemma4',
    ollama_url: str = 'http://localhost:11434',
) -> tuple[list[PendingChange], str, float]:
    """양식 문서에 장별 초안을 생성하여 PendingChange로 제안."""
    ref_section = f'\n## 참고 자료:\n{reference_context}\n' if reference_context else ''
    existing = _document_outline(editor, max_paras=40)

    prompt = f"""당신은 한글 사업계획서/제안서 작성 전문가입니다.
기존 양식 문서 구조를 유지하면서 아래 지시에 맞는 초안을 작성하세요.

## 사용자 지시:
{instruction}
{ref_section}
## 현재 문서 구조:
{existing}

## 출력 형식 (JSON만):
{{
  "sections": [
    {{"action": "fill_blank", "table_index": 0, "row": 1, "col": 2, "value": "내용"}},
    {{"action": "append", "title": "1. 개요", "body": "본문..."}},
    {{"action": "replace_paragraph", "paragraph_index": 3, "value": "새 문단"}}
  ],
  "summary": "작업 요약 1~2문장"
}}

action 종류:
- fill_blank: 표 빈칸 채우기 (table_index, row, col, value)
- append: 문서 끝에 제목+본문 추가 (title, body)
- replace_paragraph: 기존 문단 교체 (paragraph_index, value)

최대 15개 항목. 한국어 공문체."""

    start = time.time()
    data, err = _call_ollama_json(prompt, model, ollama_url, timeout=240)
    elapsed = round(time.time() - start, 1)
    if data is None:
        return [], f'LLM 오류: {err}', elapsed

    sections = data.get('sections', []) if isinstance(data, dict) else []
    summary = data.get('summary', '') if isinstance(data, dict) else ''
    changes: list[PendingChange] = []

    for sec in sections[:15]:
        if not isinstance(sec, dict):
            continue
        action = sec.get('action', '')
        if action == 'fill_blank':
            ch = editor.propose_cell_change(
                int(sec['table_index']), int(sec['row']), int(sec['col']),
                str(sec.get('value', '')),
                context=sec.get('title', ''))
            changes.append(ch)
        elif action == 'replace_paragraph':
            ch = editor.propose_paragraph_change(
                int(sec['paragraph_index']), str(sec.get('value', '')))
            changes.append(ch)
        elif action == 'append':
            title = str(sec.get('title', ''))
            body = str(sec.get('body', ''))
            ch = editor.propose_replace(
                '', f'{title}\n{body}',
                location=f'추가: {title[:30]}')
            ch.change_type = 'append'
            ch.new_text = body
            ch.search_hint = title
            changes.append(ch)

    msg = summary or f'{len(changes)}건 초안 생성'
    return changes, msg, elapsed


def rewrite_selection(
    editor: HWPXEditor,
    selection_text: str,
    instruction: str,
    reference_context: str = '',
    model: str = 'gemma4',
    ollama_url: str = 'http://localhost:11434',
) -> tuple[Optional[PendingChange], str, float]:
    """선택 텍스트를 지시에 맞게 리라이트 → PendingChange."""
    selection_text = selection_text.strip()
    if not selection_text:
        return None, '선택 텍스트가 비어 있습니다.', 0.0

    ref_section = f'\n## 참고 자료:\n{reference_context}\n' if reference_context else ''
    prompt = f"""다음 문서 일부를 사용자 지시에 맞게 다시 작성하세요.

## 사용자 지시:
{instruction}
{ref_section}
## 원문:
{selection_text}

## 출력 형식 (JSON만):
{{"rewritten": "수정된 전체 텍스트", "summary": "변경 요약"}}

규칙: 원문 의미 유지, 한국어 공문체, 지시한 분량 준수."""

    start = time.time()
    data, err = _call_ollama_json(prompt, model, ollama_url)
    elapsed = round(time.time() - start, 1)
    if data is None:
        return None, f'LLM 오류: {err}', elapsed

    new_text = ''
    summary = ''
    if isinstance(data, dict):
        new_text = str(data.get('rewritten', '')).strip()
        summary = str(data.get('summary', ''))
    if not new_text:
        return None, '생성된 텍스트가 없습니다.', elapsed

    ch = editor.propose_replace(
        selection_text, new_text,
        location=f'선택 편집: {selection_text[:30]}...')
    return ch, summary or '리라이트 완료', elapsed


def _extract_insert_payload(command: str, chat_history: list | None) -> tuple[str, str]:
    """삽입 앵커와 본문을 명령 또는 이전 채팅에서 추출."""
    cmd = command.strip()
    anchor = ''

    m = re.search(r'(.+?)\s*(?:아래|밑|하단|뒤|이후)에', cmd, re.I | re.S)
    if m:
        anchor = m.group(1).strip()
        if len(anchor) > 120:
            title_m = re.search(
                r'([^\n]+(?:개발|과제|항목|플랫폼|모델|사업|연구)[^\n]*)', anchor)
            if title_m:
                anchor = title_m.group(1).strip()

    if re.search(r'마지막|맨\s*끝|문서\s*끝', cmd, re.I):
        anchor = '__END__'
    if re.search(
        r'(?:참고자료|요약|요약한\s*내용).*(?:추가|넣|반영)|'
        r'(?:추가|넣|반영).*(?:참고자료|요약)|\.hwpx.*(?:추가|넣)|\.hwp.*(?:추가|넣)',
        cmd, re.I,
    ):
        anchor = '__END__'

    body = cmd
    if anchor:
        idx = body.find(anchor)
        if idx >= 0:
            body = body[idx + len(anchor):]
    body = re.sub(r'(?:아래|밑|하단|뒤|이후)에\s*', '', body, flags=re.I)
    body = re.sub(r'(?:이\s*)?문서(?:의)?\s*마지막에.*$', '', body, flags=re.I | re.S)
    body = re.sub(r'(?:내용\s*)?넣어\s*줘.*$', '', body, flags=re.I | re.S)
    body = re.sub(r'(?:내용\s*)?추가해\s*줘.*$', '', body, flags=re.I | re.S)
    body = re.sub(r'(?:이\s*)?(?:넣|삽입|기록).*$', '', body, flags=re.I | re.S)
    body = body.strip()

    colon_m = re.search(r'[:：]\s*(.+)$', cmd, re.S)
    if colon_m and len(colon_m.group(1).strip()) >= 20:
        body = colon_m.group(1).strip()

    if chat_history:
        for msg in reversed(chat_history):
            if msg.get('role') != 'assistant':
                continue
            content = (msg.get('content') or '').strip()
            if len(content) < 20:
                continue
            if content.startswith('👉') or '노란색' in content or '모두 적용' in content:
                continue
            body = content
            break

    junk_fragments = {'마지막', '맨끝', '문서끝', '에', ''}
    if re.sub(r'\s+', '', body) in junk_fragments or len(body) < 20:
        body = ''

    return anchor, body


def insert_content_from_command(
    editor: HWPXEditor,
    command: str,
    chat_history: list | None = None,
    source_filename: str = 'doc.hwpx',
) -> tuple[list[PendingChange], str, float]:
    """채팅/명령 본문을 앵커 문단 아래에 삽입 제안."""
    anchor, body = _extract_insert_payload(command, chat_history)
    if not body or len(body.strip()) < 20:
        return [], '삽입할 본문을 찾지 못했습니다. 이전 AI 답변 또는 붙여넣은 내용이 필요합니다.', 0.0

    from hwp_core.hwp_backends import get_backend_status, hwpilot_apply_content
    from additional.reference_parser import normalize_insert_body

    body = normalize_insert_body(body)

    if get_backend_status().hwpilot and anchor == '__END__':
        new_bytes, msg = hwpilot_apply_content(
            editor.get_working_bytes(), source_filename, body, anchor='__END__')
        if new_bytes:
            editor.reload_from_bytes(new_bytes, from_hwpilot=True)
            return [], f'{msg} — 문서 끝에 반영되었습니다.', 0.0
        return [], msg, 0.0

    if get_backend_status().hwpilot and anchor and anchor != '__END__':
        ok, msg = editor.apply_hwpilot_insert_after(anchor, body, filename=source_filename)
        if ok:
            return [], f'{msg} — hwpilot으로 문서에 반영되었습니다.', 0.0

    ch = editor.propose_insert_after_anchor(anchor, body)
    if ch:
        loc = anchor[:40] + ('...' if len(anchor) > 40 else '') if anchor else ch.location
        return [ch], f'"{loc}" 아래에 내용 삽입 제안 (1건)', 0.0

    if anchor and get_backend_status().hwpilot:
        ok, msg = editor.apply_hwpilot_insert_after(anchor, body, filename=source_filename)
        if ok:
            return [], f'{msg} — hwpilot으로 문서에 반영되었습니다.', 0.0

    if anchor:
        return [], f'문서에서 "{anchor[:50]}" 문단을 찾지 못했습니다.', 0.0
    if get_backend_status().hwpilot:
        new_bytes, msg = hwpilot_apply_content(
            editor.get_working_bytes(), source_filename, body, anchor='__END__')
        if new_bytes:
            editor.reload_from_bytes(new_bytes, from_hwpilot=True)
            return [], f'{msg} — 문서 끝에 반영되었습니다.', 0.0
    return [], '삽입 위치를 지정해 주세요. 예: "…마지막에 추가해줘"', 0.0


def extract_replace_spec(text: str) -> Optional[dict]:
    """치환 명령에서 old/new/line_num 추출."""
    t = (text or '').strip()
    m = re.search(
        r'(\d+)\s*줄\s*(.+?)\s*(?:을|를)\s*(.+?)\s*(?:으로|로)\s*(?:바꿔|수정|변경|교체|고쳐)',
        t, re.S,
    )
    if m:
        return {
            'line_num': int(m.group(1)),
            'old': m.group(2).strip(),
            'new': m.group(3).strip(),
        }
    m = re.search(
        r'(\d+)\s*줄\s*(?:을|를)?\s*(.+?)\s*(?:으로|로)\s*(?:바꿔|수정|변경|교체|고쳐)',
        t, re.S,
    )
    if m:
        return {
            'line_num': int(m.group(1)),
            'old': '',
            'new': m.group(2).strip(),
        }
    m = re.search(r'[\'""](.+?)[\'""].*?[\'""](.+?)[\'""]', t)
    if m:
        return {'line_num': None, 'old': m.group(1).strip(), 'new': m.group(2).strip()}
    for pat in (
        r'([0-9][0-9,\.]*)\s*(?:을|를)\s*([0-9][0-9,\.]*)\s*(?:으로|로)\s*(?:바꿔|수정|변경)',
        r'([0-9][0-9,\.]*)\s*에서\s*([0-9][0-9,\.]*)\s*(?:으로|로)(?:\s*(?:바꿔|수정|변경|해))?',
    ):
        matches = list(re.finditer(pat, t))
        if matches:
            m = matches[-1]
            return {'line_num': None, 'old': m.group(1).strip(), 'new': m.group(2).strip()}
    m = re.search(
        r'(.+?)\s*(?:을|를)\s*(.+?)\s*(?:으로|로)\s*(?:바꿔|수정|변경|교체|고쳐)',
        t, re.S,
    )
    if m and len(m.group(1).strip()) >= 2 and len(m.group(2).strip()) >= 1:
        return {'line_num': None, 'old': m.group(1).strip(), 'new': m.group(2).strip()}
    return None


def replace_hwp_from_command(
    file_bytes: bytes,
    filename: str,
    command: str,
) -> tuple[Optional[bytes], str, list[dict]]:
    """HWP 줄/본문 치환."""
    from hwp_core.hwp_backends import (
        apply_hwpilot_to_bytes,
        get_hwp_preview_paragraphs,
        hwpilot_edit_text,
        hwpilot_resolve_paragraph_ref,
        resolve_hwp_line_ref,
    )

    spec = extract_replace_spec(command)
    if not spec or not spec.get('new'):
        return None, '치환할 내용을 찾지 못했습니다.', []

    preview = get_hwp_preview_paragraphs(file_bytes, filename)
    line_num = spec.get('line_num')
    new_text = spec['new']
    old_text = (spec.get('old') or '').strip()
    if line_num and not old_text and 0 < line_num <= len(preview):
        old_text = preview[line_num - 1].strip()
    highlights: list[dict] = []

    def _edit(path: str) -> tuple[bool, str]:
        ref = None
        if line_num:
            ref = resolve_hwp_line_ref(path, line_num, preview)
        if not ref and old_text:
            ref = hwpilot_resolve_paragraph_ref(path, old_text=old_text)
        if not ref:
            return False, '치환할 문단을 찾지 못했습니다.'
        if not old_text and line_num and 0 < line_num <= len(preview):
            old_text_local = preview[line_num - 1].strip()
        else:
            old_text_local = old_text
        ok, err = hwpilot_edit_text(path, ref, new_text)
        if not ok:
            return False, err or '치환 실패'
        ln = line_num
        if not ln and old_text_local in preview:
            ln = preview.index(old_text_local) + 1
        highlights.append({
            'line': ln or 0,
            'old': old_text_local,
            'new': new_text,
            'type': 'replace',
        })
        label = f'{ln}줄' if ln else '본문'
        return True, f'{label}: "{old_text_local[:40]}" → "{new_text[:40]}"'

    new_bytes, msg = apply_hwpilot_to_bytes(file_bytes, filename, _edit)
    if not new_bytes:
        return None, msg, []
    return new_bytes, msg, highlights


def _norm_text(text: str) -> str:
    return re.sub(r'\s+', '', (text or '').strip())


def _texts_match(a: str, b: str) -> bool:
    a_n, b_n = _norm_text(a), _norm_text(b)
    if not a_n or not b_n:
        return False
    sample = min(len(a_n), len(b_n), 40)
    return a_n[:sample] in b_n or b_n[:sample] in a_n


def _split_content_blocks(text: str) -> list[str]:
    text = (text or '').strip()
    if not text:
        return []
    blocks = [b.strip() for b in re.split(r'\n\s*\n', text) if b.strip()]
    if len(blocks) <= 1:
        parts = re.split(
            r'(?=(?:기술적/|정책적/|시스템\s+전환|(?:\d+\.\s*)?[가-힣]+(?:/[^\n:]{1,20})?:\s))',
            text,
        )
        blocks = [p.strip() for p in parts if p.strip()]
    return blocks


def _extract_delete_targets(command: str, chat_history: list | None) -> list[str]:
    """삭제 대상 본문 블록 추출."""
    cmd = (command or '').strip()
    body = re.sub(
        r'(?:이\s*)?(?:내용|부분|문단|줄|텍스트|글)?\s*(?:을|를)?\s*(?:삭제|지워|제거|없애).*$',
        '', cmd, flags=re.I | re.S,
    ).strip()
    body = re.sub(r'^(?:삭제|지워|제거|없애)(?:해)?(?:줘)?\.?$', '', body, flags=re.I).strip()

    if len(body) >= 20:
        return _split_content_blocks(body)

    if not chat_history:
        return []

    for msg in reversed(chat_history):
        if msg.get('role') != 'assistant':
            continue
        content = (msg.get('content') or '').strip()
        m = re.search(r'(\d+)개\s*문단\s*추', content)
        if m:
            return ['__UNDO_LAST_INSERT__', m.group(1)]

    for msg in reversed(chat_history):
        if msg.get('role') != 'user':
            continue
        content = (msg.get('content') or '').strip()
        if len(content) < 40:
            continue
        if re.search(r'삭제|지워|제거', content, re.I) and len(content) < 100:
            continue
        if re.search(r'추가|넣어|삽입', content, re.I):
            blocks = _split_content_blocks(content)
            if blocks:
                return blocks
        if len(content) >= 80:
            return _split_content_blocks(content)

    return []


def _indices_for_line_number(editor: HWPXEditor, line_num: int) -> list[int]:
    """N줄 = 문서 미리보기 기준 N번째 문단 (1-based)."""
    paras = editor.get_paragraphs()
    idx = line_num - 1
    if 0 <= idx < len(paras):
        return [paras[idx]['index']]
    return []


def _indices_from_content_blocks(paras: list[dict], blocks: list[str]) -> list[int]:
    if blocks and blocks[0] == '__UNDO_LAST_INSERT__':
        n = int(blocks[1])
        if n <= 0 or not paras:
            return []
        tail = paras[-n:]
        return [p['index'] for p in tail]

    indices: list[int] = []
    used: set[int] = set()
    for block in blocks:
        for p in reversed(paras):
            if p['index'] in used:
                continue
            if _texts_match(p['text'], block):
                indices.append(p['index'])
                used.add(p['index'])
                break
    return sorted(indices)


def _apply_paragraph_deletes(
    editor: HWPXEditor,
    indices: list[int],
    source_filename: str,
) -> tuple[list[PendingChange], str]:
    if not indices:
        return [], '삭제할 문단이 없습니다.'

    from hwp_core.hwp_backends import (
        apply_hwpilot_to_bytes,
        get_backend_status,
        hwpilot_resolve_paragraph_ref,
        hwpilot_edit_text,
    )

    if get_backend_status().hwpilot:
        unique = sorted(set(indices))

        def _edit(path: str) -> tuple[bool, str]:
            refs: list[str] = []
            for idx in unique:
                ref = hwpilot_resolve_paragraph_ref(path, idx)
                if ref:
                    refs.append(ref)
            if not refs:
                return False, 'hwpilot ref를 찾지 못했습니다.'
            deleted = 0
            for ref in refs:
                ok, _ = hwpilot_edit_text(path, ref, ' ')
                if ok:
                    deleted += 1
            if deleted == 0:
                return False, '문단 삭제에 실패했습니다.'
            return True, f'hwpilot으로 {deleted}개 문단 삭제'

        new_bytes, msg = apply_hwpilot_to_bytes(
            editor.get_working_bytes(), source_filename, _edit)
        if new_bytes:
            editor.reload_from_bytes(new_bytes, from_hwpilot=True)
            return [], msg
        return [], msg

    changes: list[PendingChange] = []
    for idx in sorted(set(indices)):
        changes.append(editor.propose_paragraph_change(idx, ''))
    return changes, f'{len(changes)}건 삭제 제안'


def delete_content_from_command(
    editor: HWPXEditor,
    command: str,
    chat_history: list | None = None,
    source_filename: str = 'doc.hwpx',
) -> tuple[list[PendingChange], str, float]:
    """줄 번호 또는 본문 매칭으로 문단 삭제."""
    cmd = (command or '').strip()
    paras = editor.get_paragraphs()

    m = re.search(r'(\d+)\s*줄', cmd)
    if m:
        indices = _indices_for_line_number(editor, int(m.group(1)))
        if not indices:
            return [], f'{m.group(1)}줄 문단을 찾지 못했습니다.', 0.0
        changes, msg = _apply_paragraph_deletes(editor, indices, source_filename)
        applied = not changes and 'hwpilot' in msg
        suffix = ' — 문서에 반영되었습니다.' if applied else ' — 왼쪽에서 확인 후 「모두 적용」하세요.'
        return changes, msg + suffix, 0.0

    blocks = _extract_delete_targets(cmd, chat_history)
    if not blocks:
        if re.search(r'삭제|지워|제거', cmd, re.I) and paras:
            n = 1
            for msg in reversed(chat_history or []):
                if msg.get('role') != 'assistant':
                    continue
                m2 = re.search(r'(\d+)개\s*문단\s*추', msg.get('content', ''))
                if m2:
                    n = int(m2.group(1))
                    break
            indices = [p['index'] for p in paras[-n:]]
            changes, msg = _apply_paragraph_deletes(editor, indices, source_filename)
            applied = not changes and 'hwpilot' in msg
            suffix = ' — 문서에 반영되었습니다.' if applied else ''
            return changes, msg + suffix, 0.0
        return [], '삭제할 내용을 찾지 못했습니다.', 0.0

    indices = _indices_from_content_blocks(paras, blocks)
    if not indices:
        return [], '문서에서 삭제할 문단을 찾지 못했습니다.', 0.0

    changes, msg = _apply_paragraph_deletes(editor, indices, source_filename)
    applied = not changes and 'hwpilot' in msg
    suffix = ' — 문서에 반영되었습니다.' if applied else ' — 왼쪽에서 확인 후 「모두 적용」하세요.'
    return changes, msg + suffix, 0.0


def _paragraphs_from_hwp_bytes(file_bytes: bytes, filename: str) -> list[dict]:
    from hwp_core.hwp_backends import parse_document_with_hwpilot

    data = parse_document_with_hwpilot(file_bytes, filename)
    if not data:
        return []
    return [
        {'index': i, 'text': t}
        for i, t in enumerate(data.get('paragraphs', []))
        if t and str(t).strip()
    ]


def _apply_hwp_line_deletes(
    file_bytes: bytes,
    filename: str,
    line_nums: list[int],
    preview_paragraphs: list[str],
) -> tuple[Optional[bytes], str, list[dict]]:
    from hwp_core.hwp_backends import (
        apply_hwpilot_to_bytes,
        hwpilot_edit_text,
        resolve_hwp_line_ref,
    )

    unique_lines = sorted({ln for ln in line_nums if ln > 0}, reverse=True)
    highlights: list[dict] = []

    def _edit(path: str) -> tuple[bool, str]:
        deleted = 0
        for ln in unique_lines:
            if ln > len(preview_paragraphs):
                continue
            ref = resolve_hwp_line_ref(path, ln, preview_paragraphs)
            if not ref:
                continue
            old = preview_paragraphs[ln - 1]
            ok, _ = hwpilot_edit_text(path, ref, ' ')
            if ok:
                deleted += 1
                highlights.append({
                    'line': ln, 'old': old, 'new': '', 'type': 'delete',
                })
        if deleted == 0:
            return False, '문단 삭제에 실패했습니다.'
        return True, f'hwpilot으로 {deleted}개 문단 삭제'

    new_bytes, msg = apply_hwpilot_to_bytes(file_bytes, filename, _edit)
    if not new_bytes:
        return None, msg, []
    return new_bytes, msg, highlights


def _apply_hwp_paragraph_deletes(
    file_bytes: bytes,
    filename: str,
    indices: list[int],
) -> tuple[Optional[bytes], str, list[dict]]:
    from hwp_core.hwp_backends import get_hwp_preview_paragraphs

    preview = get_hwp_preview_paragraphs(file_bytes, filename)
    line_nums = [i + 1 for i in indices if 0 <= i < len(preview)]
    if line_nums:
        return _apply_hwp_line_deletes(file_bytes, filename, line_nums, preview)
    return None, '삭제할 문단을 찾지 못했습니다.', []


def delete_hwp_from_command(
    file_bytes: bytes,
    filename: str,
    command: str,
    chat_history: list | None = None,
) -> tuple[Optional[bytes], str, list[dict]]:
    """HWP bytes에서 줄 번호/본문 매칭으로 문단 삭제."""
    from hwp_core.hwp_backends import get_hwp_preview_paragraphs

    cmd = (command or '').strip()
    preview = get_hwp_preview_paragraphs(file_bytes, filename)
    paras = [
        {'index': i, 'text': t}
        for i, t in enumerate(preview)
        if t and str(t).strip()
    ]

    line_nums = [int(x) for x in re.findall(r'(\d+)\s*줄', cmd)]
    if line_nums:
        return _apply_hwp_line_deletes(file_bytes, filename, line_nums, preview)

    blocks = _extract_delete_targets(cmd, chat_history)
    if not blocks:
        if re.search(r'삭제|지워|제거', cmd, re.I) and paras:
            n = 1
            for msg in reversed(chat_history or []):
                if msg.get('role') != 'assistant':
                    continue
                m2 = re.search(r'(\d+)개\s*문단\s*추', msg.get('content', ''))
                if m2:
                    n = int(m2.group(1))
                    break
            tail_lines = [p['index'] + 1 for p in paras[-n:]]
            return _apply_hwp_line_deletes(file_bytes, filename, tail_lines, preview)
        return None, '삭제할 내용을 찾지 못했습니다.', []

    indices = _indices_from_content_blocks(paras, blocks)
    if not indices:
        return None, '문서에서 삭제할 문단을 찾지 못했습니다.', []
    return _apply_hwp_paragraph_deletes(file_bytes, filename, indices)


def apply_table_cell_amount_command(
    editor: HWPXEditor,
    command: str,
) -> tuple[list, str, float]:
    """표 번호·행 키워드·금액으로 셀 값 설정/추가."""
    m_table = re.search(r'표\s*(\d+)', command)
    m_amount = re.search(r'([\d][\d,]*)', command)
    if not m_table or not m_amount:
        return [], '표 번호와 금액을 찾지 못했습니다.', 0.0

    table_idx = int(m_table.group(1)) - 1
    try:
        amount_val = float(m_amount.group(1).replace(',', ''))
    except ValueError:
        return [], '금액 형식을 이해하지 못했습니다.', 0.0

    is_add = bool(re.search(r'추가', command))
    skip = {
        '표', '추가', '반영', '계산', '다시', '해줘', '하고', '넣', '기입',
        '한국', '생산성', '본부',
    }
    keywords = [
        w for w in re.findall(r'[가-힣a-zA-Z0-9]+', command)
        if len(w) >= 2 and w not in skip and not re.fullmatch(r'[\d,\.]+', w)
    ]

    if table_idx < 0 or table_idx >= editor.get_table_count():
        return [], f'표 {table_idx + 1}을 찾지 못했습니다.', 0.0

    rows = editor.get_table_as_rows(table_idx)
    if not rows:
        return [], '표가 비어 있습니다.', 0.0

    best = None
    best_score = 0
    for r_idx, row in enumerate(rows):
        row_text = ' '.join(str(c) for c in row)
        score = sum(3 for kw in keywords if kw in row_text)
        if score <= 0:
            continue
        for c_idx, cell in enumerate(row):
            cell_s = str(cell).strip()
            if not cell_s:
                continue
            if re.fullmatch(r'[\d,\.\-\s]+', cell_s) or re.search(r'\d{2,}', cell_s):
                if score > best_score:
                    best = (r_idx, c_idx, cell_s, row_text)
                    best_score = score

    if not best or best_score < 3:
        return [], (
            f'표 {table_idx + 1}에서 해당 행을 찾지 못했습니다. '
            f'(예: *표 3 한국생산성본부 현물인건비 44,000 추가하고 소계 다시 계산해줘*)'
        ), 0.0

    r_idx, c_idx, old_cell, row_text = best
    old_num = 0.0
    parsed_old = re.sub(r'[^\d.\-]', '', old_cell.replace(',', ''))
    if parsed_old and parsed_old not in ('-', ''):
        try:
            old_num = float(parsed_old)
        except ValueError:
            pass
    new_val = amount_val if not is_add else old_num + amount_val
    new_str = f'{int(new_val):,}' if abs(new_val - int(new_val)) < 0.01 else f'{new_val:,.0f}'

    ch = editor.propose_cell_change(
        table_idx, r_idx, c_idx, new_str, context=row_text[:50],
    )
    verb = '추가' if is_add else '설정'
    return [ch], (
        f'표 {table_idx + 1} {row_text[:35]}… 셀 {old_cell or "(빈칸)"} → {new_str} ({verb})'
    ), 0.0
