"""Internal fill tools invoked by Completion Planner (not chat commands)."""

from __future__ import annotations

from typing import Any

from hwp_core.workflows import institution_fill

from .planner import TOOL_FACT_FILL_INSTITUTION


def run_fact_fill_institution(
  pipeline: Any,
  *,
  command: str = "이 문서 완성해줘",
  use_llm: bool = False,
  model: str = "gemma4",
  ollama_url: str = "http://localhost:11434",
) -> dict[str, Any]:
  """Adapter: existing institution fill as FactFillTool."""
  result = institution_fill.run(
    pipeline,
    command=command,
    use_llm=use_llm,
    model=model,
    ollama_url=ollama_url,
  )
  return {
    "tool_id": TOOL_FACT_FILL_INSTITUTION,
    "ok": result.ok,
    "proposals": result.proposals,
    "skipped": result.skipped,
    "fill_trace": result.fill_trace,
    "message": result.message,
    "success_checks": result.success_checks,
    "meta": result.meta,
  }


TOOL_RUNNERS = {
  TOOL_FACT_FILL_INSTITUTION: run_fact_fill_institution,
}
