"""Explainable DocFill field traces — provenance without changing fill selection."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


STATUS_PROPOSED = "proposed"
STATUS_SKIPPED_NO_EVIDENCE = "skipped_no_evidence"
STATUS_SKIPPED_UNSAFE = "skipped_unsafe"
STATUS_SKIPPED_NO_CALC = "skipped_no_calc"
STATUS_NEEDS_REVIEW = "needs_review"


@dataclass
class CandidateTrace:
  value: str
  source_document: str
  source_location: str
  source_type: str = "table_cell"
  accepted: bool = False
  rejection_reason: str = ""

  def to_dict(self) -> dict:
    return asdict(self)


@dataclass
class FieldFillTrace:
  target_document: str
  location: dict
  raw_label: str
  concept_id: str
  grounding_confidence: float
  grounding_method: str
  expected_value_type: str = ""
  candidate_ranking: list[Any] = field(default_factory=list)
  rejected_candidates: list[Any] = field(default_factory=list)
  accepted_candidate: Optional[dict] = None
  final_proposal: Optional[dict] = None
  candidates: list[Any] = field(default_factory=list)  # alias: candidate_ranking
  selected_value: Optional[str] = None
  final_status: str = STATUS_SKIPPED_NO_EVIDENCE
  notes: list[str] = field(default_factory=list)

  def to_dict(self) -> dict:
    d = asdict(self)
    ranking = self.candidate_ranking or self.candidates
    d["candidate_ranking"] = [
      c.to_dict() if hasattr(c, "to_dict") else c for c in ranking
    ]
    d["candidates"] = d["candidate_ranking"]
    d["rejected_candidates"] = [
      c.to_dict() if hasattr(c, "to_dict") else c
      for c in (self.rejected_candidates or [])
    ]
    if self.accepted_candidate and hasattr(self.accepted_candidate, "to_dict"):
      d["accepted_candidate"] = self.accepted_candidate.to_dict()
    return d


def save_fill_trace(
  traces: list[FieldFillTrace] | list[dict],
  *,
  out_dir: str | Path = "data/validation",
  prefix: str = "docfill_trace",
  meta: dict | None = None,
) -> Path:
  root = Path(out_dir)
  root.mkdir(parents=True, exist_ok=True)
  path = root / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.json"
  payload = {
    "meta": meta or {},
    "field_count": len(traces),
    "fields": [t.to_dict() if hasattr(t, "to_dict") else t for t in traces],
  }
  path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
  return path


def summarize_trace(traces: list[FieldFillTrace] | list[dict]) -> dict[str, Any]:
  rows = [t.to_dict() if hasattr(t, "to_dict") else t for t in traces]
  by_status: dict[str, int] = {}
  for r in rows:
    st = r.get("final_status") or "?"
    by_status[st] = by_status.get(st, 0) + 1
  rejected = []
  for r in rows:
    cands = r.get("candidate_ranking") or r.get("candidates") or []
    for c in cands:
      if not c.get("accepted") and c.get("rejection_reason"):
        rejected.append({
          "label": r.get("raw_label"),
          "rank": c.get("rank"),
          "value": c.get("value"),
          "expected_type": c.get("expected_value_type") or r.get("expected_value_type"),
          "reason": c.get("rejection_reason"),
          "source": f"{c.get('source_document')} / {c.get('source_location')}",
        })
  return {
    "by_status": by_status,
    "proposed": [r for r in rows if r.get("final_status") == STATUS_PROPOSED],
    "rejected_candidates_sample": rejected[:40],
  }
