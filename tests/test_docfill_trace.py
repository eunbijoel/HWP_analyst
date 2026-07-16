"""DocFill trace + dual test types (A expected values, B provenance/safety)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from hwp_core.doc_agent.document_inspector import get_fill_resolver, find_form_label_blanks
from hwp_core.doc_agent.edit_proposal_service import FILL_EVIDENCE
from hwp_core.doc_agent.fixtures import make_minimal_hwpx, make_org_form_target_hwpx, make_org_ref_hwpx
from hwp_core.doc_agent.pipeline import DocFillPipeline
from hwp_core.doc_agent.workspace_retriever import value_rejection_reason
from hwp_core.hwpx_editor import HWPXEditor


# --- A. Deterministic expected-value regression (keep) ---

def test_A_org_evidence_expected_values():
  get_fill_resolver.cache_clear()
  pipe = DocFillPipeline()
  pipe.register_target("form.hwpx", make_org_form_target_hwpx())
  pipe.register_reference("org.hwpx", make_org_ref_hwpx())
  pipe.run_inspect()
  out = pipe.run_propose("빈칸을 참고 자료로 채워줘", use_llm=False)
  by_label = {
    p.get("label"): p
    for p in ((out["data"] or {}).get("proposals") or [])
    if p.get("action") == "write_table_cell"
  }
  assert by_label["기관명"]["after"] == "한국생산기술연구원"
  assert "이상목" in by_label["대표자명"]["after"]
  assert "천안" in by_label["주소"]["after"]
  assert "@" in (by_label.get("전자우편") or next(
    p for p in by_label.values() if "우편" in (p.get("label") or "") or "메일" in (p.get("label") or "")
  ))["after"]


def test_A_reject_known_unsafe_literals():
  assert value_rejection_reason("pi_name", "성명", "연구개발기간")
  assert value_rejection_reason("pi_name", "성명", "설립 연월일")
  assert value_rejection_reason("form_blank", "공고번호", "선정방식")
  assert value_rejection_reason("position", "직위", "주관")
  assert not value_rejection_reason("pi_name", "성명", "홍길동")


# --- B. Generalization: provenance / safety / no fabrication ---

def test_B_skips_past_rejected_to_next_valid_candidate():
  from hwp_core.doc_agent.value_type_validation import pick_accepted_candidate

  raw = [
    {"value": "연구개발기간", "source_document": "ref.hwpx", "source_location": "표1 행21열1", "source_type": "table_cell"},
    {"value": "홍길동", "source_document": "ref.hwpx", "source_location": "표1 행5열2", "source_type": "table_cell"},
  ]
  val, _, accepted, ranked = pick_accepted_candidate(raw, concept_id="pi_name", label="성명")
  assert val == "홍길동"
  assert accepted.get("rank") == 2
  assert ranked[0].get("accepted") is False
  assert ranked[1].get("accepted") is True


def test_B_fill_trace_schema_and_statuses():
  get_fill_resolver.cache_clear()
  pipe = DocFillPipeline()
  pipe.register_target("form.hwpx", make_org_form_target_hwpx())
  pipe.register_reference("org.hwpx", make_org_ref_hwpx())
  pipe.run_inspect()
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  traces = (out["data"] or {}).get("fill_trace") or []
  assert traces, "every form field attempt should emit a trace"
  required = {
    "target_document", "location", "raw_label", "concept_id",
    "grounding_confidence", "grounding_method", "candidates",
    "candidate_ranking", "rejected_candidates", "accepted_candidate",
    "expected_value_type", "final_proposal",
    "selected_value", "final_status",
  }
  for t in traces:
    assert required <= set(t.keys())
    assert t["final_status"] in {
      "proposed", "skipped_no_evidence", "skipped_unsafe", "needs_review",
    }
    for c in t.get("candidate_ranking") or t.get("candidates") or []:
      assert "rank" in c and "value" in c and "source_location" in c
      assert "accepted" in c and "rejection_reason" in c
      assert "expected_value_type" in c


def test_B_proposed_facts_have_provenance_no_label_values():
  get_fill_resolver.cache_clear()
  pipe = DocFillPipeline()
  pipe.register_target("form.hwpx", make_org_form_target_hwpx())
  pipe.register_reference("org.hwpx", make_org_ref_hwpx())
  pipe.run_inspect()
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  data = out["data"] or {}
  for p in data.get("proposals") or []:
    if p.get("action") != "write_table_cell":
      continue
    assert (p.get("meta") or {}).get("fill_mode") == FILL_EVIDENCE
    assert p.get("sources"), "no fabrication without source"
    after = (p.get("after") or "").strip()
    assert after not in {"주소", "전화번호", "대표자", "성명", "직위", "기관명"}
    assert "연월일" not in after and "유형" not in after


def test_B_no_evidence_does_not_fabricate():
  get_fill_resolver.cache_clear()
  pipe = DocFillPipeline()
  pipe.register_target("form.hwpx", make_org_form_target_hwpx())
  pipe.run_inspect()
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  props = [
    p for p in ((out["data"] or {}).get("proposals") or [])
    if p.get("action") == "write_table_cell"
  ]
  assert not props
  traces = (out["data"] or {}).get("fill_trace") or []
  assert traces
  assert all(t.get("final_status") == "skipped_no_evidence" for t in traces)


def test_B_kmx_researcher_number_not_pi_name():
  kmx = ROOT / "data" / "kmx_task" / "KMX empty.hwpx"
  if not kmx.exists():
    return
  get_fill_resolver.cache_clear()
  fields = find_form_label_blanks(HWPXEditor(kmx.read_bytes()))
  bad = [f for f in fields if f.label == "국가연구자번호" and f.concept_id == "pi_name"]
  assert not bad, f"misclassified: {[(f.label, f.concept_id) for f in bad]}"


def test_B_kmx_self_ref_does_not_propose_section_headers():
  kmx = ROOT / "data" / "kmx_task" / "KMX empty.hwpx"
  if not kmx.exists():
    return
  get_fill_resolver.cache_clear()
  raw = kmx.read_bytes()
  pipe = DocFillPipeline()
  pipe.register_target("KMX empty.hwpx", raw)
  pipe.register_reference("KMX empty.hwpx", raw)
  pipe.run_inspect()
  out = pipe.run_propose("빈칸 채워줘", use_llm=False)
  bad_vals = {"연구개발기간", "선정방식", "품목지정", "주관", "설립 연월일"}
  for p in (out["data"] or {}).get("proposals") or []:
    if p.get("action") != "write_table_cell":
      continue
    assert (p.get("after") or "").strip() not in bad_vals
