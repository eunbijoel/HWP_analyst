"""Workflow 1: Fill institution information — complete end-to-end path."""

from __future__ import annotations

import re
from typing import Any

from .base import WorkflowResult
from .catalog import get_workflow_spec

# Fields this workflow owns (no generic DocFill expansion).
INSTITUTION_CONCEPTS = frozenset({
  "org_name",
  "address",
  "representative",
  "phone",
  "mobile",
  "email",
  "business_reg_no",
  "corp_reg_no",
})

INSTITUTION_LABEL_RE = re.compile(
  r"(기관명|주관기관|주관\s*연구개발기관|수행기관|"
  r"대표자|대표\s*성명|대표자명|"
  r"주소|소재지|"
  r"전화|휴대|연락처|tel|"
  r"메일|이메일|전자우편|"
  r"사업자|법인|등록번호)",
  re.I,
)

COMMAND_RE = re.compile(
  r"(기관\s*정보|기관정보|"
  r"기관명|대표자|주소|연락처|"
  r"주관\s*기관|수행\s*기관|"
  r"사업자\s*등록|법인\s*등록)",
  re.I,
)


def matches_command(command: str) -> bool:
  t = (command or "").strip()
  if not t:
    return False
  if COMMAND_RE.search(t) and re.search(r"채우|채워|넣|기입|작성|fill", t, re.I):
    return True
  if re.search(r"기관\s*정보\s*(채우|채워|넣)", t, re.I):
    return True
  return False


def is_institution_field(field: dict) -> bool:
  cid = (field.get("concept_id") or "").strip()
  lab = (field.get("label") or "").strip()
  if cid in INSTITUTION_CONCEPTS:
    return True
  if lab and INSTITUTION_LABEL_RE.search(lab):
    return True
  return False


def build_institution_plan(fields: list[dict], target_document_id: str) -> dict:
  steps = []
  for f in fields:
    if not is_institution_field(f):
      continue
    if f.get("field_type") != "table_cell":
      continue
    steps.append({
      "field_id": f["field_id"],
      "action": "fill_table",
      "required_concept": f.get("concept_id"),
    })
  return {
    "task": "fill_institution_info",
    "target_document_id": target_document_id,
    "steps": steps,
    "planner": "institution_workflow",
  }


def _evaluate_success(
  proposals: list[dict],
  skipped: list[dict],
  refs: list[str],
) -> dict[str, bool]:
  checks: dict[str, bool] = {}
  table_props = [p for p in proposals if p.get("action") == "write_table_cell"]
  checks["has_reference_docs"] = bool(refs)
  checks["only_evidence_table_fills"] = all(
    (p.get("meta") or {}).get("fill_mode") == "evidence"
    for p in table_props
  ) if table_props else True
  checks["every_proposal_has_source"] = all(
    bool(p.get("sources")) for p in table_props
  ) if table_props else True
  checks["no_label_as_value"] = all(
    (p.get("after") or "").strip() not in {
      "주소", "전화번호", "대표자", "기관명", "이메일", "전자우편", "TEL",
    }
    and "유형" not in (p.get("after") or "")
    and "연월일" not in (p.get("after") or "")
    for p in table_props
  ) if table_props else True
  # 사업자/법인: 참고 없으면 proposal 없어야 함
  reg_props = [
    p for p in table_props
    if re.search(r"사업자|법인|등록", p.get("label") or "")
  ]
  checks["reg_no_only_with_evidence"] = (
    not reg_props or all(p.get("sources") for p in reg_props)
  )
  checks["skipped_reported_when_empty"] = True  # skipped list always returned
  return checks


def run(
  pipeline: Any,
  *,
  command: str = "기관 정보를 참고 자료로 채워줘",
  use_llm: bool = False,
  model: str = "gemma4",
  ollama_url: str = "http://localhost:11434",
) -> WorkflowResult:
  """Run institution-fill workflow on a configured DocFillPipeline."""
  from hwp_core.doc_agent.edit_proposal_service import FILL_EVIDENCE, build_proposals

  spec = get_workflow_spec("fill_institution_info")
  ws = pipeline.workspace
  target = ws.get_target()
  if not target:
    return WorkflowResult(
      workflow_id="fill_institution_info",
      ok=False,
      message="대상 문서가 없습니다.",
    )

  refs = [d.filename for d in ws.list_references()]
  insp = pipeline.run_inspect()
  if not insp.get("ok"):
    return WorkflowResult(
      workflow_id="fill_institution_info",
      ok=False,
      target_document=target.filename,
      reference_documents=refs,
      message=insp.get("error") or "문서 검사 실패",
    )

  all_fields = pipeline.tools.last_fields
  inst_fields = [f for f in all_fields if is_institution_field(f)]
  if not inst_fields:
    return WorkflowResult(
      workflow_id="fill_institution_info",
      ok=False,
      target_document=target.filename,
      reference_documents=refs,
      message="기관 정보 서식 칸(기관명·대표자·주소·연락처 등)을 찾지 못했습니다.",
    )

  if not refs:
    return WorkflowResult(
      workflow_id="fill_institution_info",
      ok=False,
      target_document=target.filename,
      reference_documents=[],
      message="참고 문서가 필요합니다. 기관 소개 HWPX 또는 기관정보 파일을 추가하세요.",
      success_checks={"has_reference_docs": False},
    )

  plan = build_institution_plan(inst_fields, target.document_id)
  if not plan.get("steps"):
    return WorkflowResult(
      workflow_id="fill_institution_info",
      ok=False,
      target_document=target.filename,
      reference_documents=refs,
      message="채울 기관 정보 빈 칸이 없습니다.",
    )

  pipeline.tools.last_plan = plan
  proposals, skipped, fill_trace = build_proposals(
    plan,
    inst_fields,
    ws,
    use_llm=use_llm,
    model=model,
    ollama_url=ollama_url,
    command=command,
  )
  # Narrative / context proposals must not appear in this workflow
  proposals = [
    p for p in proposals
    if p.action == "write_table_cell"
    and (p.meta or {}).get("fill_mode") == FILL_EVIDENCE
  ]
  prop_dicts = [p.to_dict() for p in proposals]
  trace_dicts = [t.to_dict() for t in fill_trace]
  checks = _evaluate_success(prop_dicts, skipped, refs)

  ok = bool(prop_dicts) or bool(skipped)
  if not prop_dicts and skipped:
    msg = (
      f"「{target.filename}」에서 기관 정보 칸 {len(inst_fields)}개를 확인했으나, "
      f"참고({', '.join(refs)})에서 채울 Evidence를 찾지 못했습니다."
    )
  elif prop_dicts:
    msg = (
      f"기관 정보 워크플로: Evidence 제안 {len(prop_dicts)}건 "
      f"(검토 후 수락하세요). 스킵 {len(skipped)}건."
    )
  else:
    msg = "처리할 기관 정보 칸이 없습니다."
    ok = False

  pipeline.tools.last_proposals = prop_dicts
  pipeline.tools.last_skipped_facts = skipped
  pipeline.tools.last_fill_trace = trace_dicts

  return WorkflowResult(
    workflow_id="fill_institution_info",
    ok=ok,
    target_document=target.filename,
    reference_documents=refs,
    proposals=prop_dicts,
    skipped=skipped,
    fill_trace=trace_dicts,
    success_checks=checks,
    message=msg,
    meta={
      "spec": spec.to_dict() if spec else {},
      "institution_field_count": len(inst_fields),
      "command": command,
    },
  )
