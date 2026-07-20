"""Workflow 1: fill_institution_info — named task, not generic DocFill."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "org_evidence_fill"

REF_NAMES = {"ref_org.hwpx", "ref_org.xlsx"}
FORBIDDEN_VALUES = {
  "주소", "전화번호", "대표자", "기관명", "이메일", "전자우편", "TEL",
  "주관기관", "대표 성명",
}
FILL_LABELS = [
  "기관명", "대표자", "주소", "전화번호", "이메일",
  "주관기관", "대표 성명", "TEL", "전자우편",
]
MISSING_LABELS = ["사업자등록번호", "법인등록번호"]


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


def _ref_corpus(ref_hwpx: Path, ref_xlsx: Path) -> str:
  from hwp_core.hwpx_editor import HWPXEditor

  chunks: list[str] = []
  ed = HWPXEditor(ref_hwpx.read_bytes())
  for p in ed.get_paragraphs():
    chunks.append(p.get("text") or "")
  for ti in range(ed.get_table_count()):
    for row in ed.get_table_as_rows(ti):
      chunks.extend(str(c) for c in row)
  try:
    import openpyxl
    wb = openpyxl.load_workbook(ref_xlsx, read_only=True, data_only=True)
    for ws in wb.worksheets:
      for row in ws.iter_rows(values_only=True):
        for c in row:
          if c is not None:
            chunks.append(str(c))
    wb.close()
  except Exception:
    pass
  return "\n".join(chunks)


def test_match_workflow_institution_command():
  from hwp_core.workflows.registry import match_workflow

  assert match_workflow("기관 정보 채워줘") == "fill_institution_info"
  assert match_workflow("참고로 기관정보 넣어줘") == "fill_institution_info"
  assert match_workflow("빈칸 채워줘") is None


def test_institution_workflow_e2e(org_fixture_paths):
  from hwp_core.doc_agent.edit_proposal_service import FILL_EVIDENCE
  from hwp_core.doc_agent.pipeline import DocFillPipeline
  from hwp_core.workflows.registry import run_workflow

  target = org_fixture_paths["target"].read_bytes()
  corpus = _ref_corpus(org_fixture_paths["ref_hwpx"], org_fixture_paths["ref_xlsx"])

  pipe = DocFillPipeline()
  pipe.register_target("target_org_form.hwpx", target)
  pipe.register_reference("ref_org.hwpx", org_fixture_paths["ref_hwpx"].read_bytes())
  pipe.register_reference("ref_org.xlsx", org_fixture_paths["ref_xlsx"].read_bytes())

  result = run_workflow(
    "fill_institution_info",
    pipe,
    command="기관 정보를 참고 자료로 채워줘",
    use_llm=False,
  )

  assert result.ok, result.message
  # Compatibility path now goes through Completion Planner → FactFillTool
  assert (result.meta or {}).get("via") == "completion_planner"
  assert result.success_checks.get("completion_planner_selected_institution_tool") is True

  by_label = {
    (p.get("label") or "").strip(): p
    for p in result.proposals
    if p.get("action") == "write_table_cell"
  }

  for lab in FILL_LABELS:
    p = by_label.get(lab)
    assert p is not None, f"missing proposal for {lab}"
    after = (p.get("after") or "").strip()
    assert (p.get("meta") or {}).get("fill_mode") == FILL_EVIDENCE
    assert after in corpus
    assert after not in FORBIDDEN_VALUES
    srcs = p.get("sources") or []
    assert any((s.get("document") or "") in REF_NAMES for s in srcs)

  for lab in MISSING_LABELS:
    p = by_label.get(lab)
    if p:
      assert not (p.get("after") or "").strip(), f"{lab} should stay empty"

  assert all(p.get("action") == "write_table_cell" for p in result.proposals)


def test_institution_workflow_script(org_fixture_paths):
  script = ROOT / "scripts" / "run_workflow_institution_fill.py"
  proc = subprocess.run(
    [sys.executable, str(script)],
    cwd=str(ROOT),
    capture_output=True,
    text=True,
  )
  assert proc.returncode == 0, proc.stdout + proc.stderr
