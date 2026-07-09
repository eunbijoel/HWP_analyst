"""
지능화 파이프라인 진입점 (1단계)
업로드 직후 Fact 추출 + 교차 확인 + 리포트 생성
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fact_extractor import Fact, extract_facts
from .consistency_checker import Issue, check_consistency


@dataclass
class IntelResult:
  document_id: str = ""
  facts: list[Fact] = field(default_factory=list)
  issues: list[Issue] = field(default_factory=list)
  report_markdown: str = ""

  @property
  def warning_count(self) -> int:
    return sum(1 for i in self.issues if i.severity == "warning")

  @property
  def error_count(self) -> int:
    return sum(1 for i in self.issues if i.severity == "error")

  @property
  def ok(self) -> bool:
    return not self.issues


@dataclass
class WorkspaceIntel:
  per_document: list[IntelResult] = field(default_factory=list)
  cross_issues: list[Issue] = field(default_factory=list)

  @property
  def total_issues(self) -> int:
    n = sum(len(r.issues) for r in self.per_document)
    return n + len(self.cross_issues)


def _format_report(result: IntelResult) -> str:
  lines = [f"## 자동 검토 — {result.document_id}", ""]

  lines.append(f"- 추출 Fact: **{len(result.facts)}**개")
  lines.append(f"- 확인 필요: **{result.warning_count}**건")
  lines.append("")

  if not result.issues:
    lines.append("✅ 표 합계·본문-표 교차 확인에서 눈에 띄는 불일치가 없습니다.")
    lines.append("")
    lines.append("_※ 단위·병합셀·비표준 표는 추가 확인이 필요할 수 있습니다._")
    return "\n".join(lines)

  lines.append("### 확인 필요 항목")
  lines.append("")
  for i, issue in enumerate(result.issues, 1):
    icon = "⚠️" if issue.severity == "warning" else "❌"
    lines.append(f"{i}. {icon} **{issue.message}**")
    if issue.source:
      lines.append(f"   - 위치: {issue.source}")
    if issue.expected is not None and issue.actual is not None:
      diff = issue.difference if issue.difference is not None else (issue.actual - issue.expected)
      lines.append(f"   - 기대: {issue.expected:,.0f} / 실제: {issue.actual:,.0f} / 차이: {diff:+,.0f}")
    lines.append("")

  return "\n".join(lines)


def build_intelligence(
  *,
  paragraphs: list,
  tables: list,
  text_numbers: list,
  table_numbers: list,
  document_id: str = "",
) -> IntelResult:
  facts = extract_facts(
    paragraphs=paragraphs,
    tables=tables,
    text_numbers=text_numbers,
    table_numbers=table_numbers,
    document_id=document_id,
  )
  issues = check_consistency(facts, tables, document_id=document_id)
  result = IntelResult(
    document_id=document_id,
    facts=facts,
    issues=issues,
  )
  result.report_markdown = _format_report(result)
  return result


def build_workspace_intelligence(doc_payloads: list[dict]) -> WorkspaceIntel:
  """다중 문서: 파일별 검토 + (1단계) 총사업비 개념 간단 교차."""
  per_doc: list[IntelResult] = []
  cross: list[Issue] = []

  for dp in doc_payloads:
    doc_id = dp.get("id", "")
    intel = build_intelligence(
      paragraphs=dp.get("paragraphs", []),
      tables=dp.get("tables", []),
      text_numbers=dp.get("text_numbers", []),
      table_numbers=dp.get("table_numbers", []),
      document_id=doc_id,
    )
    per_doc.append(intel)

  total_budgets: list[tuple[str, float, str]] = []
  for intel in per_doc:
    for f in intel.facts:
      if f.concept != "total_budget":
        continue
      won = f.value_in_won
      if f.source_type == "table":
        for ts in next((d.get("tables", []) for d in doc_payloads if d.get("id") == intel.document_id), []):
          if ts.index == f.table_index:
            won = f.value * (ts.unit_multiplier or 1.0)
            break
      if won is not None:
        total_budgets.append((intel.document_id, won, f.source_hint()))

  if len(total_budgets) >= 2:
    base_doc, base_val, base_src = total_budgets[0]
    for other_doc, other_val, other_src in total_budgets[1:]:
      if abs(base_val - other_val) <= max(abs(base_val), abs(other_val), 1.0) * 0.02:
        continue
      cross.append(Issue(
        issue_type="cross_doc_mismatch",
        severity="warning",
        message=(
          f"문서 간 총사업비 불일치: {base_doc} vs {other_doc}"
        ),
        expected=base_val,
        actual=other_val,
        difference=other_val - base_val,
        source=f"{base_src} ↔ {other_src}",
        document_id=f"{base_doc} | {other_doc}",
      ))

  return WorkspaceIntel(per_document=per_doc, cross_issues=cross)
