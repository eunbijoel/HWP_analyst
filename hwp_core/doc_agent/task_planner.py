"""자연어 명령 → 제한된 JSON 계획 (schema 검증)."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .document_inspector import TABLE_CONCEPTS, TEXT_CONCEPTS, EditableField
from ..llm_client import generate_json

ALLOWED_ACTIONS = {"fill_paragraph", "fill_table", "insert_after", "skip"}
ALLOWED_TASKS = {"fill_document"}


def _rule_plan(
  command: str,
  fields: list[dict],
  target_document_id: str,
) -> dict:
  """LLM 없이 키워드로 필드 필터링."""
  cmd = command or ""
  want_text = any(k in cmd for k in ("작성", "채우", "채워", "목표", "효과", "필요", "참고", "양식", "넣어"))
  want_table = any(k in cmd for k in ("표", "인건비", "엑셀", "excel", "인력", "현황"))
  # 「참고 자료로 채워줘」처럼 일반 요청이면 글·표 둘 다
  if any(k in cmd for k in ("참고", "자료로", "채워", "채우", "넣어")):
    want_text = True
    want_table = True
  # 둘 다 없으면 전부 채우기
  if not want_text and not want_table:
    want_text = want_table = True

  focus_concepts: set[str] = set()
  if "목표" in cmd:
    focus_concepts.add("rd_objective")
  if "효과" in cmd:
    focus_concepts.add("expected_effect")
  if "필요" in cmd:
    focus_concepts.add("rd_necessity")
  if any(k in cmd for k in ("인건비", "인력", "성명", "참여")):
    focus_concepts |= TABLE_CONCEPTS

  steps = []
  for f in fields:
    cid = f.get("concept_id") or ""
    ftype = f.get("field_type")
    if ftype in ("paragraph", "insert_after"):
      if not want_text:
        continue
      if focus_concepts and cid and cid not in focus_concepts and cid in TEXT_CONCEPTS:
        # 특정 글만 요청했는데 해당 concept 아니면 skip
        if not (focus_concepts & TEXT_CONCEPTS) or cid not in focus_concepts:
          continue
      action = "insert_after" if ftype == "insert_after" else "fill_paragraph"
      steps.append({
        "field_id": f["field_id"],
        "action": action,
        "required_concept": cid or None,
      })
    elif ftype == "table_cell":
      if not want_table:
        continue
      steps.append({
        "field_id": f["field_id"],
        "action": "fill_table",
        "required_concepts": [cid] if cid else list(TABLE_CONCEPTS),
      })

  # table cells: group by row into one step conceptually — keep per-cell for apply precision
  return {
    "task": "fill_document",
    "target_document_id": target_document_id,
    "steps": steps,
    "planner": "rules",
  }


def _validate_plan(plan: dict, field_ids: set[str]) -> tuple[dict, str]:
  if not isinstance(plan, dict):
    return {}, "계획이 dict가 아닙니다"
  task = plan.get("task")
  if task not in ALLOWED_TASKS:
    return {}, f"허용되지 않은 task: {task}"
  steps_in = plan.get("steps") or []
  if not isinstance(steps_in, list):
    return {}, "steps가 리스트가 아닙니다"
  clean = []
  for s in steps_in:
    if not isinstance(s, dict):
      continue
    action = s.get("action")
    if action not in ALLOWED_ACTIONS:
      continue
    fid = s.get("field_id")
    if not fid or fid not in field_ids:
      continue
    if action == "skip":
      continue
    entry = {"field_id": fid, "action": action}
    if s.get("required_concept"):
      entry["required_concept"] = s["required_concept"]
    if s.get("required_concepts"):
      entry["required_concepts"] = list(s["required_concepts"])
    clean.append(entry)
  out = {
    "task": "fill_document",
    "target_document_id": plan.get("target_document_id") or "",
    "steps": clean,
    "planner": plan.get("planner") or "validated",
  }
  return out, ""


def plan_fill_task(
  command: str,
  fields: list[dict],
  target_document_id: str,
  *,
  use_llm: bool = False,
  model: str = "gemma3:4b",
  ollama_url: str = "http://localhost:11434",
) -> dict:
  field_ids = {f["field_id"] for f in fields}
  base = _rule_plan(command, fields, target_document_id)

  if not use_llm:
    plan, err = _validate_plan(base, field_ids)
    if err:
      return {"ok": False, "error": err, "plan": base}
    return {"ok": True, "error": "", "plan": plan}

  catalog = [
    {
      "field_id": f["field_id"],
      "field_type": f["field_type"],
      "label": f.get("label"),
      "concept_id": f.get("concept_id"),
    }
    for f in fields
  ]
  prompt = (
    "다음 사용자 명령과 빈 항목 목록을 JSON 계획으로 변환하세요.\n"
    '형식: {"task":"fill_document","target_document_id":"'
    + target_document_id
    + '","steps":[{"field_id":"...","action":"fill_paragraph|fill_table|insert_after|skip","required_concept":"..."}]}\n'
    "허용 action만 사용. 목록에 없는 field_id 금지.\n"
    f"명령: {command}\n항목: {json.dumps(catalog, ensure_ascii=False)}\n"
  )
  parsed, err = generate_json(prompt, model, ollama_url, temperature=0.1, num_predict=1500)
  if err or not parsed:
    plan, verr = _validate_plan(base, field_ids)
    return {"ok": True, "error": f"LLM 계획 실패→규칙 사용: {err}", "plan": plan}

  if isinstance(parsed, list):
    parsed = {"task": "fill_document", "target_document_id": target_document_id, "steps": parsed}
  parsed.setdefault("target_document_id", target_document_id)
  parsed["planner"] = "llm"
  plan, verr = _validate_plan(parsed, field_ids)
  if verr or not plan.get("steps"):
    plan, _ = _validate_plan(base, field_ids)
    return {"ok": True, "error": f"LLM 계획 무효→규칙 사용: {verr}", "plan": plan}
  return {"ok": True, "error": "", "plan": plan}
