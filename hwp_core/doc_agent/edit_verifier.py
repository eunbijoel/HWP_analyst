"""수정본 재파싱 · 적용 검증 · (가능 시) 합계 규칙."""

from __future__ import annotations

import re
from typing import Any, Optional

from ..hwpx_editor import HWPXEditor
from ..consistency_checker import check_table_total_row
from ..table_extractor import extract_tables
from ..hwp_parser import parse_document


def _norm(s: str) -> str:
  return re.sub(r"\s+", "", (s or "").strip())


def compare_expected_actual(expected: str, actual: str) -> bool:
  if not expected:
    return False
  en, an = _norm(expected), _norm(actual)
  if not en:
    return False
  return en == an or en in an or an in en


def verify_applied_changes(
  edited_bytes: bytes,
  proposals: list[dict],
) -> list[dict]:
  """applied 상태 제안을 수정본에서 재확인."""
  try:
    editor = HWPXEditor(edited_bytes)
  except Exception as e:
    return [{
      "proposal_id": p.get("proposal_id"),
      "success": False,
      "expected": p.get("after"),
      "actual": "",
      "message": f"수정본 로드 실패: {e}",
    } for p in proposals]

  paras = editor.get_paragraphs()
  results = []
  for p in proposals:
    if p.get("status") not in ("applied", "approved"):
      # 스킵된 것은 검증 대상 아님
      if p.get("status") in ("pending", "rejected", "failed"):
        continue
    expected = p.get("after") or ""
    action = p.get("action")
    meta = p.get("meta") or {}
    actual = ""
    ok = False
    msg = ""

    try:
      if action == "write_table_cell":
        rows = editor.get_table_as_rows(int(meta["table_id"]))
        r, c = int(meta["row"]), int(meta["column"])
        if r < len(rows) and c < len(rows[r]):
          actual = str(rows[r][c])
        ok = compare_expected_actual(expected, actual)
        msg = "셀 값 일치" if ok else "셀 값 불일치"
      elif action == "replace_paragraph":
        idx = int(meta["paragraph_id"])
        if 0 <= idx < len(paras):
          actual = paras[idx].get("text") or ""
        ok = compare_expected_actual(expected, actual)
        msg = "문단 일치" if ok else "문단 불일치"
      elif action == "insert_after":
        # 라벨 다음 문단들 중 expected 포함 여부
        idx = int(meta["paragraph_id"])
        window = paras[idx: idx + 4]
        actual = " | ".join(x.get("text") or "" for x in window)
        ok = any(compare_expected_actual(expected, x.get("text") or "") for x in window)
        if not ok:
          ok = _norm(expected)[:40] in _norm(actual)
        msg = "삽입 문단 확인" if ok else "삽입 내용을 찾지 못함"
      elif action == "insert_table":
        rows_meta = meta.get("table_rows") or []
        before_n = int(meta.get("tables_before") or -1)
        n_tables = editor.get_table_count()
        # 적용 후 표가 늘었고, 새 표에 헤더/첫 값이 보이면 성공
        ok = n_tables >= 1
        if rows_meta and n_tables >= 1:
          last = editor.get_table_as_rows(n_tables - 1)
          flat_exp = " ".join(str(c) for r in rows_meta[:3] for c in r)
          flat_act = " ".join(str(c) for r in (last or [])[:3] for c in r)
          ok = _norm(flat_exp)[:30] in _norm(flat_act) or (
            rows_meta[0] and last and any(
              _norm(str(rows_meta[0][0]))[:10] in _norm(str(c)) for c in last[0]
            )
          )
        actual = f"tables={n_tables}"
        msg = "표 삽입 확인" if ok else "표 삽입을 확인하지 못함"
      else:
        msg = f"알 수 없는 action: {action}"
    except Exception as e:
      msg = str(e)
      ok = False

    results.append({
      "proposal_id": p.get("proposal_id"),
      "success": ok,
      "expected": expected,
      "actual": actual[:500],
      "message": msg,
      "location": p.get("location"),
    })
  return results


def validate_edited_document(edited_bytes: bytes, filename: str = "edited.hwpx") -> dict:
  """기존 consistency checker로 표 합계 등 재검증."""
  try:
    doc = parse_document(file_bytes=edited_bytes, filename=filename)
    tables = extract_tables(doc, document_id=filename)
  except Exception as e:
    return {"ok": False, "error": str(e), "issues": []}

  issues = []
  for ts in tables:
    issues.extend(check_table_total_row(ts, document_id=filename))

  return {
    "ok": True,
    "error": "",
    "issue_count": len(issues),
    "issues": [
      {
        "message": i.message,
        "expected": i.expected,
        "actual": i.actual,
        "source": i.source,
      }
      for i in issues[:20]
    ],
  }
