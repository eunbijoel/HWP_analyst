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

_FALLBACK_REL = 0.02
_FALLBACK_ABS = 1.0
_FALLBACK_MIN_CONF = 0.85


@functools.lru_cache(maxsize=1)
def _load_all() -> dict[str, Any]:
  """catalog + 각 path 파일을 합쳐 rule_id → config dict."""
  with CATALOG_PATH.open(encoding="utf-8") as f:
    catalog = yaml.safe_load(f) or {}

  defaults: dict[str, Any] = {}
  concept_tol: dict[str, Any] = {}
  file_cache: dict[str, dict] = {}
  merged: dict[str, Any] = {
    "_meta": {"version": catalog.get("version"), "domain": catalog.get("domain")},
  }

  for rule_id, entry in (catalog.get("rules") or {}).items():
    path_name = (entry or {}).get("path") or "budget_checks.yaml"
    if path_name not in file_cache:
      path = RULES_DIR / path_name
      with path.open(encoding="utf-8") as f:
        file_cache[path_name] = yaml.safe_load(f) or {}
    data = file_cache[path_name]
    if not defaults:
      defaults = dict(data.get("defaults") or {})
    if not concept_tol and data.get("concept_tol"):
      concept_tol = dict(data.get("concept_tol") or {})
    rule_cfg = dict((data.get("rules") or {}).get(rule_id) or {})
    rule_cfg.setdefault("description", (entry or {}).get("description", ""))
    for k, v in defaults.items():
      rule_cfg.setdefault(k, v)
    merged[rule_id] = rule_cfg

  if not concept_tol:
    for data in file_cache.values():
      for cid, cfg in (data.get("concept_tol") or {}).items():
        concept_tol[cid] = dict(cfg or {})

  merged["_defaults"] = defaults
  merged["_concept_tol"] = concept_tol
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

  def concept_tol_map(self) -> dict[str, dict[str, Any]]:
    raw = _load_all().get("_concept_tol") or {}
    return {str(k): dict(v or {}) for k, v in raw.items()}

  def clear_cache(self) -> None:
    _load_all.cache_clear()


default_rules = RuleRegistry()


def get_rule(rule_id: str) -> dict[str, Any]:
  return default_rules.get(rule_id)


def rule_enabled(rule_id: str) -> bool:
  return default_rules.enabled(rule_id)


def clear_rule_cache() -> None:
  default_rules.clear_cache()


def _concept_cfg(concept_id: str | None) -> dict[str, Any]:
  if not concept_id:
    return {}
  return dict((_load_all().get("_concept_tol") or {}).get(concept_id) or {})


def resolve_tol(
  rule_id: str | None = None,
  concept_id: str | None = None,
) -> tuple[float, float]:
  """허용오차 해석: concept_tol > 규칙(기본값 포함) > fallback.

  Returns:
    (rel_tol, abs_tol)
  """
  defaults = _load_all().get("_defaults") or {}
  rel = float(defaults.get("rel_tol", _FALLBACK_REL))
  abs_t = float(defaults.get("abs_tol", _FALLBACK_ABS))

  if rule_id:
    try:
      cfg = get_rule(rule_id)
      if cfg.get("rel_tol") is not None:
        rel = float(cfg["rel_tol"])
      if cfg.get("abs_tol") is not None:
        abs_t = float(cfg["abs_tol"])
    except KeyError:
      pass

  ccfg = _concept_cfg(concept_id)
  if ccfg.get("rel_tol") is not None:
    rel = float(ccfg["rel_tol"])
  if ccfg.get("abs_tol") is not None:
    abs_t = float(ccfg["abs_tol"])

  return rel, abs_t


def resolve_min_confidence(
  rule_id: str | None = None,
  concept_id: str | None = None,
) -> float:
  """grounding min_confidence: concept_tol > 규칙 > defaults > fallback."""
  defaults = _load_all().get("_defaults") or {}
  conf = float(defaults.get("min_confidence", _FALLBACK_MIN_CONF))

  if rule_id:
    try:
      cfg = get_rule(rule_id)
      if cfg.get("min_confidence") is not None:
        conf = float(cfg["min_confidence"])
    except KeyError:
      pass

  ccfg = _concept_cfg(concept_id)
  if ccfg.get("min_confidence") is not None:
    conf = float(ccfg["min_confidence"])

  return conf


def stricter_tol(*pairs: tuple[float, float]) -> tuple[float, float]:
  """여러 (rel, abs) 중 더 엄격한 쪽 (작은 값)을 고른다."""
  if not pairs:
    return _FALLBACK_REL, _FALLBACK_ABS
  return min(p[0] for p in pairs), min(p[1] for p in pairs)
