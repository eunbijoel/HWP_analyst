"""수정 제안 생성 — 숫자는 코드, 글은 근거 기반(선택 LLM)."""

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
)
from .workspace_service import WorkspaceService
from ..llm_client import generate


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
    },
  )


def _draft_from_sources(label: str, concept_id: str, hits: list[dict]) -> tuple[str, float]:
  if not hits:
    return "", 0.0
  bodies = []
  for h in hits:
    t = (h.get("text") or "").strip()
    if not t:
      continue
    if concept_id and get_fill_resolver().concepts.get(concept_id):
      lab = get_fill_resolver().concepts[concept_id].label_ko
      if t == lab or (len(t) < 30 and lab in t):
        continue
    bodies.append(t)
  if not bodies:
    bodies = [h.get("text", "") for h in hits if h.get("text")]
  if not bodies:
    return "", 0.0
  text = bodies[0]
  parts = re.split(r"(?<=[.。!?？])\s+|\n+", text)
  parts = [p.strip() for p in parts if p.strip()]
  draft = " ".join(parts[:4]) if parts else text[:500]
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
  excel_hits = search_excel_rows(refs, required_concepts=list(TABLE_CONCEPTS))
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
      # 엑셀 예산표를 연구목표 문단에 줄글로 넣지 않음 — 문단 근거가 있을 때만
      query = f"{f.get('label','')} {f.get('context','')} {cid}"
      hits = search_paragraphs(refs, query, concept_id=cid, limit=5)
      hit_dicts = [h.to_dict() for h in hits]
      if not hit_dicts:
        continue
      if use_llm:
        after, conf = _llm_draft(
          f.get("label") or "", cid, hit_dicts, model=model, ollama_url=ollama_url,
        )
      else:
        after, conf = _draft_from_sources(f.get("label") or "", cid, hit_dicts)
      if not after:
        continue
      proposals.append(EditProposal(
        proposal_id=f"p_{uuid.uuid4().hex[:8]}",
        field_id=fid,
        action="insert_after" if action == "insert_after" or f.get("field_type") == "insert_after" else "replace_paragraph",
        before=f.get("current_value") or "",
        after=after,
        sources=[{
          "document": h.get("document"),
          "source_type": h.get("source_type"),
          "location": h.get("location"),
        } for h in hit_dicts[:4]],
        confidence=conf,
        location=f.get("context") or f.get("label") or "",
        label=f.get("label") or "",
        concept_id=cid,
        meta={
          "paragraph_id": f.get("paragraph_id"),
          "field_type": f.get("field_type"),
        },
      ))

    elif action == "fill_table":
      # 서식 빈칸: 라벨로 참고자료에서만 매칭 (없으면 제안 안 함)
      if cid == "form_blank" or (f.get("style") or {}).get("form"):
        lab = (f.get("label") or "").strip()
        if not lab or lab in form_labels_tried:
          continue
        form_labels_tried.add(lab)
        # 참고자료에 같은 항목이 있을 때만 채움 — 가짜/하드코딩 예시 금지
        val, srcs = lookup_label_value_in_refs(refs, lab)
        if not val:
          continue
        proposals.append(EditProposal(
          proposal_id=f"p_{uuid.uuid4().hex[:8]}",
          field_id=fid,
          action="write_table_cell",
          before=f.get("current_value") or "",
          after=str(val),
          sources=srcs[:4],
          confidence=0.85,
          location=f.get("context") or lab,
          label=lab,
          concept_id=cid,
          meta={
            "table_id": f.get("table_id"),
            "row": f.get("row"),
            "column": f.get("column"),
          },
        ))
        continue

      key = (int(f.get("table_id") or 0), int(f.get("row") or 0))
      pi = row_person.get(key, 0)
      if pi >= len(people):
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
        },
      ))

  # 참고 Excel이 있으면 표로 1회 삽입 (줄글 요약 금지)
  grids = excel_grids_for_insert(refs)
  if grids and not excel_table_added and not any(p.action == "insert_table" for p in proposals):
    target = workspace.get_target()
    para_id = 0
    if target and target.file_bytes:
      try:
        from ..hwpx_editor import HWPXEditor
        paras = HWPXEditor(target.file_bytes).get_paragraphs()
        # 문서 맨 앞 제목 바로 뒤에 넣으면 서식 표와 섞여 보임 → 바깥 문단이 있으면 첫 문단 뒤
        para_id = 0 if paras else 0
      except Exception:
        para_id = 0
    unmatched_forms = [
      f.get("label") for f in fields
      if (f.get("concept_id") == "form_blank" or (f.get("style") or {}).get("form"))
      and f.get("label")
    ]
    # 이미 write_table_cell로 채운 라벨 제외
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
