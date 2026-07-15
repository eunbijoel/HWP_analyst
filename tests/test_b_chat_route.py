"""Product B chat_route unit tests."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "HWP_v2"
sys.path[:0] = [str(ROOT), str(V2)]

from chat_route import (  # noqa: E402
    EditSpec,
    decide_chat_route,
    find_edit_targets,
    is_explanatory_question,
    is_explicit_edit_command,
    parse_edit_spec,
    resolve_search_edit,
)


class FakeEditor:
    def __init__(self, paragraphs=None, tables=None):
        self._paras = [
            {"index": i, "text": t}
            for i, t in enumerate(paragraphs or [])
        ]
        self._tables = tables or []

    def get_paragraphs(self):
        return list(self._paras)

    def get_table_count(self):
        return len(self._tables)

    def get_table_as_rows(self, t_idx):
        return self._tables[t_idx]


def test_question_vs_rewrite_with_selection():
    q = decide_chat_route(
        message="데이터 스페이스가 뭐야?",
        has_selection=True,
        has_editor=True,
        has_docs=True,
    )
    assert q.action == "answer_selection"
    assert is_explanatory_question("데이터 스페이스가 뭐야?")

    e = decide_chat_route(
        message="더 짧게 수정해줘",
        has_selection=True,
        has_editor=True,
        has_docs=True,
    )
    assert e.action == "rewrite_selection"
    assert is_explicit_edit_command("더 짧게 수정해줘")


def test_no_selection_edit_is_not_redirected_to_a():
    for msg in (
        "121 연구비로 수정해줘",
        "이 문서에서 총사업비 문구를 130억원으로 바꿔줘",
        "빈칸을 참고 문서로 채워줘",
    ):
        d = decide_chat_route(
            message=msg,
            has_selection=False,
            has_editor=True,
            has_docs=True,
        )
        assert d.action != "redirect_a", msg
        assert d.action in {"search_edit", "fill", "compute_edit"}, (msg, d.action)


def test_fill_routing():
    d = decide_chat_route(
        message="빈칸을 참고 문서로 채워줘",
        has_selection=False,
        has_editor=True,
        has_docs=True,
    )
    assert d.action == "fill"


def test_whole_document_analysis_redirect():
    for msg in (
        "총 사업비는 얼마야?",
        "두 문서 비교해줘",
        "이슈가 왜 생겼어?",
    ):
        d = decide_chat_route(
            message=msg,
            has_selection=False,
            has_editor=True,
            has_docs=True,
        )
        assert d.action == "redirect_a", msg


def test_parse_edit_spec_value_label_and_phrase():
    s1 = parse_edit_spec("121 연구비로 수정해줘")
    assert s1.new == "121"
    assert "연구비" in s1.label

    s2 = parse_edit_spec("이 문서에서 총사업비 문구를 130억원으로 바꿔줘")
    assert "총사업비" in s2.old
    assert "130억원" in s2.new


def test_single_target_propose():
    ed = FakeEditor(
        paragraphs=["서론", "총사업비는 100억원이다.", "결론"],
        tables=[],
    )
    spec = EditSpec(old="총사업비", new="130억원")
    r = resolve_search_edit(ed, "총사업비 문구를 130억원으로 바꿔줘", spec)
    assert r.action == "propose_replace"
    assert len(r.targets) == 1
    assert r.targets[0].kind == "paragraph"


def test_multiple_target_candidates():
    ed = FakeEditor(
        paragraphs=["연구비 개요", "연구비 세부", "기타"],
        tables=[[["항목", "금액"], ["연구비", "10"], ["연구비", "20"]]],
    )
    spec = EditSpec(old="", new="121", label="연구비")
    r = resolve_search_edit(ed, "121 연구비로 수정해줘", spec)
    assert r.action == "choose_targets"
    assert len(r.targets) > 1
    assert "여러 위치" in r.message


def test_missing_target_asks_select():
    ed = FakeEditor(paragraphs=["서론만 있음"], tables=[])
    spec = EditSpec(old="총사업비", new="130억원")
    r = resolve_search_edit(ed, "총사업비를 130억원으로 바꿔줘", spec)
    assert r.action == "ask_select"
    assert "찾지 못" in r.message or "선택" in r.message


def test_compute_then_edit_stays_in_b():
    msg = "인건비 합계를 계산해서 합계 셀에 넣어줘"
    assert is_explicit_edit_command(msg)
    d = decide_chat_route(
        message=msg,
        has_selection=False,
        has_editor=True,
        has_docs=True,
    )
    assert d.action == "compute_edit"
    assert d.spec and d.spec.needs_compute
    assert d.action != "redirect_a"


def test_find_targets_label_cell():
    ed = FakeEditor(
        tables=[[["구분", "금액"], ["연구비", "100"], ["합계", ""]]],
    )
    found = find_edit_targets(ed, EditSpec(label="연구비", new="121"))
    assert found
    assert found[0].kind == "cell"
