"""Completion Planner — state → gaps → plan → execute internal fill tools.

Scope (complete/fill intent only):
  - build DocumentState
  - detect and classify gaps
  - select internal fill tools
  - create pending proposals (never auto-apply)

Out of scope: analyze, review, ask, rewrite.
"Document Reasoner" is reserved for a future product-level task chooser.

Package path remains ``hwp_core.doc_reasoner`` for import stability.
"""

from __future__ import annotations

from typing import Any

from .gaps import detect_gaps
from .models import CompletionPlanResult
from .planner import TOOL_FACT_FILL_INSTITUTION, create_document_plan
from .state import build_document_state
from .summary import format_summary
from .tools import TOOL_RUNNERS


def is_complete_intent(message: str) -> bool:
  """Vague complete/fill-document goals — user must not name a workflow."""
  import re
  t = (message or "").strip()
  if not t:
    return False
  if re.search(
    r"(이\s*)?문서.{0,12}(완성|채워|보완|완성해)|"
    r"완성해\s*(?:줘|주세요)|"
    r"비어\s*있는\s*(?:곳|칸).{0,8}(채우|완성)|"
    r"빈\s*칸.{0,8}(완성|모두\s*채)",
    t,
    re.I,
  ):
    return True
  return False


def run_completion_planner(
  pipeline: Any,
  *,
  command: str = "이 문서 완성해줘",
  use_llm: bool = False,
  model: str = "gemma4",
  ollama_url: str = "http://localhost:11434",
) -> CompletionPlanResult:
  """Build DocumentState → GapReport → DocumentPlan → run fill tools → summary.

  Never auto-applies proposals. Does not handle analyze/review/ask/rewrite.
  """
  state = build_document_state(pipeline)
  if not state.inspect_ok:
    return CompletionPlanResult(
      ok=False,
      state=state,
      message=state.inspect_error or "문서 상태를 만들지 못했습니다.",
      summary=state.inspect_error or "문서 상태 검사 실패",
    )

  fields = list(pipeline.tools.last_fields or [])
  gap_report = detect_gaps(state, fields)
  plan = create_document_plan(state, gap_report, user_goal=command)

  proposals: list[dict] = []
  skipped: list[dict] = []
  fill_trace: list[dict] = []
  tools_run: list[str] = []
  tool_messages: list[str] = []

  # Deduplicate tool execution: FactFill runs once for all institution gaps
  tools_needed = []
  for step in plan.executable_steps():
    if step.selected_tool and step.selected_tool not in tools_needed:
      tools_needed.append(step.selected_tool)

  for tool_id in tools_needed:
    runner = TOOL_RUNNERS.get(tool_id)
    if not runner:
      continue
    out = runner(
      pipeline,
      command=command,
      use_llm=use_llm,
      model=model,
      ollama_url=ollama_url,
    )
    tools_run.append(tool_id)
    proposals.extend(out.get("proposals") or [])
    skipped.extend(out.get("skipped") or [])
    fill_trace.extend(out.get("fill_trace") or [])
    if out.get("message"):
      tool_messages.append(out["message"])

  summary = format_summary(gap_report, plan, proposals_count=len(proposals))
  ok = True
  if not gap_report.gaps:
    summary = (
      "문서를 살펴봤습니다.\n"
      "- 채울 빈칸/미완성 섹션을 찾지 못했습니다.\n"
      "이미 채워져 있거나, 지원하는 서식 패턴이 아닐 수 있습니다."
    )
    ok = True

  plan_snapshot = {
    "state": state.to_dict(),
    "gap_report": gap_report.to_dict(),
    "plan": plan.to_dict(),
    "summary": summary,
    "tools_run": tools_run,
  }
  # Persist on pipeline for debugging / UI meta
  pipeline.tools.last_proposals = proposals
  pipeline.tools.last_skipped_facts = skipped
  pipeline.tools.last_fill_trace = fill_trace
  pipeline.tools.last_completion_plan = plan_snapshot
  # Compatibility alias
  pipeline.tools.last_reasoner = plan_snapshot

  return CompletionPlanResult(
    ok=ok,
    state=state,
    gap_report=gap_report,
    plan=plan,
    proposals=proposals,
    skipped=skipped,
    fill_trace=fill_trace,
    summary=summary,
    message=summary,
    tools_run=tools_run,
    meta={
      "tool_messages": tool_messages,
      "institution_tool": TOOL_FACT_FILL_INSTITUTION,
      "command": command,
      "planner": "completion_planner",
    },
  )


# Compatibility aliases (old "Document Reasoner" naming)
run_reasoner = run_completion_planner
