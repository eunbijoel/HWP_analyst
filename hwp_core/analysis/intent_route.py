"""Product A analysis chat routing helpers (no document writes)."""

from __future__ import annotations

from hwp_core.shared.intent_classify import classify_intent, is_edit_intent

EDIT_REDIRECT_MESSAGE = (
    "문서 **수정·채우기·저장**은 **HWP Editing Assistant** "
    "(Product B · `HWP_v2` / `apps/editor`)에서 진행하세요.\n\n"
    "여기서는 문서 **이해·검토·질문**만 지원합니다. "
    "예: *총 사업비는?*, *이 이슈가 왜 생겼어?*"
)


def route_analysis_intent(text: str) -> str:
    """Return 'qa' for analysis chat, or the edit intent name for redirect."""
    intent = classify_intent(text)
    if is_edit_intent(intent):
        return intent
    return "qa"


def analysis_chat_reply_for_edit_intent(intent: str) -> str:
    return (
        f"{EDIT_REDIRECT_MESSAGE}\n\n"
        f"(감지된 편집 의도: `{intent}`)"
    )
