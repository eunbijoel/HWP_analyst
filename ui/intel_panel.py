"""자동 검토(지능화) UI 패널"""

from __future__ import annotations

import streamlit as st

from hwp_core.intel_pipeline import IntelResult, WorkspaceIntel


def render_intel_review(result: IntelResult, *, expanded: bool = True, hide_when_clean: bool = True):
  """단일 문서 검토 패널. 이슈 없으면 기본적으로 숨김."""
  n = len(result.issues)
  if hide_when_clean and n == 0:
    return

  label = f"🔍 자동 검토 — 확인 필요 {n}건"

  with st.expander(label, expanded=expanded):
    st.caption(f"Fact {len(result.facts)}개 추출 · 합계·본문-표 교차 확인")
    for issue in result.issues:
      if issue.severity == "error":
        st.error(issue.message)
      else:
        st.warning(issue.message)
      if issue.source:
        st.caption(f"📍 {issue.source}")
    with st.expander("상세 리포트", expanded=False):
      st.markdown(result.report_markdown)
    with st.expander("추출 Fact 미리보기", expanded=False):
      _render_fact_preview(result)


def render_workspace_intel(workspace: WorkspaceIntel, *, expanded: bool = True, hide_when_clean: bool = True):
  """다중 문서 검토 패널. 이슈 없으면 기본적으로 숨김."""
  total = workspace.total_issues
  if hide_when_clean and total == 0:
    return

  label = f"🔍 자동 검토 (전체) — 확인 필요 {total}건"

  with st.expander(label, expanded=expanded):
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


def _render_fact_preview(result: IntelResult, limit: int = 20):
  if not result.facts:
    st.caption("추출된 Fact가 없습니다.")
    return
  rows = []
  for f in result.facts[:limit]:
    rows.append({
      "라벨": f.raw_label[:40],
      "값": f.display_value or f"{f.value:,.0f}",
      "단위": f.unit or "—",
      "출처": f.source_hint(),
    })
  st.dataframe(rows, use_container_width=True, hide_index=True)
  if len(result.facts) > limit:
    st.caption(f"… 외 {len(result.facts) - limit}건")
