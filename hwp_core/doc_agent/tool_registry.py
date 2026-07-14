"""제한된 Tool Registry — LLM이 파일을 직접 수정하지 않음."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import document_editor as doc_ed
from . import document_inspector as insp
from . import edit_proposal_service as prop_svc
from . import edit_verifier as verifier
from . import task_planner as planner
from . import workspace_retriever as retriever
from .workspace_service import WorkspaceService

ALLOWED_TOOLS = {
  "list_workspace_files",
  "inspect_target_document",
  "find_empty_fields",
  "search_reference_materials",
  "read_excel_range",
  "generate_paragraph_draft",
  "propose_table_mapping",
  "apply_approved_edits",
  "verify_edits",
  "export_document",
}


@dataclass
class ToolResult:
  ok: bool
  tool: str
  data: Any = None
  error: str = ""

  def to_dict(self) -> dict:
    return {"ok": self.ok, "tool": self.tool, "data": self.data, "error": self.error}


@dataclass
class ToolRegistry:
  workspace: WorkspaceService
  logs: list[dict] = field(default_factory=list)
  last_fields: list[dict] = field(default_factory=list)
  last_plan: dict = field(default_factory=dict)
  last_proposals: list[dict] = field(default_factory=list)
  last_edited_bytes: bytes | None = None
  last_verify: list[dict] = field(default_factory=list)
  job_id: str = ""

  def _log(self, tool: str, ok: bool, detail: str = "") -> None:
    self.logs.append({"tool": tool, "ok": ok, "detail": detail[:500]})

  def call(self, tool_name: str, **kwargs) -> ToolResult:
    if tool_name not in ALLOWED_TOOLS:
      self._log(tool_name, False, "forbidden")
      return ToolResult(False, tool_name, error=f"허용되지 않은 tool: {tool_name}")
    try:
      fn = getattr(self, f"_tool_{tool_name}")
      result = fn(**kwargs)
      self._log(tool_name, result.ok, result.error or "ok")
      return result
    except Exception as e:
      self._log(tool_name, False, str(e))
      return ToolResult(False, tool_name, error=f"{e}\n{traceback.format_exc()[-400:]}")

  def _tool_list_workspace_files(self) -> ToolResult:
    return ToolResult(True, "list_workspace_files", data=self.workspace.list_workspace_files())

  def _tool_inspect_target_document(self) -> ToolResult:
    target = self.workspace.get_target()
    if not target:
      return ToolResult(False, "inspect_target_document", error="대상 문서 없음")
    data = insp.inspect_document(
      target.file_bytes, document_id=target.document_id, filename=target.filename,
    )
    if data.get("ok"):
      self.last_fields = data.get("fields") or []
    return ToolResult(bool(data.get("ok")), "inspect_target_document", data=data, error=data.get("error") or "")

  def _tool_find_empty_fields(self) -> ToolResult:
    if self.last_fields:
      return ToolResult(True, "find_empty_fields", data=self.last_fields)
    r = self._tool_inspect_target_document()
    if not r.ok:
      return ToolResult(False, "find_empty_fields", error=r.error)
    return ToolResult(True, "find_empty_fields", data=self.last_fields)

  def _tool_search_reference_materials(
    self,
    query: str = "",
    concept_id: str = "",
    field_type: str = "paragraph",
    required_concepts: list | None = None,
  ) -> ToolResult:
    hits = retriever.search_references(
      self.workspace,
      query,
      concept_id=concept_id,
      field_type=field_type,
      required_concepts=required_concepts,
    )
    return ToolResult(True, "search_reference_materials", data=hits)

  def _tool_read_excel_range(self, document_id: str = "", sheet: str = "") -> ToolResult:
    docs = self.workspace.list_references()
    out = []
    for d in docs:
      if document_id and d.document_id != document_id:
        continue
      for sh in d.excel_sheets:
        if sheet and sh.get("name") != sheet:
          continue
        rows = sh.get("rows") or []
        out.append({
          "document": d.filename,
          "sheet": sh.get("name"),
          "rows": rows[:50],
          "n_rows": len(rows),
        })
    return ToolResult(True, "read_excel_range", data=out)

  def _tool_generate_paragraph_draft(
    self,
    command: str = "",
    use_llm: bool = False,
    model: str = "gemma4",
    ollama_url: str = "http://localhost:11434",
  ) -> ToolResult:
    target = self.workspace.get_target()
    if not target:
      return ToolResult(False, "generate_paragraph_draft", error="대상 문서 없음")
    if not self.last_fields:
      self._tool_find_empty_fields()
    plan_res = planner.plan_fill_task(
      command or "참고 자료를 이용해 빈 항목을 작성해줘",
      self.last_fields,
      target.document_id,
      use_llm=use_llm,
      model=model,
      ollama_url=ollama_url,
    )
    if not plan_res.get("ok"):
      return ToolResult(False, "generate_paragraph_draft", error=plan_res.get("error") or "plan fail")
    self.last_plan = plan_res["plan"]
    proposals = prop_svc.build_proposals(
      self.last_plan,
      self.last_fields,
      self.workspace,
      use_llm=use_llm,
      model=model,
      ollama_url=ollama_url,
      command=command or "",
    )
    self.last_proposals = [p.to_dict() for p in proposals]
    return ToolResult(True, "generate_paragraph_draft", data={
      "plan": self.last_plan,
      "proposals": self.last_proposals,
      "plan_note": plan_res.get("error") or "",
    })

  def _tool_propose_table_mapping(
    self,
    hwpx_headers: list | None = None,
    excel_headers: list | None = None,
  ) -> ToolResult:
    hwpx_headers = hwpx_headers or []
    excel_headers = excel_headers or []
    if not excel_headers:
      for d in self.workspace.list_references():
        for sh in d.excel_sheets:
          rows = sh.get("rows") or []
          if rows:
            excel_headers = [str(c) for c in rows[0]]
            break
    if not hwpx_headers:
      target = self.workspace.get_target()
      if target:
        from ..hwpx_editor import HWPXEditor
        ed = HWPXEditor(target.file_bytes)
        if ed.get_table_count():
          rows = ed.get_table_as_rows(0)
          if rows:
            hwpx_headers = [str(c) for c in rows[0]]
    data = retriever.propose_table_mapping(hwpx_headers, excel_headers)
    return ToolResult(True, "propose_table_mapping", data=data)

  def _tool_apply_approved_edits(self, approved_ids: list | None = None) -> ToolResult:
    target = self.workspace.get_target()
    if not target:
      return ToolResult(False, "apply_approved_edits", error="대상 문서 없음")
    if not self.last_proposals:
      return ToolResult(False, "apply_approved_edits", error="제안 없음")
    ids = set(approved_ids or [])
    # status approved도 포함
    for p in self.last_proposals:
      if p.get("status") == "approved":
        ids.add(p["proposal_id"])
    if not ids:
      return ToolResult(False, "apply_approved_edits", error="승인된 제안 없음")
    result = doc_ed.apply_proposals(
      target.file_bytes,
      self.last_proposals,
      approved_ids=ids,
    )
    self.last_edited_bytes = result.get("edited_bytes")
    self.job_id = result.get("job_id") or ""
    self.last_proposals = result.get("proposals") or self.last_proposals
    return ToolResult(bool(result.get("ok")) or bool(result.get("log", {}).get("applied")),
                      "apply_approved_edits", data=result,
                      error="" if result.get("log", {}).get("applied") else "적용된 항목 없음")

  def _tool_verify_edits(self) -> ToolResult:
    if not self.last_edited_bytes:
      return ToolResult(False, "verify_edits", error="수정본 없음")
    results = verifier.verify_applied_changes(self.last_edited_bytes, self.last_proposals)
    self.last_verify = results
    validation = verifier.validate_edited_document(self.last_edited_bytes)
    return ToolResult(True, "verify_edits", data={"checks": results, "validation": validation})

  def _tool_export_document(self) -> ToolResult:
    if not self.last_edited_bytes:
      return ToolResult(False, "export_document", error="수정본 없음")
    target = self.workspace.get_target()
    name = (target.filename if target else "document").rsplit(".", 1)[0] + "_filled.hwpx"
    return ToolResult(True, "export_document", data={
      "filename": name,
      "bytes": self.last_edited_bytes,
      "job_id": self.job_id,
      "size": len(self.last_edited_bytes),
    })
