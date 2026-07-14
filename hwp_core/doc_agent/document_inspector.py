"""대상 문서의 빈 문단·빈 표 셀 탐지 (좌표 보존)."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..concept_resolver import ConceptResolver, normalize_label
from ..hwpx_editor import HWPXEditor, PLACEHOLDER_RE, PLACEHOLDER_SUBSTR

_FILL_ONTOLOGY = Path(__file__).resolve().parent.parent / "ontology" / "doc_fill_concepts.yaml"

# 글 항목으로 취급할 concept
TEXT_CONCEPTS = {"rd_necessity", "rd_objective", "expected_effect"}
TABLE_CONCEPTS = {"person_name", "position", "participation_rate", "labor_cost_cash"}


@dataclass
class EditableField:
  field_id: str
  field_type: str  # paragraph | table_cell | insert_after
  label: str
  context: str
  document_id: str
  table_id: Optional[int] = None
  row: Optional[int] = None
  column: Optional[int] = None
  paragraph_id: Optional[int] = None
  style: dict = field(default_factory=dict)
  current_value: str = ""
  concept_id: Optional[str] = None
  concept_confidence: float = 0.0
  anchor_label: str = ""

  def to_dict(self) -> dict:
    return asdict(self)


@lru_cache(maxsize=1)
def get_fill_resolver() -> ConceptResolver:
  return ConceptResolver(str(_FILL_ONTOLOGY))


def _is_blank(text: str) -> bool:
  t = (text or "").strip()
  if not t:
    return True
  if PLACEHOLDER_RE.match(t):
    return True
  if any(s in t for s in PLACEHOLDER_SUBSTR):
    return True
  if len(t) <= 2 and not re.search(r"[가-힣A-Za-z0-9]", t):
    return True
  return False


def _ground_label(label: str, context: str = "") -> tuple[Optional[str], float]:
  gr = get_fill_resolver().ground(label, context)
  if gr.grounded:
    return gr.concept_id, float(gr.confidence)
  return None, 0.0


def find_empty_fields(editor: HWPXEditor, document_id: str = "") -> list[EditableField]:
  """의미 있는 빈 표 셀 + 서식 라벨 빈칸 + 라벨 아래 빈 문단."""
  fields: list[EditableField] = []
  fields.extend(find_empty_table_cells(editor, document_id=document_id))
  fields.extend(find_form_label_blanks(editor, document_id=document_id))
  fields.extend(find_text_section_fields(editor, document_id=document_id))
  return fields


FORM_LABEL_RE = re.compile(
  r"(번호|성명|이름|직위|직급|주소|전화|메일|이메일|팩스|"
  r"공고|과제|대표|등록|기관|부처|일자|기간|비율|금액)",
  re.I,
)


def find_form_label_blanks(editor: HWPXEditor, document_id: str = "") -> list[EditableField]:
  """라벨 옆이 비어 있는 서식 칸 (공고번호·과제번호 등). 연속 빈 칸은 첫 칸만."""
  fields: list[EditableField] = []
  seen: set[tuple[int, int, int]] = set()

  for t_idx in range(editor.get_table_count()):
    rows = editor.get_table_as_rows(t_idx)
    if not rows:
      continue
    for r_idx, row in enumerate(rows):
      c_idx = 0
      while c_idx < len(row):
        if not _is_blank(str(row[c_idx])):
          c_idx += 1
          continue
        label = ""
        for j in range(c_idx - 1, -1, -1):
          cand = str(row[j]).strip()
          if cand:
            label = cand
            break
        if not label or len(label) > 24 or not FORM_LABEL_RE.search(label):
          c_idx += 1
          continue
        # 같은 라벨이 반복 병합된 칸은 스킵
        if label == str(row[c_idx]).strip():
          c_idx += 1
          continue
        key = (t_idx, r_idx, c_idx)
        if key in seen:
          c_idx += 1
          continue
        seen.add(key)
        end = c_idx + 1
        while end < len(row) and _is_blank(str(row[end])):
          end += 1
        fields.append(EditableField(
          field_id=f"form_{t_idx}_{r_idx}_{c_idx}_{uuid.uuid4().hex[:6]}",
          field_type="table_cell",
          label=label,
          context=f"표{t_idx+1} / {label}",
          document_id=document_id,
          table_id=t_idx,
          row=r_idx,
          column=c_idx,
          current_value="",
          concept_id="form_blank",
          concept_confidence=0.7,
          style={"span_cols": end - c_idx, "form": True},
        ))
        c_idx = end
  return fields


def find_empty_table_cells(editor: HWPXEditor, document_id: str = "") -> list[EditableField]:
  fields: list[EditableField] = []
  for t_idx in range(editor.get_table_count()):
    rows = editor.get_table_as_rows(t_idx)
    if not rows or len(rows) < 2:
      continue
    header = [str(c).strip() for c in rows[0]]
    header_concepts: list[tuple[Optional[str], float]] = [
      _ground_label(h) for h in header
    ]
    # 인건비형 표만 (이름/직급 등 헤더가 1개 이상)
    if not any(cid in TABLE_CONCEPTS for cid, conf in header_concepts if cid and conf >= 0.8):
      continue
    for r_idx in range(1, len(rows)):
      row = rows[r_idx]
      row_label = str(row[0]).strip() if row else ""
      if row_label in ("합계", "총계", "소계", "계"):
        continue
      # 행에 값이 하나도 없으면 스킵 (빈 템플릿 줄은 데이터가 있을 때만)
      non_blank = sum(1 for c in row if not _is_blank(str(c)))
      # 완전 빈 데이터 행 → 매핑된 헤더 열 전부 채우기 후보
      allow_empty_row = non_blank == 0
      for c_idx, cell in enumerate(row):
        if c_idx >= len(header):
          continue
        if not _is_blank(str(cell)):
          continue
        cid, conf = header_concepts[c_idx] if c_idx < len(header_concepts) else (None, 0.0)
        if not cid or cid not in TABLE_CONCEPTS:
          continue
        if not allow_empty_row and non_blank == 0:
          continue
        label = header[c_idx] or f"열{c_idx+1}"
        fields.append(EditableField(
          field_id=f"tc_{t_idx}_{r_idx}_{c_idx}_{uuid.uuid4().hex[:6]}",
          field_type="table_cell",
          label=label,
          context=f"표{t_idx+1} / {row_label} / {label}" if row_label else f"표{t_idx+1} / {label}",
          document_id=document_id,
          table_id=t_idx,
          row=r_idx,
          column=c_idx,
          current_value=str(cell),
          concept_id=cid,
          concept_confidence=conf,
          style={"header": header},
        ))
  return fields


def find_text_section_fields(editor: HWPXEditor, document_id: str = "") -> list[EditableField]:
  """라벨 문단 바로 다음이 비어 있거나 짧으면 작성 후보."""
  paras = editor.get_paragraphs()
  fields: list[EditableField] = []
  seen_concepts: set[str] = set()

  for i, para in enumerate(paras):
    text = (para.get("text") or "").strip()
    # 표에서 추출된 긴 요약 덩어리는 섹션 라벨이 아님
    if not text or len(text) > 48:
      continue
    cid, conf = _ground_label(text)
    if not cid or cid not in TEXT_CONCEPTS or conf < 0.85:
      # 라벨이 "○ 연구개발 목표" 형태 — 짧은 제목만
      for syn_cid in TEXT_CONCEPTS:
        cdef = get_fill_resolver().concepts.get(syn_cid)
        if not cdef:
          continue
        keys = [cdef.label_ko] + list(cdef.synonyms)
        # 짧은 제목 문단에 동의어가 들어 있을 때만
        if any(normalize_label(k) and normalize_label(k) in normalize_label(text) for k in keys):
          cid, conf = syn_cid, 0.9
          break
      else:
        continue

    if cid in seen_concepts:
      continue

    # 다음 문단이 다른 섹션 라벨이면 insert_after
    next_idx = i + 1
    next_text = ""
    next_para = paras[next_idx] if next_idx < len(paras) else None
    if next_para:
      next_text = (next_para.get("text") or "").strip()
      next_cid, _ = _ground_label(next_text)
      if next_cid in TEXT_CONCEPTS and len(next_text) <= 48:
        # 바로 다음이 다른 섹션 → 이 라벨 아래 삽입
        fields.append(EditableField(
          field_id=f"ins_{i}_{uuid.uuid4().hex[:6]}",
          field_type="insert_after",
          label=get_fill_resolver().concepts[cid].label_ko,
          context=text,
          document_id=document_id,
          paragraph_id=i,
          current_value="",
          concept_id=cid,
          concept_confidence=conf,
          anchor_label=text,
        ))
        seen_concepts.add(cid)
        continue

    # 이미 내용이 있으면 채우지 않음 (짧은 본문도 허용 — placeholder만)
    if next_para and _is_blank(next_text):
      fields.append(EditableField(
        field_id=f"p_{next_idx}_{uuid.uuid4().hex[:6]}",
        field_type="paragraph",
        label=get_fill_resolver().concepts[cid].label_ko,
        context=text,
        document_id=document_id,
        paragraph_id=next_idx,
        current_value=next_text,
        concept_id=cid,
        concept_confidence=conf,
        anchor_label=text,
      ))
      seen_concepts.add(cid)
    elif next_para is None or not next_text:
      fields.append(EditableField(
        field_id=f"ins_{i}_{uuid.uuid4().hex[:6]}",
        field_type="insert_after",
        label=get_fill_resolver().concepts[cid].label_ko,
        context=text,
        document_id=document_id,
        paragraph_id=i,
        current_value="",
        concept_id=cid,
        concept_confidence=conf,
        anchor_label=text,
      ))
      seen_concepts.add(cid)

  return fields


def build_field_context(field: EditableField) -> str:
  parts = [field.label, field.context, field.anchor_label]
  return " / ".join(p for p in parts if p)


def inspect_document(file_bytes: bytes, document_id: str = "", filename: str = "") -> dict:
  """대상 HWPX 바이트를 검사해 필드 목록 반환."""
  ext = (filename.rsplit(".", 1)[-1].lower() if filename else "hwpx")
  if ext == "hwp":
    return {
      "ok": False,
      "error": "HWP 직접 쓰기는 제한됩니다. HWPX로 변환 후 작업하거나 변환 경로를 사용하세요.",
      "fields": [],
      "write_format": "hwpx",
    }
  try:
    editor = HWPXEditor(file_bytes)
  except Exception as e:
    return {"ok": False, "error": str(e), "fields": []}

  fields = find_empty_fields(editor, document_id=document_id or filename)
  return {
    "ok": True,
    "error": "",
    "fields": [f.to_dict() for f in fields],
    "paragraph_count": len(editor.get_paragraphs()),
    "table_count": editor.get_table_count(),
    "write_format": "hwpx",
  }
