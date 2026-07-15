"""
Compatibility shim — chat intent + edit execution.

Prefer:
  hwp_core.shared.intent_classify.classify_intent
  hwp_core.editing.edit_router.execute_edit_command
  hwp_core.analysis.intent_route (Product A)
"""

from hwp_core.shared.intent_classify import (  # noqa: F401
    EDIT_INTENTS,
    classify_intent,
    is_edit_intent,
)
from hwp_core.editing.edit_router import execute_edit_command  # noqa: F401

__all__ = [
    "classify_intent",
    "execute_edit_command",
    "is_edit_intent",
    "EDIT_INTENTS",
]
