"""표 내부 계산 Fill — 일반 숫자표."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from hwp_core.doc_agent.document_editor import apply_proposals
from hwp_core.doc_agent.edit_proposal_service import FILL_CALCULATION, build_proposals
from hwp_core.doc_agent.fixtures import make_minimal_hwpx, make_org_ref_hwpx
from hwp_core.doc_agent.pipeline import DocFillPipeline
from hwp_core.doc_agent.task_planner import plan_fill_task
from hwp_core.hwpx_editor import HWPXEditor


def _run_fill(table_rows: list[list[str]], refs: list[tuple[str, bytes]] | None = None):
  target = make_minimal_hwpx(paragraphs=["예산표"], tables=[table_rows])
  pipe = DocFillPipeline()
  pipe.register_target("target.hwpx", target)
  for name, data in refs or []:
    pipe.register_reference(name, data)
  insp = pipe.run_inspect()
  assert insp["ok"], insp
  fields = pipe.tools.last_fields or (insp.get("data") or {}).get("fields") or []
  plan = plan_fill_task("비어 있는 곳 채워줘", fields, "target.hwpx", use_llm=False)
  ws = pipe.workspace
  proposals, skipped, traces = build_proposals(
    plan["plan"], fields, ws, use_llm=False, command="비어 있는 곳 채워줘",
  )
  return proposals, skipped, traces, target, fields


def test_row_sum_with_total_header():
  """1. 합계 행 + 같은 열 숫자 → 계산 제안."""
  proposals, skipped, _, _, _ = _run_fill([
    ["구분", "금액"],
    ["연구비", "100"],
    ["인건비", "200"],
    ["합계", ""],
  ])
  calc = [p for p in proposals if (p.meta or {}).get("fill_mode") == FILL_CALCULATION]
  assert len(calc) == 1
  assert calc[0].after == "300"
  assert "100" in (calc[0].meta or {}).get("formula", "")
  assert not any(s.get("source_type") == "table_cell" and "ref" in str(s) for s in calc[0].sources)


def test_grand_total_from_subtotals():
  """2. 총계 행 + 하위 합계/소계 → 계산 제안."""
  proposals, _, _, _, _ = _run_fill([
    ["구분", "금액"],
    ["항목A", "100"],
    ["소계", "100"],
    ["항목B", "50"],
    ["합계", "50"],
    ["총계", ""],
  ])
  calc = [p for p in proposals if (p.meta or {}).get("fill_mode") == FILL_CALCULATION]
  assert len(calc) == 1
  assert calc[0].after == "150"


def test_normal_input_header_no_calc():
  """3. 일반 입력 항목 빈 칸 → 계산하지 않음 (Evidence도 파생 셀이 아니면)."""
  proposals, skipped, _, _, _ = _run_fill([
    ["항목", "금액"],
    ["연구비", ""],
    ["인건비", "200"],
  ], refs=[("ref.hwpx", make_org_ref_hwpx())])
  calc = [p for p in proposals if (p.meta or {}).get("fill_mode") == FILL_CALCULATION]
  assert not calc


def test_mixed_ratio_amount_skip():
  """4. 비율·금액 혼합 행 합계 → 계산하지 않음."""
  _, skipped, traces, _, _ = _run_fill([
    ["항목", "비율", "금액", "합계"],
    ["A", "10", "100", ""],
  ])
  assert not any(t.final_status == "proposed" and t.grounding_method == "table_calc" for t in traces)
  assert any(
    t.final_status == "skipped_no_calc" or SKIP in (t.notes or [""])[0]
    for t in traces for SKIP in ["표 내부"]
  )


def test_missing_operands_skip():
  """5. 피연산자 일부 누락 → 계산하지 않음."""
  _, skipped, _, _, _ = _run_fill([
    ["구분", "금액"],
    ["연구비", "100"],
    ["인건비", ""],
    ["합계", ""],
  ])
  assert any(s.get("reason", "").startswith("표 내부") for s in skipped)


def test_external_ref_not_in_sum_cell():
  """6. 외부 참고 문서 텍스트가 합계 셀에 들어가지 않음."""
  ref = make_org_ref_hwpx()
  proposals, _, _, _, _ = _run_fill([
    ["구분", "금액"],
    ["연구비", "100"],
    ["합계", ""],
  ], refs=[("ref.hwpx", ref)])
  sum_props = [
    p for p in proposals
    if p.action == "write_table_cell" and (p.meta or {}).get("row") == 2
  ]
  assert len(sum_props) == 1
  assert (sum_props[0].meta or {}).get("fill_mode") == FILL_CALCULATION
  assert sum_props[0].after == "100"
  for s in sum_props[0].sources or []:
    assert s.get("document") != "ref.hwpx"


def test_unapproved_original_unchanged():
  """7. 승인 전 원본 불변."""
  proposals, _, _, target, _ = _run_fill([
    ["구분", "금액"],
    ["연구비", "100"],
    ["합계", ""],
  ])
  from dataclasses import asdict
  prop_dicts = [asdict(p) for p in proposals]
  res = apply_proposals(target, prop_dicts, approved_ids=set())
  assert res["log"]["applied"] == []
  assert (Path(res["edited_path"]).parent / "original.hwpx").read_bytes() == target


def test_approved_persists_after_reopen():
  """8. 승인 후 저장·재오픈 값 유지."""
  proposals, _, _, target, _ = _run_fill([
    ["구분", "금액"],
    ["연구비", "100"],
    ["합계", ""],
  ])
  from dataclasses import asdict
  prop_dicts = [asdict(p) for p in proposals]
  ids = {p["proposal_id"] for p in prop_dicts}
  applied = apply_proposals(target, prop_dicts, approved_ids=ids)
  edited = applied["edited_bytes"]
  rows = HWPXEditor(edited).get_table_as_rows(0)
  assert rows[2][1].replace(",", "") == "100"


def test_same_unit_row_sum_in_total_column():
  """같은 단위 열만 있을 때 합계 열 → 행 합 계산."""
  proposals, _, _, _, _ = _run_fill([
    ["항목", "당해년도", "차년도", "합계"],
    ["A", "10", "20", ""],
  ])
  calc = [p for p in proposals if (p.meta or {}).get("fill_mode") == FILL_CALCULATION]
  assert len(calc) == 1
  assert calc[0].after == "30"


def test_column_total_row_sum_skip_mixed_units():
  """수량·금액 혼합 행의 합계 열 → 계산하지 않음."""
  _, skipped, traces, _, _ = _run_fill([
    ["항목", "수량", "금액", "합계"],
    ["A", "1", "10", ""],
    ["B", "2", "20", ""],
  ])
  calc = [t for t in traces if t.grounding_method == "table_calc" and t.final_status == "proposed"]
  assert not calc
  assert any(t.final_status == "skipped_no_calc" for t in traces)
