"""
표·본문 숫자 교차 확인 (지능화 1단계)
- 표 내 연도열 합 vs 합계열
- 표 합계행 vs 데이터행 합
- 본문 vs 표 (총사업비 등)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .fact_extractor import Fact, TOTAL_BUDGET_LABELS
from .table_extractor import (
  TableSummary,
  TOTAL_KEYWORDS,
  _normalize_number_str,
  _is_number,
  compute_column_sum,
)


@dataclass
class Issue:
  issue_type: str
  severity: str  # error | warning | info
  message: str
  expected: Optional[float] = None
  actual: Optional[float] = None
  difference: Optional[float] = None
  source: str = ""
  document_id: str = ""

  @property
  def is_ok(self) -> bool:
    return self.severity == "info" and self.issue_type == "match"


REL_TOL = 0.02  # 2% 허용 오차
ABS_TOL = 1.0   # 반올림·단위 오차


def _is_total_label(text: str) -> bool:
  t = str(text).strip().lower()
  return t in {k.lower() for k in TOTAL_KEYWORDS}


def _is_total_column(col: str) -> bool:
  c = str(col).strip()
  if _is_total_label(c):
    return True
  if c in ("합계", "소계", "총계", "계"):
    return True
  return "합계" in c or "소계" in c


def _parse_cell(cell: str) -> Optional[float]:
  cleaned = _normalize_number_str(str(cell))
  if not cleaned:
    return None
  try:
    return float(cleaned)
  except ValueError:
    return None


def _year_columns(df, ts: TableSummary) -> list[str]:
  cols = [str(c) for c in ts.year_columns if str(c) in df.columns]
  if cols:
    return cols
  return [
    str(c) for c in df.columns
    if re.search(r"20\d{2}", str(c)) and str(c) not in ("",)
  ]


def _values_close(a: float, b: float, rel: float = REL_TOL) -> bool:
  if a is None or b is None:
    return False
  diff = abs(a - b)
  if diff <= ABS_TOL:
    return True
  denom = max(abs(a), abs(b), 1.0)
  return diff / denom <= rel


def _format_num(n: float, unit: str = "") -> str:
  if n is None:
    return "—"
  if abs(n) >= 100_000_000:
    eok = n / 100_000_000
    if unit == "천원":
      return f"{n:,.0f}천원 ({eok:,.1f}억원)"
    return f"{n:,.0f}{unit}"
  return f"{n:,.0f}{unit}"


def check_table_row_totals(ts: TableSummary, document_id: str = "") -> list[Issue]:
  """각 행: 연도(숫자)열 합 vs 합계열 비교."""
  issues: list[Issue] = []
  if ts.dataframe is None or ts.dataframe.empty:
    return issues

  df = ts.dataframe
  year_cols = _year_columns(df, ts)
  total_cols = [str(c) for c in df.columns if _is_total_column(str(c))]
  if not year_cols or not total_cols:
    return issues

  doc = document_id or ts.document_id
  table_no = ts.index + 1
  unit = ts.unit or ""

  for row_idx in range(len(df)):
    row_vals = []
    for col in year_cols:
      v = _parse_cell(str(df.at[row_idx, col]))
      if v is not None:
        row_vals.append(v)
    if len(row_vals) < 2:
      continue

    calculated = sum(row_vals)
    for tcol in total_cols:
      reported = _parse_cell(str(df.at[row_idx, tcol]))
      if reported is None:
        continue
      if _values_close(calculated, reported):
        continue

      first_col = str(df.columns[0])
      row_name = str(df.at[row_idx, first_col]).strip() or f"{row_idx + 1}행"
      if _is_total_label(row_name):
        continue

      issues.append(Issue(
        issue_type="row_sum_mismatch",
        severity="warning",
        message=(
          f"표 {table_no} '{row_name}' 행: "
          f"연도별 합({calculated:,.0f}) ≠ '{tcol}'({reported:,.0f})"
          + (f" [단위: {unit}]" if unit else "")
        ),
        expected=calculated,
        actual=reported,
        difference=reported - calculated,
        source=f"표 {table_no}, {row_idx + 1}행, '{tcol}' 열",
        document_id=doc,
      ))

  return issues


def check_table_total_row(ts: TableSummary, document_id: str = "") -> list[Issue]:
  """합계 행 vs 데이터 행 열 합 비교."""
  issues: list[Issue] = []
  if ts.dataframe is None or not ts.has_total_row or ts.total_row_index < 0:
    return issues

  df = ts.dataframe
  doc = document_id or ts.document_id
  table_no = ts.index + 1
  unit = ts.unit or ""
  total_idx = ts.total_row_index

  check_cols = list(dict.fromkeys(ts.numeric_columns + ts.money_columns))
  for col in check_cols:
    if col not in df.columns or _is_total_column(str(col)):
      continue

    calculated = compute_column_sum(df, col, exclude_totals=True)
    reported = _parse_cell(str(df.at[total_idx, col]))
    if calculated is None or reported is None:
      continue
    if _values_close(calculated, reported):
      continue

    issues.append(Issue(
      issue_type="column_total_mismatch",
      severity="warning",
      message=(
        f"표 {table_no} '{col}' 열: "
        f"세부 합({calculated:,.0f}) ≠ 합계행({reported:,.0f})"
        + (f" [단위: {unit}]" if unit else "")
      ),
      expected=calculated,
      actual=reported,
      difference=reported - calculated,
      source=f"표 {table_no}, 합계행, '{col}' 열",
      document_id=doc,
    ))

  return issues


def _won_value(fact: Fact, table_multiplier: float = 1.0) -> Optional[float]:
  if fact.source_type == "table" and table_multiplier and table_multiplier != 1.0:
    return fact.value * table_multiplier
  won = fact.value_in_won
  if won is not None:
    return won
  return fact.value


def check_body_vs_table(
  facts: list[Fact],
  tables: list[TableSummary],
  document_id: str = "",
) -> list[Issue]:
  """본문 총사업비류 vs 표 동일 개념 비교."""
  issues: list[Issue] = []

  body_facts = [
    f for f in facts
    if f.source_type == "paragraph"
    and (f.concept == "total_budget" or TOTAL_BUDGET_LABELS.search(f.raw_label))
  ]
  table_facts = [
    f for f in facts
    if f.source_type == "table"
    and (f.concept == "total_budget" or TOTAL_BUDGET_LABELS.search(f.raw_label))
    and (_is_total_label(f.raw_label.split("(")[0].strip()) or TOTAL_BUDGET_LABELS.search(f.raw_label))
  ]

  if not body_facts or not table_facts:
    return issues

  mult_by_table = {ts.index: ts.unit_multiplier or 1.0 for ts in tables}

  for bf in body_facts:
    b_val = _won_value(bf)
    if b_val is None:
      continue
    for tf in table_facts:
      t_val = _won_value(tf, mult_by_table.get(tf.table_index, 1.0))
      if t_val is None:
        continue
      if _values_close(b_val, t_val):
        continue
      issues.append(Issue(
        issue_type="body_table_mismatch",
        severity="warning",
        message=(
          f"본문 '{bf.raw_label}'({bf.display_value}) vs "
          f"표 {tf.table_index + 1}({tf.display_value}): 금액 불일치"
        ),
        expected=b_val,
        actual=t_val,
        difference=t_val - b_val,
        source=f"본문 ↔ 표 {tf.table_index + 1}, {tf.row + 1}행",
        document_id=document_id or bf.document_id,
      ))
      break

  return issues


def check_tables(tables: list[TableSummary], document_id: str = "") -> list[Issue]:
  issues: list[Issue] = []
  for ts in tables:
    issues.extend(check_table_row_totals(ts, document_id=document_id))
    issues.extend(check_table_total_row(ts, document_id=document_id))
  return issues


def check_consistency(
  facts: list[Fact],
  tables: list[TableSummary],
  document_id: str = "",
) -> list[Issue]:
  issues = check_tables(tables, document_id=document_id)
  issues.extend(check_body_vs_table(facts, tables, document_id=document_id))
  return issues
