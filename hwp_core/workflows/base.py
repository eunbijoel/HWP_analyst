"""Workflow run results — structured handoff for UI and tests."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class WorkflowResult:
  workflow_id: str
  ok: bool
  target_document: str = ""
  reference_documents: list[str] = field(default_factory=list)
  proposals: list[dict] = field(default_factory=list)
  skipped: list[dict] = field(default_factory=list)
  fill_trace: list[dict] = field(default_factory=list)
  success_checks: dict[str, bool] = field(default_factory=dict)
  message: str = ""
  meta: dict = field(default_factory=dict)

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)

  @property
  def passed_all_checks(self) -> bool:
    return self.ok and all(self.success_checks.values())
