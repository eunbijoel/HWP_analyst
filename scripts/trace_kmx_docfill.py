#!/usr/bin/env python3
"""Run explainable DocFill trace on real KMX documents. Does not change fill rules."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hwp_core.doc_agent.document_inspector import get_fill_resolver  # noqa: E402
from hwp_core.doc_agent.fill_trace import save_fill_trace, summarize_trace  # noqa: E402
from hwp_core.doc_agent.pipeline import DocFillPipeline  # noqa: E402

KMX = ROOT / "data" / "kmx_task"
OUT = ROOT / "data" / "validation"


def main() -> int:
  get_fill_resolver.cache_clear()
  target = KMX / "KMX empty.hwpx"
  refs = [
    KMX / "4예실대비표.xlsx",
  ]
  # Optional second HWPX ref if present in validation
  extra = ROOT / "data" / "validation" / "src_ref.hwpx"
  if extra.exists():
    refs.append(extra)

  for p in [target, *refs]:
    if not p.exists():
      print(f"missing: {p}", file=sys.stderr)
      return 2

  pipe = DocFillPipeline()
  pipe.register_target(target.name, target.read_bytes())
  for r in refs:
    pipe.register_reference(r.name, r.read_bytes())

  insp = pipe.run_inspect()
  print("inspect ok=", insp.get("ok"), "fields=", len((insp.get("data") or {}).get("fields") or pipe.tools.last_fields))

  out = pipe.run_propose("빈칸을 참고 자료로 채워줘", use_llm=False)
  data = out.get("data") or {}
  traces = data.get("fill_trace") or pipe.tools.last_fill_trace
  proposals = data.get("proposals") or []
  skipped = data.get("skipped_facts") or []

  path = save_fill_trace(
    traces,
    out_dir=OUT,
    prefix="kmx_docfill_trace",
    meta={
      "target": str(target),
      "references": [str(r) for r in refs],
      "proposal_count": len(proposals),
      "skipped_count": len(skipped),
      "command": "빈칸을 참고 자료로 채워줘",
      "debug": os.environ.get("DOCFILL_DEBUG", "1"),
    },
  )
  summary = summarize_trace(traces)
  summary_path = OUT / "kmx_docfill_trace_summary.json"
  summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

  # Highlight suspicious proposed values (labels / instructional)
  suspicious = []
  for t in traces:
    sel = (t.get("selected_value") if isinstance(t, dict) else t.selected_value) or ""
    lab = t.get("raw_label") if isinstance(t, dict) else t.raw_label
    cid = t.get("concept_id") if isinstance(t, dict) else t.concept_id
    status = t.get("final_status") if isinstance(t, dict) else t.final_status
    if status != "proposed":
      continue
    if sel in ("주소", "전화번호", "대표자", "성명", "직위", "책임자", "기관명") or any(
      k in sel for k in ("연월일", "유형", "기입", "출연연", "중소기업")
    ):
      suspicious.append({"label": lab, "concept_id": cid, "selected": sel, "location": t.get("location") if isinstance(t, dict) else t.location})

  print("=" * 72)
  print(f"trace JSON: {path}")
  print(f"summary:    {summary_path}")
  print(f"by_status:  {summary.get('by_status')}")
  print(f"proposals:  {len(proposals)}  skipped_facts: {len(skipped)}")
  print(f"suspicious proposed: {len(suspicious)}")
  for s in suspicious[:20]:
    print("  SUSPICIOUS", s)
  # Print interesting form fields (성명/책임자/기관)
  interesting = []
  for t in traces:
    d = t if isinstance(t, dict) else t.to_dict()
    lab = d.get("raw_label") or ""
    if any(k in lab for k in ("성명", "책임자", "기관", "대표", "전화", "우편", "유형", "연월일", "연구자")):
      interesting.append(d)
  print(f"interesting fields: {len(interesting)}")
  for d in interesting[:30]:
    print(
      f"  [{d.get('final_status')}] label={d.get('raw_label')!r} "
      f"cid={d.get('concept_id')} conf={d.get('grounding_confidence')} "
      f"method={d.get('grounding_method')} "
      f"selected={d.get('selected_value')!r} "
      f"cands={len(d.get('candidates') or [])} "
      f"loc={d.get('location')}"
    )
    for c in (d.get("candidates") or [])[:5]:
      mark = "OK" if c.get("accepted") else f"REJECT:{c.get('rejection_reason')}"
      print(f"    - {mark} {c.get('value')!r} @ {c.get('source_document')}/{c.get('source_location')}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
