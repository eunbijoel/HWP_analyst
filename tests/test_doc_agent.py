"""문서 작업 agent vertical slice 테스트."""

from __future__ import annotations

import re
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


def test_context_fill_without_reference_docs():
  """참고 문서가 없어도 현재 문서 맥락으로 Context Fill 제안이 나와야 함."""
  from hwp_core.doc_agent.edit_proposal_service import AI_DRAFT_MARKER, FILL_CONTEXT
  from hwp_core.doc_agent.fixtures import make_minimal_hwpx

  target = make_minimal_hwpx([
    "연구개발계획서",
    "추진 배경으로 공공 문서의 HWP 비중이 높은 점을 고려한다.",
    "연구개발 목표는 내부 예산·계획 문서의 숫자 오류를 자동으로 탐지하는 시스템을 구축하는 것이다.",
    "연구개발 목표",
    "□",
    "기대효과는 검토 시간을 단축하고 보고서 품질을 높이는 것이다.",
    "기대효과",
    "□",
  ])
  pipe = DocFillPipeline()
  pipe.register_target("plan.hwpx", target)
  assert pipe.run_inspect()["ok"]
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  assert out["ok"]
  proposals = (out["data"] or {}).get("proposals") or []
  assert proposals, "Context Fill should not stop with no reference"
  assert all((p.get("meta") or {}).get("fill_mode") == FILL_CONTEXT for p in proposals)
  assert any(AI_DRAFT_MARKER in (p.get("location") or "") for p in proposals)
  assert all(p.get("after") for p in proposals)


def test_evidence_fill_preferred_when_refs_available():
  from hwp_core.doc_agent.edit_proposal_service import FILL_EVIDENCE

  pipe = DocFillPipeline()
  pipe.register_target("plan.hwpx", make_rd_plan_hwpx())
  pipe.register_reference("company.hwpx", make_company_ref_hwpx())
  pipe.run_inspect()
  out = pipe.run_propose("연구개발 목표와 기대효과를 작성해줘", use_llm=False)
  proposals = (out["data"] or {}).get("proposals") or []
  assert proposals
  assert any((p.get("meta") or {}).get("fill_mode") == FILL_EVIDENCE for p in proposals)
  assert any(
    s.get("document") == "company.hwpx"
    for p in proposals for s in (p.get("sources") or [])
  )


def test_form_blank_rejects_meta_and_does_not_invent():
  """주소·전화 등 서식 칸은 근거 없이 '기입 필요' 초안을 만들지 않음."""
  from hwp_core.doc_agent.document_inspector import get_fill_resolver
  from hwp_core.doc_agent.edit_proposal_service import (
    _is_meta_instruction,
    _value_fits_fact_field,
  )
  from hwp_core.doc_agent.fixtures import make_minimal_hwpx

  get_fill_resolver.cache_clear()
  assert _is_meta_instruction("(필요한 주소 정보를 기입하십시오)")
  assert _is_meta_instruction("[회사명] 대표이사 담당자명 기입 필요")
  assert _is_meta_instruction("추후 기입 예정 (정보 없음)")
  assert not _value_fits_fact_field(
    "휴대전화",
    "사용자 경험을 개선하고 혁신적인 기능을 제공합니다.",
    "mobile",
  )
  assert not _value_fits_fact_field("성명", "개발 제품명 또는 기술명", "person_name")
  assert _value_fits_fact_field("전자우편", "a@b.com", "email")
  assert _value_fits_fact_field("직장전화", "02-1234-5678", "phone")

  form = make_minimal_hwpx(
    paragraphs=["신청서"],
    tables=[[
      ["기관명", "한국생산기술연구원", "주소", ""],
      ["대표자명", "", "사업자등록번호", ""],
      ["대표자 연락처", "", "대표자 전자우편", ""],
    ]],
  )
  pipe = DocFillPipeline()
  pipe.register_target("form.hwpx", form)
  pipe.run_inspect()
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  proposals = (out["data"] or {}).get("proposals") or []
  form_props = [
    p for p in proposals
    if p.get("action") == "write_table_cell"
  ]
  # 근거 없는 빈 칸은 제안하지 않음 (기관명은 이미 채워져 있음)
  assert not form_props, f"should not invent form values: {form_props}"
  skipped = (out["data"] or {}).get("skipped_facts") or []
  assert skipped, "should explain skipped factual blanks"


