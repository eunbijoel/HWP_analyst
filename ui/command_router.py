"""
채팅 명령 라우터 — 질문 vs 편집 의도 분류 및 실행
"""

import re
from typing import Optional

from hwp_core.hwpx_editor import HWPXEditor
from additional.ai_editor import (
    generate_blank_fills,
    generate_document_draft,
    rewrite_selection,
    generate_fill_fallback,
    insert_content_from_command,
    delete_content_from_command,
    delete_hwp_from_command,
    replace_hwp_from_command,
    extract_replace_spec,
)


EDIT_FILL = re.compile(r'빈칸|채워|채우|기입|입력해|공란', re.I)
EDIT_DRAFT = re.compile(r'초안|작성해|써줘|작성해줘|제안서|계획서.*작성', re.I)
EDIT_REWRITE = re.compile(r'리라이트|다듬|명확하게|개선', re.I)
EDIT_REPLACE = re.compile(r'찾아서.*바꿔|치환|전체.*바꿔|표', re.I)
EDIT_DELETE = re.compile(r'삭제|지워|제거|없애|빼\s*줘|취소해', re.I)
INSERT_CMD = re.compile(r'넣어|삽입|기입해|적어\s*넣|기록해|추가해', re.I)
INSERT_ANCHOR = re.compile(r'(?:아래|밑|하단|뒤|이후|마지막|맨\s*끝|문서\s*끝)에', re.I)
QUESTION = re.compile(r'\?|얼마|몇 |무엇|어떤|알려|합계|총 |평균|비교|목록|리스트', re.I)
KNOWLEDGE_REQUEST = re.compile(
    r'필요성|중요성|의미|정의|개념|장점|단점|배경|목적|정리|요약|설명|bullet|불릿',
    re.I,
)
DOC_EDIT_ANCHOR = re.compile(r'문단|표|셀|빈칸|본문|문서|여기|이 부분|아래|밑|넣어|삽입', re.I)


def _hwp_editor_required_message(intent: str) -> dict:
    hints = {
        'draft': '초안 작성은 HWPX에서 지원됩니다. HWP는 "마지막에 추가해줘"로 본문 삽입을 이용하세요.',
        'fill': '빈칸 채우기는 HWPX에서 지원됩니다. HWP는 "마지막에 추가해줘" 형식을 이용하세요.',
        'rewrite': '문단 다듬기는 HWPX에서 지원됩니다.',
        'replace': '치환은 HWPX에서 지원되거나, "A를 B로 바꿔" 형식을 사용하세요.',
    }
    return {
        'type': 'edit',
        'intent': intent,
        'message': hints.get(intent, 'HWP 편집은 "마지막에 추가해줘", "삭제해" 같은 명령을 사용하세요.'),
        'changes': 0,
    }


def _try_hwp_bytes_insert(
    command: str,
    source_filename: str,
    file_bytes: bytes | None,
    chat_history: list | None,
    intent: str,
) -> Optional[dict]:
    """editor 없이 .hwp 바이트에 hwpilot 삽입 시도."""
    from hwp_core.hwp_backends import get_backend_status, hwpilot_apply_content
    from additional.ai_editor import _extract_insert_payload

    if not file_bytes or not source_filename.lower().endswith('.hwp'):
        return None
    if not get_backend_status().hwpilot:
        return None

    anchor_hint = '__END__' if re.search(r'마지막|맨\s*끝|문서\s*끝', command, re.I) else ''
    anchor, body = _extract_insert_payload(command, chat_history)
    if anchor_hint:
        anchor = anchor_hint
    if not body or len(body.strip()) < 20:
        return None

    new_bytes, msg = hwpilot_apply_content(
        file_bytes, source_filename, body, anchor=anchor or anchor_hint)
    if not new_bytes:
        return None
    return {
        'type': 'edit',
        'intent': intent,
        'message': f'{msg} — HWP 파일에 반영되었습니다. 다운로드로 저장하세요.',
        'changes': 1,
        'elapsed': 0.0,
        'applied_direct': True,
        'new_file_bytes': new_bytes,
    }


def classify_intent(text: str) -> str:
    t = text.strip()
    if extract_replace_spec(t):
        return 'replace'
    if EDIT_DELETE.search(t):
        return 'delete'
    if EDIT_FILL.search(t):
        return 'fill'
    if INSERT_CMD.search(t) or (INSERT_ANCHOR.search(t) and re.search(r'넣|삽입|추가', t, re.I)):
        return 'insert'
    if KNOWLEDGE_REQUEST.search(t) and not DOC_EDIT_ANCHOR.search(t):
        return 'qa'
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
    if len(t) < 60 and any(w in t for w in ['해줘', '해 주세요', '하세요']):
        return 'draft'
    return 'qa'


def _extract_replace_pair(text: str) -> Optional[tuple[str, str]]:
    """'A를 B로 바꿔' / 'A에서 B로 수정' 패턴 추출."""
    spec = extract_replace_spec(text)
    if spec and spec.get('old') and spec.get('new'):
        return spec['old'], spec['new']
    if spec and spec.get('new'):
        return spec.get('old') or '', spec['new']
    return None


