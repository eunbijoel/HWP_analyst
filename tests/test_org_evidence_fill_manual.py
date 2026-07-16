"""Real-file org Evidence Fill verification (uses tests/fixtures/org_evidence_fill)."""

from __future__ import annotations

import runpy
from pathlib import Path


def test_org_evidence_fill_real_file_verification():
  script = Path(__file__).resolve().parents[1] / "scripts" / "verify_org_evidence_fill.py"
  assert script.exists()
  ns = runpy.run_path(str(script))
  assert ns["main"]() == 0
