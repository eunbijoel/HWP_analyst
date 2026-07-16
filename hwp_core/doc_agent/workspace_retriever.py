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
  """하위 호환: 라벨 문자열만으로 검색."""
  return lookup_fact_value(refs, label=label, concept_id="")


def _concept_label_aliases(concept_id: str, label: str) -> list[str]:
  """ontology 동의어 + 원 라벨."""
  aliases: list[str] = []
  seen: set[str] = set()

  def _add(s: str) -> None:
    t = (s or "").strip()
    if not t:
      return
    n = normalize_label(t)
    if not n or n in seen:
      return
    seen.add(n)
    aliases.append(t)

  _add(label)
  if concept_id and concept_id in get_fill_resolver().concepts:
    cdef = get_fill_resolver().concepts[concept_id]
    _add(cdef.label_ko)
    for s in cdef.synonyms:
      _add(s)
  # Also accept any ontology concept that grounds this label
  if label:
    gr = get_fill_resolver().ground(label)
    if gr.grounded and gr.concept_id and gr.concept_id != concept_id:
      cdef = get_fill_resolver().concepts.get(gr.concept_id)
      if cdef:
        _add(cdef.label_ko)
        for s in cdef.synonyms:
          _add(s)
  # longer aliases first for matching
  aliases.sort(key=lambda x: len(normalize_label(x)), reverse=True)
  return aliases


def _label_matches(cell: str, aliases: list[str]) -> bool:
  ncell = normalize_label(cell)
  if not ncell:
    return False
  for a in aliases:
    na = normalize_label(a)
    if not na:
      continue
    if ncell == na or na in ncell or (len(ncell) >= 2 and ncell in na):
      return True
  return False


def _iter_ref_table_grids(ref: WorkspaceDocument) -> list[tuple[str, list[list[str]]]]:
  """참고 문서의 모든 표 그리드 (Excel · HWPX · parsed)."""
  grids: list[tuple[str, list[list[str]]]] = []
  for sheet in ref.excel_sheets or []:
    rows = sheet.get("rows") or []
    if rows:
      grids.append((str(sheet.get("name") or "Sheet"), rows))

  # Prefer native HWPX grid (accurate label/value cells)
  if ref.file_bytes:
    ext = (ref.file_type or "").lower().lstrip(".")
    name = (ref.filename or "").lower()
    if ext in ("hwpx", "hwp") or name.endswith(".hwpx") or name.endswith(".hwp"):
      try:
        from ..hwpx_editor import HWPXEditor
        ed = HWPXEditor(ref.file_bytes)
        for ti in range(ed.get_table_count()):
          rows = ed.get_table_as_rows(ti)
          if rows:
            grids.append((f"표{ti + 1}", rows))
      except Exception:
        pass

  if ref.parsed is not None:
    for si, rows in enumerate(getattr(ref.parsed, "tables", None) or []):
      if rows and isinstance(rows, list):
        grids.append((f"Sheet{si + 1}", rows))

  # Avoid broken dataframe collapses when we already have HWPX grids
  if not grids:
    for i, t in enumerate(ref.tables or []):
      df = getattr(t, "dataframe", None)
      if df is None:
        continue
      try:
        rows = [list(map(str, df.columns))] + [
          [("" if str(c) == "nan" else str(c)) for c in row]
          for row in df.values.tolist()
        ]
        if rows:
          grids.append((f"table{i + 1}", rows))
      except Exception:
        pass
  return grids


def _looks_like_field_label(text: str) -> bool:
  from .value_type_validation import _looks_like_field_label as _is_label
  return _is_label(text)


def value_rejection_reason(concept_id: str, label: str, value: str) -> str:
  from .value_type_validation import value_rejection_reason as _reason
  return _reason(concept_id, label, value)


def _value_ok_for_concept(concept_id: str, label: str, value: str) -> bool:
  return value_rejection_reason(concept_id, label, value) == ""


