"""Build DocumentState for Completion Planner from a DocFillPipeline."""

from __future__ import annotations

import hashlib
from typing import Any

from .models import DocumentState, EmptyFieldRef


def _working_copy_version(file_bytes: bytes | None, filename: str) -> str:
  h = hashlib.sha1((file_bytes or b"")[:65536]).hexdigest()[:12]
  return f"{filename}:{h}"


def _type_hypothesis(fields: list[dict], headings: list[str]) -> str:
  concepts = {(f.get("concept_id") or "") for f in fields}
  head_blob = " ".join(headings)
  has_org = bool(concepts & {
    "org_name", "address", "representative", "phone", "email",
    "business_reg_no", "corp_reg_no",
  }) or any("기관" in h or "대표" in h for h in headings)
  has_rd = bool(concepts & {"rd_necessity", "rd_objective", "expected_effect"}) or (
    "필요성" in head_blob or "연구개발" in head_blob
  )
  if has_org and has_rd:
    return "rd_proposal_form"
  if has_org:
    return "institution_form"
  if has_rd:
    return "rd_narrative_form"
  return "unknown_form"


def build_document_state(pipeline: Any) -> DocumentState:
  ws = pipeline.workspace
  target = ws.get_target()
  if not target:
    return DocumentState(
      target_document_id="",
      target_filename="",
      type_hypothesis="unknown_form",
      inspect_ok=False,
      inspect_error="대상 문서가 없습니다.",
    )

  insp = pipeline.run_inspect()
  fields = list(pipeline.tools.last_fields or [])
  if not insp.get("ok") and not fields:
    return DocumentState(
      target_document_id=target.document_id,
      target_filename=target.filename,
      type_hypothesis="unknown_form",
      reference_documents=[d.filename for d in ws.list_references()],
      working_copy_version=_working_copy_version(target.file_bytes, target.filename),
      inspect_ok=False,
      inspect_error=insp.get("error") or "문서 검사 실패",
    )

  paragraphs: list[str] = []
  section_headings: list[str] = []
  tables: list[dict] = []
  editor = getattr(target, "editor", None)
  if editor is None and target.filename.lower().endswith(".hwpx"):
    try:
      from hwp_core.hwpx_editor import HWPXEditor
      editor = HWPXEditor(target.file_bytes)
    except Exception:
      editor = None

  if editor is not None:
    for p in editor.get_paragraphs()[:80]:
      text = (p.get("text") or "").strip()
      if not text:
        continue
      paragraphs.append(text[:200])
      if len(text) <= 28 and not text.endswith(("다.", "요.", "음.")):
        section_headings.append(text)
    try:
      for ti in range(editor.get_table_count()):
        rows = editor.get_table_as_rows(ti)
        tables.append({
          "table_id": ti,
          "row_count": len(rows),
          "col_count": max((len(r) for r in rows), default=0),
        })
    except Exception:
      pass

  # Headings from text fields' context/anchor
  for f in fields:
    if f.get("field_type") in ("paragraph", "insert_after"):
      ctx = (f.get("context") or f.get("anchor_label") or f.get("label") or "").strip()
      if ctx and ctx not in section_headings:
        section_headings.append(ctx)

  empty_fields: list[EmptyFieldRef] = []
  incomplete_sections: list[str] = []
  for f in fields:
    loc = ""
    if f.get("field_type") == "table_cell":
      loc = f"table:{f.get('table_id')}[{f.get('row')},{f.get('column')}]"
    elif f.get("paragraph_id") is not None:
      loc = f"paragraph:{f.get('paragraph_id')}"
    else:
      loc = f.get("anchor_label") or f.get("label") or ""
    empty_fields.append(EmptyFieldRef(
      field_id=f.get("field_id") or "",
      field_type=f.get("field_type") or "",
      label=(f.get("label") or "").strip(),
      concept_id=(f.get("concept_id") or "").strip(),
      location=loc,
      current_value=(f.get("current_value") or "")[:80],
    ))
    if f.get("field_type") in ("paragraph", "insert_after"):
      title = (f.get("context") or f.get("label") or "").strip()
      if title and title not in incomplete_sections:
        incomplete_sections.append(title)

  refs = [d.filename for d in ws.list_references()]
  return DocumentState(
    target_document_id=target.document_id,
    target_filename=target.filename,
    type_hypothesis=_type_hypothesis(fields, section_headings),
    paragraphs=paragraphs,
    tables=tables,
    section_headings=section_headings,
    empty_fields=empty_fields,
    incomplete_sections=incomplete_sections,
    reference_documents=refs,
    working_copy_version=_working_copy_version(target.file_bytes, target.filename),
    inspect_ok=True,
  )
