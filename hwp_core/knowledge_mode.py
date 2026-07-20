"""Product A knowledge modes — document evidence vs general knowledge (never mixed).

Modes:
  - document_only: answer only from uploaded documents
  - document_plus_general: document section first, then labeled general supplement (default)
  - general_only: local LLM general knowledge only (no document grounding)

"Document Reasoner" / Completion Planner are unrelated — this is Product A Q&A only.
"""

from __future__ import annotations

from typing import Literal, Optional

KnowledgeMode = Literal["document_only", "document_plus_general", "general_only"]

DEFAULT_KNOWLEDGE_MODE: KnowledgeMode = "document_plus_general"

MODE_LABELS_KO: dict[KnowledgeMode, str] = {
  "document_only": "문서 전용",
  "document_plus_general": "문서 + 일반 지식",
  "general_only": "일반 설명",
}

MODE_HELP_KO: dict[KnowledgeMode, str] = {
  "document_only": "업로드·선택된 문서에 있는 내용만 답합니다.",
  "document_plus_general": "문서 근거를 먼저 쓰고, 부족한 배경은 「문서 외」로 따로 표시합니다.",
  "general_only": "문서와 무관하게 일반 지식으로만 답합니다.",
}

DOC_SECTION_TITLE = "📄 문서에서 확인된 내용"
GENERAL_SECTION_TITLE = "📚 일반 지식 보충 (문서 외)"
GENERAL_ONLY_TITLE = "📚 일반 설명 (문서 외)"


def normalize_knowledge_mode(value: Optional[str]) -> KnowledgeMode:
  v = (value or "").strip().lower().replace("-", "_")
  aliases = {
    "document": "document_only",
    "doc_only": "document_only",
    "doc": "document_only",
    "plus": "document_plus_general",
    "hybrid": "document_plus_general",
    "default": "document_plus_general",
    "general": "general_only",
    "llm_only": "general_only",
  }
  v = aliases.get(v, v)
  if v in MODE_LABELS_KO:
    return v  # type: ignore[return-value]
  return DEFAULT_KNOWLEDGE_MODE


def format_split_answer(
  *,
  document_part: str,
  general_part: str = "",
  mode: KnowledgeMode = DEFAULT_KNOWLEDGE_MODE,
) -> str:
  """Combine sections with explicit labels. Never interleave sentences."""
  doc = (document_part or "").strip()
  gen = (general_part or "").strip()

  if mode == "general_only":
    body = gen or doc or "답변 없음"
    return f"**{GENERAL_ONLY_TITLE}**\n\n{body}"

  if mode == "document_only":
    body = doc or "문서에서 관련 내용을 찾지 못했습니다."
    return f"**{DOC_SECTION_TITLE}**\n\n{body}"

  # document_plus_general
  parts = [f"**{DOC_SECTION_TITLE}**\n\n{doc or '문서에서 관련 내용을 확인하지 못했습니다.'}"]
  if gen and not _is_empty_general(gen):
    parts.append(f"**{GENERAL_SECTION_TITLE}**\n\n{gen}")
  return "\n\n---\n\n".join(parts)


def _is_empty_general(text: str) -> bool:
  t = (text or "").strip()
  if not t:
    return True
  empty_markers = (
    "추가로 설명할 일반 지식이 없습니다",
    "보충할 일반 지식이 없습니다",
    "일반 지식 보충이 필요 없습니다",
    "문서 외 지식이 필요 없습니다",
  )
  return any(m in t for m in empty_markers)


def wrap_general_only(text: str) -> str:
  return format_split_answer(document_part="", general_part=text, mode="general_only")
