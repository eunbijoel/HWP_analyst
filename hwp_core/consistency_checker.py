"""
표·본문 숫자 교차 확인 (지능화 1단계)
- 표 내 연도열 합 vs 합계열
- 표 합계행 vs 데이터행 합
- 본문 vs 표 (총사업비 등)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .fact_extractor import Fact, TOTAL_BUDGET_LABELS
from .concept_resolver import get_concept_resolver
from .rule_registry import (
  get_rule,
  resolve_min_confidence,
  resolve_tol,
  rule_enabled,
  stricter_tol,
)
from .table_extractor import (
  TableSummary,
  TOTAL_KEYWORDS,
  _normalize_number_str,
  _is_number,
  compute_column_sum,
  resolve_column,
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
  table_index: Optional[int] = None  # 0-based
  row_index: Optional[int] = None    # 0-based (DataFrame / 표 데이터 행)
  concept_id: Optional[str] = None
  rel_tol: Optional[float] = None
  abs_tol: Optional[float] = None
  grounding_confidence: Optional[float] = None

  @property
  def is_ok(self) -> bool:
    return self.severity == "info" and self.issue_type == "match"

  @property
  def rule_id(self) -> str:
    """YAML 규칙 id와 동일하게 쓰는 별칭 (issue_type)."""
    return self.issue_type

  def to_context_dict(self) -> dict[str, Any]:
    """QAEngine / session_state용 JSON-직렬화 가능 페이로드."""
    return {
      "rule_id": self.issue_type,
      "issue_type": self.issue_type,
      "severity": self.severity,
      "message": self.message,
      "expected": self.expected,
      "actual": self.actual,
      "difference": self.difference,
      "source": self.source,
      "document_id": self.document_id,
      "table_index": self.table_index,
      "row_index": self.row_index,
      "concept_id": self.concept_id,
      "rel_tol": self.rel_tol,
      "abs_tol": self.abs_tol,
      "grounding_confidence": self.grounding_confidence,
    }

  @classmethod
  def from_context_dict(cls, data: dict[str, Any]) -> "Issue":
    rule = data.get("rule_id") or data.get("issue_type") or "unknown"
    return cls(
      issue_type=str(rule),
      severity=str(data.get("severity") or "warning"),
      message=str(data.get("message") or ""),
      expected=data.get("expected"),
      actual=data.get("actual"),
      difference=data.get("difference"),
      source=str(data.get("source") or ""),
      document_id=str(data.get("document_id") or ""),
      table_index=data.get("table_index"),
      row_index=data.get("row_index"),
      concept_id=data.get("concept_id"),
      rel_tol=data.get("rel_tol"),
      abs_tol=data.get("abs_tol"),
      grounding_confidence=data.get("grounding_confidence"),
    )


def _as_issue_dict(item: Any) -> dict[str, Any]:
  if isinstance(item, Issue):
    return item.to_context_dict()
  if isinstance(item, dict):
    return dict(item)
  raise TypeError(f"Unsupported issue payload: {type(item)!r}")


def format_issues_for_prompt(issues: list | None) -> str:
  """Stage2용 구조화 이슈 블록. 비어 있으면 빈 문자열."""
  payloads = [_as_issue_dict(i) for i in (issues or [])]
  if not payloads:
    return ""
  lines = [
    "## 검토 엔진이 확정한 이슈 "
    "(숫자·판정은 아래만 신뢰 — 재계산·수정·새 숫자 제안 금지):",
  ]
  for i, p in enumerate(payloads, 1):
    rule = p.get("rule_id") or p.get("issue_type") or "?"
    lines.append(f"### 이슈 {i}")
    lines.append(f"- rule_id: {rule}")
    lines.append(f"- severity: {p.get('severity', '')}")
    lines.append(f"- message: {p.get('message', '')}")
    if p.get("expected") is not None:
      lines.append(f"- expected: {p['expected']}")
    if p.get("actual") is not None:
      lines.append(f"- actual: {p['actual']}")
    if p.get("difference") is not None:
      lines.append(f"- difference: {p['difference']}")
    if p.get("concept_id"):
      lines.append(f"- concept_id: {p['concept_id']}")
    if p.get("rel_tol") is not None or p.get("abs_tol") is not None:
      lines.append(f"- tol: rel={p.get('rel_tol')}, abs={p.get('abs_tol')}")
    if p.get("grounding_confidence") is not None:
      lines.append(f"- grounding_confidence: {p['grounding_confidence']}")
    if p.get("source"):
      lines.append(f"- source: {p['source']}")
    if p.get("document_id"):
      lines.append(f"- document_id: {p['document_id']}")
    if p.get("table_index") is not None:
      lines.append(f"- table_index (0-based): {p['table_index']}")
    if p.get("row_index") is not None:
      lines.append(f"- row_index (0-based): {p['row_index']}")
  return "\n".join(lines) + "\n"


def deterministic_issue_explanation(issues: list | None) -> str:
  """LLM 없이 이슈 필드만으로 설명·체크리스트 생성."""
  payloads = [_as_issue_dict(i) for i in (issues or [])]
  if not payloads:
    return ""
  parts: list[str] = []
  for i, p in enumerate(payloads, 1):
    rule = p.get("rule_id") or p.get("issue_type") or "?"
    prefix = f"이슈 {i}. " if len(payloads) > 1 else ""
    lines = [
      f"{prefix}**{p.get('message') or rule}**",
      f"- 규칙: `{rule}` · 심각도: {p.get('severity') or '-'}",
    ]
    if p.get("source"):
      lines.append(f"- 위치: {p['source']}")
    loc_bits = []
    if p.get("table_index") is not None:
      loc_bits.append(f"표 {int(p['table_index']) + 1}")
    if p.get("row_index") is not None:
      loc_bits.append(f"{int(p['row_index']) + 1}행")
    if loc_bits:
      lines.append(f"- 좌표: {', '.join(loc_bits)}")
    if p.get("expected") is not None or p.get("actual") is not None:
      lines.append(
        f"- 엔진 판정 숫자: expected={p.get('expected')}, "
        f"actual={p.get('actual')}, difference={p.get('difference')}"
      )
      tol_note = ""
      if p.get("rel_tol") is not None or p.get("abs_tol") is not None:
        tol_note = (
          f" (적용 허용오차 rel={p.get('rel_tol')}, abs={p.get('abs_tol')}"
          + (f", concept={p.get('concept_id')}" if p.get("concept_id") else "")
          + ")"
        )
      lines.append(
        "- 왜 발생했는지: 규칙이 기댓값과 표/본문 값을 비교했을 때 "
        f"허용 오차를 넘는 차이가 났습니다{tol_note}. "
        "(숫자는 위 값을 그대로 보세요.)"
      )
    else:
      lines.append(
        "- 왜 발생했는지: 규칙 조건을 만족하지 않아 플래그되었습니다. "
        "추가 숫자는 문서에서만 확인하세요."
      )
    lines.append("- 수정 시 체크리스트:")
    lines.append("  1. 위 위치의 칸·행이 맞는지 확인")
    lines.append("  2. expected/actual 중 어느 쪽이 원본과 다른지 대조")
    lines.append("  3. 단위(원/천원)와 합계 행·열 정의가 규칙과 같은지 확인")
    lines.append("  4. 고친 뒤 같은 규칙으로 다시 검토")
    parts.append("\n".join(lines))
  return "\n\n".join(parts)


# YAML 없을 때 fallback (rule_registry 로드 실패 시)
_FALLBACK_REL = 0.02
_FALLBACK_ABS = 1.0


def _tol(rule_id: str, concept_id: str | None = None) -> tuple[float, float]:
  try:
    return resolve_tol(rule_id, concept_id)
  except Exception:
    return _FALLBACK_REL, _FALLBACK_ABS


def _min_conf(rule_id: str, concept_id: str | None = None) -> float:
  try:
    return resolve_min_confidence(rule_id, concept_id)
  except Exception:
    return 0.85


def _severity(rule_id: str, key: str = "severity", default: str = "warning") -> str:
  try:
    return str(get_rule(rule_id).get(key) or default)
  except Exception:
    return default


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


def _year_columns(df, ts: TableSummary) -> list:
  cols = []
  for c in ts.year_columns:
    key = resolve_column(df, c)
    if key is not None:
      cols.append(key)
  if cols:
    return cols
  return [
    c for c in df.columns
    if re.search(r"20\d{2}", str(c)) and str(c) not in ("",)
  ]


def _values_close(
  a: float,
  b: float,
  rel: float | None = None,
  abs_tol: float | None = None,
) -> bool:
  if a is None or b is None:
    return False
  r = _FALLBACK_REL if rel is None else rel
  at = _FALLBACK_ABS if abs_tol is None else abs_tol
  diff = abs(a - b)
  if diff <= at:
    return True
  denom = max(abs(a), abs(b), 1.0)
  return diff / denom <= r


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
  if not rule_enabled("row_sum"):
    return []
  rel, abs_tol = _tol("row_sum")
  sev = _severity("row_sum")
  issues: list[Issue] = []
  if ts.dataframe is None or ts.dataframe.empty:
    return issues

  df = ts.dataframe
  year_cols = _year_columns(df, ts)
  total_cols = [c for c in df.columns if _is_total_column(str(c))]
  if not year_cols or not total_cols:
    return issues

  doc = document_id or ts.document_id
  table_no = ts.index + 1
  unit = ts.unit or ""

  for row_idx in range(len(df)):
    row_vals = []
    for col in year_cols:
      key = resolve_column(df, col)
      if key is None:
        continue
      v = _parse_cell(str(df.at[row_idx, key]))
      if v is not None:
        row_vals.append(v)
    if len(row_vals) < 2:
      continue

    calculated = sum(row_vals)
    for tcol in total_cols:
      tkey = resolve_column(df, tcol)
      if tkey is None:
        continue
      reported = _parse_cell(str(df.at[row_idx, tkey]))
      if reported is None:
        continue
      if _values_close(calculated, reported, rel=rel, abs_tol=abs_tol):
        continue

      first_key = df.columns[0]
      row_name = str(df.at[row_idx, first_key]).strip() or f"{row_idx + 1}행"
      if _is_total_label(row_name):
        continue

      tcol_s = str(tkey)
      issues.append(Issue(
        issue_type="row_sum_mismatch",
        severity=sev,
        message=(
          f"표 {table_no} '{row_name}' 행: "
          f"연도별 합({calculated:,.0f}) ≠ '{tcol_s}'({reported:,.0f})"
          + (f" [단위: {unit}]" if unit else "")
        ),
        expected=calculated,
        actual=reported,
        difference=reported - calculated,
        source=f"표 {table_no}, {row_idx + 1}행, '{tcol_s}' 열",
        document_id=doc,
        table_index=ts.index,
        row_index=row_idx,
        rel_tol=rel,
        abs_tol=abs_tol,
      ))

  return issues


def _column_concept_id(
  column_name: str,
  *,
  rule_id: str | None = None,
  min_confidence: float | None = None,
) -> Optional[str]:
  gr = get_concept_resolver().column_concept(column_name)
  if gr.concept_id is None:
    return None
  thresh = (
    min_confidence
    if min_confidence is not None
    else _min_conf(rule_id or "column_total", gr.concept_id)
  )
  if gr.confidence >= thresh:
    return gr.concept_id
  return None


def _is_budget_value_column(df, col, ts: TableSummary) -> bool:
  """합계 검증 대상 금액 열만 (비용코드 열 제외)."""
  key = resolve_column(df, col)
  if key is None or _is_total_column(str(key)):
    return False

  col_s = str(key)
  concept = _column_concept_id(col_s, rule_id="column_total")
  if concept in (
    "planned_amount", "actual_amount", "variance", "total_budget",
    "government_contribution", "private_contribution", "local_contribution",
    "item_budget", "annual_budget",
  ):
    return True

  nums: list[float] = []
  for row_idx in range(len(df)):
    if ts.has_total_row and row_idx == ts.total_row_index:
      continue
    v = _parse_cell(str(df.at[row_idx, key]))
    if v is not None:
      nums.append(v)
  if not nums:
    return False
  return max(nums) >= 10_000


def _row_name(df, row_idx: int) -> str:
  first_key = df.columns[0]
  return str(df.at[row_idx, first_key]).strip() or f"{row_idx + 1}행"


def check_table_total_row(ts: TableSummary, document_id: str = "") -> list[Issue]:
  """합계 행 vs 데이터 행 열 합 비교."""
  if not rule_enabled("column_total"):
    return []
  sev = _severity("column_total")
  issues: list[Issue] = []
  if ts.dataframe is None or not ts.has_total_row or ts.total_row_index < 0:
    return issues

  df = ts.dataframe
  doc = document_id or ts.document_id
  table_no = ts.index + 1
  unit = ts.unit or ""
  total_idx = ts.total_row_index

  check_cols = [
    c for c in df.columns
    if _is_budget_value_column(df, c, ts)
  ]
  for col in check_cols:
    key = resolve_column(df, col)
    if key is None:
      continue
    concept = _column_concept_id(str(key), rule_id="column_total")
    rel, abs_tol = _tol("column_total", concept)
    calculated = compute_column_sum(df, key, exclude_totals=True)
    reported = _parse_cell(str(df.at[total_idx, key]))
    if calculated is None or reported is None:
      continue
    if _values_close(calculated, reported, rel=rel, abs_tol=abs_tol):
      continue

    col_s = str(key)
    issues.append(Issue(
      issue_type="column_total_mismatch",
      severity=sev,
      message=(
        f"표 {table_no} '{col_s}' 열: "
        f"세부 합({calculated:,.0f}) ≠ 합계행({reported:,.0f})"
        + (f" [단위: {unit}]" if unit else "")
      ),
      expected=calculated,
      actual=reported,
      difference=reported - calculated,
      source=f"표 {table_no}, 합계행, '{col_s}' 열",
      document_id=doc,
      table_index=ts.index,
      row_index=total_idx,
      concept_id=concept,
      rel_tol=rel,
      abs_tol=abs_tol,
    ))

  return issues


def check_planned_vs_actual(ts: TableSummary, document_id: str = "") -> list[Issue]:
  """예실대비: 계획 vs 실행 열, 증감 열 검증."""
  if not rule_enabled("planned_vs_actual"):
    return []
  try:
    cfg = get_rule("planned_vs_actual")
  except KeyError:
    cfg = {}
  planned_id = str(cfg.get("planned_concept") or "planned_amount")
  actual_id = str(cfg.get("actual_concept") or "actual_amount")
  variance_id = str(cfg.get("variance_concept") or "variance")
  var_sev = str(cfg.get("variance_severity") or "warning")
  over_sev = str(cfg.get("overrun_severity") or "info")

  var_rel, var_abs = _tol("planned_vs_actual", variance_id)
  over_rel, over_abs = stricter_tol(
    _tol("planned_vs_actual", planned_id),
    _tol("planned_vs_actual", actual_id),
  )

  issues: list[Issue] = []
  if ts.dataframe is None or ts.dataframe.empty:
    return issues

  df = ts.dataframe
  doc = document_id or ts.document_id
  table_no = ts.index + 1
  unit = ts.unit or ""

  planned_cols = [
    c for c in df.columns
    if _column_concept_id(str(c), rule_id="planned_vs_actual") == planned_id
  ]
  actual_cols = [
    c for c in df.columns
    if _column_concept_id(str(c), rule_id="planned_vs_actual") == actual_id
  ]
  variance_cols = [
    c for c in df.columns
    if _column_concept_id(str(c), rule_id="planned_vs_actual") == variance_id
  ]

  if not planned_cols or not actual_cols:
    return issues

  pcol, acol = planned_cols[0], actual_cols[0]
  vcol = variance_cols[0] if variance_cols else None

  for row_idx in range(len(df)):
    if ts.has_total_row and row_idx == ts.total_row_index:
      continue
    row_name = _row_name(df, row_idx)
    if _is_total_label(row_name):
      continue

    planned = _parse_cell(str(df.at[row_idx, pcol]))
    actual = _parse_cell(str(df.at[row_idx, acol]))
    if planned is None or actual is None:
      continue

    if vcol:
      reported_var = _parse_cell(str(df.at[row_idx, vcol]))
      calc_var = actual - planned
      if reported_var is not None and not _values_close(
        reported_var, calc_var, rel=var_rel, abs_tol=var_abs,
      ):
        issues.append(Issue(
          issue_type="variance_mismatch",
          severity=var_sev,
          message=(
            f"표 {table_no} '{row_name}': "
            f"증감({reported_var:,.0f}) ≠ 실행-계획({calc_var:,.0f})"
            + (f" [단위: {unit}]" if unit else "")
          ),
          expected=calc_var,
          actual=reported_var,
          difference=reported_var - calc_var,
          source=f"표 {table_no}, {row_idx + 1}행, '{vcol}' 열",
          document_id=doc,
          table_index=ts.index,
          row_index=row_idx,
          concept_id=variance_id,
          rel_tol=var_rel,
          abs_tol=var_abs,
        ))

    if actual > planned and not _values_close(
      actual, planned, rel=over_rel, abs_tol=over_abs,
    ):
      overrun = actual - planned
      if overrun / max(abs(planned), 1.0) > over_rel:
        issues.append(Issue(
          issue_type="execution_overrun",
          severity=over_sev,
          message=(
            f"표 {table_no} '{row_name}': "
            f"실행({actual:,.0f}) > 계획({planned:,.0f}), 초과 {overrun:,.0f}"
            + (f" [단위: {unit}]" if unit else "")
          ),
          expected=planned,
          actual=actual,
          difference=overrun,
          source=f"표 {table_no}, {row_idx + 1}행, '{pcol}'↔'{acol}'",
          document_id=doc,
          table_index=ts.index,
          row_index=row_idx,
          concept_id=actual_id,
          rel_tol=over_rel,
          abs_tol=over_abs,
        ))

  return issues


def _won_value(fact: Fact, table_multiplier: float = 1.0) -> Optional[float]:
  if fact.source_type == "table" and table_multiplier and table_multiplier != 1.0:
    return fact.value * table_multiplier
  won = fact.value_in_won
  if won is not None:
    return won
  return fact.value


def _fact_has_concept(
  f: Fact,
  concept_id: str,
  *,
  rule_id: str = "body_vs_table",
) -> bool:
  """ontology grounding (confidence 필터) + total_budget 레거시 패턴."""
  thresh = _min_conf(rule_id, concept_id)
  if f.concept == concept_id:
    conf = getattr(f, "concept_confidence", 0.0) or 0.0
    if conf >= thresh:
      return True
  if concept_id == "total_budget" and TOTAL_BUDGET_LABELS.search(f.raw_label):
    return True
  ctx = f.column or f.context or ""
  gr = get_concept_resolver().ground(f.raw_label, ctx)
  return gr.concept_id == concept_id and gr.confidence >= thresh


def check_body_vs_table(
  facts: list[Fact],
  tables: list[TableSummary],
  document_id: str = "",
) -> list[Issue]:
  """본문 vs 표 동일 개념 비교 (concept는 rules YAML)."""
  if not rule_enabled("body_vs_table"):
    return []
  try:
    cfg = get_rule("body_vs_table")
  except KeyError:
    cfg = {}
  concept = str(cfg.get("concept") or "total_budget")
  sev = str(cfg.get("severity") or "warning")
  rel, abs_tol = _tol("body_vs_table", concept)
  issues: list[Issue] = []

  body_facts = [
    f for f in facts
    if f.source_type == "paragraph"
    and _fact_has_concept(f, concept, rule_id="body_vs_table")
  ]
  table_facts = [
    f for f in facts
    if f.source_type == "table"
    and _fact_has_concept(f, concept, rule_id="body_vs_table")
    and (
      concept != "total_budget"
      or _is_total_label(f.raw_label.split("(")[0].strip())
      or TOTAL_BUDGET_LABELS.search(f.raw_label)
    )
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
      if _values_close(b_val, t_val, rel=rel, abs_tol=abs_tol):
        continue
      conf = getattr(bf, "concept_confidence", None) or getattr(
        tf, "concept_confidence", None,
      )
      issues.append(Issue(
        issue_type="body_table_mismatch",
        severity=sev,
        message=(
          f"본문 '{bf.raw_label}'({bf.display_value}) vs "
          f"표 {tf.table_index + 1}({tf.display_value}): 금액 불일치"
        ),
        expected=b_val,
        actual=t_val,
        difference=t_val - b_val,
        source=f"본문 ↔ 표 {tf.table_index + 1}, {tf.row + 1}행",
        document_id=document_id or bf.document_id,
        table_index=tf.table_index,
        row_index=tf.row,
        concept_id=concept,
        rel_tol=rel,
        abs_tol=abs_tol,
        grounding_confidence=float(conf) if conf is not None else None,
      ))
      break

  return issues


def check_tables(tables: list[TableSummary], document_id: str = "") -> list[Issue]:
  issues: list[Issue] = []
  for ts in tables:
    issues.extend(check_table_row_totals(ts, document_id=document_id))
    issues.extend(check_table_total_row(ts, document_id=document_id))
    issues.extend(check_planned_vs_actual(ts, document_id=document_id))
  return issues


def check_consistency(
  facts: list[Fact],
  tables: list[TableSummary],
  document_id: str = "",
) -> list[Issue]:
  issues = check_tables(tables, document_id=document_id)
  issues.extend(check_body_vs_table(facts, tables, document_id=document_id))
  return issues
