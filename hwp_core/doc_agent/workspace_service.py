"""업로드된 대상/참고 문서 워크스페이스 관리."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class WorkspaceDocument:
  document_id: str
  filename: str
  role: str  # target | reference
  file_type: str
  file_bytes: bytes
  registered_at: float = field(default_factory=time.time)
  parsed: Optional[Any] = None  # ParsedDocument or ReferenceDocument
  tables: list = field(default_factory=list)
  excel_sheets: list[dict] = field(default_factory=list)  # [{name, rows}]
  parse_error: str = ""
  meta: dict = field(default_factory=dict)

  @property
  def size(self) -> int:
    return len(self.file_bytes or b"")


class WorkspaceService:
  def __init__(self) -> None:
    self._docs: dict[str, WorkspaceDocument] = {}
    self.target_id: Optional[str] = None

  def clear(self) -> None:
    self._docs.clear()
    self.target_id = None

  def _make_id(self, filename: str, role: str) -> str:
    h = hashlib.sha256(f"{role}:{filename}:{time.time()}".encode()).hexdigest()[:10]
    safe = filename.replace(" ", "_")[:40]
    return f"{role}_{safe}_{h}"

  def register_target_document(
    self,
    filename: str,
    file_bytes: bytes,
    *,
    parsed: Any = None,
    tables: list | None = None,
  ) -> WorkspaceDocument:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    doc_id = self._make_id(filename, "target")
    # 같은 파일명 대상이 있으면 교체
    for existing in list(self._docs.values()):
      if existing.role == "target" and existing.filename == filename:
        del self._docs[existing.document_id]
    wd = WorkspaceDocument(
      document_id=doc_id,
      filename=filename,
      role="target",
      file_type=ext,
      file_bytes=bytes(file_bytes),
      parsed=parsed,
      tables=tables or [],
    )
    self._docs[doc_id] = wd
    self.target_id = doc_id
    return wd

  def register_reference_document(
    self,
    filename: str,
    file_bytes: bytes,
    *,
    parsed: Any = None,
    tables: list | None = None,
    excel_sheets: list[dict] | None = None,
  ) -> WorkspaceDocument:
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    doc_id = self._make_id(filename, "ref")
    for existing in list(self._docs.values()):
      if existing.role == "reference" and existing.filename == filename:
        del self._docs[existing.document_id]
    wd = WorkspaceDocument(
      document_id=doc_id,
      filename=filename,
      role="reference",
      file_type=ext,
      file_bytes=bytes(file_bytes),
      parsed=parsed,
      tables=tables or [],
      excel_sheets=excel_sheets or [],
    )
    self._docs[doc_id] = wd
    return wd

  def list_workspace_files(self) -> list[dict]:
    rows = []
    for d in self._docs.values():
      rows.append({
        "document_id": d.document_id,
        "filename": d.filename,
        "role": d.role,
        "file_type": d.file_type,
        "size": d.size,
        "parse_error": d.parse_error,
        "is_target": d.document_id == self.target_id,
      })
    return rows

  def get_document(self, document_id: str) -> Optional[WorkspaceDocument]:
    return self._docs.get(document_id)

  def get_target(self) -> Optional[WorkspaceDocument]:
    if not self.target_id:
      return None
    return self._docs.get(self.target_id)

  def list_references(self) -> list[WorkspaceDocument]:
    return [d for d in self._docs.values() if d.role == "reference"]
