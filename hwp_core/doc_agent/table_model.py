"""표 구조 모델 — 열 semantic type, 그룹 라벨 상속, Evidence 정합."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..hwpx_editor import HWPXEditor, _parse_number
from ..table_extractor import ParsedTableGrid, parse_table_grid
from .table_calc_fill import (
  DERIVED_LABELS,
  _get_parsed_table,
  _infer_header_rows,
  _norm_label,
  is_derived_header_label,
)

logger = logging.getLogger(__name__)

SEM_TEXT = "text"
SEM_NUMERIC = "numeric"
SEM_UNKNOWN = "unknown"


@dataclass
class ColumnModel:
  index: int
  header_path: list[str] = field(default_factory=list)
  semantic_type: str = SEM_UNKNOWN

  @property
  def header(self) -> str:
    return " / ".join(self.header_path) if self.header_path else f"열{self.index + 1}"


@dataclass
class CellModel:
  row: int
  col: int
  text: str = ""
  semantic_type: str = SEM_UNKNOWN
  header_path: list[str] = field(default_factory=list)
  row_path: list[str] = field(default_factory=list)
  is_blank: bool = False
  is_inherited_group_blank: bool = False
  inherited_group_label: str = ""
  is_header: bool = False
  is_label_col: bool = False

  @property
  def header(self) -> str:
    return " / ".join(self.header_path) if self.header_path else f"열{self.col + 1}"


@dataclass
class TableModel:
  table_index: int
  header_rows: int
  columns: list[ColumnModel] = field(default_factory=list)
  cells: list[list[CellModel]] = field(default_factory=list)
  label_cols: list[int] = field(default_factory=list)

  def column_type(self, col: int) -> str:
    if 0 <= col < len(self.columns):
      return self.columns[col].semantic_type
    return SEM_UNKNOWN

  def cell_at(self, row: int, col: int) -> Optional[CellModel]:
    if 0 <= row < len(self.cells) and 0 <= col < len(self.cells[row]):
      return self.cells[row][col]
    return None

  def row_key(self, row: int) -> str:
    """행 의미 키 — 라벨 열에서 비어 있지 않은 텍스트(상속 포함)."""
    parts: list[str] = []
    for c in self.label_cols:
      cell = self.cell_at(row, c)
      if not cell:
        continue
      t = (cell.inherited_group_label or cell.text or "").strip()
      if t and _parse_number(t) is None:
        parts.append(t)
    if parts:
      return " / ".join(parts)
    # fallback: first non-numeric cell in row
    for cell in self.cells[row] if row < len(self.cells) else []:
      t = (cell.text or "").strip()
      if t and _parse_number(t) is None and not cell.is_header:
        return t
    return ""


def _header_path_for_col(parsed: ParsedTableGrid, col: int, header_rows: int) -> list[str]:
  parts: list[str] = []
  for hr in range(header_rows):
    if hr >= parsed.num_rows or col >= len(parsed.rows[hr]):
      continue
    t = str(parsed.rows[hr][col]).strip()
    if not t:
      # 병합: 왼쪽으로 부모 헤더 찾기
      for j in range(col - 1, -1, -1):
        cand = str(parsed.rows[hr][j]).strip()
        if cand:
          t = cand
          break
    if t and (not parts or parts[-1] != t):
      parts.append(t)
  return parts


def _infer_column_type(parsed: ParsedTableGrid, col: int, data_start: int) -> str:
  samples: list[str] = []
  for r in range(data_start, parsed.num_rows):
    if col >= len(parsed.rows[r]):
      continue
    t = str(parsed.rows[r][col]).strip()
    if not t:
      continue
    if is_derived_header_label(t):
      continue
    samples.append(t)
  if not samples:
    # 헤더 힌트: 금액/예산/계 등이면 numeric
    hdr = " ".join(_header_path_for_col(parsed, col, data_start))
    if re.search(r"예산|금액|합계|소계|집행|잔액|원|비용|수량|비율|%", hdr):
      return SEM_NUMERIC
    if re.search(r"분류|명|항목|구분|비목|적요|내용|비고", hdr):
      return SEM_TEXT
    return SEM_UNKNOWN
  numeric = sum(1 for t in samples if _parse_number(t) is not None)
  ratio = numeric / len(samples)
  if ratio >= 0.6:
    return SEM_NUMERIC
  if ratio <= 0.25:
    return SEM_TEXT
  return SEM_UNKNOWN


def _value_semantic_type(value: str) -> str:
  t = (value or "").strip()
  if not t:
    return SEM_UNKNOWN
  if _parse_number(t) is not None:
    return SEM_NUMERIC
  return SEM_TEXT


def _is_blank_text(text: str) -> bool:
  t = (text or "").strip()
  return not t


def _find_inherited_group_label(
  rows: list[list[str]],
  row: int,
  col: int,
  data_start: int,
) -> str:
  """같은 열에서 위쪽 최근 비어 있지 않은 텍스트 라벨 (숫자·파생행 제외)."""
  for r in range(row - 1, data_start - 1, -1):
    if col >= len(rows[r]):
      continue
    t = str(rows[r][col]).strip()
    if not t:
      continue
    if is_derived_header_label(t):
      break
    if _parse_number(t) is not None:
      continue
    return t
  return ""


def _row_has_other_content(rows: list[list[str]], row: int, skip_col: int) -> bool:
  if row >= len(rows):
    return False
  for c, cell in enumerate(rows[row]):
    if c == skip_col:
      continue
    if str(cell).strip():
      return True
  return False


def build_table_model(editor: HWPXEditor, table_index: int) -> TableModel:
  parsed = _get_parsed_table(editor, table_index)
  if not parsed.rows:
    return TableModel(table_index=table_index, header_rows=0)

  header_rows = _infer_header_rows(parsed.rows)
  data_start = header_rows
  n_cols = parsed.num_cols

  columns: list[ColumnModel] = []
  for c in range(n_cols):
    path = _header_path_for_col(parsed, c, header_rows)
    columns.append(ColumnModel(
      index=c,
      header_path=path,
      semantic_type=_infer_column_type(parsed, c, data_start),
    ))

  # 라벨 열: 좌측 text 열들
  label_cols = [
    c.index for c in columns
    if c.semantic_type == SEM_TEXT and c.index < max(2, n_cols // 3 + 1)
  ]
  if not label_cols and n_cols:
    label_cols = [0]

  cells: list[list[CellModel]] = []
  for r in range(parsed.num_rows):
    row_cells: list[CellModel] = []
    for c in range(n_cols):
      raw = str(parsed.rows[r][c]) if c < len(parsed.rows[r]) else ""
      text = raw.strip()
      is_header = r < header_rows
      col_type = columns[c].semantic_type
      inherited = ""
      is_inherited = False
      if (
        not is_header
        and _is_blank_text(text)
        and col_type in (SEM_TEXT, SEM_UNKNOWN)
        and c in label_cols
      ):
        inherited = _find_inherited_group_label(parsed.rows, r, c, data_start)
        if inherited and _row_has_other_content(parsed.rows, r, c):
          is_inherited = True

      row_path: list[str] = []
      for lc in label_cols:
        if lc >= len(parsed.rows[r]):
          continue
        lt = str(parsed.rows[r][lc]).strip()
        if not lt and lc == c and inherited:
          lt = inherited
        if lt and _parse_number(lt) is None:
          row_path.append(lt)

      row_cells.append(CellModel(
        row=r,
        col=c,
        text=text,
        semantic_type=col_type if not is_header else SEM_TEXT,
        header_path=list(columns[c].header_path),
        row_path=row_path,
        is_blank=_is_blank_text(text),
        is_inherited_group_blank=is_inherited,
        inherited_group_label=inherited,
        is_header=is_header,
        is_label_col=c in label_cols,
      ))
    cells.append(row_cells)

  return TableModel(
    table_index=table_index,
    header_rows=header_rows,
    columns=columns,
    cells=cells,
    label_cols=label_cols,
  )


def is_inherited_group_blank_cell(
  editor: HWPXEditor,
  table_index: int,
  row: int,
  col: int,
) -> bool:
  model = build_table_model(editor, table_index)
  cell = model.cell_at(row, col)
  return bool(cell and cell.is_inherited_group_blank)


def value_matches_column_type(value: str, column_type: str) -> bool:
  if column_type == SEM_UNKNOWN:
    return True
  vt = _value_semantic_type(value)
  if vt == SEM_UNKNOWN:
    return False
  return vt == column_type


def headers_compatible(target_header: str, source_header: str) -> bool:
  """열 의미 정합 — 정규화 후 동일·포함·공통 leaf."""
  a = _norm_label(target_header)
  b = _norm_label(source_header)
  if not a or not b:
    return False
  if a == b:
    return True
  if a in b or b in a:
    return True
  a_leaf = a.split("/")[-1]
  b_leaf = b.split("/")[-1]
  return bool(a_leaf and a_leaf == b_leaf)


def row_keys_compatible(target_key: str, source_key: str) -> bool:
  a = _norm_label(target_key)
  b = _norm_label(source_key)
  if not a or not b:
    return False
  if a == b:
    return True
  # 부분 일치: "내부인건비 / 계약직" vs "계약직"
  a_parts = set(a.replace("/", " ").split())
  b_parts = set(b.replace("/", " ").split())
  if a_parts & b_parts:
    # 너무 짧은 공통어만 있으면 거부
    shared = a_parts & b_parts
    if any(len(p) >= 2 for p in shared):
      # 행 키가 완전히 다르면(교집합이 leaf만) — 계약직 vs 내부인건비는 교집합 없음
      return True
  return False


def log_fill_proposal(
  *,
  action: str,
  target_row: int | None,
  target_col: int | None,
  source_row: int | None,
  source_col: int | None,
  inferred_type: str,
  reason: str,
  value: str = "",
  label: str = "",
) -> None:
  msg = (
    f"[FillProposal] action={action} label={label!r} value={value!r} "
    f"target=({target_row},{target_col}) source=({source_row},{source_col}) "
    f"type={inferred_type} reason={reason}"
  )
  logger.info(msg)
  # Product B 서버 로그에서도 보이게
  print(msg, flush=True)


_table_model_cache: dict[tuple[int, int], TableModel] = {}


def get_table_model(editor: HWPXEditor, table_index: int) -> TableModel:
  key = (id(editor), table_index)
  if key not in _table_model_cache:
    _table_model_cache[key] = build_table_model(editor, table_index)
  return _table_model_cache[key]


def clear_table_model_cache() -> None:
  _table_model_cache.clear()


def lookup_structurally_aligned_cell(
  refs: list,
  *,
  target_editor: HWPXEditor,
  table_index: int,
  row: int,
  col: int,
) -> dict:
  """행 의미 + 열 의미가 모두 맞는 참고 표 셀만 Evidence 후보로 반환."""
  target = build_table_model(target_editor, table_index)
  tcell = target.cell_at(row, col)
  if not tcell:
    return {"value": "", "sources": [], "reason": "대상 셀 없음", "source_row": None, "source_col": None}
  if tcell.is_inherited_group_blank:
    return {
      "value": "",
      "sources": [],
      "reason": "그룹 라벨 상속 빈칸(미작성 칸 아님)",
      "source_row": None,
      "source_col": None,
      "inferred_type": tcell.semantic_type,
    }

  col_type = tcell.semantic_type
  t_row_key = target.row_key(row)
  t_header = tcell.header

  rejected: list[str] = []
  best: Optional[dict] = None

  for ref in refs or []:
    raw = getattr(ref, "file_bytes", None)
    if not raw:
      continue
    try:
      ref_ed = HWPXEditor(raw)
    except Exception:
      continue
    for ti in range(ref_ed.get_table_count()):
      rm = build_table_model(ref_ed, ti)
      for rr in range(rm.header_rows, len(rm.cells)):
        for cc in range(len(rm.cells[rr])):
          scell = rm.cells[rr][cc]
          if scell.is_blank or scell.is_header:
            continue
          if scell.is_inherited_group_blank:
            continue
          if not headers_compatible(t_header, scell.header):
            continue
          s_row_key = rm.row_key(rr)
          if not row_keys_compatible(t_row_key, s_row_key):
            rejected.append(
              f"{ref.filename}({rr},{cc}) row_mismatch {s_row_key!r}≠{t_row_key!r}"
            )
            continue
          if not value_matches_column_type(scell.text, col_type):
            rejected.append(
              f"{ref.filename}({rr},{cc}) type_mismatch "
              f"value={scell.text!r} col={col_type}"
            )
            continue
          cand = {
            "value": scell.text,
            "sources": [{
              "document": ref.filename,
              "source_type": "table_cell",
              "location": f"표{ti + 1} ({rr},{cc})",
              "row": rr,
              "column": cc,
            }],
            "reason": (
              f"행·열 의미 정합 (row={t_row_key!r}, col={t_header!r}, type={col_type})"
            ),
            "source_row": rr,
            "source_col": cc,
            "inferred_type": col_type,
          }
          # exact row key preferred
          if _norm_label(t_row_key) == _norm_label(s_row_key):
            return cand
          if best is None:
            best = cand

  if best:
    return best
  reason = "행·열 의미가 맞는 Evidence 없음"
  if rejected:
    reason = f"{reason} (거절 {min(3, len(rejected))}건: " + "; ".join(rejected[:3]) + ")"
  return {
    "value": "",
    "sources": [],
    "reason": reason,
    "source_row": None,
    "source_col": None,
    "inferred_type": col_type,
  }
