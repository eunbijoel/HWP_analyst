"""Completion Planner structures — DocumentState, GapReport, DocumentPlan.

Used only for complete/fill intent (not analyze / review / ask / rewrite).
The name "Document Reasoner" is reserved for a future product-level layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

GapType = Literal["institution_fact", "narrative_necessity", "unsupported"]
ContentKind = Literal["factual", "narrative"]
RiskLevel = Literal["low", "medium", "high"]


@dataclass
class EmptyFieldRef:
  field_id: str
  field_type: str
  label: str
  concept_id: str = ""
  location: str = ""
  current_value: str = ""

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


@dataclass
class DocumentState:
  target_document_id: str
  target_filename: str
  type_hypothesis: str
  paragraphs: list[str] = field(default_factory=list)
  tables: list[dict[str, Any]] = field(default_factory=list)
  section_headings: list[str] = field(default_factory=list)
  empty_fields: list[EmptyFieldRef] = field(default_factory=list)
  incomplete_sections: list[str] = field(default_factory=list)
  reference_documents: list[str] = field(default_factory=list)
  working_copy_version: str = ""
  inspect_ok: bool = True
  inspect_error: str = ""

  def to_dict(self) -> dict[str, Any]:
    d = asdict(self)
    return d


@dataclass
class Gap:
  gap_id: str
  gap_type: GapType
  location: str
  raw_label: str
  content_kind: ContentKind
  required_evidence: list[str]
  confidence: float
  risk_level: RiskLevel
  field_id: str = ""
  concept_id: str = ""
  notes: str = ""

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


@dataclass
class GapReport:
  gaps: list[Gap] = field(default_factory=list)
  target_document: str = ""

  def by_type(self, gap_type: GapType) -> list[Gap]:
    return [g for g in self.gaps if g.gap_type == gap_type]

  def to_dict(self) -> dict[str, Any]:
    return {
      "target_document": self.target_document,
      "gaps": [g.to_dict() for g in self.gaps],
      "counts": {
        "institution_fact": len(self.by_type("institution_fact")),
        "narrative_necessity": len(self.by_type("narrative_necessity")),
        "unsupported": len(self.by_type("unsupported")),
      },
    }


@dataclass
class PlanStep:
  gap_id: str
  gap_type: GapType
  selected_tool: Optional[str]
  reason: str
  execution_order: int
  evidence_exists: bool
  user_review_required: bool
  will_execute: bool = False
  raw_label: str = ""

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


@dataclass
class DocumentPlan:
  steps: list[PlanStep] = field(default_factory=list)
  target_document: str = ""
  user_goal: str = ""

  def executable_steps(self) -> list[PlanStep]:
    return [s for s in self.steps if s.will_execute and s.selected_tool]

  def to_dict(self) -> dict[str, Any]:
    return {
      "target_document": self.target_document,
      "user_goal": self.user_goal,
      "steps": [s.to_dict() for s in self.steps],
    }


@dataclass
class CompletionPlanResult:
  """Result of Completion Planner (complete/fill only)."""

  ok: bool
  state: Optional[DocumentState] = None
  gap_report: Optional[GapReport] = None
  plan: Optional[DocumentPlan] = None
  proposals: list[dict] = field(default_factory=list)
  skipped: list[dict] = field(default_factory=list)
  fill_trace: list[dict] = field(default_factory=list)
  summary: str = ""
  message: str = ""
  tools_run: list[str] = field(default_factory=list)
  meta: dict = field(default_factory=dict)

  def to_dict(self) -> dict[str, Any]:
    return {
      "ok": self.ok,
      "state": self.state.to_dict() if self.state else None,
      "gap_report": self.gap_report.to_dict() if self.gap_report else None,
      "plan": self.plan.to_dict() if self.plan else None,
      "proposals": self.proposals,
      "skipped": self.skipped,
      "fill_trace": self.fill_trace,
      "summary": self.summary,
      "message": self.message,
      "tools_run": self.tools_run,
      "meta": self.meta,
    }


# Compatibility alias (old name overstated the role)
ReasonerResult = CompletionPlanResult
