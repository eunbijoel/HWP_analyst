"""
채팅 명령 라우터 — 질문 vs 편집 의도 분류 및 실행
"""

import re
from typing import Optional

from main.hwpx_editor import HWPXEditor
from additional.ai_editor import generate_blank_fills, generate_document_draft, rewrite_selection


EDIT_FILL = re.compile(r'빈칸|채워|채우|기입|입력해|공란', re.I)
EDIT_DRAFT = re.compile(r'초안|작성해|써줘|작성해줘|제안서|계획서.*작성', re.I)
EDIT_REWRITE = re.compile(r'리라이트|다듬|명확하게|개선', re.I)
EDIT_REPLACE = re.compile(r'찾아서.*바꿔|치환|전체.*바꿔|표', re.I)
QUESTION = re.compile(r'\?|얼마|몇 |무엇|어떤|알려|합계|총 |평균|비교|목록|리스트', re.I)


def classify_intent(text: str) -> str:
    t = text.strip()
    if _extract_replace_pair(t):
        return 'replace'
    if EDIT_FILL.search(t):
        return 'fill'
    if EDIT_DRAFT.search(t):
        return 'draft'
    if EDIT_REPLACE.search(t):
        return 'replace'
    if re.search(r'바꿔|수정|고쳐|교체|변경', t, re.I):
        return 'replace'
    if EDIT_REWRITE.search(t):
        return 'rewrite'
    if QUESTION.search(t):
        return 'qa'
    if len(t) < 60 and any(w in t for w in ['해줘', '해 주세요', '하세요', '넣어']):
        return 'draft'
    return 'qa'


def _extract_replace_pair(text: str) -> Optional[tuple[str, str]]:
    """'A를 B로 바꿔' / 'A에서 B로 수정' 패턴 추출."""
    m = re.search(r'[\'""](.+?)[\'""].*?[\'""](.+?)[\'""]', text)
    if m:
        return m.group(1), m.group(2)

    pair_patterns = [
        r'([0-9][0-9,\.]*)\s*(?:을|를)\s*([0-9][0-9,\.]*)\s*(?:으로|로)\s*(?:바꿔|수정|변경)',
        r'([0-9][0-9,\.]*)\s*에서\s*([0-9][0-9,\.]*)\s*(?:으로|로)(?:\s*(?:바꿔|수정|변경|해))?',
    ]
    for pat in pair_patterns:
        matches = list(re.finditer(pat, text))
        if matches:
            m = matches[-1]
            return m.group(1).strip(), m.group(2).strip()
    return None


def execute_edit_command(
    editor: HWPXEditor,
    command: str,
    reference_context: str,
    model: str,
    ollama_url: str,
    selection_text: str = '',
) -> dict:
    """편집 명령 실행 → pending changes 생성."""
    intent = classify_intent(command)
    pair = _extract_replace_pair(command)

    if intent == 'replace' and pair:
        old, new = pair
        ch = editor.propose_table_value_replace(command, old, new)
        if ch:
            return {
                'type': 'edit', 'intent': 'replace',
                'message': (
                    f'표 셀 변경 제안: "{ch.old_text}" → "{ch.new_text}" '
                    f'({ch.location}, {ch.id})'
                ),
                'changes': 1,
            }
        ch = editor.propose_replace(old, new, location=f'치환: {old[:20]}')
        return {
            'type': 'edit', 'intent': 'replace',
            'message': f'"{old}" → "{new}" 변경 제안 ({ch.id}) — 표에서 못 찾으면 본문 치환',
            'changes': 1,
        }

    if intent == 'fill':
        changes, msg, elapsed = generate_blank_fills(
            editor, command, reference_context, model, ollama_url, max_blanks=30)
        return {
            'type': 'edit', 'intent': 'fill',
            'message': f'{msg} ({elapsed}s) — 왼쪽 문서에서 노란색으로 표시됩니다.',
            'changes': len(changes), 'elapsed': elapsed,
        }

    if intent == 'draft':
        changes, msg, elapsed = generate_document_draft(
            editor, command, reference_context, model, ollama_url)
        return {
            'type': 'edit', 'intent': 'draft',
            'message': f'{msg} ({elapsed}s)',
            'changes': len(changes), 'elapsed': elapsed,
        }

    if intent == 'rewrite':
        target = selection_text.strip()
        if not target:
            paras = editor.get_paragraphs()
            if paras:
                target = paras[0]['text']
        if target:
            change, msg, elapsed = rewrite_selection(
                editor, target, command, reference_context, model, ollama_url)
            if change:
                return {
                    'type': 'edit', 'intent': 'rewrite',
                    'message': f'{msg} ({elapsed}s)',
                    'changes': 1, 'elapsed': elapsed,
                }
            return {'type': 'edit', 'intent': 'rewrite', 'message': msg, 'changes': 0}

    changes, msg, elapsed = generate_document_draft(
        editor, command, reference_context, model, ollama_url)
    return {
        'type': 'edit', 'intent': 'draft',
        'message': msg, 'changes': len(changes), 'elapsed': elapsed,
    }
