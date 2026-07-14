"""승인된 제안만 HWPX 복사본에 적용. 원본 바이트는 불변."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ..hwpx_editor import HWPXEditor, PendingChange


def _job_dir(base: Path, job_id: str) -> Path:
  d = base / job_id
  d.mkdir(parents=True, exist_ok=True)
  return d


def save_snapshot(
  original_bytes: bytes,
  proposals: list[dict],
  *,
  base_dir: str | Path = "data/doc_work",
  job_id: str | None = None,
) -> dict:
  job_id = job_id or f"job_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
  root = _job_dir(Path(base_dir), job_id)
  (root / "original.hwpx").write_bytes(original_bytes)
  (root / "proposals.json").write_text(
    json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8",
  )
  return {"job_id": job_id, "dir": str(root), "original_path": str(root / "original.hwpx")}


def write_paragraph(editor: HWPXEditor, paragraph_index: int, new_text: str) -> bool:
  return editor._set_paragraph_text(paragraph_index, new_text, track_changes=False)


def write_table_cell(editor: HWPXEditor, table_index: int, row: int, col: int, new_value: str) -> bool:
  return editor.edit_table_cell(table_index, row, col, new_value)


def insert_after_paragraph(editor: HWPXEditor, paragraph_index: int, new_text: str) -> bool:
  return editor._insert_text_after_paragraph(paragraph_index, new_text, track_changes=False)


def insert_table_after_paragraph(editor: HWPXEditor, paragraph_index: int, rows: list[list[str]]) -> bool:
  return editor.insert_table_after_paragraph(paragraph_index, rows, track_changes=False)


def apply_proposals(
  original_bytes: bytes,
  proposals: list[dict],
  *,
  approved_ids: set[str] | None = None,
  base_dir: str | Path = "data/doc_work",
  job_id: str | None = None,
) -> dict:
  """approved만 적용. 원본 파일/바이트는 수정하지 않음."""
  snap = save_snapshot(original_bytes, proposals, base_dir=base_dir, job_id=job_id)
  job_dir = Path(snap["dir"])

  # 원본 보존 확인용 해시 전
  original_copy = bytes(original_bytes)
  editor = HWPXEditor(original_copy)

  applied = []
  failed = []
  skipped = []

  for p in proposals:
    pid = p.get("proposal_id")
    status = p.get("status") or "pending"
    if approved_ids is not None and pid not in approved_ids:
      skipped.append(pid)
      continue
    if status == "rejected":
      skipped.append(pid)
      continue

    action = p.get("action")
    meta = p.get("meta") or {}
    ok = False
    try:
      if action == "replace_paragraph":
        idx = meta.get("paragraph_id")
        if idx is None:
          raise ValueError("paragraph_id 없음")
        ok = write_paragraph(editor, int(idx), p.get("after") or "")
      elif action == "insert_after":
        idx = meta.get("paragraph_id")
        if idx is None:
          raise ValueError("paragraph_id 없음")
        ok = insert_after_paragraph(editor, int(idx), p.get("after") or "")
      elif action == "insert_table":
        idx = meta.get("paragraph_id")
        rows = meta.get("table_rows") or []
        if idx is None:
          raise ValueError("paragraph_id 없음")
        if not rows:
          raise ValueError("table_rows 없음")
        ok = insert_table_after_paragraph(editor, int(idx), rows)
      elif action == "write_table_cell":
        ok = write_table_cell(
          editor,
          int(meta["table_id"]),
          int(meta["row"]),
          int(meta["column"]),
          p.get("after") or "",
        )
      else:
        raise ValueError(f"허용되지 않은 action: {action}")
    except Exception as e:
      failed.append({"proposal_id": pid, "error": str(e)})
      p["status"] = "failed"
      continue

    if ok:
      p["status"] = "applied"
      applied.append(pid)
    else:
      p["status"] = "failed"
      failed.append({"proposal_id": pid, "error": "write returned False"})

  edited_bytes = editor.get_export_bytes()
  out_path = job_dir / "edited.hwpx"
  out_path.write_bytes(edited_bytes)
  log = {
    "job_id": snap["job_id"],
    "applied": applied,
    "failed": failed,
    "skipped": skipped,
    "edited_path": str(out_path),
    "original_unchanged": original_bytes == original_copy,  # always True - we copied
  }
  # 원본 스냅샷과 입력 원본 동일 확인
  saved_orig = (job_dir / "original.hwpx").read_bytes()
  log["original_file_matches_input"] = saved_orig == original_bytes
  (job_dir / "apply_log.json").write_text(
    json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8",
  )
  (job_dir / "proposals.json").write_text(
    json.dumps(proposals, ensure_ascii=False, indent=2), encoding="utf-8",
  )
  return {
    "ok": len(failed) == 0 and len(applied) > 0,
    "job_id": snap["job_id"],
    "edited_bytes": edited_bytes,
    "edited_path": str(out_path),
    "log": log,
    "proposals": proposals,
  }


def save_as_hwpx(editor: HWPXEditor, path: str | Path) -> Path:
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_bytes(editor.get_export_bytes())
  return path
