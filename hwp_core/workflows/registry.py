"""Named workflow registry — route commands to task-specific runners."""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from . import institution_fill
from .base import WorkflowResult
from .catalog import TASK_CATALOG, get_task_catalog, get_workflow_spec

# Only implemented workflows are registered for routing.
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
  """Return workflow_id if command targets a named workflow."""
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
    if implemented_only and not d["runnable"]:
      continue
    out.append(d)
  return out
