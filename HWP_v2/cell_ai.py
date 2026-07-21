"""선택 셀·문단 지시용 프롬프트 · 로컬 축약. 가짜 이름/직위 생성 없음."""

from __future__ import annotations

import re


def detect_cell_intent(user_msg: str) -> str:
    m = (user_msg or "").strip()
    if re.search(r"줄여|간결|요약|짧게|단어\s*수", m):
        return "shorten"
    if re.search(r"문장\s*끝|문장으로|완성", m):
        return "finish"
    return "rewrite"


def extract_literal_cell_value(user_msg: str) -> str | None:
    """「100,000으로 채워/작성/넣어」처럼 넣을 값이 명시되면 그대로 반환. LLM 우회용."""
    t = (user_msg or "").strip()
    if not t:
        return None
    # 1) 숫자 + (으로|로) + 쓰기 동사
    m = re.search(
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:원|천원)?\s*"
        r"(?:으로|로)\s*(?:채우|채워|넣|작성|기입|수정|바꿔|변경|고쳐|입력)",
        t,
    )
    if m:
        return m.group(1)
    # 2) 쓰기 동사 근처의 마지막 숫자
    if re.search(r"(?:채우|채워|넣|작성|기입|수정|바꿔|입력)", t):
        nums = re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", t)
        if nums:
            return nums[-1]
    # 3) 따옴표/「」 안 짧은 값
    m = re.search(r"[「\"']([^」\"']{1,40})[」\"']", t)
    if m and re.search(r"(?:채우|채워|넣|작성|기입|바꿔)", t):
        return m.group(1).strip()
    return None


def shorten_locally(text: str, aggressive: bool = False) -> str:
    """LLM 실패 시 문장만 짧게 자르기 (값 날조 없음)."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return t
    parts = re.split(r"(?<=[.。])\s+|(?<=다)\s+", t)
    parts = [p.strip() for p in parts if p.strip()]
    if parts:
        t = parts[0]
    max_len = 36 if aggressive else 56
    if len(t) > max_len:
        t = t[: max_len - 1].rstrip() + "…"
    return t


def build_cell_prompt(
    *,
    filename: str,
    t: int,
    r: int,
    c: int,
    old: str,
    user_msg: str,
    intent: str,
    row_hint: str = "",
) -> str:
    if intent == "shorten":
        return f"""셀 텍스트를 더 짧게. rewritten에는 축약된 본문만.
설명 문구 금지.

현재:
\"\"\"{old}\"\"\"

지시: {user_msg}
JSON만: {{"rewritten":"짧은 본문","summary":"축약"}}"""

    if intent == "finish":
        return f"""개조식을 완성된 한국어 문장 하나로. rewritten = 그 문장만.

현재:
\"\"\"{old}\"\"\"

지시: {user_msg}
JSON만: {{"rewritten":"완성 문장.","summary":"문장 완성"}}"""

    return f"""셀에 들어갈 최종 문자열만 rewritten에.
위치: {filename} 표{t+1} {r+1}행 {c+1}열
행 맥락(참고만, rewritten에 넣지 말 것): {row_hint[:200]}
현재: {old!r}
지시: {user_msg}

규칙:
- 지시가 숫자/금액(예: 100,000)을 넣으라고 하면 rewritten은 그 숫자만.
- 행 라벨(비용명·비목분류 등)을 rewritten에 넣지 말 것.
- 설명 문장 금지.
JSON만: {{"rewritten":"...","summary":"한 줄"}}"""


def build_para_prompt(*, old: str, user_msg: str, intent: str) -> str:
    if intent == "shorten":
        return f"""문단을 짧게. rewritten = 축약 본문만.

\"\"\"{old}\"\"\"

지시: {user_msg}
JSON만: {{"rewritten":"...","summary":"축약"}}"""
    return f"""문단을 지시대로 수정. rewritten = 문단 전체.

\"\"\"{old}\"\"\"

지시: {user_msg}
JSON만: {{"rewritten":"...","summary":"한 줄"}}"""
