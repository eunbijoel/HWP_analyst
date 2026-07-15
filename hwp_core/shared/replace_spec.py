"""Shared replace-command parsing (no document mutation)."""

from __future__ import annotations

import re
from typing import Optional


def extract_replace_spec(text: str) -> Optional[dict]:
    """치환 명령에서 old/new/line_num 추출."""
    t = (text or "").strip()
    t = re.sub(r"^이\s*문서에서\s*", "", t)

    m = re.search(
        r"(.+?)\s*문구(?:를|을)\s*(.+?)\s*(?:으로|로)\s*(?:바꿔|수정|변경|고쳐)",
        t,
        re.S,
    )
    if m:
        return {
            "line_num": None,
            "old": m.group(1).strip(),
            "new": m.group(2).strip(),
        }

    m = re.search(
        r"(\d+)\s*줄\s*(.+?)\s*(?:을|를)\s*(.+?)\s*(?:으로|로)\s*(?:바꿔|수정|변경|교체|고쳐)",
        t,
        re.S,
    )
    if m:
        return {
            "line_num": int(m.group(1)),
            "old": m.group(2).strip(),
            "new": m.group(3).strip(),
        }
    m = re.search(
        r"(\d+)\s*줄\s*(?:을|를)?\s*(.+?)\s*(?:으로|로)\s*(?:바꿔|수정|변경|교체|고쳐)",
        t,
        re.S,
    )
    if m:
        return {
            "line_num": int(m.group(1)),
            "old": "",
            "new": m.group(2).strip(),
        }
    m = re.search(r"[\'\"\"](.+?)[\'\"\"].*?[\'\"\"](.+?)[\'\"\"]", t)
    if m:
        return {"line_num": None, "old": m.group(1).strip(), "new": m.group(2).strip()}
    for pat in (
        r"([0-9][0-9,\.]*)\s*(?:을|를)\s*([0-9][0-9,\.]*)\s*(?:으로|로)\s*(?:바꿔|수정|변경)",
        r"([0-9][0-9,\.]*)\s*에서\s*([0-9][0-9,\.]*)\s*(?:으로|로)(?:\s*(?:바꿔|수정|변경|해))?",
    ):
        matches = list(re.finditer(pat, t))
        if matches:
            m = matches[-1]
            return {"line_num": None, "old": m.group(1).strip(), "new": m.group(2).strip()}
    for pat in (
        r"(.+?)\s*에서\s*(.+?)\s*(?:으로|로)\s*(?:바꿔|수정|변경|교체|고쳐|해)",
        r"(.+?)\s*(?:을|를)\s*(.+?)\s*(?:으로|로)\s*(?:바꿔|수정|변경|교체|고쳐|해)",
    ):
        m = re.search(pat, t, re.S)
        if m:
            old_s = m.group(1).strip()
            new_s = m.group(2).strip()
            if len(old_s) >= 1 and len(new_s) >= 1:
                return {"line_num": None, "old": old_s, "new": new_s}
    return None
