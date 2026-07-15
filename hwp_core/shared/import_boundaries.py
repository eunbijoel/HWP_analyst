"""
Phase-1 import boundary helpers.

Allowed cross-product imports are intentionally narrow.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]

# Modules Product A (intelligence app + analysis package) must not import
PRODUCT_A_FORBIDDEN = frozenset({
    "ui.canvas_editor",
    "ui.doc_work_panel",
    "ui.session_store",
    "hwp_core.editing.edit_router",
    "hwp_core.hwpx_editor",
    "hwp_core.doc_agent",
    "additional.ai_editor",
})

# Shared must not import analysis or editing
SHARED_FORBIDDEN_PREFIXES = (
    "hwp_core.analysis",
    "hwp_core.editing",
    "ui.canvas_editor",
    "ui.doc_work_panel",
    "ui.command_router",
)

# Product B must not import Product A UI
PRODUCT_B_FORBIDDEN = frozenset({
    "ui.review_home",
    "ui.issue_panel",
    "ui.intel_panel",
    "ui.canvas_editor",
    "ui.doc_work_panel",
    "apps.intelligence",
})

# Only this analysis surface is OK for Product B
PRODUCT_B_ANALYSIS_ALLOW = frozenset({
    "hwp_core.analysis.validation_api",
})


def _iter_py_files(base: Path) -> Iterable[Path]:
    if base.is_file() and base.suffix == ".py":
        yield base
        return
    for p in base.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def collect_imports(path: Path) -> set[str]:
    """Return top-level and from-import module names found in a file."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0] if False else alias.name)
                # keep full dotted path for our checks
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                found.add(node.module)
                # also record package roots used with submodule imports
                parts = node.module.split(".")
                for i in range(1, len(parts) + 1):
                    found.add(".".join(parts[:i]))
            for alias in node.names:
                if node.module and alias.name != "*":
                    found.add(f"{node.module}.{alias.name}")
    return found


def forbidden_hits(imports: set[str], forbidden: Iterable[str]) -> list[str]:
    hits = []
    for f in forbidden:
        if f in imports:
            hits.append(f)
            continue
        # prefix: hwp_core.doc_agent.pipeline matches hwp_core.doc_agent
        for imp in imports:
            if imp == f or imp.startswith(f + "."):
                hits.append(imp)
                break
    return sorted(set(hits))


def check_shared_tree() -> list[str]:
    problems: list[str] = []
    base = ROOT / "hwp_core" / "shared"
    for path in _iter_py_files(base):
        imports = collect_imports(path)
        for imp in imports:
            for bad in SHARED_FORBIDDEN_PREFIXES:
                if imp == bad or imp.startswith(bad + "."):
                    problems.append(f"{path.relative_to(ROOT)}: shared imports {imp}")
    return problems


def check_product_a_entrypoints() -> list[str]:
    problems: list[str] = []
    targets = [
        ROOT / "apps" / "intelligence" / "app.py",
        ROOT / "hwp_core" / "analysis",
    ]
    for target in targets:
        for path in _iter_py_files(target):
            # validation_api intentionally imports intel_pipeline (same product)
            imports = collect_imports(path)
            hits = forbidden_hits(imports, PRODUCT_A_FORBIDDEN)
            for h in hits:
                problems.append(f"{path.relative_to(ROOT)}: Product A imports {h}")
    return problems


def check_product_b_entrypoints() -> list[str]:
    problems: list[str] = []
    targets = [
        ROOT / "HWP_v2",
        ROOT / "apps" / "editor",
        ROOT / "hwp_core" / "editing",
    ]
    for target in targets:
        for path in _iter_py_files(target):
            imports = collect_imports(path)
            hits = forbidden_hits(imports, PRODUCT_B_FORBIDDEN)
            for h in hits:
                problems.append(f"{path.relative_to(ROOT)}: Product B imports {h}")
            # analysis imports other than validation_api
            for imp in imports:
                if imp.startswith("hwp_core.analysis") and imp not in PRODUCT_B_ANALYSIS_ALLOW:
                    # allow importing validation_api sub-symbols as module path only
                    if not (
                        imp == "hwp_core.analysis"
                        or imp.startswith("hwp_core.analysis.validation_api")
                    ):
                        if imp.startswith("hwp_core.analysis.") and not imp.startswith(
                            "hwp_core.analysis.validation_api"
                        ):
                            problems.append(
                                f"{path.relative_to(ROOT)}: Product B imports analysis module {imp}"
                            )
    return problems
