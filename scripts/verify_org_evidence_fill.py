#!/usr/bin/env python3
"""
Real-file manual verification for organization-form Evidence Fill.

Uses tests/fixtures/org_evidence_fill/* (actual HWPX/XLSX).
No hardcoded expected fill strings — pass/fail is behavioral:
  evidence proposal + source in refs + apply/reopen integrity.

Cases:
  1. Exact labels: 기관명, 대표자, 주소, 전화번호, 이메일
  2. Synonym labels: 주관기관, 대표 성명, TEL, 전자우편
  3. Missing evidence: 사업자등록번호, 법인등록번호 stay empty
  4. Label rejection: 주소/전화번호/대표자 never written as values
  5. Review-before-apply: fills stay proposals until accepted
  6. Save and reopen: accepted values persist; unrelated cells unchanged
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hwp_core.doc_agent.document_editor import apply_proposals  # noqa: E402
from hwp_core.doc_agent.document_inspector import get_fill_resolver  # noqa: E402
from hwp_core.doc_agent.edit_proposal_service import FILL_EVIDENCE  # noqa: E402
from hwp_core.doc_agent.pipeline import DocFillPipeline  # noqa: E402
from hwp_core.hwpx_editor import HWPXEditor  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "org_evidence_fill"
OUT = ROOT / "data" / "validation"
OUT.mkdir(parents=True, exist_ok=True)

EXACT_LABELS = ["기관명", "대표자", "주소", "전화번호", "이메일"]
SYNONYM_LABELS = ["주관기관", "대표 성명", "TEL", "전자우편"]
FILL_LABELS = EXACT_LABELS + SYNONYM_LABELS
MISSING_LABELS = ["사업자등록번호", "법인등록번호"]
FORBIDDEN_VALUES = {"주소", "전화번호", "대표자", "기관명", "이메일", "전자우편", "TEL", "주관기관", "대표 성명"}
SENTINEL = "KEEP_SENTINEL"
REF_NAMES = {"ref_org.hwpx", "ref_org.xlsx"}


def _src_loc(p: dict) -> str:
  srcs = p.get("sources") or []
  if not srcs:
    return "(no source)"
  bits = []
  for s in srcs[:2]:
    bits.append(
      f"{s.get('document', '?')} / {s.get('source_type', '?')} / {s.get('location', '?')}"
    )
  return "; ".join(bits)


def _cell_map(hwpx_bytes: bytes) -> dict[tuple[int, int, int], str]:
  ed = HWPXEditor(hwpx_bytes)
  out: dict[tuple[int, int, int], str] = {}
  for ti in range(ed.get_table_count()):
    rows = ed.get_table_as_rows(ti)
    for ri, row in enumerate(rows):
      for ci, cell in enumerate(row):
        out[(ti, ri, ci)] = str(cell).strip()
  return out


def _find_sentinel(grid: dict[tuple[int, int, int], str]) -> tuple[int, int, int] | None:
  for k, v in grid.items():
    if v == SENTINEL:
      return k
  return None


def _ref_corpus(ref_hwpx: Path, ref_xlsx: Path) -> str:
  """Literal text present in reference files (ground truth for 'came from evidence')."""
  chunks: list[str] = []
  ed = HWPXEditor(ref_hwpx.read_bytes())
  for p in ed.get_paragraphs():
    chunks.append(p.get("text") or "")
  for ti in range(ed.get_table_count()):
    for row in ed.get_table_as_rows(ti):
      chunks.extend(str(c) for c in row)
  try:
    import openpyxl
    wb = openpyxl.load_workbook(ref_xlsx, read_only=True, data_only=True)
    for ws in wb.worksheets:
      for row in ws.iter_rows(values_only=True):
        for c in row:
          if c is not None:
            chunks.append(str(c))
    wb.close()
  except Exception as e:
    chunks.append(f"__xlsx_read_error__:{e}")
  return "\n".join(chunks)


def _sources_from_refs(p: dict) -> bool:
  srcs = p.get("sources") or []
  if not srcs:
    return False
  return any((s.get("document") or "") in REF_NAMES for s in srcs)


def _row(
  case: str,
  test_input: str,
  proposed: str,
  source: str,
  accepted: str,
  reopened: str,
  passed: bool,
  note: str = "",
) -> dict:
  return {
    "case": case,
    "test_input": test_input,
    "proposed_value": proposed,
    "source_location": source,
    "accepted_value": accepted,
    "reopened_value": reopened,
    "pass": passed,
    "note": note,
  }


def main() -> int:
  get_fill_resolver.cache_clear()
  target_path = FIXTURE / "target_org_form.hwpx"
  ref_hwpx = FIXTURE / "ref_org.hwpx"
  ref_xlsx = FIXTURE / "ref_org.xlsx"
  for p in (target_path, ref_hwpx, ref_xlsx):
    if not p.exists():
      print(f"FAIL: missing fixture {p}", file=sys.stderr)
      return 2

  target = target_path.read_bytes()
  original_grid = _cell_map(target)
  sentinel_key = _find_sentinel(original_grid)
  assert sentinel_key is not None, "KEEP_SENTINEL missing in target fixture"
  corpus = _ref_corpus(ref_hwpx, ref_xlsx)

  pipe = DocFillPipeline()
  pipe.register_target("target_org_form.hwpx", target)
  pipe.register_reference("ref_org.hwpx", ref_hwpx.read_bytes())
  pipe.register_reference("ref_org.xlsx", ref_xlsx.read_bytes())
  insp = pipe.run_inspect()
  assert insp["ok"], insp.get("error")

  prop = pipe.run_propose("빈칸을 참고 자료로 채워줘", use_llm=False)
  assert prop["ok"], prop.get("error")
  data = prop["data"] or {}
  proposals = list(data.get("proposals") or [])
  skipped = list(data.get("skipped_facts") or [])
  by_label = {
    (p.get("label") or "").strip(): p
    for p in proposals
    if p.get("action") == "write_table_cell"
  }

  rows: list[dict] = []

  # --- Case 5: before accept, original unchanged ---
  no_apply = apply_proposals(target, proposals, approved_ids=set(), base_dir=OUT / "org_evidence_jobs")
  pending_grid = _cell_map(no_apply["edited_bytes"])
  case5_ok = (
    pending_grid == original_grid
    and no_apply["log"]["applied"] == []
    and all((p.get("status") or "pending") == "pending" for p in proposals)
  )
  rows.append(_row(
    "5. Review-before-apply",
    "propose then apply with approved_ids=∅",
    f"{len(proposals)} proposals pending",
    "n/a",
    "(none applied)",
    "original unchanged" if pending_grid == original_grid else "CHANGED",
    case5_ok,
    f"applied={no_apply['log']['applied']}",
  ))

  # --- Cases 1 & 2: proposal from Evidence, value literally in refs ---
  for case_name, labels in (
    ("1. Exact labels", EXACT_LABELS),
    ("2. Synonym labels", SYNONYM_LABELS),
  ):
    for lab in labels:
      p = by_label.get(lab)
      if not p:
        rows.append(_row(
          case_name, lab, "(no proposal)", "(none)", "—", "—", False, "missing proposal",
        ))
        continue
      after = (p.get("after") or "").strip()
      mode = (p.get("meta") or {}).get("fill_mode")
      src = _src_loc(p)
      in_corpus = bool(after) and after in corpus
      ok = (
        mode == FILL_EVIDENCE
        and _sources_from_refs(p)
        and in_corpus
        and after not in FORBIDDEN_VALUES
        and after != lab
      )
      note = f"fill_mode={mode}; in_ref_corpus={in_corpus}"
      rows.append(_row(case_name, lab, after, src, "(pending)", "(pending)", ok, note))

  # --- Case 3: missing evidence ---
  skipped_labels = {(s.get("label") or "").strip() for s in skipped}
  for lab in MISSING_LABELS:
    p = by_label.get(lab)
    has_prop = p is not None and bool((p.get("after") or "").strip())
    ok = not has_prop
    rows.append(_row(
      "3. Missing evidence",
      lab,
      (p.get("after") if p else "") or "(empty / skipped)",
      _src_loc(p) if p else "skipped_facts",
      "must stay empty",
      "must stay empty",
      ok,
      "in skipped_facts" if lab in skipped_labels else "no proposal",
    ))

  # --- Case 4: label rejection ---
  bad = [
    p for p in proposals
    if p.get("action") == "write_table_cell"
    and (p.get("after") or "").strip() in FORBIDDEN_VALUES
  ]
  rows.append(_row(
    "4. Label rejection",
    "proposed after must not be a field label",
    f"{len(bad)} bad proposal(s)",
    "; ".join(_src_loc(p) for p in bad) or "n/a",
    "n/a",
    "n/a",
    len(bad) == 0,
    f"bad={[((p.get('label') or '') + '=>' + (p.get('after') or '')) for p in bad]}" if bad else "clean",
  ))

  # --- Case 6: accept whatever Evidence proposed; reopen must match ---
  fillable = [
    p for p in proposals
    if p.get("action") == "write_table_cell"
    and (p.get("label") or "").strip() in FILL_LABELS
  ]
  ids = {p["proposal_id"] for p in fillable}
  applied = apply_proposals(
    target, proposals, approved_ids=ids, base_dir=OUT / "org_evidence_jobs",
  )
  edited = applied["edited_bytes"]
  export_path = OUT / "org_evidence_fill_exported.hwpx"
  export_path.write_bytes(edited)
  reopen_grid = _cell_map(export_path.read_bytes())

  filled_keys = {
    (
      int((p.get("meta") or {})["table_id"]),
      int((p.get("meta") or {})["row"]),
      int((p.get("meta") or {})["column"]),
    )
    for p in fillable
    if p["proposal_id"] in applied["log"]["applied"]
  }

  for p in fillable:
    lab = (p.get("label") or "").strip()
    meta = p.get("meta") or {}
    key = (int(meta["table_id"]), int(meta["row"]), int(meta["column"]))
    proposed = (p.get("after") or "").strip()
    accepted = proposed if p["proposal_id"] in applied["log"]["applied"] else "(not applied)"
    reopened_val = reopen_grid.get(key, "")
    ok = (
      p["proposal_id"] in applied["log"]["applied"]
      and reopened_val == proposed
      and reopen_grid.get(sentinel_key) == SENTINEL
    )
    rows.append(_row(
      "6. Save and reopen",
      lab,
      proposed,
      _src_loc(p),
      accepted,
      reopened_val,
      ok,
      f"cell={key}",
    ))

  changed_unrelated = []
  for key, before in original_grid.items():
    after = reopen_grid.get(key, "")
    if before != after and key not in filled_keys:
      changed_unrelated.append((key, before, after))

  sentinel_ok = reopen_grid.get(sentinel_key) == SENTINEL and not changed_unrelated
  rows.append(_row(
    "6. Save and reopen",
    "KEEP_SENTINEL + unrelated cells",
    "—",
    "—",
    SENTINEL,
    reopen_grid.get(sentinel_key, ""),
    sentinel_ok,
    f"unrelated_changes={changed_unrelated[:5]}",
  ))

  print("=" * 100)
  print("Org-form Evidence Fill — real-file verification (no hardcoded expected values)")
  print(f"target: {target_path}")
  print(f"refs:   {ref_hwpx.name}, {ref_xlsx.name}")
  print(f"export: {export_path}")
  print("=" * 100)
  hdr = (
    f"{'CASE':<22} {'INPUT':<28} {'PROPOSED':<36} "
    f"{'SOURCE':<42} {'ACCEPTED':<28} {'REOPENED':<36} {'PASS'}"
  )
  print(hdr)
  print("-" * len(hdr))
  for r in rows:
    print(
      f"{r['case']:<22} {r['test_input'][:28]:<28} {r['proposed_value'][:36]:<36} "
      f"{r['source_location'][:42]:<42} {r['accepted_value'][:28]:<28} "
      f"{r['reopened_value'][:36]:<36} {'PASS' if r['pass'] else 'FAIL'}"
    )
    if r.get("note"):
      print(f"  note: {r['note']}")

  all_pass = all(r["pass"] for r in rows)
  report_path = OUT / "org_evidence_fill_report.json"
  report_path.write_text(
    json.dumps({
      "fixtures": {
        "target": str(target_path),
        "ref_hwpx": str(ref_hwpx),
        "ref_xlsx": str(ref_xlsx),
        "export": str(export_path),
      },
      "proposal_count": len(proposals),
      "skipped_facts": skipped,
      "rows": rows,
      "all_pass": all_pass,
    }, ensure_ascii=False, indent=2),
    encoding="utf-8",
  )
  print("=" * 100)
  print(f"RESULT: {'ALL PASS' if all_pass else 'FAILED'}  ({report_path})")
  return 0 if all_pass else 1


if __name__ == "__main__":
  raise SystemExit(main())
