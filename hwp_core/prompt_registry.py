"""
버전된 프롬프트 로더.

프롬프트 본문은 hwp_core/prompts/ 아래 MD, 메타는 catalog.yaml.
치환은 whitelist 키만 `{name}` → 값 (JSON 중괄호와 충돌하지 않음).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
CATALOG_PATH = PROMPTS_DIR / "catalog.yaml"


def _substitute(template: str, variables: dict[str, Any]) -> str:
  """알려진 키만 치환. 나머지 `{...}`는 그대로 둔다."""
  out = template
  for key, value in variables.items():
    out = out.replace("{" + key + "}", "" if value is None else str(value))
  return out


@functools.lru_cache(maxsize=1)
def _load_catalog() -> dict:
  with CATALOG_PATH.open(encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}
  return data.get("prompts") or {}


class PromptRegistry:
  """catalog.yaml 기준 프롬프트 조회·렌더."""

  def __init__(self, prompts_dir: Path | None = None):
    self.prompts_dir = Path(prompts_dir) if prompts_dir else PROMPTS_DIR
    self._cache: dict[str, str] = {}

  def list_ids(self) -> list[str]:
    return sorted(_load_catalog().keys())

  def meta(self, prompt_id: str) -> dict:
    entry = _load_catalog().get(prompt_id)
    if not entry:
      raise KeyError(f"Unknown prompt id: {prompt_id}")
    return dict(entry)

  def raw(self, prompt_id: str) -> str:
    if prompt_id in self._cache:
      return self._cache[prompt_id]
    entry = self.meta(prompt_id)
    path = self.prompts_dir / entry["path"]
    if not path.is_file():
      raise FileNotFoundError(f"Prompt file missing: {path}")
    text = path.read_text(encoding="utf-8")
    self._cache[prompt_id] = text
    return text

  def get(self, prompt_id: str, **variables: Any) -> str:
    return _substitute(self.raw(prompt_id), variables)

  def clear_cache(self) -> None:
    self._cache.clear()
    _load_catalog.cache_clear()


def format_memory_section(memory: str | None) -> str:
  """Stage2용 장기 기억 블록. 비어 있으면 빈 문자열 (슬롯만 예약)."""
  text = (memory or "").strip()
  if not text:
    return ""
  return (
    "\n## 장기 기억 (참고용 — 숫자 판단은 문서·사전 계산 결과를 우선):\n"
    f"{text}\n"
  )


def format_issue_section(issues: list | None) -> str:
  """Stage2용 구조화 이슈 블록. consistency_checker.format_issues_for_prompt 위임."""
  if not issues:
    return ""
  from .consistency_checker import format_issues_for_prompt

  text = format_issues_for_prompt(issues).strip()
  return f"\n{text}\n" if text else ""


# 앱 전역에서 재사용하는 기본 레지스트리
default_registry = PromptRegistry()


def render_prompt(prompt_id: str, **variables: Any) -> str:
  return default_registry.get(prompt_id, **variables)
