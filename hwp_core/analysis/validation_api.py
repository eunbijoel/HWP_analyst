"""
Narrow post-edit validation interface for Product B.

Product B may import this module only (not Streamlit UI or full intel UI).
"""

from __future__ import annotations

from typing import Any, Optional


def validate_parsed_document(
    *,
    paragraphs: list[str],
    tables: list,
    text_numbers: list | None = None,
    table_numbers: list | None = None,
    document_id: str = "document",
) -> Any:
    """Run intelligence/validation and return IntelligenceResult-like object."""
    from hwp_core.intel_pipeline import build_intelligence

    return build_intelligence(
        paragraphs=paragraphs,
        tables=tables,
        text_numbers=text_numbers or [],
        table_numbers=table_numbers or [],
        document_id=document_id,
    )


def issues_as_dicts(intel: Any) -> list[dict]:
    """Serialize issues for editor UI / API responses."""
    out: list[dict] = []
    for issue in getattr(intel, "issues", None) or []:
        if hasattr(issue, "to_context_dict"):
            out.append(issue.to_context_dict())
            continue
        out.append({
            "issue_type": getattr(issue, "issue_type", ""),
            "severity": getattr(issue, "severity", ""),
            "message": getattr(issue, "message", "") or "",
            "source": getattr(issue, "source", "") or "",
            "document_id": getattr(issue, "document_id", "") or "",
            "table_index": getattr(issue, "table_index", None),
            "row_index": getattr(issue, "row_index", None),
        })
    return out


def validate_workspace_slots(
    documents: list[dict],
) -> Optional[Any]:
    """Optional multi-doc compare after edits. `documents` are QA-style payloads."""
    if len(documents) < 2:
        return None
    from hwp_core.intel_pipeline import build_workspace_intelligence

    return build_workspace_intelligence(documents)
