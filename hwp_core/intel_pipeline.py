"""
지능화 파이프라인 진입점 (1단계)
업로드 직후 Fact 추출 + 교차 확인 + 리포트 생성
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fact_extractor import Fact, extract_facts, grounding_stats_for_facts, TOTAL_BUDGET_LABELS
from .consistency_checker import Issue, check_consistency
from .concept_resolver import GroundingOptions
from .rule_registry import get_rule, resolve_min_confidence, resolve_tol, rule_enabled


@dataclass
class IntelResult:
  document_id: str = ""
  facts: list[Fact] = field(default_factory=list)
  issues: list[Issue] = field(default_factory=list)
  grounding: dict = field(default_factory=dict)
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
  if result.grounding:
    lines.append(
      f"- 개념 연결(grounding): **{result.grounding.get('coverage_pct', 0)}%** "
      f"({result.grounding.get('grounded_facts', 0)}/{result.grounding.get('total_facts', 0)})"
    )
    llm_n = result.grounding.get("llm_grounded_facts", 0)
    if llm_n:
      lines.append(f"- LLM 보조 grounding: **{llm_n}**건")
    unmatched = result.grounding.get("unmatched_labels") or []
    if unmatched:
      lines.append(f"- 미매칭 라벨: {len(unmatched)}개 (YAML synonyms 보강 후보)")
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
  grounding: GroundingOptions | None = None,
) -> IntelResult:
  facts = extract_facts(
    paragraphs=paragraphs,
    tables=tables,
    text_numbers=text_numbers,
    table_numbers=table_numbers,
    document_id=document_id,
    grounding=grounding,
  )
  issues = check_consistency(facts, tables, document_id=document_id)
  result = IntelResult(
    document_id=document_id,
    facts=facts,
    issues=issues,
    grounding=grounding_stats_for_facts(facts),
  )
  result.report_markdown = _format_report(result)
  return result


def build_workspace_intelligence(
  doc_payloads: list[dict],
) -> WorkspaceIntel:
  """다중 문서: 캐시된 intel 재사용 + 문서 간 총사업비 교차만."""
  per_doc: list[IntelResult] = []
  cross: list[Issue] = []

  for dp in doc_payloads:
    cached = dp.get("intel")
    if cached is not None:
      per_doc.append(cached)
      continue
    intel = build_intelligence(
      paragraphs=dp.get("paragraphs", []),
      tables=dp.get("tables", []),
      text_numbers=dp.get("text_numbers", []),
      table_numbers=dp.get("table_numbers", []),
      document_id=dp.get("id", ""),
      grounding=None,
    )
    per_doc.append(intel)

  total_budgets: list[tuple[str, float, str]] = []
  if not rule_enabled("cross_doc"):
    return WorkspaceIntel(per_document=per_doc, cross_issues=cross)

  try:
    cfg = get_rule("cross_doc")
  except KeyError:
    cfg = {}
  concept = str(cfg.get("concept") or "total_budget")
  sev = str(cfg.get("severity") or "warning")
  rel, abs_tol = resolve_tol("cross_doc", concept)
  min_conf = resolve_min_confidence("cross_doc", concept)

  for intel in per_doc:
    for f in intel.facts:
      if f.concept != concept:
        continue
      conf = getattr(f, "concept_confidence", 0.0) or 0.0
      if conf < min_conf and not (
        concept == "total_budget" and TOTAL_BUDGET_LABELS.search(f.raw_label)
      ):
        continue
      won = f.value_in_won
      if f.source_type == "table":
        for ts in next((d.get("tables", []) for d in doc_payloads if d.get("id") == intel.document_id), []):
          if ts.index == f.table_index:
            won = f.value * (ts.unit_multiplier or 1.0)
            break
      if won is not None:
        total_budgets.append((intel.document_id, won, f.source_hint(), conf))

  if len(total_budgets) >= 2:
    base_doc, base_val, base_src, base_conf = total_budgets[0]
    for other_doc, other_val, other_src, other_conf in total_budgets[1:]:
      diff = abs(base_val - other_val)
      if diff <= abs_tol:
        continue
      if diff / max(abs(base_val), abs(other_val), 1.0) <= rel:
        continue
      cross.append(Issue(
        issue_type="cross_doc_mismatch",
        severity=sev,
        message=(
          f"문서 간 {concept} 불일치: {base_doc} vs {other_doc}"
        ),
        expected=base_val,
        actual=other_val,
        difference=other_val - base_val,
        source=f"{base_src} ↔ {other_src}",
        document_id=f"{base_doc} | {other_doc}",
        concept_id=concept,
        rel_tol=rel,
        abs_tol=abs_tol,
        grounding_confidence=min(base_conf, other_conf),
      ))

  return WorkspaceIntel(per_document=per_doc, cross_issues=cross)
