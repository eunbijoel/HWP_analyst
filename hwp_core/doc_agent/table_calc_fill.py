"""표 내부 구조만으로 파생값(합계·총계 등) 계산 — Evidence Fill 전에 시도."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..hwpx_editor import HWPXEditor, _format_number, _parse_number
from ..table_extractor import ParsedTableGrid, parse_table_grid

DERIVED_LABELS = frozenset({"합계", "총계", "소계", "계"})
SKIP_REASON = "표 내부 관계만으로 계산식을 확정할 수 없음"

_RATIO_RE = re.compile(r"비율|참여율|율|%")
_QTY_RE = re.compile(r"수량|개수|건수|인원|명")
_AMOUNT_RE = re.compile(r"금액|비용|원|가격|예산|단가")


@dataclass
class CalcOperand:
  row: int
  col: int
  value: float
  display: str
  header: str

  def to_dict(self) -> dict:
    return {
      "row": self.row,
      "col": self.col,
      "value": self.value,
      "display": self.display,
      "header": self.header,
      "location": f"({self.row},{self.col})",
    }


@dataclass
class TableCalcResult:
  ok: bool
  value: str = ""
  numeric_value: float = 0.0
  formula: str = ""
  operands: list[CalcOperand] = field(default_factory=list)
  reason: str = ""
  direction: str = ""


def _norm_label(text: str) -> str:
  return re.sub(r"\s+", "", (text or "").strip())


def is_derived_header_label(text: str) -> bool:
  t = _norm_label(text)
  if not t:
    return False
  return t in DERIVED_LABELS


def _classify_header(header: str) -> str:
  h = (header or "").strip()
  if not h:
    return "unknown"
  leaf = h.split("/")[-1].strip()
  if is_derived_header_label(leaf) or _header_looks_derived(h):
    return "derived"
  if _RATIO_RE.search(h):
    return "ratio"
  if _QTY_RE.search(h):
    return "quantity"
  if _AMOUNT_RE.search(h):
    return "amount"
  return "unknown"


def _header_looks_derived(header: str) -> bool:
  """파생 열 보조 신호 — exact 키워드 외 '○○계/잔액' 등."""
  leaf = _norm_label(header.split("/")[-1] if header else "")
  if not leaf:
    return False
  if leaf in DERIVED_LABELS:
    return True
  if leaf.endswith("계") and leaf not in ("연계", "회계", "관계", "체계"):
    return True
  if leaf.endswith("잔액") or leaf.endswith("잔고"):
    return True
  return False


def _get_parsed_table(editor: HWPXEditor, table_index: int) -> ParsedTableGrid:
  all_tables = []
  for root in editor.section_trees.values():
    for tbl in editor._get_tables(root):
      all_tables.append(tbl)
  if table_index >= len(all_tables):
    return ParsedTableGrid()
  return parse_table_grid(all_tables[table_index])


def _row_label(rows: list[list[str]], row: int) -> str:
  if row < 0 or row >= len(rows):
    return ""
  for c in range(min(2, len(rows[row]))):
    t = str(rows[row][c]).strip()
    if not t:
      continue
    if _parse_number(t) is not None:
      continue
    return t
  return str(rows[row][0]).strip() if rows[row] else ""


def _col_header(parsed: ParsedTableGrid, row: int, col: int, header_rows: int) -> str:
  parts: list[str] = []
  for hr in range(min(header_rows, row)):
    if col < len(parsed.rows[hr]):
      t = str(parsed.rows[hr][col]).strip()
      if t and t not in parts:
        parts.append(t)
  return " / ".join(parts)


def _infer_header_rows(rows: list[list[str]]) -> int:
  if not rows:
    return 0
  count = 1
  for r in range(1, min(4, len(rows))):
    lab = _row_label(rows, r)
    if is_derived_header_label(lab):
      break
    cells = [str(rows[r][c]).strip() for c in range(len(rows[r])) if str(rows[r][c]).strip()]
    if not cells:
      break
    has_numeric = any(
      _parse_number(str(rows[r][c])) is not None
      for c in range(1, len(rows[r]))
      if str(rows[r][c]).strip()
    )
    if lab and has_numeric:
      break
    numeric = sum(1 for t in cells if _parse_number(t) is not None)
    if numeric / len(cells) > 0.5:
      break
    count += 1
  return min(count, len(rows))


def _column_group_key(parsed: ParsedTableGrid, col: int, header_rows: int) -> tuple:
  for hr in range(header_rows):
    for m in parsed.merges:
      if m.row == hr and m.col <= col < m.col + m.colspan:
        return (hr, m.col, str(parsed.rows[m.row][m.col]).strip())
  top = str(parsed.rows[0][col]).strip() if parsed.rows and col < len(parsed.rows[0]) else ""
  return (0, col, top)


def _is_label_column(parsed: ParsedTableGrid, col: int, header_rows: int, data_start: int) -> bool:
  if col == 0:
    return True
  samples: list[str] = []
  for r in range(data_start, min(parsed.num_rows, data_start + 8)):
    if r >= parsed.num_rows or col >= len(parsed.rows[r]):
      continue
    t = str(parsed.rows[r][col]).strip()
    if t:
      samples.append(t)
  if not samples:
    hdr = _col_header(parsed, parsed.num_rows - 1, col, header_rows)
    return _classify_header(hdr) not in ("amount", "quantity", "ratio", "unknown")
  numeric = sum(1 for t in samples if _parse_number(t) is not None)
  return numeric / len(samples) < 0.34


def _cell_ref(table_index: int, row: int, col: int) -> dict:
  return {
    "document": "target",
    "source_type": "table_cell",
    "location": f"표{table_index + 1} ({row},{col})",
    "table_id": table_index,
    "row": row,
    "column": col,
  }


def is_target_derived_cell(editor: HWPXEditor, table_index: int, row: int, col: int) -> bool:
  parsed = _get_parsed_table(editor, table_index)
  if not parsed.rows or row >= parsed.num_rows or col >= parsed.num_cols:
    return False
  header_rows = _infer_header_rows(parsed.rows)
  row_lab = _row_label(parsed.rows, row)
  col_hdr = _col_header(parsed, row, col, header_rows)
  if is_derived_header_label(row_lab):
    return True
  if col_hdr:
    for part in col_hdr.split("/"):
      p = part.strip()
      if is_derived_header_label(p) or _header_looks_derived(p):
        return True
  return False


def try_table_cell_calculation(
  editor: HWPXEditor,
  table_index: int,
  row: int,
  col: int,
) -> TableCalcResult:
  parsed = _get_parsed_table(editor, table_index)
  rows = parsed.rows
  if not rows or row >= len(rows) or col >= len(rows[row]):
    return TableCalcResult(ok=False, reason=SKIP_REASON)

  header_rows = _infer_header_rows(rows)
  data_start = header_rows
  row_lab = _row_label(rows, row)
  col_hdr = _col_header(parsed, row, col, header_rows)
  row_derived = is_derived_header_label(row_lab)
  col_derived = any(
    is_derived_header_label(p.strip()) or _header_looks_derived(p.strip())
    for p in col_hdr.split("/") if p.strip()
  )

  if not row_derived and not col_derived:
    return TableCalcResult(ok=False, reason=SKIP_REASON)
  if row_derived and col_derived:
    return TableCalcResult(ok=False, reason=SKIP_REASON)

  operands: list[CalcOperand] = []
  direction = ""

  if row_derived and _norm_label(row_lab) == "총계":
    direction = "subtotal_sum"
    for r in range(data_start, row):
      lab = _row_label(rows, r)
      if _norm_label(lab) not in ("합계", "소계"):
        continue
      disp = str(rows[r][col]).strip()
      val = _parse_number(disp)
      if val is None:
        return TableCalcResult(ok=False, reason=SKIP_REASON)
      operands.append(CalcOperand(
        row=r, col=col, value=val, display=disp,
        header=lab or col_hdr,
      ))
  elif row_derived:
    direction = "col_sum"
    # 소계: 바로 위 파생행(소계/합계/총계) 이후의 연속 데이터 행만.
    # 그 외(합계/계): 위쪽 소계·합계 행이 있으면 그것만, 없으면 데이터 행 전부.
    start = data_start
    for r in range(row - 1, data_start - 1, -1):
      lab = _row_label(rows, r)
      if is_derived_header_label(lab):
        start = r + 1
        break

    prefer_subtotals = _norm_label(row_lab) in ("합계", "총계")
    subtotal_rows: list[int] = []
    if prefer_subtotals:
      for r in range(data_start, row):
        lab = _row_label(rows, r)
        if _norm_label(lab) in ("소계", "합계") and _norm_label(lab) != _norm_label(row_lab):
          # 소계만 (같은 레벨 합계 행 제외 — 보통 소계)
          if _norm_label(lab) == "소계":
            subtotal_rows.append(r)

    if prefer_subtotals and subtotal_rows:
      scan_rows = subtotal_rows
    else:
      scan_rows = list(range(start, row))

    for r in scan_rows:
      lab = _row_label(rows, r)
      if is_derived_header_label(lab) and r not in subtotal_rows:
        continue
      disp = str(rows[r][col]).strip()
      if not disp:
        return TableCalcResult(ok=False, reason=SKIP_REASON)
      val = _parse_number(disp)
      if val is None:
        return TableCalcResult(ok=False, reason=SKIP_REASON)
      operands.append(CalcOperand(
        row=r, col=col, value=val, display=disp,
        header=lab or col_hdr,
      ))
  elif col_derived:
    direction = "row_sum"
    # 구조 규칙: 대상 열 바로 왼쪽에서, 파생 헤더/라벨 열을 만나기 전까지의
    # 연속된 숫자 열만 합산. (같은 행의 모든 숫자를 더하지 않음)
    categories: set[str] = set()
    candidate_cols: list[int] = []
    for j in range(col - 1, -1, -1):
      if _is_label_column(parsed, j, header_rows, data_start):
        break
      hdr = _col_header(parsed, row, j, header_rows)
      if _classify_header(hdr) == "derived" or _header_looks_derived(hdr):
        break
      # 비어 있는 중간 열은 구간을 끊지 않고 건너뜀
      disp = str(rows[row][j]).strip() if j < len(rows[row]) else ""
      if not disp:
        continue
      if _parse_number(disp) is None:
        break
      candidate_cols.append(j)

    candidate_cols.reverse()
    if not candidate_cols:
      return TableCalcResult(ok=False, reason=SKIP_REASON)

    for j in candidate_cols:
      hdr = _col_header(parsed, row, j, header_rows)
      disp = str(rows[row][j]).strip()
      val = _parse_number(disp)
      if val is None:
        return TableCalcResult(ok=False, reason=SKIP_REASON)
      cat = _classify_header(hdr)
      categories.add(cat)
      operands.append(CalcOperand(
        row=row, col=j, value=val, display=disp, header=hdr or f"열{j+1}",
      ))
    known = {c for c in categories if c not in ("unknown", "derived")}
    if len(known) > 1:
      return TableCalcResult(ok=False, reason=SKIP_REASON)
    if "ratio" in known and ("amount" in known or "quantity" in known):
      return TableCalcResult(ok=False, reason=SKIP_REASON)

  if not operands:
    return TableCalcResult(ok=False, reason=SKIP_REASON)

  total = sum(op.value for op in operands)
  ref = operands[0].display
  formatted = _format_number(total, ref)

  if direction == "row_sum":
    terms = " + ".join(
      f"{op.display}({op.row},{op.col})" for op in operands
    )
    formula = f"행 합계: {terms} = {formatted}"
  elif direction == "col_sum":
    terms = " + ".join(
      f"{op.display}({op.row},{op.col})" for op in operands
    )
    formula = f"열 합계 [{col_hdr or row_lab}]: {terms} = {formatted}"
  else:
    terms = " + ".join(
      f"{op.display}({op.row},{op.col})" for op in operands
    )
    formula = f"총계: {terms} = {formatted}"

  return TableCalcResult(
    ok=True,
    value=formatted,
    numeric_value=total,
    formula=formula,
    operands=operands,
    direction=direction,
  )
