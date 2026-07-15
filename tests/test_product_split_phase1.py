"""Phase 1 — import boundaries and product separation smoke tests."""

from __future__ import annotations

import ast
import importlib
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hwp_core.shared.import_boundaries import (
    check_product_a_entrypoints,
    check_product_b_entrypoints,
    check_shared_tree,
)
from hwp_core.shared.intent_classify import classify_intent, is_edit_intent
from hwp_core.analysis.intent_route import route_analysis_intent


def test_shared_has_no_analysis_or_editing_imports():
    problems = check_shared_tree()
    assert problems == [], problems


def test_product_a_forbids_editing_imports():
    problems = check_product_a_entrypoints()
    assert problems == [], problems


def test_product_b_forbids_product_a_ui_imports():
    problems = check_product_b_entrypoints()
    assert problems == [], problems


def test_intent_split_qa_vs_edit():
    assert classify_intent("총 사업비는 얼마야?") == "qa"
    assert is_edit_intent(classify_intent("참고 자료로 채워줘"))
    assert route_analysis_intent("총 사업비는?") == "qa"
    assert route_analysis_intent("빈칸 채워줘") != "qa"


def test_shared_preview_importable_without_editor():
    mod = importlib.import_module("hwp_core.shared.preview.plain")
    html = mod.build_preview_from_text(["hello"], [[["a", "b"]]], filename="t.hwp")
    assert "hello" in html
    assert "표 1" in html
    # shared must not pull hwpx_editor
    src = (ROOT / "hwp_core/shared/preview/plain.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "hwpx_editor" not in node.module
            assert "editing" not in node.module


def test_command_router_shim_still_works():
    from ui.command_router import classify_intent as c1
    from ui.command_router import execute_edit_command

    assert c1("합계는?") == "qa"
    assert callable(execute_edit_command)


def test_document_preview_shim_still_works():
    from ui.document_preview import build_preview_from_text, build_preview_html

    assert callable(build_preview_from_text)
    assert callable(build_preview_html)


def test_intelligence_entrypoint_compiles():
    path = ROOT / "apps/intelligence/app.py"
    src = path.read_text(encoding="utf-8")
    ast.parse(src)
    # Must not import canvas / doc fill / session_store / hwpx_editor
    banned = (
        "ui.canvas_editor",
        "ui.doc_work_panel",
        "ui.session_store",
        "hwp_core.hwpx_editor",
        "execute_edit_command",
    )
    for b in banned:
        assert b not in src, f"Product A entry still references {b}"


def test_editor_entrypoint_compiles():
    path = ROOT / "apps/editor/server.py"
    ast.parse(path.read_text(encoding="utf-8"))
    v2 = ROOT / "HWP_v2/server.py"
    src = v2.read_text(encoding="utf-8")
    for b in ("ui.review_home", "ui.issue_panel", "ui.canvas_editor", "QAEngine"):
        assert b not in src, f"Product B still references {b}"


def test_compat_app_py_points_at_intelligence():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "apps" in src and "intelligence" in src


def test_validation_api_importable():
    from hwp_core.analysis.validation_api import issues_as_dicts, validate_parsed_document

    assert callable(validate_parsed_document)
    assert callable(issues_as_dicts)
