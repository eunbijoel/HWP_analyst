"""수정 제안 생성 — Evidence Fill 우선, 없으면 Context Fill 폴백."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .document_inspector import TABLE_CONCEPTS, TEXT_CONCEPTS, get_fill_resolver
from .workspace_retriever import (
  excel_grids_for_insert,
  format_grid_preview,
  lookup_label_value_in_refs,
  search_excel_rows,
  search_paragraphs,
  search_tables,
)
from .workspace_service import WorkspaceDocument, WorkspaceService
from ..llm_client import generate

# Proposal review label — never auto-written into the document body.
AI_DRAFT_MARKER = "AI Draft (Generated from current document context)"
FILL_EVIDENCE = "evidence"
FILL_CONTEXT = "context"


@dataclass
class EditProposal:
  proposal_id: str
  field_id: str
  action: str  # replace_paragraph | write_table_cell | insert_after | insert_table
  before: str
  after: str
  sources: list[dict] = field(default_factory=list)
  confidence: float = 0.0
  status: str = "pending"  # pending | approved | rejected | applied | failed
  location: str = ""
  label: str = ""
  concept_id: str = ""
  meta: dict = field(default_factory=dict)

  def to_dict(self) -> dict:
    return asdict(self)


def _proposal_from_excel_grid(
  *,
  field_id: str,
  label: str,
  para_id: int,
  grid_info: dict,
  location: str = "",
  concept_id: str = "",
) -> EditProposal:
  rows = grid_info.get("rows") or []
  preview = format_grid_preview(rows)
  doc = grid_info.get("document") or ""
  sheet = grid_info.get("sheet") or ""
  return EditProposal(
    proposal_id=f"p_{uuid.uuid4().hex[:8]}",
    field_id=field_id,
    action="insert_table",
    before="",
    after=preview,
    sources=[{
      "document": doc,
      "source_type": "excel_table",
      "location": f"{sheet} ({len(rows)}행)",
    }],
    confidence=0.7,
    location=location or label or "참고 표",
    label=label or f"{doc} 표",
    concept_id=concept_id,
    meta={
      "paragraph_id": para_id,
      "field_type": "insert_table",
      "table_rows": rows,
      "source_document": doc,
      "source_sheet": sheet,
      "fill_mode": FILL_EVIDENCE,
    },
  )


def _is_weak_draft(text: str, label: str = "") -> bool:
  t = (text or "").strip()
  lab = (label or "").strip()
  if len(t) < 8:
    return True
  if t in ("□", "○", "-", "—", "해당 없음", "자료에서 확인되지 않음"):
    return True
  if lab and (t == lab or (len(t) <= len(lab) + 2 and lab in t)):
    return True
  return False


def _draft_from_sources(label: str, concept_id: str, hits: list[dict]) -> tuple[str, float]:
  if not hits:
    return "", 0.0
  bodies = []
  lab = ""
  if concept_id and get_fill_resolver().concepts.get(concept_id):
    lab = get_fill_resolver().concepts[concept_id].label_ko
  lab = lab or label
  for h in hits:
    t = (h.get("text") or "").strip()
    if not t or _is_weak_draft(t, lab):
      continue
    if lab and (t == lab or (len(t) < 30 and lab in t)):
      continue
    bodies.append(t)
  if not bodies:
    return "", 0.0
  text = bodies[0]
  parts = re.split(r"(?<=[.。!?？])\s+|\n+", text)
  parts = [p.strip() for p in parts if p.strip() and not _is_weak_draft(p, lab)]
  draft = " ".join(parts[:4]) if parts else text[:500]
  if _is_weak_draft(draft, lab):
    return "", 0.0
  conf = min(0.95, 0.55 + 0.1 * len(hits) + float(hits[0].get("score") or 0) * 0.2)
  return draft.strip(), conf


def _llm_draft(
  label: str,
  concept_id: str,
  hits: list[dict],
  *,
  model: str,
  ollama_url: str,
) -> tuple[str, float]:
  evidence = "\n\n".join(
    f"[{h.get('document')} {h.get('location')}]\n{h.get('text','')[:800]}"
    for h in hits[:4]
  )
  if not evidence.strip():
    return "", 0.0
  prompt = (
    f"항목 '{label}'({concept_id}) 초안을 한국어로 작성하세요.\n"
    "반드시 아래 근거만 사용하고, 숫자·사실을 지어내지 마세요.\n"
    "2~5문장. 근거에 없으면 짧게 '자료에서 확인되지 않음'만 쓰세요.\n\n"
    f"근거:\n{evidence}\n\n초안:"
  )
  result = generate(prompt, model, ollama_url, temperature=0.2, num_predict=800, timeout=120)
  if result.get("error"):
    return _draft_from_sources(label, concept_id, hits)
  text = (result.get("text") or "").strip()
  if not text:
    return _draft_from_sources(label, concept_id, hits)
  return text, 0.75


def _target_paragraphs(target: Optional[WorkspaceDocument]) -> list[str]:
  if not target or target.parsed is None:
    return []
  return [str(p).strip() for p in (getattr(target.parsed, "paragraphs", None) or []) if str(p).strip()]


def gather_target_context(
  workspace: WorkspaceService,
  field: dict,
) -> dict[str, Any]:
  """Current-document context for Context Fill (title, outline, neighbors, tables)."""
  target = workspace.get_target()
  paras = _target_paragraphs(target)
  title = paras[0] if paras else (target.filename if target else "")
  outline = [p for p in paras if len(p) <= 48][:20]
  pid = field.get("paragraph_id")
  surrounding: list[str] = []
  if pid is not None and paras:
    i = int(pid)
    for j in range(max(0, i - 3), min(len(paras), i + 4)):
      if j == i:
        continue
      surrounding.append(f"[문단 {j + 1}] {paras[j][:400]}")
  label = (field.get("label") or field.get("context") or "").strip()
  nearby_tables: list[str] = []
  if target:
    for h in search_tables([target], f"{label} {field.get('concept_id') or ''}", limit=3):
      nearby_tables.append(f"[{h.location}] {h.text[:500]}")
  return {
    "title": title,
    "outline": outline,
    "section_title": field.get("context") or field.get("anchor_label") or label,
    "field_label": label,
    "surrounding": surrounding,
    "nearby_tables": nearby_tables,
    "filename": target.filename if target else "",
  }


def _context_hits_from_target(
  workspace: WorkspaceService,
  field: dict,
  concept_id: str,
) -> list[dict]:
  """Reuse reference search over the target document itself."""
  target = workspace.get_target()
  if not target:
    return []
  query = f"{field.get('label', '')} {field.get('context', '')} {concept_id}"
  hits = search_paragraphs([target], query, concept_id=concept_id, limit=6)
  skip_pid = field.get("paragraph_id")
  out: list[dict] = []
  for h in hits:
    loc = h.location or ""
    if skip_pid is not None and loc == f"문단 {int(skip_pid) + 1}":
      continue
    # Skip pure section labels / placeholders
    t = (h.text or "").strip()
    if len(t) <= 2 or t in ("□", "○", "-", "—"):
      continue
    d = h.to_dict()
    d["document"] = target.filename
    d["source_type"] = "context_paragraph"
    out.append(d)
  for h in search_tables([target], query, limit=3):
    d = h.to_dict()
    d["document"] = target.filename
    d["source_type"] = "context_table"
    out.append(d)
  return out


def _llm_context_draft(
  label: str,
  concept_id: str,
  ctx: dict,
  hits: list[dict],
  *,
  model: str,
  ollama_url: str,
  short: bool = False,
) -> tuple[str, float]:
  parts = [
    f"문서 제목: {ctx.get('title') or ''}",
    f"파일: {ctx.get('filename') or ''}",
    f"섹션/라벨: {ctx.get('section_title') or label}",
    "목차:\n- " + "\n- ".join(ctx.get("outline") or [])[:800],
  ]
  if ctx.get("surrounding"):
    parts.append("주변 문단:\n" + "\n".join(ctx["surrounding"][:6]))
  if ctx.get("nearby_tables"):
    parts.append("근처 표:\n" + "\n".join(ctx["nearby_tables"][:3]))
  if hits:
    parts.append(
      "문서 내 관련 내용:\n"
      + "\n\n".join(f"[{h.get('location')}]\n{(h.get('text') or '')[:600]}" for h in hits[:4])
    )
  blob = "\n\n".join(p for p in parts if p and str(p).strip())
  if not blob.strip():
    return "", 0.0
  length = "한 줄 짧은 값" if short else "2~5문장"
  prompt = (
    f"현재 문서만 보고 빈 항목 '{label}'({concept_id or 'general'}) 초안을 한국어로 작성하세요.\n"
    f"{length}. 참고 파일이 없으므로 문서 맥락·라벨·주변 문단에서 추론하세요.\n"
    "없는 숫자·고유명사는 지어내지 말고, 추론이면 일반적인 표현을 쓰세요.\n"
    "초안 본문만 출력하세요 (표시 문구·따옴표 금지).\n\n"
    f"문서 맥락:\n{blob[:3500]}\n\n초안:"
  )
  result = generate(prompt, model, ollama_url, temperature=0.35, num_predict=700, timeout=120)
  if result.get("error"):
    return "", 0.0
  text = (result.get("text") or "").strip()
  if not text or "확인되지 않" in text:
    return "", 0.0
  return text, 0.45


def _context_draft_heuristic(label: str, concept_id: str, ctx: dict, hits: list[dict]) -> tuple[str, float]:
  draft, conf = _draft_from_sources(label, concept_id, hits)
  if draft and not _is_weak_draft(draft, label):
    return draft, min(conf, 0.5)
  bits = []
  title = (ctx.get("title") or "").strip()
  section = (ctx.get("section_title") or label or "").strip()
  for line in ctx.get("surrounding") or []:
    body = re.sub(r"^\[문단 \d+\]\s*", "", line).strip()
    if body and not _is_weak_draft(body, label) and body not in (title, section):
      bits.append(body)
  for h in hits:
    t = (h.get("text") or "").strip()
    if t and not _is_weak_draft(t, label):
      bits.append(t)
  if not bits:
    return "", 0.0
  joined = " ".join(bits[0].split()[:60])
  if _is_weak_draft(joined, label):
    return "", 0.0
  return joined, 0.35


def _make_context_sources(ctx: dict, hits: list[dict]) -> list[dict]:
  sources = [{
    "document": AI_DRAFT_MARKER,
    "source_type": "context",
    "location": ctx.get("section_title") or ctx.get("field_label") or ctx.get("title") or "현재 문서",
  }]
  for h in hits[:3]:
    sources.append({
      "document": h.get("document") or ctx.get("filename") or "현재 문서",
      "source_type": h.get("source_type") or "context_paragraph",
      "location": h.get("location") or "",
    })
  return sources


def _prefer_concept_hits(hits: list[dict], concept_id: str) -> list[dict]:
  keys = {
    "rd_objective": ("목표", "목적"),
    "expected_effect": ("효과", "성과", "파급"),
    "rd_necessity": ("필요", "배경", "추진"),
  }.get(concept_id or "", ())
  if not keys:
    return hits
  preferred, rest = [], []
  for h in hits:
    t = h.get("text") or ""
    (preferred if any(k in t for k in keys) else rest).append(h)
  return preferred + rest


def _context_fill_text(
  field: dict,
  workspace: WorkspaceService,
  *,
  use_llm: bool,
  model: str,
  ollama_url: str,
  short: bool = False,
) -> tuple[str, float, list[dict]]:
  cid = field.get("concept_id") or ""
  label = field.get("label") or field.get("context") or ""
  ctx = gather_target_context(workspace, field)
  hits = _prefer_concept_hits(_context_hits_from_target(workspace, field, cid), cid)
  after, conf = "", 0.0
  if use_llm:
    after, conf = _llm_context_draft(
      label, cid, ctx, hits, model=model, ollama_url=ollama_url, short=short,
    )
  if not after or _is_weak_draft(after, label):
    after, conf = _context_draft_heuristic(label, cid, ctx, hits)
  if not after or _is_weak_draft(after, label):
    return "", 0.0, []
  if short:
    after = after.split("\n")[0].strip()[:80]
    if _is_weak_draft(after, label):
      return "", 0.0, []
  return after, conf, _make_context_sources(ctx, hits)


def build_proposals(
  plan: dict,
  fields: list[dict],
  workspace: WorkspaceService,
  *,
  use_llm: bool = False,
  model: str = "gemma4",
  ollama_url: str = "http://localhost:11434",
  command: str = "",
) -> list[EditProposal]:
  by_id = {f["field_id"]: f for f in fields}
  proposals: list[EditProposal] = []
  refs = workspace.list_references()
  excel_table_added = False
  form_labels_tried: set[str] = set()

  # Excel 행 캐시 (인건비 표 채우기)
  excel_hits = search_excel_rows(refs, required_concepts=list(TABLE_CONCEPTS)) if refs else []
  people: list[dict] = []
  for h in excel_hits:
    rv = (h.meta or {}).get("row_values") or {}
    if rv.get("person_name"):
      people.append({"values": rv, "source": h.to_dict()})

  table_cells = [
    by_id[s["field_id"]]
    for s in plan.get("steps") or []
    if s.get("action") == "fill_table" and s.get("field_id") in by_id
  ]
  row_person: dict[tuple[int, int], int] = {}
  person_i = 0
  for f in sorted(table_cells, key=lambda x: (x.get("table_id") or 0, x.get("row") or 0, x.get("column") or 0)):
    key = (int(f.get("table_id") or 0), int(f.get("row") or 0))
    if key not in row_person:
      row_person[key] = person_i
      person_i += 1

  for step in plan.get("steps") or []:
    fid = step.get("field_id")
    f = by_id.get(fid)
    if not f:
      continue
    action = step.get("action")
    cid = f.get("concept_id") or step.get("required_concept") or ""

    if action in ("fill_paragraph", "insert_after"):
      query = f"{f.get('label','')} {f.get('context','')} {cid}"
      hits = search_paragraphs(refs, query, concept_id=cid, limit=5) if refs else []
      hit_dicts = [h.to_dict() for h in hits]
      fill_mode = FILL_EVIDENCE
      sources: list[dict] = []
      after, conf = "", 0.0

      if hit_dicts:
        if use_llm:
          after, conf = _llm_draft(
            f.get("label") or "", cid, hit_dicts, model=model, ollama_url=ollama_url,
          )
        else:
          after, conf = _draft_from_sources(f.get("label") or "", cid, hit_dicts)
        sources = [{
          "document": h.get("document"),
          "source_type": h.get("source_type"),
          "location": h.get("location"),
        } for h in hit_dicts[:4]]

      # Mode 2: Context Fill — current document only
      if not after:
        after, conf, sources = _context_fill_text(
          f, workspace, use_llm=use_llm, model=model, ollama_url=ollama_url,
        )
        fill_mode = FILL_CONTEXT

      if not after:
        continue

      loc = f.get("context") or f.get("label") or ""
      if fill_mode == FILL_CONTEXT:
        loc = f"{AI_DRAFT_MARKER} · {loc}" if loc else AI_DRAFT_MARKER

      proposals.append(EditProposal(
        proposal_id=f"p_{uuid.uuid4().hex[:8]}",
        field_id=fid,
        action="insert_after" if action == "insert_after" or f.get("field_type") == "insert_after" else "replace_paragraph",
        before=f.get("current_value") or "",
        after=after,
        sources=sources,
        confidence=conf,
        location=loc,
        label=f.get("label") or "",
        concept_id=cid,
        meta={
          "paragraph_id": f.get("paragraph_id"),
          "field_type": f.get("field_type"),
          "fill_mode": fill_mode,
          "anchor_label": f.get("anchor_label") or f.get("context") or "",
        },
      ))

    elif action == "fill_table":
      # 서식 빈칸: Evidence (참고 라벨 매칭) → Context Fill
      if cid == "form_blank" or (f.get("style") or {}).get("form"):
        lab = (f.get("label") or "").strip()
        if not lab or lab in form_labels_tried:
          continue
        form_labels_tried.add(lab)
        val, srcs = lookup_label_value_in_refs(refs, lab) if refs else ("", [])
        fill_mode = FILL_EVIDENCE
        conf_use = 0.85
        if not val:
          target = workspace.get_target()
          if target:
            val, srcs = lookup_label_value_in_refs([target], lab)
        if not val:
          after, conf, srcs = _context_fill_text(
            f, workspace, use_llm=use_llm, model=model, ollama_url=ollama_url, short=True,
          )
          if not after:
            continue
          val = after
          fill_mode = FILL_CONTEXT
          conf_use = conf

        loc = f.get("context") or lab
        if fill_mode == FILL_CONTEXT:
          loc = f"{AI_DRAFT_MARKER} · {loc}"
        proposals.append(EditProposal(
          proposal_id=f"p_{uuid.uuid4().hex[:8]}",
          field_id=fid,
          action="write_table_cell",
          before=f.get("current_value") or "",
          after=str(val),
          sources=srcs[:4],
          confidence=conf_use,
          location=loc,
          label=lab,
          concept_id=cid,
          meta={
            "table_id": f.get("table_id"),
            "row": f.get("row"),
            "column": f.get("column"),
            "fill_mode": fill_mode,
          },
        ))
        continue

      key = (int(f.get("table_id") or 0), int(f.get("row") or 0))
      pi = row_person.get(key, 0)
      if pi >= len(people):
        # No staff evidence — try same-document label lookup for this concept
        lab = (f.get("label") or "").strip()
        target = workspace.get_target()
        val, srcs = ("", [])
        if target and lab:
          val, srcs = lookup_label_value_in_refs([target], lab)
        if not val:
          continue
        proposals.append(EditProposal(
          proposal_id=f"p_{uuid.uuid4().hex[:8]}",
          field_id=fid,
          action="write_table_cell",
          before=f.get("current_value") or "",
          after=str(val),
          sources=srcs[:4] or _make_context_sources(gather_target_context(workspace, f), []),
          confidence=0.4,
          location=f"{AI_DRAFT_MARKER} · {f.get('context') or lab}",
          label=lab,
          concept_id=cid,
          meta={
            "table_id": f.get("table_id"),
            "row": f.get("row"),
            "column": f.get("column"),
            "fill_mode": FILL_CONTEXT,
          },
        ))
        continue
      person = people[pi]
      val = (person["values"] or {}).get(cid, "")
      if val == "" or val is None:
        continue
      src = person["source"]
      proposals.append(EditProposal(
        proposal_id=f"p_{uuid.uuid4().hex[:8]}",
        field_id=fid,
        action="write_table_cell",
        before=f.get("current_value") or "",
        after=str(val),
        sources=[{
          "document": src.get("document"),
          "source_type": "excel_cell",
          "location": src.get("location"),
        }],
        confidence=0.95,
        location=f.get("context") or "",
        label=f.get("label") or "",
        concept_id=cid,
        meta={
          "table_id": f.get("table_id"),
          "row": f.get("row"),
          "column": f.get("column"),
          "fill_mode": FILL_EVIDENCE,
        },
      ))

  # 참고 Excel이 있으면 표로 1회 삽입 (줄글 요약 금지)
  grids = excel_grids_for_insert(refs) if refs else []
  if grids and not excel_table_added and not any(p.action == "insert_table" for p in proposals):
    target = workspace.get_target()
    para_id = 0
    if target and target.file_bytes:
      try:
        from ..hwpx_editor import HWPXEditor
        paras = HWPXEditor(target.file_bytes).get_paragraphs()
        para_id = 0 if paras else 0
      except Exception:
        para_id = 0
    unmatched_forms = [
      f.get("label") for f in fields
      if (f.get("concept_id") == "form_blank" or (f.get("style") or {}).get("form"))
      and f.get("label")
    ]
    filled = {p.label for p in proposals if p.action == "write_table_cell"}
    unmatched_forms = [x for x in dict.fromkeys(unmatched_forms) if x not in filled]
    prop = _proposal_from_excel_grid(
      field_id="excel_table_insert",
      label=f"{grids[0].get('document', '참고 자료')} 표",
      para_id=para_id,
      grid_info=grids[0],
      location="문서에 표로 삽입",
    )
    if unmatched_forms:
      prop.meta["unmatched_form_labels"] = unmatched_forms[:12]
      prop.after = (
        (prop.after or "")
        + "\n\n※ 아래 서식 칸은 참고자료에 같은 항목이 없어 비워 둠: "
        + ", ".join(unmatched_forms[:8])
      )
    proposals.append(prop)

  return proposals
