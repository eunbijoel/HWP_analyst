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
TEXT_CONCEPTS = {
  "rd_necessity", "rd_objective", "expected_effect",
  "rd_content", "rd_strategy", "utilization",
}
# 인건비형 표
TABLE_CONCEPTS = {"person_name", "position", "participation_rate", "labor_cost_cash"}
# 서식 사실 칸 (Evidence only)
FACT_CONCEPTS = {
  "org_name", "address", "representative", "pi_name",
  "phone", "mobile", "email", "business_reg_no", "corp_reg_no",
  "person_name", "position", "form_blank",
} | TABLE_CONCEPTS


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
  r"(성명|이름|직위|직급|주소|전화번호|전화|메일|이메일|전자우편|팩스|"
  r"공고번호|과제번호|대표자명|대표자|대표전화|등록번호|기관명|"
  r"주관기관|수행기관|부처|일자|기간|비율|금액|책임자|소재지|"
  r"휴대전화|연구자번호|연월일|연락처|기관\s*유형|유형)",
  re.I,
)

_ORG_OR_VALUE_LIKE_RE = re.compile(
  r"(연구원|연구소|협회|학회|대학교|대학|센터|주식회사|㈜|[(（]주[)）]|"
  r"유한회사|조합|본부|회의소|얼라이언스|테크|산업)$"
)

_INSTRUCTIONAL_VALUE_RE = re.compile(
  r"(유형|연월일|기입|작성|예시|해당\s*없음|선택|해당란|"
  r"대학\s*,|출연연|중소기업\s*등|\(비어|"
  r"등\s*\)|등\))",
  re.I,
)


def _looks_like_org_or_filled_value(text: str) -> bool:
  """왼쪽 칸이 라벨이 아니라 이미 채워진 값(기관명 등)인지."""
  t = (text or "").strip()
  if not t:
    return False
  if len(t) > 18:
    return True
  if _ORG_OR_VALUE_LIKE_RE.search(t):
    return True
  # 한글 고유명사처럼 보이는데 ontology 라벨이 아니면 값으로 본다
  gr = _ground_label(t)
  if gr[0] in FACT_CONCEPTS and gr[1] >= 0.95 and len(t) <= 12:
    return False
  if not FORM_LABEL_RE.search(t) and len(t) >= 4:
    return True
  return False


def _is_usable_form_label(text: str) -> bool:
  t = (text or "").strip()
  if not t or len(t) > 40:
    return False
  if _looks_like_org_or_filled_value(t):
    return False
  cid, conf = _ground_label(t)
  if cid in FACT_CONCEPTS and conf >= 0.85:
    return True
  # "기관 유형 (대학, …)" 안내 헤더도 열 라벨로 인정 (값은 별도 검증에서 차단)
  if re.search(r"기관\s*유형|유형\s*\(", t) and len(t) <= 40:
    return True
  return bool(FORM_LABEL_RE.search(t)) and len(t) <= 24


def find_form_label_blanks(editor: HWPXEditor, document_id: str = "") -> list[EditableField]:
  """라벨 옆/열 헤더 아래가 비어 있는 서식 칸. ontology로 concept grounding."""
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

        # 1) 열 헤더(위쪽) 우선 — 기관명|책임자|직위 표
        header_label = ""
        for hr in range(0, min(r_idx, 3)):
          if c_idx >= len(rows[hr]):
            continue
          cand = str(rows[hr][c_idx]).strip()
          if cand and _is_usable_form_label(cand):
            header_label = cand
            break

        # 2) 왼쪽 라벨 — 값이 아닌 짧은 라벨만
        left_label = ""
        for j in range(c_idx - 1, -1, -1):
          cand = str(row[j]).strip()
          if not cand:
            continue
          if _looks_like_org_or_filled_value(cand):
            break
          if _is_usable_form_label(cand):
            left_label = cand
          break

        label = header_label or left_label
        if not label:
          c_idx += 1
          continue

        row_ctx = " ".join(
          str(row[j]).strip() for j in range(0, min(c_idx, len(row))) if str(row[j]).strip()
        )
        # 헤더 행 텍스트도 맥락에 포함
        if r_idx > 0 and c_idx < len(rows[0]):
          row_ctx = f"{rows[0][c_idx]} {row_ctx}".strip()

        # 라벨 자체만 grounding (row_ctx에 연구책임자가 있으면 패턴이 라벨을 덮어씀)
        cid, conf = _ground_label(label)
        if (
          (cid in ("person_name", None, "") or not cid)
          and re.search(r"연구\s*책임|책임자", f"{row_ctx} {label}")
          and not re.search(r"번호|등록", label)
        ):
          cid, conf = "pi_name", max(float(conf or 0), 0.9)
        if cid in TEXT_CONCEPTS:
          c_idx += 1
          continue
        if cid not in FACT_CONCEPTS:
          if not FORM_LABEL_RE.search(label):
            c_idx += 1
            continue
          cid, conf = "form_blank", 0.7

        if label == str(row[c_idx]).strip():
          c_idx += 1
          continue
        key = (t_idx, r_idx, c_idx)
        if key in seen:
          c_idx += 1
          continue
        seen.add(key)
        end = c_idx + 1
        # 왼쪽 라벨 서식(라벨|값|값)만 연속 빈 칸 병합.
        # 열 헤더 표(기관명|책임자|직위)는 칸마다 별도 필드로 둔다.
        if not header_label:
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
          concept_id=cid,
          concept_confidence=conf if conf else 0.7,
          style={"span_cols": end - c_idx, "form": True, "factual": True},
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


def _looks_like_section_heading(text: str) -> bool:
  """본문 문장이 아닌 짧은 섹션 제목인지."""
  t = (text or "").strip()
  if not t or len(t) > 28:
    return False
  # 서술형 문장 배제
  if re.search(r"(다\.|요\.|음\.|이다\.|된다\.|한다\.)$", t):
    return False
  if re.search(r"(을|를)\s+\S+(다|요)", t):
    return False
  return True


def find_text_section_fields(editor: HWPXEditor, document_id: str = "") -> list[EditableField]:
  """라벨 문단 바로 다음이 비어 있거나 짧으면 작성 후보."""
  paras = editor.get_paragraphs()
  fields: list[EditableField] = []
  seen_concepts: set[str] = set()

  for i, para in enumerate(paras):
    text = (para.get("text") or "").strip()
    if not _looks_like_section_heading(text):
      continue
    cid, conf = _ground_label(text)
    if not cid or cid not in TEXT_CONCEPTS or conf < 0.85:
      # 라벨이 "○ 연구개발 목표" 형태 — 짧은 제목만
      for syn_cid in TEXT_CONCEPTS:
        cdef = get_fill_resolver().concepts.get(syn_cid)
        if not cdef:
          continue
        keys = [cdef.label_ko] + list(cdef.synonyms)
        # 제목 전체가 동의어와 가깝거나, 짧은 제목에 동의어가 포함
        nt = normalize_label(text)
        hit = False
        for k in keys:
          nk = normalize_label(k)
          if not nk:
            continue
          if nt == nk or (len(nt) <= 20 and nk in nt):
            hit = True
            break
        if hit:
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
      if (
        next_cid in TEXT_CONCEPTS
        and _looks_like_section_heading(next_text)
      ):
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
