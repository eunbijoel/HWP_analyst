"""Product B editing domain (mutations, highlight preview, edit router)."""

from hwp_core.editing.edit_router import execute_edit_command
from hwp_core.shared.intent_classify import classify_intent

__all__ = ["classify_intent", "execute_edit_command"]
