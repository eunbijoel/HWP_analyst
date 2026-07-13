"""자동 검토(지능화) UI 패널"""

from __future__ import annotations

import streamlit as st

from hwp_core.intel_pipeline import IntelResult, WorkspaceIntel


def render_intel_review(result: IntelResult, *, expanded: bool = True):
  """단일 문서: grounding은 항상 표시, 일관성 이슈는 별도."""
  _render_grounding_panel(result, expanded=expanded)

  n = len(result.issues)
  issue_label = f"🔍 일관성 검토 — 확인 필요 {n}건" if n else "🔍 일관성 검토 — 이슈 없음"
  with st.expander(issue_label, expanded=expanded and n > 0):
    if n == 0:
      st.success("표 합계·본문-표·예실대비 교차 확인에서 눈에 띄는 불일치가 없습니다.")
    else:
      for issue in result.issues:
        if issue.severity == "error":
          st.error(issue.message)
        elif issue.severity == "info":
          st.info(issue.message)
        else:
          st.warning(issue.message)
        if issue.source:
          st.caption(f"📍 {issue.source}")
    with st.expander("상세 리포트", expanded=False):
      st.markdown(result.report_markdown)


def render_workspace_intel(workspace: WorkspaceIntel, *, expanded: bool = True):
  """다중 문서: 전체 교차 이슈 + 파일별 grounding 요약."""
  with st.expander("🧠 개념 연결 (전체)", expanded=expanded):
    for result in workspace.per_document:
      g = result.grounding or {}
      st.caption(
        f"**{result.document_id}** — "
        f"grounding {g.get('coverage_pct', 0)}% "
        f"({g.get('grounded_facts', 0)}/{g.get('total_facts', 0)})"
      )

  total = workspace.total_issues
  label = (
    f"🔍 일관성 검토 (전체) — 확인 필요 {total}건"
    if total else "🔍 일관성 검토 (전체) — 이슈 없음"
  )
  with st.expander(label, expanded=expanded and total > 0):
    if total == 0:
      st.success("문서 간·파일별 일관성 이슈가 없습니다.")
    for result in workspace.per_document:
      if not result.issues:
        continue
      st.markdown(f"**{result.document_id}** — 이슈 {len(result.issues)}건")
      for issue in result.issues:
        st.warning(issue.message)
        st.caption(f"📍 {issue.source}")

    if workspace.cross_issues:
      st.markdown("---")
      st.markdown("**문서 간 비교**")
      for issue in workspace.cross_issues:
        st.warning(issue.message)
        st.caption(f"📍 {issue.source}")


def _render_grounding_panel(result: IntelResult, *, expanded: bool = True):
  g = result.grounding or {}
  total = g.get("total_facts", 0)
  grounded = g.get("grounded_facts", 0)
  pct = g.get("coverage_pct", 0)
  llm_n = g.get("llm_grounded_facts", 0)

  with st.expander(f"🧠 개념 연결 (grounding) — {pct}%", expanded=expanded):
    st.progress(min(1.0, pct / 100.0) if total else 0.0)
    st.caption(f"Fact **{grounded}/{total}**개에 concept 연결 · LLM 보조 **{llm_n}**건")

    with st.expander("추출 Fact 미리보기", expanded=False):
      _render_fact_preview(result)

    unmatched = g.get("unmatched_labels") or []
    if unmatched:
      st.markdown("**미매칭 라벨** (→ `budget_concepts.yaml` synonyms 추가 후보)")
      for label in unmatched[:15]:
        st.code(label, language=None)
      hints = g.get("unmatched_hints") or []
      if hints:
        with st.expander("YAML 보강 힌트", expanded=False):
          for h in hints[:10]:
            st.text(h.get("yaml_hint", ""))


def _render_fact_preview(result: IntelResult, limit: int = 25):
  if not result.facts:
    st.caption("추출된 Fact가 없습니다.")
    return
  rows = []
  for f in result.facts[:limit]:
    concept = f.concept or "—"
    conf = getattr(f, "concept_confidence", 0.0) or 0.0
    method = getattr(f, "grounding_method", "") or "—"
    rows.append({
      "라벨": f.raw_label[:36],
      "concept": concept,
      "확신도": f"{conf:.2f}" if conf else "—",
      "방법": method,
      "값": f.display_value or f"{f.value:,.0f}",
      "출처": f.source_hint(),
    })
  st.dataframe(rows, use_container_width=True, hide_index=True)
  if len(result.facts) > limit:
    st.caption(f"… 외 {len(result.facts) - limit}건")
