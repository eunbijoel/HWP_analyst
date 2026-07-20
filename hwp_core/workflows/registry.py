"""Named workflow registry — legacy match helpers; Product B uses Completion Planner.

Institution fill is an internal FactFillTool under Completion Planner, not a chat product.
Package path for the planner remains hwp_core.doc_reasoner (import stability).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from . import institution_fill
from .base import WorkflowResult
from .catalog import TASK_CATALOG, get_task_catalog, get_workflow_spec

# Internal runners retained for tests / direct calls. Chat routes via Completion Planner.
_RUNNERS: dict[str, Callable[..., WorkflowResult]] = {
  "fill_institution_info": institution_fill.run,
}

_ROUTE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
  (
    "fill_institution_info",
    re.compile(
      r"(기관\s*정보|기관정보).{0,12}(채우|채워|넣|기입)|"
      r"(채우|채워|넣).{0,12}(기관\s*정보|기관정보)",
      re.I,
    ),
  ),
]


def match_workflow(command: str) -> Optional[str]:
  """Legacy phrase match — Product B maps institution to Completion Planner."""
  t = (command or "").strip()
  if not t:
    return None
  if institution_fill.matches_command(t):
    return "fill_institution_info"
  for wf_id, pat in _ROUTE_PATTERNS:
    if pat.search(t):
      return wf_id
  return None


def run_workflow(
  workflow_id: str,
  pipeline: Any,
  *,
  command: str = "",
  use_llm: bool = False,
  model: str = "gemma4",
  ollama_url: str = "http://localhost:11434",
) -> WorkflowResult:
  # Prefer Completion Planner path for institution so tools stay internal.
  if workflow_id == "fill_institution_info":
    from hwp_core.doc_reasoner import run_completion_planner
    from hwp_core.doc_reasoner.planner import TOOL_FACT_FILL_INSTITUTION

    rr = run_completion_planner(
      pipeline,
      command=command or "이 문서 완성해줘",
      use_llm=use_llm,
      model=model,
      ollama_url=ollama_url,
    )
    return WorkflowResult(
      workflow_id=workflow_id,
      ok=rr.ok,
      target_document=(rr.state.target_filename if rr.state else ""),
      reference_documents=(rr.state.reference_documents if rr.state else []),
      proposals=rr.proposals,
      skipped=rr.skipped,
      fill_trace=rr.fill_trace,
      success_checks={
        "completion_planner_selected_institution_tool": (
          TOOL_FACT_FILL_INSTITUTION in (rr.tools_run or [])
        ),
        # compat key
        "reasoner_selected_institution_tool": (
          TOOL_FACT_FILL_INSTITUTION in (rr.tools_run or [])
        ),
      },
      message=rr.summary or rr.message,
      meta={
        "via": "completion_planner",
        "tools_run": rr.tools_run,
        "plan": rr.plan.to_dict() if rr.plan else {},
        "gap_report": rr.gap_report.to_dict() if rr.gap_report else {},
      },
    )

  runner = _RUNNERS.get(workflow_id)
  if not runner:
    spec = get_workflow_spec(workflow_id)
    return WorkflowResult(
      workflow_id=workflow_id,
      ok=False,
      message=f"워크플로 '{workflow_id}'는 아직 구현되지 않았습니다."
      + (f" ({spec.name_ko})" if spec else ""),
    )
  return runner(
    pipeline,
    command=command,
    use_llm=use_llm,
    model=model,
    ollama_url=ollama_url,
  )


def list_workflows(*, implemented_only: bool = False) -> list[dict]:
  out = []
  for spec in TASK_CATALOG:
    d = spec.to_dict()
    d["runnable"] = spec.id in _RUNNERS
    d["user_facing"] = False  # tools are Completion Planner–internal
    if implemented_only and not d["runnable"]:
      continue
    out.append(d)
  return out
