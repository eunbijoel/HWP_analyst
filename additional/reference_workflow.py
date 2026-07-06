"""호환성 re-export — 실제 구현은 reference_parser.py로 이동."""
from .reference_parser import (  # noqa: F401
    normalize_insert_body,
    generate_reference_summary,
    pick_summary_text,
    propose_append_at_end,
    append_summary_to_document,
)
