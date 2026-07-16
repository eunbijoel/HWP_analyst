"""Named document workflows for Korean R&D forms."""

from .base import WorkflowResult
from .catalog import TASK_CATALOG, get_task_catalog, get_workflow_spec
from .registry import list_workflows, match_workflow, run_workflow

__all__ = [
  "WorkflowResult",
  "TASK_CATALOG",
  "get_task_catalog",
  "get_workflow_spec",
  "list_workflows",
  "match_workflow",
  "run_workflow",
]
