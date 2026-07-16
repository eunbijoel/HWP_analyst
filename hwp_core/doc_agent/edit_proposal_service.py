"""수정 제안 생성 — Evidence Fill 우선, 없으면 Context Fill 폴백."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .document_inspector import FACT_CONCEPTS, TABLE_CONCEPTS, TEXT_CONCEPTS, get_fill_resolver
from .fill_trace import (
  STATUS_PROPOSED,
  STATUS_SKIPPED_NO_EVIDENCE,
  STATUS_SKIPPED_UNSAFE,
  FieldFillTrace,
)
from .workspace_retriever import (
  excel_grids_for_insert,
  format_grid_preview,
  resolve_fact_field,
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

_META_INSTRUCTION_RE = re.compile(
  r"기입\s*(?:필요|하십시오|하세요)|작성\s*(?:필요|요망)|입력\s*(?:필요|요망)|"
  r"정보\s*(?:없음|입력)|추후\s*기입|비어\s*있음|해당\s*없음|"
  r"확인되지\s*않|자료에서\s*확인|담당자명\s*기입|회사명\]|"
  r"법인등록번호\s*기입|또는\s*\(작성|또는\s*\(정보",
  re.I,
)
_FACT_LABEL_RE = re.compile(
  r"전화|휴대|메일|이메일|전자우편|주소|등록번호|사업자|법인|"
  r"성명|이름|직위|직급|연락처|팩스|대표자|책임자|기관명|소재지",
  re.I,
)


def _is_meta_instruction(text: str) -> bool:
  t = (text or "").strip()
  if not t:
    return True
  if _META_INSTRUCTION_RE.search(t):
    return True
  if t.startswith("[") and ("필요" in t or "기입" in t):
    return True
  if t.startswith("(") and any(k in t for k in ("필요", "기입", "요망", "없음", "비어")):
    return True
  return False


def _is_fact_field(label: str, concept_id: str = "") -> bool:
  """사실 칸 — Evidence only."""
  if concept_id in TEXT_CONCEPTS:
    return False
  if concept_id in FACT_CONCEPTS or concept_id == "form_blank":
    return True
  if bool((label or "") and _FACT_LABEL_RE.search(label)):
    return True
  return False


def _value_fits_fact_field(label: str, value: str, concept_id: str = "") -> bool:
  """사실 칸 값이 concept expected type에 맞는지."""
  from .value_type_validation import value_fits_type
  return value_fits_type(concept_id, label, value)


def _is_weak_draft(text: str, label: str = "") -> bool:
  t = (text or "").strip()
  lab = (label or "").strip()
  if not t:
    return True
  if _is_meta_instruction(t):
    return True
  if t in ("□", "○", "-", "—"):
    return True
  if lab and (t == lab or (len(t) <= len(lab) + 2 and lab in t)):
    return True
  # narrative drafts need a bit of substance; short fact values handled separately
  if len(t) < 8 and not _is_fact_field(lab):
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
    "2~5문장. 근거에 없으면 아무 것도 출력하지 마세요 (빈 응답).\n"
    "'기입 필요'·'작성 필요'·'정보 없음' 같은 안내문은 쓰지 마세요.\n\n"
    f"근거:\n{evidence}\n\n초안:"
  )
  result = generate(prompt, model, ollama_url, temperature=0.2, num_predict=800, timeout=120)
  if result.get("error"):
    return _draft_from_sources(label, concept_id, hits)
  text = (result.get("text") or "").strip()
  if not text or _is_weak_draft(text, label):
    return _draft_from_sources(label, concept_id, hits)
  return text, 0.75


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
    "없는 숫자·고유명사·연락처는 지어내지 마세요. 근거가 부족하면 빈 응답만 하세요.\n"
    "'기입 필요'·'작성 필요'·'정보 없음'·안내문·홍보 문구는 금지입니다.\n"
    "초안 본문만 출력하세요 (표시 문구·따옴표 금지).\n\n"
    f"문서 맥락:\n{blob[:3500]}\n\n초안:"
  )
  result = generate(prompt, model, ollama_url, temperature=0.35, num_predict=700, timeout=120)
  if result.get("error"):
    return "", 0.0
  text = (result.get("text") or "").strip()
  if not text or _is_weak_draft(text, label) or _is_meta_instruction(text):
    return "", 0.0
  return text, 0.45


_CONCEPT_KEYS = {
  "rd_objective": ("목표", "목적"),
  "expected_effect": ("효과", "성과", "파급"),
  "rd_necessity": ("필요", "배경", "추진"),
  "rd_content": ("연구내용", "개발내용", "내용"),
  "rd_strategy": ("전략",),
  "utilization": ("활용",),
}


def _text_matches_concept(text: str, concept_id: str) -> bool:
  keys = _CONCEPT_KEYS.get(concept_id or "", ())
  if not keys:
    return True
  return any(k in (text or "") for k in keys)


def _text_matches_other_narrative(text: str, concept_id: str) -> bool:
  """다른 글 섹션 키워드가 더 분명하면 True (교차 오염 방지)."""
  t = text or ""
  own = _CONCEPT_KEYS.get(concept_id or "", ())
  for other, keys in _CONCEPT_KEYS.items():
    if other == concept_id:
      continue
    if any(k in t for k in keys) and not any(k in t for k in own):
      return True
  return False


def _context_draft_heuristic(label: str, concept_id: str, ctx: dict, hits: list[dict]) -> tuple[str, float]:
  # 1) concept에 맞는 검색 hit 우선
  draft, conf = _draft_from_sources(label, concept_id, hits)
  if draft and not _is_weak_draft(draft, label) and not _text_matches_other_narrative(draft, concept_id):
    return draft, min(conf, 0.5)

  bits: list[str] = []
  for h in hits:
    t = (h.get("text") or "").strip()
    if not t or _is_weak_draft(t, label):
      continue
    if _text_matches_other_narrative(t, concept_id):
      continue
    if concept_id and not _text_matches_concept(t, concept_id):
      continue
    bits.append(t)

  # 2) 주변 문단은 concept 키워드가 맞을 때만
  title = (ctx.get("title") or "").strip()
  section = (ctx.get("section_title") or label or "").strip()
  for line in ctx.get("surrounding") or []:
    body = re.sub(r"^\[문단 \d+\]\s*", "", line).strip()
    if not body or body in (title, section) or _is_weak_draft(body, label):
      continue
    if _text_matches_other_narrative(body, concept_id):
      continue
    if concept_id and not _text_matches_concept(body, concept_id):
      continue
    bits.append(body)

  if not bits:
    return "", 0.0
  joined = " ".join(bits[0].split()[:60])
  if _is_weak_draft(joined, label) or _text_matches_other_narrative(joined, concept_id):
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
  if not concept_id:
    return hits
  preferred, rest = [], []
  for h in hits:
    t = h.get("text") or ""
    if _text_matches_other_narrative(t, concept_id):
      continue
    if _text_matches_concept(t, concept_id):
      preferred.append(h)
    else:
      rest.append(h)
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
  # 사실 칸(전화·주소·성명 등)은 Context로 지어내지 않음
  if _is_fact_field(label, cid) or short:
    return "", 0.0, []
  ctx = gather_target_context(workspace, field)
  hits = _prefer_concept_hits(_context_hits_from_target(workspace, field, cid), cid)
  after, conf = "", 0.0
  if use_llm:
    after, conf = _llm_context_draft(
      label, cid, ctx, hits, model=model, ollama_url=ollama_url, short=False,
    )
    if after and _text_matches_other_narrative(after, cid):
      after, conf = "", 0.0
  if not after or _is_weak_draft(after, label):
    after, conf = _context_draft_heuristic(label, cid, ctx, hits)
  if not after or _is_weak_draft(after, label):
    return "", 0.0, []
  if _text_matches_other_narrative(after, cid):
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
) -> tuple[list[EditProposal], list[dict], list[FieldFillTrace]]:
  by_id = {f["field_id"]: f for f in fields}
  proposals: list[EditProposal] = []
  skipped_facts: list[dict] = []
  fill_traces: list[FieldFillTrace] = []
  refs = workspace.list_references()
  target_doc = workspace.get_target()
  target_name = target_doc.filename if target_doc else ""
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
      if _is_weak_draft(after, f.get("label") or ""):
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
      is_form = (
        cid == "form_blank"
        or bool((f.get("style") or {}).get("form"))
        or bool((f.get("style") or {}).get("factual"))
        or (cid in FACT_CONCEPTS - TABLE_CONCEPTS)
      )

      # 서식 사실 칸: Evidence only (동의어·전 참고문서 검색)
      if is_form:
        lab = (f.get("label") or "").strip()
        try_key = f"{cid}:{lab}"
        if not lab or try_key in form_labels_tried:
          continue
        form_labels_tried.add(try_key)
        gr = get_fill_resolver().ground(lab, f.get("context") or "")
        loc = {
          "field_type": f.get("field_type") or "table_cell",
          "table_id": f.get("table_id"),
          "row": f.get("row"),
          "column": f.get("column"),
          "paragraph_id": f.get("paragraph_id"),
          "context": f.get("context") or "",
        }
        resolved = resolve_fact_field(refs, label=lab, concept_id=cid) if refs else {
          "expected_value_type": "",
          "candidate_ranking": [],
          "rejected_candidates": [],
          "accepted_candidate": None,
          "value": "",
          "sources": [],
          "final_proposal": None,
        }
        val = resolved.get("value") or ""
        srcs = resolved.get("sources") or []
        ranked = resolved.get("candidate_ranking") or []
        rejected = resolved.get("rejected_candidates") or []
        accepted = resolved.get("accepted_candidate")
        vtype = resolved.get("expected_value_type") or ""

        if not val:
          if ranked:
            status = STATUS_SKIPPED_UNSAFE
            reason = "모든 후보가 타입 검증에서 거절됨"
          else:
            status = STATUS_SKIPPED_NO_EVIDENCE
            reason = "참고 자료에서 근거를 찾지 못해 비워 둠"
        else:
          status = STATUS_PROPOSED
          reason = ""

        fill_traces.append(FieldFillTrace(
          target_document=target_name,
          location=loc,
          raw_label=lab,
          concept_id=cid,
          grounding_confidence=float(
            f.get("concept_confidence")
            or (gr.confidence if gr.grounded else 0.0)
          ),
          grounding_method=str(
            f.get("grounding_method")
            or (gr.method if gr.grounded else "none")
          ),
          expected_value_type=vtype,
          candidate_ranking=ranked,
          rejected_candidates=rejected,
          accepted_candidate=accepted,
          final_proposal=resolved.get("final_proposal"),
          candidates=ranked,
          selected_value=str(val).strip() if val else None,
          final_status=status,
          notes=[reason] if reason else [],
        ))

        if status != STATUS_PROPOSED:
          skipped_facts.append({
            "label": lab or cid,
            "concept_id": cid,
            "reason": reason,
          })
          continue
        proposals.append(EditProposal(
          proposal_id=f"p_{uuid.uuid4().hex[:8]}",
          field_id=fid,
          action="write_table_cell",
          before=f.get("current_value") or "",
          after=str(val).strip(),
          sources=srcs[:4],
          confidence=0.9,
          location=f.get("context") or lab,
          label=lab,
          concept_id=cid,
          meta={
            "table_id": f.get("table_id"),
            "row": f.get("row"),
            "column": f.get("column"),
            "fill_mode": FILL_EVIDENCE,
            "expected_value_type": vtype,
            "accepted_rank": (accepted or {}).get("rank"),
          },
        ))
        continue

      # 인건비형 표: 엑셀 행 매핑. 근거 없으면 비움.
      key = (int(f.get("table_id") or 0), int(f.get("row") or 0))
      pi = row_person.get(key, 0)
      if pi >= len(people):
        lab = (f.get("label") or "").strip()
        skipped_facts.append({
          "label": lab or cid,
          "concept_id": cid,
          "reason": "인력/엑셀 근거가 없어 비워 둠",
        })
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

  return proposals, skipped_facts, fill_traces
