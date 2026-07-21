"""계산 채팅 ↔ 채우기 제안이 끊기지 않는지."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_V2 = _ROOT / "HWP_v2"
if str(_V2) not in sys.path:
    sys.path.insert(0, str(_V2))

from cell_ai import (  # noqa: E402
    extract_value_from_recent_chat,
    is_calc_question,
)
from chat_route import decide_chat_route  # noqa: E402


def test_is_calc_question():
    assert is_calc_question("국내여비 합계 계산해줘")
    assert is_calc_question("이 숫자들 보고 합계 얼마야")
    assert not is_calc_question("그럼 채워넣어! 합계에")
    assert not is_calc_question("30,000으로 채워줘")


def test_extract_value_from_recent_chat():
    chat = [
        {"role": "user", "content": "국내여비 합계 계산해줘"},
        {"role": "assistant", "content": "국내여비 합계는 30,000입니다."},
        {"role": "user", "content": "그럼 채워넣어! 합계에"},
    ]
    assert extract_value_from_recent_chat(chat) == "30,000"


def test_extract_skips_fill_failure_message():
    chat = [
        {"role": "assistant", "content": "국내여비 합계는 30,000입니다."},
        {
            "role": "assistant",
            "content": "선택 칸(표1 5행 4열) 우선. 제안을 만들지 못했습니다.",
        },
    ]
    assert extract_value_from_recent_chat(chat) == "30,000"


def test_compute_then_write_with_selection_goes_fill():
    d = decide_chat_route(
        message="계산해서 합계에 넣어줘",
        has_selection=True,
        has_editor=True,
        has_docs=True,
    )
    assert d.action == "fill"