def execute_edit_command(
    editor: Optional[HWPXEditor],
    command: str,
    reference_context: str,
    model: str,
    ollama_url: str,
    selection_text: str = '',
    chat_history: list | None = None,
    source_filename: str = 'doc.hwpx',
    file_bytes: bytes | None = None,
) -> dict:
    """편집 명령 실행 → pending changes 생성."""
    intent = classify_intent(command)
    spec = extract_replace_spec(command)
    pair = (spec['old'], spec['new']) if spec and spec.get('new') else None

    if intent == 'replace':
        if (
            editor is None
            and file_bytes
            and source_filename.lower().endswith('.hwp')
        ):
            from hwp_core.hwp_backends import get_backend_status
            if get_backend_status().hwpilot:
                new_bytes, msg, highlights = replace_hwp_from_command(
                    file_bytes, source_filename, command)
                if new_bytes:
                    return {
                        'type': 'edit', 'intent': 'replace',
                        'message': f'{msg} — HWP 파일에 반영되었습니다. 다운로드로 저장하세요.',
                        'changes': len(highlights), 'elapsed': 0.0,
                        'applied_direct': True,
                        'new_file_bytes': new_bytes,
                        'hwp_highlights': highlights,
                    }
                return {
                    'type': 'edit', 'intent': 'replace',
                    'message': msg or '치환에 실패했습니다.',
                    'changes': 0, 'elapsed': 0.0,
                }
        if editor is None:
            return _hwp_editor_required_message('replace')
        if pair and pair[0] and pair[1]:
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
                'message': f'"{old}" → "{new}" 변경 제안 ({ch.id}) — 왼쪽에서 노란색 확인 후 「모두 적용」',
                'changes': 1,
            }
        if spec and spec.get('line_num') and spec.get('new'):
            line = spec['line_num']
            new = spec['new']
            paras = editor.get_paragraphs()
            idx = line - 1
            if 0 <= idx < len(paras):
                old = paras[idx]['text']
                ch = editor.propose_replace(old, new, location=f'{line}줄 치환')
                return {
                    'type': 'edit', 'intent': 'replace',
                    'message': f'{line}줄 "{old[:30]}" → "{new[:30]}" 변경 제안',
                    'changes': 1,
                }
        return {
            'type': 'edit', 'intent': 'replace',
            'message': '치환할 내용을 찾지 못했습니다. 예: *9줄 A를 B로 바꿔줘*',
            'changes': 0,
        }

    if intent == 'delete':
        from hwp_core.hwp_backends import get_backend_status

        if (
            editor is None
            and file_bytes
            and source_filename.lower().endswith('.hwp')
            and get_backend_status().hwpilot
        ):
            new_bytes, msg, highlights = delete_hwp_from_command(
                file_bytes, source_filename, command, chat_history=chat_history)
            if new_bytes:
                return {
                    'type': 'edit', 'intent': 'delete',
                    'message': f'{msg} — HWP 파일에 반영되었습니다. 다운로드로 저장하세요.',
                    'changes': len(highlights), 'elapsed': 0.0,
                    'applied_direct': True,
                    'new_file_bytes': new_bytes,
                    'hwp_highlights': highlights,
                }
            return {
                'type': 'edit', 'intent': 'delete',
                'message': msg or '삭제에 실패했습니다.',
                'changes': 0, 'elapsed': 0.0,
            }
        if editor is None:
            return {
                'type': 'edit', 'intent': 'delete',
                'message': 'HWP는 HWPX 변환 후 삭제 명령을 이용하세요.',
                'changes': 0,
            }
        changes, msg, elapsed = delete_content_from_command(
            editor, command, chat_history=chat_history, source_filename=source_filename)
        applied = 'hwpilot' in msg and len(changes) == 0
        return {
            'type': 'edit', 'intent': 'delete',
            'message': msg if changes or applied else f'{msg} — 줄 번호(예: 17줄 삭제) 또는 삭제할 내용을 알려 주세요.',
            'changes': len(changes),
            'elapsed': elapsed,
            'applied_direct': applied,
        }

    if intent == 'insert':
        from hwp_core.hwp_backends import get_backend_status, hwpilot_apply_content

        anchor_hint = ''
        if re.search(r'마지막|맨\s*끝|문서\s*끝', command, re.I):
            anchor_hint = '__END__'

        if (
            file_bytes
            and source_filename.lower().endswith('.hwp')
            and get_backend_status().hwpilot
        ):
            from additional.ai_editor import _extract_insert_payload
            anchor, body = _extract_insert_payload(command, chat_history)
            if anchor_hint:
                anchor = anchor_hint
            if not body or len(body.strip()) < 20:
                return {
                    'type': 'edit', 'intent': 'insert',
                    'message': (
                        '삽입할 본문을 찾지 못했습니다. '
                        '명령에 내용을 넣거나(예: *마지막에 ○○○ 추가해줘*), '
                        '먼저 Q&A로 초안을 받은 뒤 "마지막에 추가해줘"를 입력하세요.'
                    ),
                    'changes': 0, 'elapsed': 0.0,
                }
            new_bytes, msg = hwpilot_apply_content(
                file_bytes, source_filename, body, anchor=anchor or anchor_hint)
            if new_bytes:
                return {
                    'type': 'edit', 'intent': 'insert',
                    'message': f'{msg} — HWP 파일에 반영되었습니다. 다운로드로 저장하세요.',
                    'changes': 1, 'elapsed': 0.0,
                    'applied_direct': True,
                    'new_file_bytes': new_bytes,
                }
            return {
                'type': 'edit', 'intent': 'insert',
                'message': msg,
                'changes': 0, 'elapsed': 0.0,
            }

        if editor is None:
            return {
                'type': 'edit', 'intent': 'insert',
                'message': 'HWP 편집 실패 — hwpilot으로 문서 끝 추가를 시도했으나 반영되지 않았습니다.',
                'changes': 0, 'elapsed': 0.0,
            }

        changes, msg, elapsed = insert_content_from_command(
            editor, command, chat_history=chat_history, source_filename=source_filename)
        applied = '반영' in msg or 'hwpilot' in msg
        return {
            'type': 'edit', 'intent': 'insert',
            'message': msg if changes or applied else f'{msg} — 문서에 해당 항목 제목이 있는지 확인해 주세요.',
            'changes': len(changes),
            'elapsed': elapsed,
            'applied_direct': applied,
        }

    if intent == 'fill':
        if editor is None:
            return {'type': 'edit', 'intent': 'fill', 'message': 'HWP는 "마지막에 추가해줘" 형식으로 편집하거나 HWPX 변환 후 이용하세요.', 'changes': 0}
        changes, msg, elapsed = generate_blank_fills(
            editor, command, reference_context, model, ollama_url, max_blanks=30)
        if not changes:
            fb_changes, fb_msg, fb_elapsed = generate_fill_fallback(
                editor, command, reference_context, model, ollama_url)
            if fb_changes:
                return {
                    'type': 'edit',
                    'intent': 'fill',
                    'message': f'{fb_msg} ({fb_elapsed}s) — 추천 대상 기준으로 생성했습니다.',
                    'changes': len(fb_changes),
                    'elapsed': fb_elapsed,
                }
            return {
                'type': 'edit',
                'intent': 'fill',
                'message': f'{fb_msg} ({fb_elapsed}s) — 문서 편집 대상이 없어 Q&A로 전환해 질문해도 됩니다.',
                'changes': 0,
                'elapsed': fb_elapsed,
            }
        return {
            'type': 'edit', 'intent': 'fill',
            'message': f'{msg} ({elapsed}s) — 왼쪽 문서에서 노란색으로 표시됩니다.',
            'changes': len(changes), 'elapsed': elapsed,
        }

    if intent == 'draft':
        if editor is None:
            hwp = _try_hwp_bytes_insert(
                command, source_filename, file_bytes, chat_history, 'draft')
            return hwp or _hwp_editor_required_message('draft')
        changes, msg, elapsed = generate_document_draft(
            editor, command, reference_context, model, ollama_url)
        if not changes and not EDIT_DELETE.search(command):
            ins_changes, ins_msg, ins_elapsed = insert_content_from_command(
                editor, command, chat_history=chat_history, source_filename=source_filename)
            if ins_changes or 'hwpilot으로 문서에 반영' in ins_msg:
                return {
                    'type': 'edit', 'intent': 'insert',
                    'message': f'{ins_msg} (초안 JSON 대신 채팅 내용 삽입)',
                    'changes': len(ins_changes), 'elapsed': ins_elapsed,
                    'applied_direct': 'hwpilot' in ins_msg,
                }
        return {
            'type': 'edit', 'intent': 'draft',
            'message': f'{msg} ({elapsed}s)',
            'changes': len(changes), 'elapsed': elapsed,
        }

    if intent == 'rewrite':
        if editor is None:
            hwp = _try_hwp_bytes_insert(
                command, source_filename, file_bytes, chat_history, 'rewrite')
            return hwp or _hwp_editor_required_message('rewrite')
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

    if editor is None:
        if intent in ('replace', 'delete'):
            return _hwp_editor_required_message(intent)
        hwp = _try_hwp_bytes_insert(
            command, source_filename, file_bytes, chat_history, intent)
        return hwp or _hwp_editor_required_message(intent)

    changes, msg, elapsed = generate_document_draft(
        editor, command, reference_context, model, ollama_url)
    if not changes and not EDIT_DELETE.search(command):
        ins_changes, ins_msg, ins_elapsed = insert_content_from_command(
            editor, command, chat_history=chat_history, source_filename=source_filename)
        if ins_changes or 'hwpilot으로 문서에 반영' in ins_msg:
            return {
                'type': 'edit', 'intent': 'insert',
                'message': ins_msg,
                'changes': len(ins_changes), 'elapsed': ins_elapsed,
            }
    return {
        'type': 'edit', 'intent': 'draft',
        'message': msg, 'changes': len(changes), 'elapsed': elapsed,
    }
