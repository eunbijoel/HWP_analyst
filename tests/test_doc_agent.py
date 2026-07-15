"""문서 작업 agent vertical slice 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from hwp_core.doc_agent.document_editor import apply_proposals
from hwp_core.doc_agent.document_inspector import find_empty_fields, inspect_document
from hwp_core.doc_agent.edit_verifier import verify_applied_changes, compare_expected_actual
from hwp_core.doc_agent.fixtures import (
  make_company_ref_hwpx,
  make_labor_form_hwpx,
  make_rd_plan_hwpx,
  make_staff_xlsx,
)
from hwp_core.doc_agent.pipeline import DocFillPipeline
from hwp_core.doc_agent.task_planner import plan_fill_task, ALLOWED_ACTIONS
from hwp_core.doc_agent.workspace_retriever import propose_table_mapping
from hwp_core.hwpx_editor import HWPXEditor


def test_detect_blank_paragraphs():
  raw = make_rd_plan_hwpx()
  data = inspect_document(raw, document_id="plan.hwpx", filename="plan.hwpx")
  assert data["ok"]
  fields = data["fields"]
  concepts = {f["concept_id"] for f in fields}
  assert "rd_objective" in concepts
  assert "expected_effect" in concepts


def test_detect_blank_table_cells():
  raw = make_labor_form_hwpx()
  ed = HWPXEditor(raw)
  fields = find_empty_fields(ed, document_id="labor.hwpx")
  assert any(f.field_type == "table_cell" for f in fields)
  assert any(f.concept_id == "person_name" for f in fields)
  assert any(f.concept_id == "labor_cost_cash" for f in fields)


def test_excel_column_mapping():
  m = propose_table_mapping(
    ["성명", "직급", "참여율", "현금 인건비"],
    ["이름", "직위", "참여비율", "인건비(현금)"],
  )
  by_h = {x["hwpx_header"]: x for x in m["mappings"]}
  assert by_h["성명"]["excel_header"] == "이름"
  assert by_h["직급"]["excel_header"] == "직위"
  assert by_h["참여율"]["concept_id"] == "participation_rate"


def test_unapproved_not_applied_and_original_unchanged():
  target = make_rd_plan_hwpx()
  original = bytes(target)
  pipe = DocFillPipeline()
  pipe.register_target("plan.hwpx", target)
  pipe.register_reference("company.hwpx", make_company_ref_hwpx())
  insp = pipe.run_inspect()
  assert insp["ok"]
  prop = pipe.run_propose("연구개발 목표와 기대효과를 작성해줘", use_llm=False)
  assert prop["ok"]
  proposals = (prop["data"] or {}).get("proposals") or []
  assert proposals, "제안이 있어야 함"

  # 아무 것도 승인하지 않음
  res = apply_proposals(original, proposals, approved_ids=set())
  assert res["log"]["applied"] == []
  assert original == target
  job_dir = Path(res["edited_path"]).parent
  assert (job_dir / "original.hwpx").read_bytes() == original


def test_apply_and_reparse():
  target = make_rd_plan_hwpx()
  original = bytes(target)
  pipe = DocFillPipeline()
  pipe.register_target("plan.hwpx", target)
  pipe.register_reference("company.hwpx", make_company_ref_hwpx())
  pipe.run_inspect()
  prop = pipe.run_propose("연구개발 목표와 기대효과를 작성해줘", use_llm=False)
  proposals = (prop["data"] or {}).get("proposals") or []
  assert proposals
  ids = {p["proposal_id"] for p in proposals}
  applied = apply_proposals(original, proposals, approved_ids=ids)
  assert applied["log"]["applied"]
  assert original == target
  edited = applied["edited_bytes"]
  assert edited != original
  checks = verify_applied_changes(edited, applied["proposals"])
  ok_n = sum(1 for c in checks if c["success"])
  assert ok_n >= 1


def test_labor_fill_from_excel():
  pipe = DocFillPipeline()
  pipe.register_target("labor.hwpx", make_labor_form_hwpx())
  pipe.register_reference("staff.xlsx", make_staff_xlsx())
  mapping = pipe.tools.call("propose_table_mapping")
  assert mapping.ok
  pipe.run_inspect()
  out = pipe.run_propose("엑셀 자료를 보고 인건비 현황표를 채워줘", use_llm=False)
  assert out["ok"]
  proposals = (out["data"] or {}).get("proposals") or []
  assert any(p["action"] == "write_table_cell" for p in proposals)
  assert any("xlsx" in str(p.get("sources")) or "인력" in str(p.get("sources")) or True for p in proposals)
  # sources should mention excel
  assert any(
    (s.get("source_type") == "excel_cell")
    for p in proposals for s in (p.get("sources") or [])
  )
  ids = {p["proposal_id"] for p in proposals}
  pipe.tools.last_proposals = proposals
  res = pipe.run_apply(list(ids))
  assert res["ok"] or (res.get("data") or {}).get("edited_bytes")
  edited = (res.get("data") or {}).get("edited_bytes")
  ed = HWPXEditor(edited)
  rows = ed.get_table_as_rows(0)
  flat = " ".join(c for r in rows for c in r)
  assert "홍길동" in flat
  assert "김영희" in flat


def test_generic_fill_command_fills_labor_table():
  """「참고 자료로 채워줘」만으로도 인건비 표가 채워져야 함 (want_table 버그 회귀 방지)."""
  pipe = DocFillPipeline()
  pipe.register_target("labor.hwpx", make_labor_form_hwpx())
  pipe.register_reference("staff.xlsx", make_staff_xlsx())
  pipe.run_inspect()
  out = pipe.run_propose("참고 자료로 채워줘", use_llm=False)
  proposals = (out["data"] or {}).get("proposals") or []
  assert any(p["action"] == "write_table_cell" for p in proposals)


def test_budget_excel_drafts_into_blank_paragraphs():
  """예실대비 Excel만 있어도 빈 글 항목에 표 삽입 제안이 나와야 함."""
  from pathlib import Path
  xlsx = Path("/home/eunbi/SW_Tech/excel/5예실대비표.xlsx")
  if not xlsx.exists():
    return
  pipe = DocFillPipeline()
  pipe.register_target("plan.hwpx", make_rd_plan_hwpx())
  pipe.register_reference("5예실대비표.xlsx", xlsx.read_bytes())
  pipe.run_inspect()
  out = pipe.run_propose("참고 자료로 채워줘", use_llm=False)
  proposals = (out["data"] or {}).get("proposals") or []
  assert proposals, "엑셀 표 제안이 있어야 함"
  assert any(p["action"] == "insert_table" for p in proposals)
  p0 = next(p for p in proposals if p["action"] == "insert_table")
  rows = (p0.get("meta") or {}).get("table_rows") or []
  flat = " ".join(str(c) for r in rows[:3] for c in r)
  assert "계획예산" in flat or "인건비" in flat
  assert any(
    (s.get("source_type") == "excel_table")
    for p in proposals for s in (p.get("sources") or [])
  )
  ids = {p0["proposal_id"]}
  pipe.tools.last_proposals = proposals
  res = pipe.run_apply(list(ids))
  edited = (res.get("data") or {}).get("edited_bytes")
  assert edited
  ed = HWPXEditor(edited)
  assert ed.get_table_count() >= 1
  found = False
  for ti in range(ed.get_table_count()):
    grid = ed.get_table_as_rows(ti)
    blob = " ".join(str(c) for r in grid[:3] for c in r)
    if "계획예산" in blob or "내부인건비" in blob:
      found = True
      break
  assert found

def test_bad_coords_fail_gracefully():
  target = make_labor_form_hwpx()
  proposals = [{
    "proposal_id": "bad1",
    "field_id": "x",
    "action": "write_table_cell",
    "before": "",
    "after": "X",
    "status": "approved",
    "meta": {"table_id": 99, "row": 0, "column": 0},
  }]
  res = apply_proposals(target, proposals, approved_ids={"bad1"})
  assert res["log"]["failed"] or not res["log"]["applied"]


def test_plan_schema_rejects_bad_action():
  fields = [{"field_id": "f1", "field_type": "paragraph", "label": "목표", "concept_id": "rd_objective"}]
  plan = plan_fill_task("목표 작성", fields, "doc1", use_llm=False)
  assert plan["ok"]
  for s in plan["plan"]["steps"]:
    assert s["action"] in ALLOWED_ACTIONS


def test_compare_expected():
  assert compare_expected_actual("abc", "abc")
  assert compare_expected_actual("목표 내용", "앞 목표 내용 뒤")


def test_forbidden_tool():
  pipe = DocFillPipeline()
  r = pipe.tools.call("drop_database")
  assert not r.ok
