"""Completion Planner — complete/fill planning only (not a general document brain).

Builds DocumentState, classifies gaps, selects internal fill tools, pending proposals.
Does not handle analyze, review, ask, or rewrite.

Package directory remains ``doc_reasoner`` for import stability.
The product-level name "Document Reasoner" is reserved for a future layer that
may choose among analyze / review / ask / rewrite / complete.
"""

from .models import (
  CompletionPlanResult,
  DocumentPlan,
  DocumentState,
  Gap,
  GapReport,
  PlanStep,
  ReasonerResult,  # compat alias
)
from .planner import TOOL_FACT_FILL_INSTITUTION, TOOL_NARRATIVE_DRAFT
from .reasoner import (
  is_complete_intent,
  run_completion_planner,
  run_reasoner,  # compat alias
)

__all__ = [
  "DocumentState",
  "Gap",
  "GapReport",
  "PlanStep",
  "DocumentPlan",
  "CompletionPlanResult",
  "ReasonerResult",
  "TOOL_FACT_FILL_INSTITUTION",
  "TOOL_NARRATIVE_DRAFT",
  "is_complete_intent",
  "run_completion_planner",
  "run_reasoner",
]
