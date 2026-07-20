"""Create DocumentPlan from GapReport — Completion Planner tool selection."""

from __future__ import annotations

from .models import DocumentPlan, DocumentState, GapReport, PlanStep

# Internal tool ids (not user-facing workflow names)
TOOL_FACT_FILL_INSTITUTION = "fact_fill_institution"
TOOL_NARRATIVE_DRAFT = "narrative_draft"  # planned; not executed in MVP


def create_document_plan(
  state: DocumentState,
  gap_report: GapReport,
  *,
  user_goal: str = "이 문서 완성해줘",
) -> DocumentPlan:
  steps: list[PlanStep] = []
  has_refs = bool(state.reference_documents)
  order = 1

  # Group institution facts into one executable tool invocation (order 1)
  inst_gaps = gap_report.by_type("institution_fact")
  for g in inst_gaps:
    evidence_ok = has_refs
    steps.append(PlanStep(
      gap_id=g.gap_id,
      gap_type=g.gap_type,
      selected_tool=TOOL_FACT_FILL_INSTITUTION if evidence_ok else None,
      reason=(
        "Empty institution fact cell; FactFillTool can copy from references"
        if evidence_ok
        else "Institution fact gap but no reference documents — cannot execute FactFill"
      ),
      execution_order=order if evidence_ok else 99,
      evidence_exists=evidence_ok,
      user_review_required=True,
      will_execute=evidence_ok,
      raw_label=g.raw_label,
    ))
  if inst_gaps and has_refs:
    order += 1

  for g in gap_report.by_type("narrative_necessity"):
    steps.append(PlanStep(
      gap_id=g.gap_id,
      gap_type=g.gap_type,
      selected_tool=TOOL_NARRATIVE_DRAFT,
      reason=(
        "Incomplete research-necessity section; narrative draft tool planned "
        "(deferred — not auto-executed in MVP)"
      ),
      execution_order=order,
      evidence_exists=False,
      user_review_required=True,
      will_execute=False,
      raw_label=g.raw_label,
    ))
    order += 1

  for g in gap_report.by_type("unsupported"):
    steps.append(PlanStep(
      gap_id=g.gap_id,
      gap_type=g.gap_type,
      selected_tool=None,
      reason="No supporting internal tool for this gap type yet",
      execution_order=90,
      evidence_exists=False,
      user_review_required=True,
      will_execute=False,
      raw_label=g.raw_label,
    ))

  return DocumentPlan(
    steps=steps,
    target_document=state.target_filename,
    user_goal=user_goal,
  )