def lookup_fact_candidates(
  refs: list[WorkspaceDocument],
  *,
  label: str = "",
  concept_id: str = "",
) -> list[dict]:
  """사실 칸 후보를 전부 나열 (검색 순서 = rank). 검증은 rank_validate_candidates에서."""
  aliases = _concept_label_aliases(concept_id, label)
  out: list[dict] = []
  if not aliases and not concept_id:
    return []

  def _push(val: str, doc: str, loc: str, stype: str) -> None:
    v = (val or "").strip()
    if not v:
      return
    out.append({
      "value": v,
      "source_document": doc,
      "source_location": loc,
      "source_type": stype,
    })

  # 1) 표: 라벨 옆/아래
  for ref in refs:
    for gname, rows in _iter_ref_table_grids(ref):
      if not rows:
        continue
      for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
          if not _label_matches(str(cell), aliases):
            continue
          for nj in (ci + 1, ci + 2):
            if nj < len(row):
              _push(str(row[nj]), ref.filename, f"{gname} 행{ri + 1}열{nj + 1}", "table_cell")
          if ri + 1 < len(rows) and ci < len(rows[ri + 1]):
            _push(
              str(rows[ri + 1][ci]),
              ref.filename,
              f"{gname} 행{ri + 2}열{ci + 1}",
              "table_cell",
            )

  # 2) 헤더 열
  for ref in refs:
    for gname, rows in _iter_ref_table_grids(ref):
      if len(rows) < 2:
        continue
      header = [str(c).strip() for c in rows[0]]
      if len(header) <= 2 and _looks_like_field_label(header[0]):
        continue
      for ci, h in enumerate(header):
        if not _label_matches(h, aliases):
          continue
        for ri in range(1, len(rows)):
          if ci >= len(rows[ri]):
            continue
          _push(
            str(rows[ri][ci]),
            ref.filename,
            f"{gname} 헤더'{h}' 행{ri + 1}",
            "table_cell",
          )

  # 3) 문단
  for ref in refs:
    paras = list(getattr(ref.parsed, "paragraphs", None) or []) if ref.parsed else []
    if not paras and ref.file_bytes:
      try:
        from ..hwpx_editor import HWPXEditor
        paras = [p["text"] for p in HWPXEditor(ref.file_bytes).get_paragraphs()]
      except Exception:
        paras = []
    for i, p in enumerate(paras):
      text = (p or "").strip()
      if not text:
        continue
      for alias in aliases:
        pat = re.compile(re.escape(alias) + r"\s*[:：]\s*(.+)$", re.I)
        m = pat.search(text)
        if not m:
          m = re.compile(r"^" + re.escape(alias) + r"\s+(.{2,200})$", re.I).search(text)
        if not m:
          continue
        val = re.split(r"\s{2,}|\t", m.group(1).strip())[0].strip()
        _push(val, ref.filename, f"문단 {i + 1}", "paragraph")

  return out


def lookup_fact_value(
  refs: list[WorkspaceDocument],
  *,
  label: str = "",
  concept_id: str = "",
) -> tuple[str, list[dict]]:
  """첫 수락 후보 선택. 거절 시 나머지 후보 계속 평가."""
  from .value_type_validation import pick_accepted_candidate

  raw = lookup_fact_candidates(refs, label=label, concept_id=concept_id)
  val, srcs, _, _ = pick_accepted_candidate(raw, concept_id=concept_id, label=label)
  return val, srcs


def resolve_fact_field(
  refs: list[WorkspaceDocument],
  *,
  label: str,
  concept_id: str,
) -> dict:
  """Ranked candidates + accepted + proposal metadata for trace."""
  from .value_type_validation import expected_value_type, pick_accepted_candidate

  raw: list[dict] = []
  if refs:
    raw = lookup_fact_candidates(refs, label=label, concept_id=concept_id)
    if concept_id == "person_name":
      raw = raw + lookup_fact_candidates(refs, label=label, concept_id="pi_name")
    elif concept_id == "pi_name":
      raw = raw + lookup_fact_candidates(refs, label="연구책임자", concept_id="pi_name")
      raw = raw + lookup_fact_candidates(refs, label=label, concept_id="person_name")

  val, srcs, accepted, ranked = pick_accepted_candidate(
    raw, concept_id=concept_id, label=label,
  )
  rejected = [c for c in ranked if not c.get("accepted")]
  vtype = expected_value_type(concept_id, label)
  proposal = None
  if val:
    proposal = {
      "after": val,
      "sources": srcs,
      "fill_mode": "evidence",
    }
  return {
    "expected_value_type": vtype,
    "candidate_ranking": ranked,
    "rejected_candidates": rejected,
    "accepted_candidate": accepted or None,
    "value": val,
    "sources": srcs,
    "final_proposal": proposal,
  }


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
