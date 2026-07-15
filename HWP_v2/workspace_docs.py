"""v2 워크스페이스 — 다중 HWP/HWPX/Excel 슬롯."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from additional.reference_parser import parse_reference
from hwp_core.hwp_parser import parse_document
from hwp_core.hwpx_editor import HWPXEditor
from hwp_core.table_extractor import (
    detect_numbers_in_tables,
    detect_numbers_in_text,
    extract_tables,
)
from ui.document_preview import build_preview_from_text, build_preview_html

from convert_hwp import hwp_to_hwpx_bytes


@dataclass
class DocSlot:
    id: str
    filename: str
    kind: str  # hwp | hwpx | xlsx | other
    bytes_data: bytes
    editor: Optional[HWPXEditor] = None
    read_only: bool = False
    source_was_hwp: bool = False
    original_hwp_name: str = ""
    original_hwp_bytes: Optional[bytes] = None
    convert_note: str = ""
    preview_html: str = ""
    paragraphs: list[str] = field(default_factory=list)
    tables_raw: list = field(default_factory=list)
    qa_tables: list = field(default_factory=list)
    text_numbers: list = field(default_factory=list)
    table_numbers: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_editable(self) -> bool:
        return self.editor is not None and not self.read_only

    @property
    def is_excel(self) -> bool:
        return self.kind == "xlsx"

    def qa_payload(self) -> dict:
        return {
            "id": self.filename,
            "paragraphs": list(self.paragraphs or []),
            "tables": list(self.qa_tables or []),
            "text_numbers": list(self.text_numbers or []),
            "table_numbers": list(self.table_numbers or []),
        }

    def summary_line(self) -> str:
        tag = {"hwp": "HWP", "hwpx": "HWPX", "xlsx": "Excel"}.get(self.kind, self.kind)
        if self.is_excel:
            return f"{self.filename} · {tag} · 표 {len(self.tables_raw)}개"
        note = "편집" if self.is_editable else "읽기전용"
        return f"{self.filename} · {tag} · {note}"


def _kind_of(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".hwp"):
        return "hwp"
    if lower.endswith(".hwpx"):
        return "hwpx"
    if lower.endswith((".xlsx", ".xls")):
        return "xlsx"
    return "other"


def _preview_from_editor(editor: HWPXEditor, filename: str) -> str:
    import re

    html = build_preview_html(
        editor,
        filename=filename,
        canvas_mode=True,
        max_paras=400,
        max_tables=40,
        max_rows_per_table=80,
    )
    return re.sub(
        r'<div style="font-size:11px;color:#666;margin-bottom:10px">.*?</div>',
        "",
        html,
        count=1,
        flags=re.S,
    )


def _fill_qa_from_parsed(slot: DocSlot, parsed) -> None:
    slot.paragraphs = list(getattr(parsed, "paragraphs", None) or [])
    slot.tables_raw = list(getattr(parsed, "tables_raw", None) or [])
    full = getattr(parsed, "full_text", "") or "\n".join(slot.paragraphs)
    try:
        slot.qa_tables = extract_tables(parsed, document_id=slot.filename)
        slot.text_numbers = detect_numbers_in_text(full, document_id=slot.filename)
        slot.table_numbers = detect_numbers_in_tables(
            slot.qa_tables, document_id=slot.filename,
        )
    except Exception as e:
        slot.errors.append(f"표 분석: {e}")


def load_doc_slot(data: bytes, filename: str) -> DocSlot:
    """바이트 → DocSlot (HWP/HWPX/Excel)."""
    kind = _kind_of(filename)
    slot = DocSlot(
        id=uuid.uuid4().hex[:10],
        filename=filename,
        kind=kind,
        bytes_data=data,
    )

    if kind == "xlsx":
        ref = parse_reference(data, filename)
        slot.errors.extend(ref.errors or [])
        n = len(ref.tables or [])
        slot.paragraphs = [
            f"엑셀 {filename} · 시트/표 {n}개" if n else f"엑셀 {filename}",
        ]
        slot.tables_raw = [
            {"rows": rows, "caption": f"시트 {i+1}", "unit": ""}
            for i, rows in enumerate(ref.tables or [])
        ]
        table_rows = [t["rows"] for t in slot.tables_raw]
        slot.preview_html = build_preview_from_text(
            slot.paragraphs, table_rows, filename=filename,
        )
        parsed = SimpleNamespace(
            filename=filename,
            file_type="xlsx",
            full_text=ref.full_text or "",
            paragraphs=slot.paragraphs,
            tables_raw=slot.tables_raw,
            errors=ref.errors,
        )
        _fill_qa_from_parsed(slot, parsed)
        slot.read_only = True
        return slot

    if kind == "other":
        slot.errors.append(f"지원하지 않는 형식: {filename}")
        slot.read_only = True
        slot.preview_html = f"<p>지원하지 않는 파일: {filename}</p>"
        return slot

    working = data
    display_name = filename

    if kind == "hwp":
        slot.source_was_hwp = True
        slot.original_hwp_name = filename
        slot.original_hwp_bytes = data
        converted, note = hwp_to_hwpx_bytes(data, filename)
        slot.convert_note = note or ""
        if not converted:
            doc = parse_document(file_bytes=data, filename=filename)
            tables = [t.get("rows", []) for t in (doc.tables_raw or [])]
            slot.preview_html = build_preview_from_text(
                doc.paragraphs, tables, filename=filename,
            )
            slot.paragraphs = list(doc.paragraphs or [])
            slot.tables_raw = list(doc.tables_raw or [])
            _fill_qa_from_parsed(slot, doc)
            slot.read_only = True
            slot.errors.append(f"HWP→HWPX 변환 실패 — 읽기 전용: {note}")
            return slot
        working = converted
        display_name = Path(filename).stem + ".hwpx"
        slot.filename = display_name
        slot.kind = "hwpx"

    try:
        editor = HWPXEditor(working)
        editor._source_filename = display_name
        slot.editor = editor
        slot.bytes_data = working
        slot.preview_html = _preview_from_editor(editor, display_name)
        paras = editor.get_paragraphs()
        slot.paragraphs = [p.get("text") or "" for p in paras]
        # QA용 재파싱
        try:
            parsed = parse_document(file_bytes=working, filename=display_name)
            _fill_qa_from_parsed(slot, parsed)
        except Exception:
            slot.qa_tables = []
    except Exception as e:
        slot.errors.append(f"문서 로드 실패: {e}")
        slot.read_only = True
        slot.preview_html = f"<p>로드 실패: {e}</p>"

    return slot


def slot_list_payload(slots: list[DocSlot], active_id: str) -> list[dict]:
    out = []
    for s in slots:
        out.append({
            "id": s.id,
            "filename": s.filename,
            "kind": s.kind,
            "active": s.id == active_id,
            "editable": s.is_editable,
            "excel": s.is_excel,
            "summary": s.summary_line(),
            "errors": s.errors[:3],
        })
    return out
