"""호환성 re-export — 실제 구현은 table_extractor.py로 이동."""
from .table_extractor import (  # noqa: F401
    local_tag, get_cell_text, CellMerge, ParsedTableGrid,
    parse_table_grid, build_element_grid, is_inside_table,
    _collect_addressed_cells,
)
