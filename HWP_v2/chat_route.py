"""
Product B chat routing — selection-aware edit vs question decisions.

Whole-document analysis stays in Product A.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from hwp_core.shared.intent_classify import (
    EDIT_FILL,
    classify_intent,
    is_edit_intent,
)
from hwp_core.shared.replace_spec import extract_replace_spec

try:
    from hwp_core.workflows.registry import match_workflow as match_named_workflow
except ImportError:  # pragma: no cover
    def match_named_workflow(_message: str):
        return None

WRITE_VERB = re.compile(
    r"(?:수정|바꿔|변경|고쳐|다듬|리라이트|짧게|줄여|간결|"
    r"채우|채워|넣어|기입|삽입|추가|반영|삭제|지워|치환|작성)"
    r"(?:\s*(?:해|하))?(?:줘|주세요|라|요)?",
    re.I,
)
EXPLAIN_QUESTION = re.compile(
    r"\?|뭐야|무엇|뭔지|알려(?:줘|주세요)?|의미|정의|개념|설명해|"
    r"어떻게\s*돼|왜\s*(?:이|그|저)|무엇인가요|뭔가요",
    re.I,
)
ANALYSIS_ONLY = re.compile(
    r"(?:두|여러|멀티).{0,8}문서.{0,16}(?:비교|대조)|"
    r"(?:비교|검증|타당성|검토)\s*(?:해|하)|"
    r"이슈(?:가|를)?\s*(?:왜|설명)|"
    r"(?:전체|문서).{0,12}(?:분석|리뷰)|"
    r"(?:합계|소계|총\s*사업비|평균).{0,12}(?:은|는|이|가)?\s*(?:얼마|몇)|"
    r"(?:얼마|몇)\s*(?:야|인가요|입니까|지)",
    re.I,
)
COMPUTE_THEN_WRITE = re.compile(
    r"(?:계산|산출|구해).{0,40}(?:넣|채우|기입|반영)|"
    r"(?:넣|채우|기입|반영).{0,40}(?:계산|산출)",
    re.I,
)
EXPLAIN_PENDING = re.compile(
    r"(?:변경|제안|pending).{0,12}(?:설명|알려|뭐|무엇)|"
    r"(?:설명|알려).{0,12}(?:변경|제안)|"
    r"왜\s*(?:바꿨|수정|변경)",
    re.I,
)
VALUE_LABEL_EDIT = re.compile(
    r"^(.+?)\s+([가-힣A-Za-z0-9_/\-·\s]{1,40}?)(?:으로|로)\s*(?:수정|바꿔|변경|고쳐)",
    re.I,
)
PHRASE_EDIT = re.compile(
    r"(?:이\s*문서에서\s*)?(.+?)\s*문구(?:를|을)\s*(.+?)\s*(?:으로|로)\s*"
    r"(?:바꿔|수정|변경|고쳐)",
    re.I,
)


@dataclass
class TargetCandidate:
    kind: str  # paragraph | cell
    label: str
    text: str
    para_index: Optional[int] = None
    table_index: Optional[int] = None
    row: Optional[int] = None
    col: Optional[int] = None


@dataclass
class EditSpec:
    old: str = ""
    new: str = ""
    label: str = ""
    needs_compute: bool = False


@dataclass
class ChatRoute:
    action: str
    message: str = ""
    spec: Optional[EditSpec] = None
    targets: list[TargetCandidate] = field(default_factory=list)


def is_explanatory_question(message: str) -> bool:
    t = (message or "").strip()
    if not t:
        return False
    if COMPUTE_THEN_WRITE.search(t):
        return False
    if EXPLAIN_PENDING.search(t):
        return True
    if WRITE_VERB.search(t):
        return False
    if re.search(r"(?:으로|로)\s*(?:수정|바꿔|변경|고쳐)", t):
        return False
    return bool(EXPLAIN_QUESTION.search(t) or classify_intent(t) == "qa")


def is_explicit_edit_command(message: str) -> bool:
    t = (message or "").strip()
    if not t:
        return False
    if COMPUTE_THEN_WRITE.search(t):
        return True
    if EDIT_FILL.search(t) and not re.search(r"요약\s*만", t):
        return True
    if is_edit_intent(classify_intent(t)):
        return True
    if WRITE_VERB.search(t):
        return True
    if re.search(r"(?:으로|로)\s*(?:수정|바꿔|변경|고쳐)", t):
        return True
    return False


def is_analysis_redirect(message: str) -> bool:
    t = (message or "").strip()
    if not t:
        return False
    if COMPUTE_THEN_WRITE.search(t):
        return False
    if is_explicit_edit_command(t):
        return False
    return bool(ANALYSIS_ONLY.search(t) or classify_intent(t) == "qa")


def parse_edit_spec(command: str) -> EditSpec:
    t = (command or "").strip()
    needs_compute = bool(COMPUTE_THEN_WRITE.search(t))

    m = PHRASE_EDIT.search(t)
    if m:
        return EditSpec(
            old=m.group(1).strip(),
            new=m.group(2).strip(),
            needs_compute=needs_compute,
        )

    t2 = re.sub(r"^이\s*문서에서\s*", "", t)
    spec = extract_replace_spec(t2)
    if spec and spec.get("new"):
        old = (spec.get("old") or "").strip()
        new = (spec.get("new") or "").strip()
        if old in ("이 문서", "문서", "이"):
            old = ""
        if old and new and old != new:
            return EditSpec(old=old, new=new, needs_compute=needs_compute)

    m = VALUE_LABEL_EDIT.match(t)
    if m:
        left, label = m.group(1).strip(), m.group(2).strip()
        if label and left and len(left) <= 40:
            return EditSpec(
                old="", new=left, label=label, needs_compute=needs_compute,
            )

    if needs_compute:
        label = ""
        m = re.search(r"([가-힣A-Za-z0-9_/\-·]+)\s*합계", t)
        if m:
            label = m.group(1)
        else:
            label = "합계"
        return EditSpec(old="", new="", label=label, needs_compute=True)

    # Fallback: use classify old/new from loose patterns
    intent = classify_intent(t)
    if intent == "replace" and spec and spec.get("new"):
        return EditSpec(
            old=(spec.get("old") or "").strip(),
            new=(spec.get("new") or "").strip(),
            needs_compute=needs_compute,
        )

    return EditSpec(needs_compute=needs_compute)


def find_edit_targets(editor: Any, spec: EditSpec) -> list[TargetCandidate]:
    if editor is None:
        return []

    needle = (spec.old or spec.label or "").strip()
    if not needle:
        return []

    out: list[TargetCandidate] = []
    seen: set[tuple] = set()

    try:
        paras = editor.get_paragraphs() or []
    except Exception:
        paras = []
    needle_ns = re.sub(r"\s+", "", needle)
    for p in paras:
        text = (p.get("text") if isinstance(p, dict) else str(p)) or ""
        idx = p.get("index") if isinstance(p, dict) else None
        if needle in text or (needle_ns and needle_ns in re.sub(r"\s+", "", text)):
            key = ("p", idx)
            if key in seen:
                continue
            seen.add(key)
            out.append(TargetCandidate(
                kind="paragraph",
                label=f"문단 {(idx or 0) + 1}",
                text=text[:120],
                para_index=idx,
            ))

    try:
        n_tables = int(editor.get_table_count())
    except Exception:
        n_tables = 0

    for t_idx in range(n_tables):
        try:
            rows = editor.get_table_as_rows(t_idx) or []
        except Exception:
            continue
        for r_idx, row in enumerate(rows):
            row_text = " ".join(str(c or "") for c in row)
            if spec.label and spec.label in row_text and not spec.old:
                # Prefer last non-header-ish value column in the matching row
                if len(row) <= 1:
                    continue
                c2 = len(row) - 1
                key = ("c", t_idx, r_idx, c2)
                if key in seen:
                    continue
                seen.add(key)
                out.append(TargetCandidate(
                    kind="cell",
                    label=f"표{t_idx + 1} ({r_idx + 1}행,{c2 + 1}열)",
                    text=str(row[c2] or "")[:120],
                    table_index=t_idx,
                    row=r_idx,
                    col=c2,
                ))
                continue

            for c_idx, cell in enumerate(row):
                cell_s = str(cell or "")
                if not spec.old:
                    continue
                if spec.old in cell_s or cell_s.strip() == spec.old.strip():
                    key = ("c", t_idx, r_idx, c_idx)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(TargetCandidate(
                        kind="cell",
                        label=f"표{t_idx + 1} ({r_idx + 1}행,{c_idx + 1}열)",
                        text=cell_s[:120],
                        table_index=t_idx,
                        row=r_idx,
                        col=c_idx,
                    ))

    return out


def decide_chat_route(
    *,
    message: str,
    has_selection: bool,
    has_editor: bool,
    has_docs: bool,
) -> ChatRoute:
    t = (message or "").strip()
    if not t:
        return ChatRoute(action="ask_select", message="메시지를 입력하세요.")

    if not has_docs:
        return ChatRoute(
            action="ask_select",
            message="문서를 열어 주세요. HWP / HWPX를 여러 개 올릴 수 있습니다.",
        )

    wf_id = match_named_workflow(t)
    if wf_id:
        return ChatRoute(action=f"workflow:{wf_id}")

    if classify_intent(t) == "fill" or (
        EDIT_FILL.search(t) and not re.search(r"요약\s*만", t)
    ):
        return ChatRoute(action="fill")

    if EXPLAIN_PENDING.search(t):
        return ChatRoute(action="explain_pending")

    if has_selection:
        if is_explanatory_question(t):
            return ChatRoute(action="answer_selection")
        if is_explicit_edit_command(t):
            return ChatRoute(action="rewrite_selection")
        if EXPLAIN_QUESTION.search(t):
            return ChatRoute(action="answer_selection")
        # Imperative / short instruction with a selection → rewrite
        return ChatRoute(action="rewrite_selection")

    # No selection
    if is_analysis_redirect(t):
        return ChatRoute(action="redirect_a", message=_redirect_msg(t))

    if is_explicit_edit_command(t):
        if not has_editor:
            return ChatRoute(
                action="ask_select",
                message=(
                    "활성 문서는 읽기 전용입니다. "
                    "편집은 HWPX 문서를 활성으로 선택하세요."
                ),
            )
        spec = parse_edit_spec(t)
        if spec.needs_compute:
            return ChatRoute(action="compute_edit", spec=spec)
        return ChatRoute(action="search_edit", spec=spec)

    return ChatRoute(action="redirect_a", message=_redirect_msg(t))


def resolve_search_edit(editor: Any, message: str, spec: Optional[EditSpec] = None) -> ChatRoute:
    """No-selection edit: resolve 0 / 1 / many targets."""
    spec = spec or parse_edit_spec(message)
    if spec.needs_compute:
        return ChatRoute(action="compute_edit", spec=spec)

    targets = find_edit_targets(editor, spec)
    if len(targets) == 1:
        return ChatRoute(action="propose_replace", spec=spec, targets=targets)
    if len(targets) > 1:
        lines = [
            "여러 위치가 맞습니다. 문단을 직접 선택하거나 더 구체적으로 적어 주세요:",
            "",
        ]
        for i, tg in enumerate(targets[:12], 1):
            preview = tg.text if len(tg.text) <= 60 else tg.text[:57] + "…"
            lines.append(f"{i}. {tg.label} — {preview}")
        if len(targets) > 12:
            lines.append(f"… 외 {len(targets) - 12}곳")
        return ChatRoute(
            action="choose_targets",
            message="\n".join(lines),
            spec=spec,
            targets=targets,
        )
    return ChatRoute(
        action="ask_select",
        message=(
            f"「{(spec.old or spec.label or '대상')[:40]}」위치를 찾지 못했습니다. "
            "문단을 선택한 뒤 다시 지시해 주세요."
        ),
        spec=spec,
    )


def _redirect_msg(question: str) -> str:
    return (
        "전체 문서 Q&A·비교·검증·계산 질문은 **HWP Document Intelligence** "
        "(Product A)에서 하세요.\n"
        "`streamlit run apps/intelligence/app.py`\n\n"
        "편집기에서는 선택 리라이트·채우기·제안 설명만 지원합니다.\n"
        f"(질문: {question[:120]})"
    )


def compute_label_total(editor: Any, label: str) -> tuple[Optional[str], str]:
    """
    Sum numeric cells on rows matching label (excluding the last cell if it looks like total).
    Returns (formatted_value, note).
    """
    if editor is None or not label:
        return None, "계산할 표가 없습니다."

    try:
        import pandas as pd
    except Exception:
        pd = None  # type: ignore

    totals: list[float] = []
    try:
        n_tables = int(editor.get_table_count())
    except Exception:
        return None, "표를 읽지 못했습니다."

    for t_idx in range(n_tables):
        rows = editor.get_table_as_rows(t_idx) or []
        matched = []
        for row in rows:
            row_text = " ".join(str(c or "") for c in row)
            if label not in row_text:
                continue
            nums = []
            for cell in row[1:]:
                s = str(cell or "").replace(",", "").strip()
                if re.fullmatch(r"-?\d+(?:\.\d+)?", s):
                    nums.append(float(s))
            if nums:
                # sum detail values; if one number only, use it
                matched.append(sum(nums) if len(nums) > 1 else nums[0])
        if matched and pd is not None:
            totals.append(float(pd.Series(matched).sum()))
        elif matched:
            totals.append(sum(matched))

    if not totals:
        return None, f"「{label}」행에서 숫자 합계를 계산하지 못했습니다."
    val = totals[0] if len(totals) == 1 else sum(totals)
    if val == int(val):
        formatted = f"{int(val):,}"
    else:
        formatted = f"{val:,.2f}"
    return formatted, f"{label} 합계 계산"
