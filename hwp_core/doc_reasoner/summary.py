"""Human-readable Completion Planner summary for Product B chat."""

from __future__ import annotations

from .models import DocumentPlan, GapReport


def format_summary(
  gap_report: GapReport,
  plan: DocumentPlan,
  *,
  proposals_count: int = 0,
) -> str:
  n_inst = len(gap_report.by_type("institution_fact"))
  n_nec = len(gap_report.by_type("narrative_necessity"))
  n_unsup = len(gap_report.by_type("unsupported"))

  inst_steps = [s for s in plan.steps if s.gap_type == "institution_fact"]
  inst_executable = [s for s in inst_steps if s.will_execute]
  inst_no_ref = [s for s in inst_steps if not s.evidence_exists]

  lines = ["문서를 살펴 완성할 항목을 정리했습니다."]

  if n_inst:
    if inst_executable:
      lines.append(
        f"- 기관정보 빈칸 {n_inst}개: 참고자료 근거로 제안 가능"
        + (f" (이번 실행 제안 {proposals_count}건)" if proposals_count else "")
      )
    elif inst_no_ref:
      lines.append(
        f"- 기관정보 빈칸 {n_inst}개: 참고 문서가 없어 제안하지 않음"
      )
    else:
      lines.append(f"- 기관정보 빈칸 {n_inst}개: 검토 필요")
  else:
    lines.append("- 기관정보 빈칸: 없음")

  if n_nec:
    lines.append(
      f"- 연구개발 필요성 {n_nec}개: 현재 문맥 기반 AI 초안 가능"
      " (이번에는 제안하지 않음 — 검토 후 별도 작성)"
    )
  else:
    lines.append("- 연구개발 필요성: 비어 있는 섹션 없음")

  if n_unsup:
    lines.append(f"- 지원하지 않는 빈칸 {n_unsup}개: 검토 필요")
  else:
    lines.append("- 지원하지 않는 빈칸: 없음")

  lines.append("")
  lines.append("자동 반영하지 않았습니다. 제안은 검토 후 수락하세요.")
  return "\n".join(lines)
