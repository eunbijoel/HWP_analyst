"""Detect typed gaps for Completion Planner (complete/fill only)."""

from __future__ import annotations

from typing import Any

from hwp_core.workflows.institution_fill import is_institution_field

from .models import DocumentState, Gap, GapReport

NECESSITY_CONCEPTS = frozenset({"rd_necessity"})


def _location_for_field(field: dict) -> str:
  if field.get("field_type") == "table_cell":
    return f"table:{field.get('table_id')}[{field.get('row')},{field.get('column')}]"
  if field.get("paragraph_id") is not None:
    return f"paragraph:{field.get('paragraph_id')}"
  return (field.get("anchor_label") or field.get("label") or "").strip()


def _risk_for_fact(label: str, has_refs: bool) -> str:
  if not has_refs:
    return "high"
  if any(k in (label or "") for k in ("사업자", "법인", "등록")):
    return "medium"
  return "low"


def detect_gaps(state: DocumentState, fields: list[dict]) -> GapReport:
  """Classify each empty field into institution_fact | narrative_necessity | unsupported."""
  gaps: list[Gap] = []
  has_refs = bool(state.reference_documents)
  seen_necessity = False

  for i, f in enumerate(fields):
    fid = f.get("field_id") or f"field_{i}"
    label = (f.get("label") or f.get("context") or "").strip()
    cid = (f.get("concept_id") or "").strip()
    loc = _location_for_field(f)
    conf = float(f.get("concept_confidence") or 0.0)

    if is_institution_field(f) and f.get("field_type") == "table_cell":
      gaps.append(Gap(
        gap_id=f"gap_inst_{fid}",
        gap_type="institution_fact",
        location=loc,
        raw_label=label,
        content_kind="factual",
        required_evidence=[
          "reference_document_table_or_paragraph",
          "literal_value_copy_only",
        ],
        confidence=max(conf, 0.85) if cid else 0.7,
        risk_level=_risk_for_fact(label, has_refs),  # type: ignore[arg-type]
        field_id=fid,
        concept_id=cid,
      ))
      continue

    if cid in NECESSITY_CONCEPTS or (
      "필요성" in label and f.get("field_type") in ("paragraph", "insert_after")
    ):
      if seen_necessity:
        # one narrative gap per section type for MVP
        continue
      seen_necessity = True
      gaps.append(Gap(
        gap_id=f"gap_nec_{fid}",
        gap_type="narrative_necessity",
        location=loc,
        raw_label=label or "연구개발 필요성",
        content_kind="narrative",
        required_evidence=[
          "optional_reference_background",
          "or_current_document_context",
        ],
        confidence=max(conf, 0.8),
        risk_level="medium",
        field_id=fid,
        concept_id=cid or "rd_necessity",
      ))
      continue

    gaps.append(Gap(
      gap_id=f"gap_unsup_{fid}",
      gap_type="unsupported",
      location=loc,
      raw_label=label or cid or "(unnamed)",
      content_kind="factual" if f.get("field_type") == "table_cell" else "narrative",
      required_evidence=["human_review"],
      confidence=conf if conf else 0.4,
      risk_level="high",
      field_id=fid,
      concept_id=cid,
      notes="No internal tool mapped for this gap type yet",
    ))

  return GapReport(gaps=gaps, target_document=state.target_filename)
