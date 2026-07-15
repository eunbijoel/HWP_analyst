"""
Compatibility shim for document HTML preview.

Prefer:
  hwp_core.shared.preview.plain — read-only
  hwp_core.editing.preview_layer — selection / pending / applied
"""

from hwp_core.shared.preview.plain import build_preview_from_text  # noqa: F401
from hwp_core.editing.preview_layer import (  # noqa: F401
    PREVIEW_CSS,
    append_viewer_scripts,
    build_preview_html,
    format_pending_label,
)

__all__ = [
    "PREVIEW_CSS",
    "append_viewer_scripts",
    "build_preview_from_text",
    "build_preview_html",
    "format_pending_label",
]