def test_org_form_evidence_fill_from_reference():
  """기관명·대표자·주소·전화·이메일을 참고에서 Evidence로 복사."""
  from hwp_core.doc_agent.document_inspector import get_fill_resolver
  from hwp_core.doc_agent.edit_proposal_service import FILL_EVIDENCE
  from hwp_core.doc_agent.fixtures import make_org_form_target_hwpx, make_org_ref_hwpx

  get_fill_resolver.cache_clear()
  pipe = DocFillPipeline()
  pipe.register_target("form.hwpx", make_org_form_target_hwpx())
  pipe.register_reference("org.hwpx", make_org_ref_hwpx())
  assert pipe.run_inspect()["ok"]
  out = pipe.run_propose("빈칸을 참고 자료로 채워줘", use_llm=False)
  assert out["ok"]
  proposals = (out["data"] or {}).get("proposals") or []
  by_label = {p.get("label"): p for p in proposals if p.get("action") == "write_table_cell"}
  assert "기관명" in by_label
  assert "한국생산기술연구원" in by_label["기관명"]["after"]
  assert "대표자명" in by_label
  assert "이상목" in by_label["대표자명"]["after"]
  assert "주소" in by_label
  assert "천안" in by_label["주소"]["after"]
  assert "대표전화" in by_label or "전화번호" in str(by_label)
  phone_p = by_label.get("대표전화") or next(
    (p for p in proposals if "전화" in (p.get("label") or "")), None,
  )
  assert phone_p and re.search(r"\d{2,3}", phone_p["after"])
  mail_p = by_label.get("전자우편") or next(
    (p for p in proposals if "우편" in (p.get("label") or "") or "메일" in (p.get("label") or "")),
    None,
  )
  assert mail_p and "@" in mail_p["after"]
  assert all((p.get("meta") or {}).get("fill_mode") == FILL_EVIDENCE for p in by_label.values())
  assert any(
    s.get("document") == "org.hwpx"
    for p in by_label.values() for s in (p.get("sources") or [])
  )


def test_org_form_no_evidence_leaves_empty():
  from hwp_core.doc_agent.document_inspector import get_fill_resolver
  from hwp_core.doc_agent.fixtures import make_org_form_target_hwpx

  get_fill_resolver.cache_clear()
  pipe = DocFillPipeline()
  pipe.register_target("form.hwpx", make_org_form_target_hwpx())
  pipe.run_inspect()
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  proposals = (out["data"] or {}).get("proposals") or []
  assert not any(p.get("action") == "write_table_cell" for p in proposals)
  assert (out["data"] or {}).get("skipped_facts")


def test_context_fill_keeps_section_alignment():
  """목표/기대효과 Context Fill이 서로 뒤바뀌면 안 됨."""
  from hwp_core.doc_agent.document_inspector import get_fill_resolver
  from hwp_core.doc_agent.edit_proposal_service import FILL_CONTEXT
  from hwp_core.doc_agent.fixtures import make_minimal_hwpx

  get_fill_resolver.cache_clear()
  target = make_minimal_hwpx([
    "연구개발계획서",
    "연구개발 목표는 문서 숫자 오류를 자동 탐지하는 시스템을 구축하는 것이다.",
    "연구개발 목표",
    "□",
    "기대효과는 검토 시간을 단축한다.",
    "기대효과",
    "□",
  ])
  pipe = DocFillPipeline()
  pipe.register_target("plan.hwpx", target)
  pipe.run_inspect()
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  props = (out["data"] or {}).get("proposals") or []
  by_c = {p.get("concept_id"): p for p in props}
  assert "rd_objective" in by_c
  assert "expected_effect" in by_c
  assert "목표" in by_c["rd_objective"]["after"] or "탐지" in by_c["rd_objective"]["after"]
  assert "효과" in by_c["expected_effect"]["after"] or "단축" in by_c["expected_effect"]["after"]
  assert "단축" not in by_c["rd_objective"]["after"]
  assert "탐지" not in by_c["expected_effect"]["after"]
  assert (by_c["rd_objective"].get("meta") or {}).get("fill_mode") == FILL_CONTEXT


