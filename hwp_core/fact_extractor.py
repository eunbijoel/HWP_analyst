"""
문서·표에서 핵심 Fact 추출 (지능화 1단계)
- 표: 행 라벨 + 숫자 셀
- 본문: 금액/수치 문장
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .concept_resolver import (
  get_concept_resolver,
  compute_grounding_stats,
  GroundingOptions,
  MIN_GROUNDING_CONFIDENCE,
)
from .table_extractor import (
    TableSummary,
    NumberInfo,
    UNIT_MULTIPLIERS,
    TOTAL_KEYWORDS,
    _normalize_number_str,
    _is_number,
)


@dataclass
class Fact:
  raw_label: str
  value: float
  unit: str = ""
  display_value: str = ""
  concept: Optional[str] = None
  concept_confidence: float = 0.0
  grounding_method: str = ""  # exact | substring | pattern | none
  normalized_label: str = ""
  source_type: str = ""  # table | paragraph
  document_id: str = ""
  table_index: int = -1
  row: int = -1
  column: str = ""
  paragraph_index: int = -1
  context: str = ""

  @property
  def value_in_won(self) -> Optional[float]:
    if self.unit in UNIT_MULTIPLIERS:
      return self.value * UNIT_MULTIPLIERS[self.unit]
    if self.unit in ("", "원"):
      return self.value
    return None

  def source_hint(self) -> str:
    if self.source_type == "table" and self.table_index >= 0:
      loc = f"표 {self.table_index + 1}"
      if self.row >= 0:
        loc += f", {self.row + 1}행"
      if self.column:
        loc += f", '{self.column}' 열"
      return loc
    if self.source_type == "paragraph" and self.paragraph_index >= 0:
      return f"본문 {self.paragraph_index + 1}문단"
    return self.document_id or "문서"


TOTAL_BUDGET_LABELS = re.compile(
  r"총\s*사업비|사업비\s*합계|총\s*예산|전체\s*예산|사업비\s*총액|총액",
  re.I,
)


def _parse_cell_number(cell: str) -> Optional[float]:
  cleaned = _normalize_number_str(str(cell))
  if not cleaned:
    return None
  try:
    return float(cleaned)
  except ValueError:
    return None


def _is_total_label(text: str) -> bool:
  t = str(text).strip().lower()
  return t in {k.lower() for k in TOTAL_KEYWORDS}


def _is_total_column(col: str) -> bool:
  c = str(col).strip().lower()
  if _is_total_label(c):
    return True
  return any(kw in c for kw in ("합계", "계", "소계", "총계"))


def _guess_label_columns(df) -> list[str]:
  cols = []
  for col in df.columns:
    values = df[col].astype(str)
    numeric_count = sum(1 for v in values if v.strip() and _is_number(v))
    if numeric_count <= len(values) * 0.4:
      cols.append(str(col))
    if len(cols) >= 2:
      break
  if not cols and len(df.columns):
    cols = [str(df.columns[0])]
  return cols


def _row_label(df, row_idx: int, label_cols: list[str]) -> str:
  parts = []
  for col in label_cols:
    if col not in df.columns:
      continue
    val = str(df.at[row_idx, col]).strip()
    if val and not _is_number(val):
      parts.append(val)
  return " / ".join(parts) if parts else f"행{int(row_idx) + 1}"


def _ground_fact_fields(
  label: str,
  context: str = "",
  *,
  grounding: Optional[GroundingOptions] = None,
) -> dict:
  """ontology resolver (+ optional LLM)로 concept + confidence 부여."""
  resolver = get_concept_resolver()
  if grounding and grounding.use_llm:
    gr = resolver.ground_with_llm(label, context, options=grounding)
  else:
    gr = resolver.ground(label, context)
  return {
    "concept": gr.concept_id,
    "concept_confidence": gr.confidence,
    "grounding_method": gr.method,
    "normalized_label": gr.normalized_text,
  }


def _label_from_money_context(context: str) -> str:
  ctx = context.strip()
  m = re.search(
    r"([\w가-힣\s]{2,30}?)\s*[\d,]+(?:\.\d+)?\s*(?:원|천원|만원|백만원|억원)",
    ctx,
  )
  if m:
    label = re.sub(r"\s+", " ", m.group(1)).strip()
    label = re.sub(r"^(은|는|이|가|의|에|으로|에서)\s*", "", label)
    if len(label) >= 2:
      return label
  return "본문 수치"


def _is_code_like_column(df, col: str) -> bool:
  """비용코드·번호 열은 Fact 추출·검증에서 제외."""
  col_s = str(col)
  if any(k in col_s for k in ("코드", "번호", "비용명", "code", "id", "ID")):
    nums = []
    for i in range(len(df)):
      v = _parse_cell_number(str(df.at[i, col_s]))
      if v is not None:
        nums.append(v)
    if nums and max(nums) < 100_000:
      return True
  return False


def extract_facts_from_tables(
  tables: list[TableSummary],
  document_id: str = "",
  *,
  grounding: Optional[GroundingOptions] = None,
) -> list[Fact]:
  facts: list[Fact] = []

  for ts in tables:
    if ts.dataframe is None or ts.dataframe.empty:
      continue

    df = ts.dataframe
    label_cols = _guess_label_columns(df)
    numeric_cols = [
      str(c) for c in df.columns
      if str(c) in ts.numeric_columns + ts.money_columns or _is_number(str(df[c].iloc[0] if len(df) else ""))
    ]
    if not numeric_cols:
      numeric_cols = [str(c) for c in df.columns if c not in label_cols]

    unit = ts.unit or ""
    multiplier = ts.unit_multiplier or 1.0

    for row_idx in range(len(df)):
      row_label = _row_label(df, row_idx, label_cols)
      for col in numeric_cols:
        if col in label_cols or _is_code_like_column(df, col):
          continue
        cell = str(df.at[row_idx, col]).strip()
        if not cell or not _is_number(cell):
          continue
        num = _parse_cell_number(cell)
        if num is None:
          continue

        raw_label = row_label
        if col not in label_cols and not _is_total_column(col):
          raw_label = f"{row_label} ({col})" if row_label else str(col)

        grounded = _ground_fact_fields(raw_label, str(col), grounding=grounding)
        facts.append(Fact(
          raw_label=raw_label,
          value=num,
          unit=unit,
          display_value=cell,
          **grounded,
          source_type="table",
          document_id=document_id or ts.document_id,
          table_index=ts.index,
          row=int(row_idx),
          column=str(col),
        ))

  return facts


def extract_facts_from_text(
  paragraphs: list[str],
  text_numbers: list[NumberInfo],
  document_id: str = "",
  *,
  grounding: Optional[GroundingOptions] = None,
) -> list[Fact]:
  facts: list[Fact] = []

  para_by_snippet: dict[str, int] = {}
  for pi, para in enumerate(paragraphs):
    para_by_snippet[para[:80]] = pi

  for ni in text_numbers:
    if ni.numeric_value is None:
      continue
    if ni.category != "money":
      continue

    label = _label_from_money_context(ni.context or ni.value)
    para_idx = -1
    for pi, para in enumerate(paragraphs):
      if ni.context and ni.context in para:
        para_idx = pi
        break

    # detect_numbers_in_text는 금액을 이미 원 단위로 환산해 둠
    unit = "원" if ni.category == "money" else (ni.unit or "")
    value = float(ni.numeric_value) if ni.numeric_value is not None else 0.0

    grounded = _ground_fact_fields(label, ni.context or "", grounding=grounding)
    facts.append(Fact(
      raw_label=label,
      value=value,
      unit=unit,
      display_value=ni.value,
      **grounded,
      source_type="paragraph",
      document_id=document_id or ni.document_id,
      paragraph_index=para_idx,
      context=(ni.context or "")[:120],
    ))

  return facts


def extract_facts(
  *,
  paragraphs: list[str],
  tables: list[TableSummary],
  text_numbers: list[NumberInfo],
  table_numbers: list[NumberInfo],
  document_id: str = "",
  grounding: Optional[GroundingOptions] = None,
) -> list[Fact]:
  """표 + 본문 Fact 통합 추출."""
  facts = extract_facts_from_tables(tables, document_id=document_id, grounding=grounding)
  facts.extend(extract_facts_from_text(
    paragraphs, text_numbers, document_id=document_id, grounding=grounding,
  ))
  return facts


def grounding_stats_for_facts(facts: list[Fact]) -> dict:
  """grounding 커버리지 요약 (리포트·디버그용)."""
  stats = compute_grounding_stats(facts)
  return {
    "total_facts": stats.total,
    "grounded_facts": stats.grounded,
    "llm_grounded_facts": stats.llm_grounded,
    "coverage_pct": stats.coverage_pct,
    "unmatched_labels": stats.unmatched_labels[:30],
    "unmatched_hints": stats.unmatched_hints[:20],
  }
