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

import requests

from hwpx_editor import HWPXEditor, BlankField, PendingChange


def _call_ollama_json(prompt: str, model: str, ollama_url: str,
                      timeout: int = 180) -> tuple[Optional[dict | list], str]:
    try:
        response = requests.post(
            f'{ollama_url}/api/generate',
            json={
                'model': model,
                'prompt': prompt,
                'stream': False,
                'format': 'json',
                'options': {'temperature': 0.3, 'num_predict': 4096, 'num_ctx': 32768},
            },
            timeout=timeout,
        )
        if response.status_code != 200:
            return None, f'HTTP {response.status_code}'
        raw = response.json().get('response', '').strip()
        if not raw:
            return None, '빈 응답'
        return json.loads(raw), ''
    except json.JSONDecodeError as e:
        m = re.search(r'[\[{].*[\]}]', raw, re.S)
        if m:
            try:
                return json.loads(m.group()), ''
            except json.JSONDecodeError:
                pass
        return None, f'JSON 파싱 오류: {e}'
    except requests.RequestException as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)


def _document_outline(editor: HWPXEditor, max_paras: int = 30) -> str:
    paras = editor.get_paragraphs()[:max_paras]
    lines = [f'- 문단{p["index"]+1}: {p["text"][:100]}' for p in paras]
    table_lines = []
    for t_idx in range(min(editor.get_table_count(), 5)):
        rows = editor.get_table_as_rows(t_idx)
        if rows:
            table_lines.append(f'[표{t_idx+1}] 헤더: {rows[0][:6]}')
    return '\n'.join(lines + table_lines)


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
