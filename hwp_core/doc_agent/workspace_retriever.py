"""참고 자료(문단·표·Excel) 검색 — 출처 보존. 벡터DB 없이 키워드+ontology."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .document_inspector import TABLE_CONCEPTS, TEXT_CONCEPTS, get_fill_resolver
from .workspace_service import WorkspaceDocument, WorkspaceService
from ..concept_resolver import normalize_label


@dataclass
class SourceHit:
  document: str
  document_id: str
  source_type: str  # paragraph | table_cell | excel_cell
  location: str
  text: str
  score: float = 0.0
  concept_id: str = ""
  meta: dict = field(default_factory=dict)

  def to_dict(self) -> dict:
    return asdict(self)


def _tokens(text: str) -> set[str]:
  return {t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "") if t}


def _score_overlap(query: str, body: str) -> float:
  qt = _tokens(query)
  bt = _tokens(body)
  if not qt or not bt:
    return 0.0
  hit = len(qt & bt)
  return hit / max(len(qt), 1)


def search_paragraphs(
  refs: list[WorkspaceDocument],
  query: str,
  *,
  concept_id: str = "",
  limit: int = 5,
) -> list[SourceHit]:
  hits: list[SourceHit] = []
  labels = []
  if concept_id and concept_id in get_fill_resolver().concepts:
    c = get_fill_resolver().concepts[concept_id]
    labels = [c.label_ko] + list(c.synonyms)

  for ref in refs:
    paras: list[str] = []
    if ref.parsed is not None:
      paras = list(getattr(ref.parsed, "paragraphs", None) or [])
    if not paras and ref.tables:
      # fallback
      pass
    for i, p in enumerate(paras):
      sc = _score_overlap(query, p)
      for lab in labels:
        if lab and lab in p:
          sc += 0.4
      if concept_id == "rd_objective" and any(k in p for k in ("목표", "목적")):
        sc += 0.2
      if concept_id == "expected_effect" and any(k in p for k in ("효과", "성과")):
        sc += 0.2
      if concept_id == "rd_necessity" and any(k in p for k in ("필요", "배경")):
        sc += 0.2
      if sc < 0.15 and len(p) < 40:
        continue
      if sc <= 0 and not any(lab in p for lab in labels if lab):
        continue
      hits.append(SourceHit(
        document=ref.filename,
        document_id=ref.document_id,
        source_type="paragraph",
        location=f"문단 {i+1}",
        text=p[:1200],
        score=sc,
        concept_id=concept_id,
      ))
  hits.sort(key=lambda h: h.score, reverse=True)
  return hits[:limit]


def search_excel_rows(
  refs: list[WorkspaceDocument],
  *,
  required_concepts: list[str] | None = None,
  limit: int = 50,
) -> list[SourceHit]:
  """Excel 시트에서 헤더↔concept 매핑 후 데이터 행을 출처와 함께 반환."""
  required = required_concepts or list(TABLE_CONCEPTS)
  resolver = get_fill_resolver()
  hits: list[SourceHit] = []

  for ref in refs:
    sheets = ref.excel_sheets
    if not sheets and ref.parsed is not None:
      # ReferenceDocument.tables: list of row grids
      for si, rows in enumerate(getattr(ref.parsed, "tables", None) or []):
        sheets.append({"name": f"Sheet{si+1}", "rows": rows})

    for sheet in sheets:
      rows = sheet.get("rows") or []
      if not rows:
        continue
      header = [str(c).strip() for c in rows[0]]
      col_map: dict[str, int] = {}
      for ci, h in enumerate(header):
        gr = resolver.ground(h)
        if gr.grounded and gr.concept_id in required:
          col_map[gr.concept_id] = ci
      if "person_name" not in col_map:
        # 동의어 강제 스캔
        for ci, h in enumerate(header):
          nh = normalize_label(h)
          if nh in ("성명", "이름", "연구원명", "name"):
            col_map["person_name"] = ci
      if "person_name" not in col_map:
        continue
      for ri in range(1, len(rows)):
        row = rows[ri]
        name = str(row[col_map["person_name"]]).strip() if col_map["person_name"] < len(row) else ""
        if not name:
          continue
        payload = {}
        for cid, ci in col_map.items():
          if ci < len(row):
            payload[cid] = str(row[ci]).strip()
        loc_parts = []
        for cid, ci in col_map.items():
          col_letter = chr(ord("A") + ci) if ci < 26 else f"C{ci}"
          loc_parts.append(
            f"{sheet.get('name', 'Sheet')}!{col_letter}{ri+1}={payload.get(cid, '')}"
          )
        hits.append(SourceHit(
          document=ref.filename,
          document_id=ref.document_id,
          source_type="excel_cell",
          location="; ".join(loc_parts),
          text=name,
          score=1.0,
          concept_id="person_name",
          meta={"row_values": payload, "sheet": sheet.get("name"), "excel_row": ri + 1, "col_map": col_map},
        ))
        if len(hits) >= limit:
          return hits
  return hits


def _format_excel_row(header: list[str], row: list[str], max_cols: int = 8) -> str:
  parts = []
  for i, cell in enumerate(row[:max_cols]):
    val = str(cell or "").strip()
    if not val:
      continue
    h = header[i].strip() if i < len(header) and str(header[i]).strip() else ""
    parts.append(f"{h}: {val}" if h else val)
  return " · ".join(parts)


def search_excel_text_evidence(
  refs: list[WorkspaceDocument],
  query: str = "",
  *,
  limit: int = 12,
  max_rows_per_sheet: int = 18,
) -> list[SourceHit]:
  """문단이 없는 Excel도 글 초안용 근거로 쓸 수 있게 행 텍스트를 반환."""
  q_tokens = _tokens(query)
  hits: list[SourceHit] = []

  for ref in refs:
    sheets = list(ref.excel_sheets or [])
    if not sheets and ref.parsed is not None:
      for si, rows in enumerate(getattr(ref.parsed, "tables", None) or []):
        sheets.append({"name": f"Sheet{si+1}", "rows": rows})

    for sheet in sheets:
      rows = sheet.get("rows") or []
      if not rows:
        continue
      header = [str(c).strip() for c in rows[0]]
      sheet_name = sheet.get("name") or "Sheet"
      for ri in range(1, min(len(rows), max_rows_per_sheet + 1)):
        row = [str(c).strip() if c is not None else "" for c in rows[ri]]
        if not any(row):
          continue
        # 합계·소계 행은 요약에 유용하므로 포함
        line = _format_excel_row(header, row)
        if not line:
          continue
        sc = _score_overlap(query, line) if query else 0.35
        # 질의 토큰이 전혀 없어도 예산표면 기본 점수
        if sc <= 0 and q_tokens:
          flat = " ".join(row)
          sc = 0.2 if any(t in flat for t in q_tokens) else 0.12
        if sc <= 0:
          sc = 0.25
        hits.append(SourceHit(
          document=ref.filename,
          document_id=ref.document_id,
          source_type="excel_cell",
          location=f"{sheet_name}!행{ri+1}",
          text=line,
          score=sc,
          meta={"sheet": sheet_name, "excel_row": ri + 1, "header": header},
        ))

  hits.sort(key=lambda h: h.score, reverse=True)
  return hits[:limit]


def excel_summary_draft(refs: list[WorkspaceDocument], *, max_lines: int = 12) -> tuple[str, list[dict]]:
  """엑셀 표를 짧은 한국어 초안 + 출처로 변환."""
  hits = search_excel_text_evidence(refs, query="", limit=max_lines, max_rows_per_sheet=max_lines + 2)
  if not hits:
    return "", []
  by_doc: dict[str, list[SourceHit]] = {}
  for h in hits:
    by_doc.setdefault(h.document, []).append(h)
  blocks = []
  sources = []
  for doc, rows in by_doc.items():
    blocks.append(f"【{doc}】")
    for h in rows[:max_lines]:
      blocks.append(f"- {h.text}")
      sources.append({
        "document": h.document,
        "source_type": h.source_type,
        "location": h.location,
      })
  return "\n".join(blocks).strip(), sources


def _compact_excel_grid(
  raw_rows: list[list],
  *,
  max_rows: int = 28,
  max_cols: int = 10,
) -> list[list[str]]:
  """빈 열 제거·열 수 제한한 표 그리드."""
  if not raw_rows:
    return []
  cleaned: list[list[str]] = []
  for row in raw_rows:
    cleaned.append([("" if c is None else str(c).strip()) for c in row])
  # 헤더 병합: 2행 헤더면 빈 칸을 위 행으로 채움
  if len(cleaned) >= 2:
    h0, h1 = cleaned[0], cleaned[1]
    # 2행이 소제목(이월/당해/합계)만 있으면 헤더 병합
    if sum(1 for c in h1 if c) >= 2 and sum(1 for c in h0 if c) >= 2:
      width = max(len(h0), len(h1))
      merged = []
      for i in range(width):
        a = h0[i] if i < len(h0) else ""
        b = h1[i] if i < len(h1) else ""
        if a and b and a != b:
          merged.append(f"{a}/{b}")
        else:
          merged.append(a or b)
      cleaned = [merged] + cleaned[2:]

  width = max((len(r) for r in cleaned), default=0)
  for r in cleaned:
    while len(r) < width:
      r.append("")

  keep_cols = [
    i for i in range(width)
    if any((cleaned[r][i] if i < len(cleaned[r]) else "").strip() for r in range(len(cleaned)))
  ]
  if not keep_cols:
    return []
  keep_cols = keep_cols[:max_cols]
  out = []
  for r in cleaned[:max_rows]:
    out.append([r[i] if i < len(r) else "" for i in keep_cols])
  # 완전 빈 데이터 행 제거 (헤더 제외)
  if len(out) > 1:
    out = [out[0]] + [r for r in out[1:] if any(c.strip() for c in r)]
  return out


def excel_grids_for_insert(
  refs: list[WorkspaceDocument],
  *,
  max_rows: int = 28,
  max_cols: int = 10,
) -> list[dict]:
  """삽입용 표 그리드 목록 [{document, sheet, rows}]."""
  grids: list[dict] = []
  for ref in refs:
    sheets = list(ref.excel_sheets or [])
    if not sheets and ref.parsed is not None:
      for si, rows in enumerate(getattr(ref.parsed, "tables", None) or []):
        sheets.append({"name": f"Sheet{si+1}", "rows": rows})
    for sheet in sheets:
      raw = sheet.get("rows") or []
      grid = _compact_excel_grid(raw, max_rows=max_rows, max_cols=max_cols)
      if len(grid) < 2:
        continue
      grids.append({
        "document": ref.filename,
        "document_id": ref.document_id,
        "sheet": sheet.get("name") or "Sheet",
        "rows": grid,
      })
  return grids


def format_grid_preview(rows: list[list[str]], *, max_rows: int = 12) -> str:
  """카드/미리보기용 파이프 표 텍스트."""
  if not rows:
    return ""
  shown = rows[:max_rows]
  lines = [" | ".join(c or " " for c in r) for r in shown]
  if len(rows) > max_rows:
    lines.append(f"… (+{len(rows) - max_rows}행)")
  return "\n".join(lines)


def lookup_label_value_in_refs(
  refs: list[WorkspaceDocument],
  label: str,
) -> tuple[str, list[dict]]:
  """라벨(공고번호·성명 등)에 대응되는 값을 참고자료에서 찾음. 없으면 ("", [])."""
  lab = (label or "").strip()
  if not lab:
    return "", []
  nlab = normalize_label(lab)

  # 1) Excel: 헤더==라벨인 열의 첫 데이터, 또는 같은 행에서 라벨 옆 값
  for ref in refs:
    sheets = list(ref.excel_sheets or [])
    if not sheets and ref.parsed is not None:
      for si, rows in enumerate(getattr(ref.parsed, "tables", None) or []):
        sheets.append({"name": f"Sheet{si+1}", "rows": rows})
    for sheet in sheets:
      rows = sheet.get("rows") or []
      if not rows:
        continue
      header = [str(c).strip() for c in rows[0]]
      for ci, h in enumerate(header):
        if normalize_label(h) == nlab or (nlab and nlab in normalize_label(h)):
          for ri in range(1, len(rows)):
            if ci < len(rows[ri]):
              val = str(rows[ri][ci]).strip()
              if val:
                return val, [{
                  "document": ref.filename,
                  "source_type": "excel_cell",
                  "location": f"{sheet.get('name')}!{ri+1}열{ci+1}",
                }]
      # 행 안에서 라벨 셀 옆에 값
      for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
          if normalize_label(str(cell)) != nlab and nlab not in normalize_label(str(cell)):
            continue
          for nj in (ci + 1, ci + 2):
            if nj < len(row):
              val = str(row[nj]).strip()
              if val and normalize_label(val) != nlab:
                return val, [{
                  "document": ref.filename,
                  "source_type": "excel_cell",
                  "location": f"{sheet.get('name')} 행{ri+1}",
                }]

  # 2) 문단/표 텍스트: "공고번호: XXX" 패턴
  pat = re.compile(
    re.escape(lab) + r"\s*[:：]?\s*([^\s,;/|]{2,40})",
  )
  for ref in refs:
    paras = list(getattr(ref.parsed, "paragraphs", None) or []) if ref.parsed else []
    for i, p in enumerate(paras):
      m = pat.search(p or "")
      if m:
        return m.group(1).strip(), [{
          "document": ref.filename,
          "source_type": "paragraph",
          "location": f"문단 {i+1}",
        }]
  return "", []


def search_tables(
  refs: list[WorkspaceDocument],
  query: str,
  limit: int = 5,
) -> list[SourceHit]:
  hits: list[SourceHit] = []
  for ref in refs:
    tables = getattr(ref.parsed, "tables", None) or []
    for ti, rows in enumerate(tables):
      flat = " ".join(str(c) for r in rows for c in r)
      sc = _score_overlap(query, flat)
      if sc < 0.1:
        continue
      hits.append(SourceHit(
        document=ref.filename,
        document_id=ref.document_id,
        source_type="table_cell",
        location=f"표 {ti+1}",
        text=flat[:800],
        score=sc,
      ))
  hits.sort(key=lambda h: h.score, reverse=True)
  return hits[:limit]


def search_references(
  workspace: WorkspaceService,
  query: str,
  *,
  concept_id: str = "",
  field_type: str = "paragraph",
  required_concepts: list[str] | None = None,
) -> list[dict]:
  refs = workspace.list_references()
  if field_type == "table_cell" or (required_concepts and any(c in TABLE_CONCEPTS for c in required_concepts)):
    hits = search_excel_rows(refs, required_concepts=required_concepts or list(TABLE_CONCEPTS))
    if hits:
      return [h.to_dict() for h in hits]
  hits = search_paragraphs(refs, query, concept_id=concept_id)
  if not hits:
    hits = search_tables(refs, query)
  return [h.to_dict() for h in hits]


def propose_table_mapping(hwpx_headers: list[str], excel_headers: list[str]) -> dict:
  """헤더 의미 매핑 (ontology). LLM 없이도 동작."""
  resolver = get_fill_resolver()
  mapping = []
  excel_by_concept: dict[str, str] = {}
  for h in excel_headers:
    gr = resolver.ground(h)
    if gr.grounded:
      excel_by_concept[gr.concept_id] = h
  for h in hwpx_headers:
    gr = resolver.ground(h)
    excel_h = excel_by_concept.get(gr.concept_id or "", "")
    mapping.append({
      "hwpx_header": h,
      "concept_id": gr.concept_id,
      "confidence": gr.confidence,
      "excel_header": excel_h,
    })
  return {"mappings": mapping, "ok": True}