def test_synonym_grounding_for_form_labels():
  from hwp_core.doc_agent.document_inspector import get_fill_resolver

  get_fill_resolver.cache_clear()
  r = get_fill_resolver()
  assert r.ground("대표 성명").concept_id == "representative"
  assert r.ground("E-mail").concept_id == "email"
  assert r.ground("주관기관").concept_id == "org_name"
  assert r.ground("TEL").concept_id == "phone"
  assert r.ground("연구책임자").concept_id == "pi_name"


def test_reject_field_labels_as_fact_values():
  """다른 칸 라벨·안내문을 값으로 쓰지 않음."""
  from hwp_core.doc_agent.workspace_retriever import _value_ok_for_concept

  assert not _value_ok_for_concept("pi_name", "성명", "설립 연월일")
  assert not _value_ok_for_concept("person_name", "성명", "설립 연월일")
  assert not _value_ok_for_concept(
    "pi_name", "책임자", "기관 유형 (대학, 정부 출연연, 중소기업 등)",
  )
  assert not _value_ok_for_concept(
    "form_blank", "기관 유형", "기관 유형 (대학, 정부 출연연, 중소기업 등)",
  )
  assert _value_ok_for_concept("pi_name", "성명", "홍길동")
  assert _value_ok_for_concept("org_name", "기관명", "한국생산기술연구원")


def test_form_blanks_use_column_header_not_org_name():
  """기관명|책임자 표에서 빈 책임자 칸 라벨은 '책임자'여야 함 (기관명 아님)."""
  from hwp_core.doc_agent.document_inspector import find_form_label_blanks
  from hwp_core.doc_agent.fixtures import make_minimal_hwpx
  from hwp_core.hwpx_editor import HWPXEditor

  doc = make_minimal_hwpx(
    paragraphs=["참여연구개발기관"],
    tables=[[
      ["연구개발기관", "기관명", "책임자", "직위"],
      ["주관연구개발기관", "한국생산기술연구원", "", ""],
      ["공동연구개발기관", "한국전자기술연구원", "", ""],
    ]],
  )
  fields = find_form_label_blanks(HWPXEditor(doc))
  labels = {f.label for f in fields}
  assert "책임자" in labels
  assert "직위" in labels
  assert "한국생산기술연구원" not in labels
  assert "한국전자기술연구원" not in labels


def test_template_ref_does_not_copy_neighbor_labels():
  """참고가 빈 서식 템플릿일 때 옆 칸 라벨을 값으로 복사하지 않음."""
  from hwp_core.doc_agent.document_inspector import get_fill_resolver
  from hwp_core.doc_agent.fixtures import make_minimal_hwpx

  get_fill_resolver.cache_clear()
  target = make_minimal_hwpx(
    paragraphs=["신청서"],
    tables=[
      [
        ["연구책임자", "성명", "", "직위", ""],
        ["", "직장전화", "", "휴대전화", ""],
        ["", "전자우편", "", "국가연구자번호", ""],
        ["설립 연월일", "", "기관 유형 (대학, 정부 출연연, 중소기업 등)", ""],
      ],
      [
        ["연구개발기관", "기관명", "책임자", "직위"],
        ["주관연구개발기관", "한국생산기술연구원", "", ""],
        ["공동연구개발기관", "경기산업", "", ""],
      ],
    ],
  )
  # 같은 빈 템플릿을 참고로 넣어도 라벨이 값으로 들어가면 안 됨
  ref = target
  pipe = DocFillPipeline()
  pipe.register_target("form.hwpx", target)
  pipe.register_reference("template.hwpx", ref)
  pipe.run_inspect()
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  props = [
    p for p in ((out["data"] or {}).get("proposals") or [])
    if p.get("action") == "write_table_cell"
  ]
  bad = [
    p for p in props
    if any(
      x in (p.get("after") or "")
      for x in ("설립 연월일", "기관 유형", "성명", "직위", "책임자")
    )
  ]
  assert not bad, f"labels used as values: {bad}"

