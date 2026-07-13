"""
검증 규칙 로더 — ontology / prompts 와 동일 패턴.

계산 로직은 consistency_checker, 허용오차·on/off·concept 매핑은 YAML.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

RULES_DIR = Path(__file__).resolve().parent / "rules"
CATALOG_PATH = RULES_DIR / "catalog.yaml"


@functools.lru_cache(maxsize=1)
def _load_all() -> dict[str, Any]:
  """catalog + 각 path 파일을 합쳐 rule_id → config dict."""
  with CATALOG_PATH.open(encoding="utf-8") as f:
    catalog = yaml.safe_load(f) or {}

  defaults: dict[str, Any] = {}
  file_cache: dict[str, dict] = {}
  merged: dict[str, Any] = {"_meta": {"version": catalog.get("version"), "domain": catalog.get("domain")}}

  for rule_id, entry in (catalog.get("rules") or {}).items():
    path_name = (entry or {}).get("path") or "budget_checks.yaml"
    if path_name not in file_cache:
      path = RULES_DIR / path_name
      with path.open(encoding="utf-8") as f:
        file_cache[path_name] = yaml.safe_load(f) or {}
    data = file_cache[path_name]
    if not defaults:
      defaults = dict(data.get("defaults") or {})
    rule_cfg = dict((data.get("rules") or {}).get(rule_id) or {})
    # catalog 메타 덮어쓰기
    rule_cfg.setdefault("description", (entry or {}).get("description", ""))
    # defaults 병합 (rule에 없는 키만)
    for k, v in defaults.items():
      rule_cfg.setdefault(k, v)
    merged[rule_id] = rule_cfg

  merged["_defaults"] = defaults
  return merged


class RuleRegistry:
  def list_ids(self) -> list[str]:
    return sorted(k for k in _load_all() if not k.startswith("_"))

  def get(self, rule_id: str) -> dict[str, Any]:
    data = _load_all()
    if rule_id not in data or rule_id.startswith("_"):
      raise KeyError(f"Unknown rule id: {rule_id}")
    return dict(data[rule_id])

  def enabled(self, rule_id: str) -> bool:
    try:
      return bool(self.get(rule_id).get("enabled", True))
    except KeyError:
      return False

  def defaults(self) -> dict[str, Any]:
    return dict(_load_all().get("_defaults") or {})

  def clear_cache(self) -> None:
    _load_all.cache_clear()


default_rules = RuleRegistry()


def get_rule(rule_id: str) -> dict[str, Any]:
  return default_rules.get(rule_id)


def rule_enabled(rule_id: str) -> bool:
  return default_rules.enabled(rule_id)


def clear_rule_cache() -> None:
  default_rules.clear_cache()
