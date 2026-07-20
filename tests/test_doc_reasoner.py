"""Completion Planner — vague complete intent selects internal fill tools."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "org_evidence_fill"


@pytest.fixture
def org_fixture_paths():
  paths = {
    "target": FIXTURE / "target_org_form.hwpx",
    "ref_hwpx": FIXTURE / "ref_org.hwpx",
    "ref_xlsx": FIXTURE / "ref_org.xlsx",
  }
  missing = [p for p in paths.values() if not p.exists()]
  if missing:
    pytest.skip(f"missing fixtures: {missing}")
  return paths


def test_complete_intent_detection():
  from hwp_core.doc_reasoner import is_complete_intent

  assert is_complete_intent("이 문서 완성해줘") is True
  assert is_complete_intent("문서 좀 완성해주세요") is True
  assert is_complete_intent("기관 정보 채워줘") is False


def test_chat_route_complete_goes_to_completion_planner():
  import sys
  v2 = str(ROOT / "HWP_v2")
  if v2 not in sys.path:
    sys.path.insert(0, v2)
  from chat_route import decide_chat_route

  decision = decide_chat_route(
    message="이 문서 완성해줘",
    has_selection=False,
    has_editor=True,
    has_docs=True,
  )
  assert decision.action == "complete_plan"


def test_chat_route_institution_phrase_also_completion_planner():
  import sys
  v2 = str(ROOT / "HWP_v2")
  if v2 not in sys.path:
    sys.path.insert(0, v2)
  from chat_route import decide_chat_route

  decision = decide_chat_route(
    message="기관 정보 채워줘",
    has_selection=False,
    has_editor=True,
    has_docs=True,
  )
  assert decision.action == "complete_plan"


def test_completion_planner_selects_institution_tool(org_fixture_paths):
  from hwp_core.doc_agent.pipeline import DocFillPipeline
  from hwp_core.doc_reasoner import run_completion_planner
  from hwp_core.doc_reasoner.planner import TOOL_FACT_FILL_INSTITUTION

  pipe = DocFillPipeline()
  pipe.register_target(
    "target_org_form.hwpx", org_fixture_paths["target"].read_bytes(),
  )
  pipe.register_reference(
    "ref_org.hwpx", org_fixture_paths["ref_hwpx"].read_bytes(),
  )
  pipe.register_reference(
    "ref_org.xlsx", org_fixture_paths["ref_xlsx"].read_bytes(),
  )

  result = run_completion_planner(pipe, command="이 문서 완성해줘", use_llm=False)

  assert result.ok
  assert result.state is not None
  assert result.gap_report is not None
  assert result.plan is not None

  inst_gaps = result.gap_report.by_type("institution_fact")
  assert len(inst_gaps) >= 1

  assert TOOL_FACT_FILL_INSTITUTION in result.tools_run
  assert any(
    s.selected_tool == TOOL_FACT_FILL_INSTITUTION and s.will_execute
    for s in result.plan.steps
  )
  assert len(result.proposals) >= 1
  assert all(p.get("action") == "write_table_cell" for p in result.proposals)
  assert "문서를 살펴" in result.summary
  assert "기관정보" in result.summary


def test_run_reasoner_compat_alias(org_fixture_paths):
  from hwp_core.doc_agent.pipeline import DocFillPipeline
  from hwp_core.doc_reasoner import run_reasoner

  pipe = DocFillPipeline()
  pipe.register_target(
    "target_org_form.hwpx", org_fixture_paths["target"].read_bytes(),
  )
  pipe.register_reference(
    "ref_org.hwpx", org_fixture_paths["ref_hwpx"].read_bytes(),
  )

  result = run_reasoner(pipe, command="이 문서 완성해줘", use_llm=False)
  assert result.ok
  assert len(result.proposals) >= 1


def test_completion_planner_structures_shape(org_fixture_paths):
  from hwp_core.doc_agent.pipeline import DocFillPipeline
  from hwp_core.doc_reasoner import run_completion_planner

  pipe = DocFillPipeline()
  pipe.register_target(
    "target_org_form.hwpx", org_fixture_paths["target"].read_bytes(),
  )
  pipe.register_reference(
    "ref_org.hwpx", org_fixture_paths["ref_hwpx"].read_bytes(),
  )

  result = run_completion_planner(pipe, command="이 문서 완성해줘", use_llm=False)
  state = result.state.to_dict()
  assert "target_document_id" in state
  assert "type_hypothesis" in state
  assert "empty_fields" in state
  assert "reference_documents" in state
  assert "working_copy_version" in state

  for g in result.gap_report.gaps:
    d = g.to_dict()
    for key in (
      "gap_id", "gap_type", "location", "raw_label",
      "content_kind", "required_evidence", "confidence", "risk_level",
    ):
      assert key in d

  for s in result.plan.steps:
    d = s.to_dict()
    for key in (
      "gap_id", "selected_tool", "reason", "execution_order",
      "evidence_exists", "user_review_required",
    ):
      assert key in d
