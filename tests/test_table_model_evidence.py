"""구조적 Evidence 정합 — 타입/행·열 의미, 그룹 라벨 상속."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
  sys.path.insert(0, str(ROOT))

from hwp_core.doc_agent.document_inspector import find_empty_fields
from hwp_core.doc_agent.edit_proposal_service import FILL_EVIDENCE, FILL_CALCULATION, build_proposals
from hwp_core.doc_agent.fixtures import make_minimal_hwpx
from hwp_core.doc_agent.pipeline import DocFillPipeline
from hwp_core.doc_agent.table_model import (
  SEM_NUMERIC,
  SEM_TEXT,
  build_table_model,
  value_matches_column_type,
)
from hwp_core.doc_agent.task_planner import plan_fill_task
from hwp_core.hwpx_editor import HWPXEditor

FULL_ROWS = [
  ["비목분류", "비용명", "계획예산", "실행예산", "합계", "당년도집행", "집행계", "예산잔액"],
  ["내부인건비", "내부인건비", "120000", "30000", "150000", "20000", "20000", "130000"],
  ["", "계약직", "80000", "20000", "100000", "10000", "10000", "90000"],
  ["소 계", "", "200000", "50000", "250000", "30000", "30000", "220000"],
  ["연구활동비", "국내여비", "20000", "10000", "30000", "5000", "5000", "25000"],
  ["", "세미나비", "30000", "20000", "50000", "10000", "10000", "40000"],
  ["소 계", "", "50000", "30000", "80000", "15000", "15000", "65000"],
  ["합 계", "", "250000", "80000", "330000", "45000", "45000", "285000"],
]

# Empty: leaf numbers kept; derived cols blank; group label blanks under 비목분류
EMPTY_ROWS = [
  ["비목분류", "비용명", "계획예산", "실행예산", "합계", "당년도집행", "집행계", "예산잔액"],
  ["내부인건비", "내부인건비", "120000", "30000", "", "20000", "", ""],
  ["", "계약직", "80000", "20000", "", "10000", "", ""],
  ["소 계", "", "", "", "", "", "", ""],
  ["연구활동비", "국내여비", "20000", "10000", "", "5000", "", ""],
  ["", "세미나비", "30000", "20000", "", "10000", "", ""],
  ["소 계", "", "", "", "", "", "", ""],
  ["합 계", "", "", "", "", "", "", ""],
]


def test_column_semantic_types():
  ed = HWPXEditor(make_minimal_hwpx(tables=[FULL_ROWS]))
  model = build_table_model(ed, 0)
  assert model.columns[0].semantic_type == SEM_TEXT
  assert model.columns[1].semantic_type == SEM_TEXT
  assert model.columns[2].semantic_type == SEM_NUMERIC
  assert model.columns[4].semantic_type == SEM_NUMERIC


def test_inherited_group_blank_not_fill_candidate():
  ed = HWPXEditor(make_minimal_hwpx(tables=[EMPTY_ROWS]))
  model = build_table_model(ed, 0)
  # 계약직 행(2), 비목분류(0)
  cell = model.cell_at(2, 0)
  assert cell is not None
  assert cell.is_inherited_group_blank
  assert cell.inherited_group_label == "내부인건비"

  fields = find_empty_fields(ed, "empty.hwpx")
  coords = {(f.row, f.column) for f in fields if f.field_type == "table_cell"}
  assert (2, 0) not in coords


def test_type_mismatch_rejects_numeric_into_text_col():
  assert not value_matches_column_type("120,000", SEM_TEXT)
  assert value_matches_column_type("120,000", SEM_NUMERIC)
  assert value_matches_column_type("내부인건비", SEM_TEXT)
  assert not value_matches_column_type("내부인건비", SEM_NUMERIC)


def test_fill_does_not_propose_number_into_category_blank():
  """Full=target with 계약직 비목분류 blank; Empty=ref with 120000 — must not propose number."""
  # Target like Full but 계약직 비목분류 empty (as in real Full_test)
  target_rows = [list(r) for r in FULL_ROWS]
  target_rows[2][0] = ""
  target = make_minimal_hwpx(paragraphs=["예실대비"], tables=[target_rows])
  # Ref: Empty still has 내부인건비/120000 in plan col — classic wrong Evidence source
  ref = make_minimal_hwpx(paragraphs=["empty"], tables=[EMPTY_ROWS])

  pipe = DocFillPipeline()
  pipe.register_target("Full_test.hwpx", target)
  pipe.register_reference("Empty_test.hwpx", ref)
  pipe.run_inspect()
  fields = pipe.tools.last_fields
  plan = plan_fill_task("빈칸 채워줘", fields, "Full_test.hwpx", use_llm=False)
  proposals, skipped, traces = build_proposals(
    plan["plan"], fields, pipe.workspace, use_llm=False, command="빈칸 채워줘",
  )

  # No proposal writing a number into (2,0) 비목분류
  bad = [
    p for p in proposals
    if p.action == "write_table_cell"
    and (p.meta or {}).get("row") == 2
    and (p.meta or {}).get("column") == 0
  ]
  assert not bad, f"unexpected proposals for category blank: {[(p.after, p.meta) for p in bad]}"

  # If any evidence proposal exists, numeric must not go into text columns
  from hwp_core.hwpx_editor import _parse_number
  for p in proposals:
    if (p.meta or {}).get("fill_mode") != FILL_EVIDENCE:
      continue
    inferred = (p.meta or {}).get("inferred_type")
    if inferred == SEM_TEXT:
      assert _parse_number(p.after) is None, p.after


def test_calc_tried_before_evidence_for_sum_cells():
  target = make_minimal_hwpx(tables=[EMPTY_ROWS])
  ref = make_minimal_hwpx(tables=[FULL_ROWS])
  pipe = DocFillPipeline()
  pipe.register_target("Empty_test.hwpx", target)
  pipe.register_reference("Full_test.hwpx", ref)
  pipe.run_inspect()
  fields = pipe.tools.last_fields
  plan = plan_fill_task("빈칸 채워줘", fields, "Empty_test.hwpx", use_llm=False)
  proposals, skipped, traces = build_proposals(
    plan["plan"], fields, pipe.workspace, use_llm=False, command="빈칸 채워줘",
  )
  # 합계 열 빈칸은 calc 또는 skip_no_calc — Evidence로 숫자 행 복사 금지
  for t in traces:
    loc = t.location or {}
    if loc.get("column") == 4:  # 합계
      assert t.grounding_method in ("table_calc", "table_model", "table_align", "none")
      if t.final_status == "proposed":
        assert t.grounding_method == "table_calc"
