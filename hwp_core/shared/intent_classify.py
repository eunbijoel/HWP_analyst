"""
Shared intent classification — no mutation, no product UI.

Used by Product A (analysis vs edit-redirect) and Product B (editing routes).
"""

from __future__ import annotations

import re

from hwp_core.shared.replace_spec import extract_replace_spec

EDIT_FILL = re.compile(
    r"빈\s*칸|빈칸|공란|"
    r"(?:채우|채워|보완|보충|보강)(?:\s*줘|\s*주세요|기|해)?|"
    r"기입해|입력해|"
    r"참고\s*자료(?:를|로|로써)?.{0,24}(?:넣(?:어)?|채우|채워|작성|기입|반영|이용|보완)|"
    r"(?:넣(?:어)?|채우|채워|작성|기입|반영|보완).{0,16}(?:참고\s*자료|엑셀|문서|자료)|"
    r"(?:두|2)\s*(?:개\s*)?(?:문서|자료|파일).{0,24}(?:보|참고|반영|채|넣|작|보완)|"
    r"(?:문서|자료|내용).{0,16}(?:보|참고|합쳐).{0,20}(?:보완|반영|채|넣|작)|"
    r"내용을?\s*보(?:고|아서).{0,20}(?:보완|반영|채|넣|작)",
    re.I,
)
EDIT_DRAFT = re.compile(r"초안|작성해|써줘|작성해줘|제안서|계획서.*작성", re.I)
EDIT_REWRITE = re.compile(
    r"리라이트|다듬|명확하게|개선|짧게|줄여|간결|공문체|기술문서|"
    r"제목.{0,20}(?:수정|바꿔|변경|고쳐)|"
    r"(?:수정|바꿔|변경|고쳐).{0,20}제목|"
    r"이\s*부분.{0,24}(?:수정|짧게|줄여|다듬|변경)",
    re.I,
)
EDIT_REPLACE = re.compile(r"찾아서.*바꿔|치환|전체.*바꿔", re.I)
EDIT_DELETE = re.compile(r"삭제|지워|제거|없애|빼\s*줘|취소해", re.I)
INSERT_CMD = re.compile(r"넣어|삽입|기입해|적어\s*넣|기록해|추가해|추가하", re.I)
INSERT_ANCHOR = re.compile(r"(?:아래|밑|하단|뒤|이후|마지막|맨\s*끝|문서\s*끝)에", re.I)
QUESTION = re.compile(
    r"\?|얼마|몇 |무엇|어떤|어떻게|뭐야|뭔지|돼|알려|합계|소계|총 |평균|비교|목록|리스트",
    re.I,
)
TABLE_QUESTION = re.compile(
    r"표\s*\d+.*(?:어떻게|뭐|얼마|소계|합계|알려|돼|계산|뭔지)",
    re.I,
)
TABLE_CELL_REF = re.compile(
    r"\d+\s*행\s*\d+\s*열|"
    r"[A-Za-z]+\s*열\s*\d+\s*행|"
    r"\d+\s*행\s*[A-Za-z]+\s*열|"
    r"[A-Za-z]\s*열\s*[A-Za-z]\s*행",
    re.I,
)
TABLE_CELL_EDIT = re.compile(
    r"표\s*\d+.*(?:추가|반영|넣|기입).*(?:계산|소계|합계|재계산)|"
    r"표\s*\d+.*[\d,]+.*(?:추가|반영)",
    re.I,
)
APPEND_REF = re.compile(
    r"(?:참고자료|참고\s*자료).*(?:요약|추가|넣|반영)|"
    r"(?:요약|요약한\s*내용).*(?:추가|넣|반영|삽입)|"
    r"(?:추가|넣|반영).*(?:참고자료|요약한\s*내용)|"
    r"\.hwpx.*(?:에\s*)?(?:추가|넣)|\.hwp.*(?:에\s*)?(?:추가|넣)",
    re.I,
)
REPLACE_VERBS = re.compile(r"(?:바꿔|치환|교체|고쳐|변경)(?:줘|주세요)?", re.I)
REPLACE_PATTERN = re.compile(
    r"(?:을|를)\s*.+\s*(?:으로|로)\s*(?:바꿔|수정|변경|교체|고쳐)",
    re.I,
)
KNOWLEDGE_REQUEST = re.compile(
    r"필요성|중요성|의미|정의|개념|장점|단점|배경|목적|정리|요약|설명|bullet|불릿",
    re.I,
)
DOC_EDIT_ANCHOR = re.compile(r"문단|표|셀|빈칸|본문|문서|여기|이 부분|아래|밑|넣어|삽입", re.I)

EDIT_INTENTS = frozenset({
    "fill",
    "draft",
    "rewrite",
    "replace",
    "delete",
    "insert",
    "table_edit",
    "append_ref",
})


COMPUTE_THEN_WRITE = re.compile(
    r"(?:계산|산출|구해).{0,40}(?:넣|채우|기입|반영)|"
    r"(?:넣|채우|기입|반영).{0,40}(?:계산|산출)",
    re.I,
)
VALUE_AS_EDIT = re.compile(
    r"(?:으로|로)\s*(?:수정|바꿔|변경|고쳐)",
    re.I,
)
PHRASE_EDIT = re.compile(
    r"문구(?:를|을)\s*.+?\s*(?:으로|로)\s*(?:바꿔|수정|변경|고쳐)",
    re.I,
)


def classify_intent(text: str) -> str:
    t = text.strip()
    spec = extract_replace_spec(t)

    if TABLE_CELL_EDIT.search(t):
        return "table_edit"
    if COMPUTE_THEN_WRITE.search(t):
        return "table_edit"
    if TABLE_CELL_REF.search(t) and (
        REPLACE_VERBS.search(t) or re.search(r"(?:으로|로)\s*[\d,]", t)
    ):
        return "replace"
    if EDIT_FILL.search(t) and not re.search(r"요약\s*만", t):
        return "fill"
    if APPEND_REF.search(t):
        return "append_ref"
    if PHRASE_EDIT.search(t) or VALUE_AS_EDIT.search(t):
        return "replace"
    if TABLE_QUESTION.search(t) and not (spec and REPLACE_VERBS.search(t)):
        return "qa"
    if QUESTION.search(t) and not (spec and (REPLACE_VERBS.search(t) or spec.get("line_num"))):
        if not REPLACE_PATTERN.search(t):
            return "qa"

    if spec and spec.get("old") and spec.get("new"):
        return "replace"
    if spec and (spec.get("line_num") or REPLACE_VERBS.search(t) or REPLACE_PATTERN.search(t)):
        return "replace"
    if EDIT_DELETE.search(t):
        return "delete"
    if INSERT_CMD.search(t) or (INSERT_ANCHOR.search(t) and re.search(r"넣|삽입|추가", t, re.I)):
        return "insert"
    if KNOWLEDGE_REQUEST.search(t) and not DOC_EDIT_ANCHOR.search(t):
        return "qa"
    if EDIT_DRAFT.search(t):
        return "draft"
    if EDIT_REPLACE.search(t):
        return "replace"
    if REPLACE_PATTERN.search(t):
        return "replace"
    if EDIT_REWRITE.search(t):
        return "rewrite"
    if QUESTION.search(t):
        return "qa"
    return "qa"


def is_edit_intent(intent: str) -> bool:
    return intent in EDIT_INTENTS
