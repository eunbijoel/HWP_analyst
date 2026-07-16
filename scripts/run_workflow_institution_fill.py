#!/usr/bin/env python3
"""Run workflow 1: fill_institution_info on org-form fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hwp_core.doc_agent.pipeline import DocFillPipeline  # noqa: E402
from hwp_core.workflows.registry import run_workflow  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "org_evidence_fill"
OUT = ROOT / "data" / "validation"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> int:
  target_path = FIXTURE / "target_org_form.hwpx"
  ref_hwpx = FIXTURE / "ref_org.hwpx"
  ref_xlsx = FIXTURE / "ref_org.xlsx"
  for p in (target_path, ref_hwpx, ref_xlsx):
    if not p.exists():
      print(f"FAIL: missing fixture {p}", file=sys.stderr)
      return 2

  pipe = DocFillPipeline()
  pipe.register_target("target_org_form.hwpx", target_path.read_bytes())
  pipe.register_reference("ref_org.hwpx", ref_hwpx.read_bytes())
  pipe.register_reference("ref_org.xlsx", ref_xlsx.read_bytes())

  result = run_workflow(
    "fill_institution_info",
    pipe,
    command="기관 정보를 참고 자료로 채워줘",
    use_llm=False,
  )

  report = result.to_dict()
  report_path = OUT / "workflow_institution_fill_report.json"
  report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

  print("=" * 80)
  print("Workflow: fill_institution_info (기관 정보 채우기)")
  print(f"ok={result.ok}  proposals={len(result.proposals)}  skipped={len(result.skipped)}")
  print(f"message: {result.message}")
  print("success_checks:")
  for k, v in sorted(result.success_checks.items()):
    print(f"  {k}: {'PASS' if v else 'FAIL'}")
  print("-" * 80)
  for p in result.proposals[:15]:
    lab = p.get("label") or "?"
    after = (p.get("after") or "")[:50]
    src = (p.get("sources") or [{}])[0]
    loc = f"{src.get('document', '?')} / {src.get('location', '?')}"
    print(f"  · {lab}: 「{after}」 ← {loc}")
  if result.skipped:
    print("skipped:")
    for s in result.skipped[:10]:
      print(f"  · {s.get('label') or s.get('concept_id')}: {s.get('reason')}")
  print("=" * 80)
  print(f"report: {report_path}")

  passed = result.ok and all(result.success_checks.values())
  print(f"RESULT: {'ALL PASS' if passed else 'FAILED'}")
  return 0 if passed else 1


if __name__ == "__main__":
  raise SystemExit(main())
