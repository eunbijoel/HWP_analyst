"""Product A knowledge mode — document vs general sections never mixed."""

from __future__ import annotations

from hwp_core.knowledge_mode import (
  DEFAULT_KNOWLEDGE_MODE,
  DOC_SECTION_TITLE,
  GENERAL_ONLY_TITLE,
  GENERAL_SECTION_TITLE,
  format_split_answer,
  normalize_knowledge_mode,
)


def test_default_mode_is_document_plus_general():
  assert DEFAULT_KNOWLEDGE_MODE == "document_plus_general"
  assert normalize_knowledge_mode(None) == "document_plus_general"
  assert normalize_knowledge_mode("hybrid") == "document_plus_general"


def test_format_document_only():
  text = format_split_answer(
    document_part="표 1에 W3C VC/DID가 언급됨.",
    general_part="이 내용은 나오면 안 됨",
    mode="document_only",
  )
  assert DOC_SECTION_TITLE in text
  assert "표 1에 W3C" in text
  assert GENERAL_SECTION_TITLE not in text
  assert "나오면 안 됨" not in text


def test_format_document_plus_general_separated():
  text = format_split_answer(
    document_part="문서에는 W3C 약자 정의가 없습니다.",
    general_part="W3C는 World Wide Web Consortium의 약자입니다.",
    mode="document_plus_general",
  )
  assert DOC_SECTION_TITLE in text
  assert GENERAL_SECTION_TITLE in text
  assert "문서에는 W3C" in text
  assert "World Wide Web Consortium" in text
  # Document block appears before general
  assert text.index(DOC_SECTION_TITLE) < text.index(GENERAL_SECTION_TITLE)
  assert "---" in text


def test_format_skips_empty_general_marker():
  text = format_split_answer(
    document_part="문서 답변",
    general_part="추가로 설명할 일반 지식이 없습니다.",
    mode="document_plus_general",
  )
  assert DOC_SECTION_TITLE in text
  assert GENERAL_SECTION_TITLE not in text


def test_format_general_only():
  text = format_split_answer(
    document_part="",
    general_part="HTTP는 웹 전송 프로토콜입니다.",
    mode="general_only",
  )
  assert GENERAL_ONLY_TITLE in text
  assert "HTTP" in text
  assert DOC_SECTION_TITLE not in text
