"""
개념 사전(ontology) 기반 semantic grounding.

문서 표현(라벨·헤더) → concept_id + confidence
1) exact synonym  2) regex pattern  3) (optional) LLM classify
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

try:
  import yaml
except ImportError:  # pragma: no cover
  yaml = None

from .llm_client import generate_json


_ONTOLOGY_DIR = os.path.join(os.path.dirname(__file__), "ontology")
_DEFAULT_ONTOLOGY = os.path.join(_ONTOLOGY_DIR, "budget_concepts.yaml")

MIN_GROUNDING_CONFIDENCE = 0.85
LLM_GROUNDING_CONFIDENCE = 0.72


@dataclass
class ConceptDef:
  concept_id: str
  label_ko: str = ""
  value_type: str = "money"
  synonyms: list[str] = field(default_factory=list)
  patterns: list[str] = field(default_factory=list)


@dataclass
class GroundingResult:
  raw_text: str
  normalized_text: str
  concept_id: Optional[str] = None
  confidence: float = 0.0
  method: str = "none"  # exact | substring | pattern | llm | none

  @property
  def grounded(self) -> bool:
    return self.concept_id is not None and self.confidence > 0


@dataclass
class GroundingStats:
  total: int = 0
  grounded: int = 0
  llm_grounded: int = 0
  unmatched_labels: list[str] = field(default_factory=list)
  unmatched_hints: list[dict] = field(default_factory=list)

  @property
  def coverage_pct(self) -> float:
    if self.total <= 0:
      return 0.0
    return round(100.0 * self.grounded / self.total, 1)


@dataclass
class GroundingOptions:
  use_llm: bool = False
  model: str = "gemma3:4b"
  ollama_url: str = "http://localhost:11434"
  max_llm_labels: int = 15  # 업로드 시 LLM 호출 상한 (고유 라벨)


def normalize_label(text: str) -> str:
  if not text:
    return ""
  t = str(text).strip().lower()
  t = re.sub(r"\([^)]*\)", " ", t)
  t = re.sub(r"\[[^\]]*\]", " ", t)
  t = re.sub(r"[^\w가-힣]+", "", t)
  return t


class ConceptResolver:
  def __init__(self, ontology_path: Optional[str] = None):
    path = ontology_path or _DEFAULT_ONTOLOGY
    self.ontology_path = path
    self.concepts: dict[str, ConceptDef] = {}
    self._synonym_index: dict[str, str] = {}
    self._compiled_patterns: list[tuple[re.Pattern, str]] = []
    self._llm_cache: dict[str, GroundingResult] = {}
    self._load(path)

  def _load(self, path: str) -> None:
    if not os.path.isfile(path):
      raise FileNotFoundError(f"Ontology not found: {path}")
    with open(path, encoding="utf-8") as f:
      if yaml is None:
        raise ImportError(
          "PyYAML required for ontology loading. Install: pip install pyyaml"
        )
      data = yaml.safe_load(f) or {}

    raw_concepts = data.get("concepts") or {}
    for concept_id, spec in raw_concepts.items():
      if not isinstance(spec, dict):
        continue
      cdef = ConceptDef(
        concept_id=str(concept_id),
        label_ko=str(spec.get("label_ko") or concept_id),
        value_type=str(spec.get("value_type") or "money"),
        synonyms=[str(s) for s in (spec.get("synonyms") or [])],
        patterns=[str(p) for p in (spec.get("patterns") or [])],
      )
      self.concepts[concept_id] = cdef

    self._build_indexes()

  def _build_indexes(self) -> None:
    self._synonym_index.clear()
    self._compiled_patterns.clear()

    for concept_id, cdef in self.concepts.items():
      for syn in cdef.synonyms:
        key = normalize_label(syn)
        if key and key not in self._synonym_index:
          self._synonym_index[key] = concept_id
      label_key = normalize_label(cdef.label_ko)
      if label_key and label_key not in self._synonym_index:
        self._synonym_index[label_key] = concept_id

      for pat in cdef.patterns:
        try:
          self._compiled_patterns.append((re.compile(pat, re.I), concept_id))
        except re.error:
          continue

  def concept_catalog(self) -> list[dict]:
    return [
      {
        "id": c.concept_id,
        "label_ko": c.label_ko,
        "synonyms": c.synonyms[:8],
      }
      for c in self.concepts.values()
    ]

  def ground(self, label: str, context: str = "") -> GroundingResult:
    raw = f"{label} {context}".strip() if context else str(label or "").strip()
    norm_label = normalize_label(label or "")
    norm_full = normalize_label(raw)
    best: Optional[GroundingResult] = None

    def _consider(candidate: GroundingResult) -> None:
      nonlocal best
      if not candidate.grounded:
        return
      if best is None or candidate.confidence > best.confidence:
        best = candidate

    if norm_label in self._synonym_index:
      _consider(GroundingResult(
        raw_text=raw,
        normalized_text=norm_label,
        concept_id=self._synonym_index[norm_label],
        confidence=1.0,
        method="exact",
      ))

    for norm_syn, concept_id in self._synonym_index.items():
      if len(norm_syn) < 2:
        continue
      if norm_label == norm_syn:
        continue
      if norm_syn in norm_label:
        _consider(GroundingResult(
          raw_text=raw,
          normalized_text=norm_label,
          concept_id=concept_id,
          confidence=0.95,
          method="substring",
        ))
      elif len(norm_syn) >= 3 and norm_syn in norm_full:
        _consider(GroundingResult(
          raw_text=raw,
          normalized_text=norm_full,
          concept_id=concept_id,
          confidence=0.88,
          method="substring",
        ))

    for compiled, concept_id in self._compiled_patterns:
      if compiled.search(raw):
        _consider(GroundingResult(
          raw_text=raw,
          normalized_text=norm_full or norm_label,
          concept_id=concept_id,
          confidence=0.90,
          method="pattern",
        ))

    if best is not None:
      return best

    return GroundingResult(
      raw_text=raw,
      normalized_text=norm_label or norm_full,
      concept_id=None,
      confidence=0.0,
      method="none",
    )

  def ground_with_llm(
    self,
    label: str,
    context: str = "",
    *,
    options: Optional[GroundingOptions] = None,
  ) -> GroundingResult:
    """규칙 매칭 후 실패 시에만 LLM 분류 (optional)."""
    base = self.ground(label, context)
    if base.confidence >= MIN_GROUNDING_CONFIDENCE:
      return base

    opts = options or GroundingOptions()
    if not opts.use_llm:
      return base

    cache_key = normalize_label(f"{label}|{context}")
    if cache_key in self._llm_cache:
      return self._llm_cache[cache_key]

    llm_result = self._llm_classify(label, context, options=opts)
    self._llm_cache[cache_key] = llm_result
    if llm_result.grounded and (base.confidence == 0 or llm_result.confidence > base.confidence):
      return llm_result
    return base if base.grounded else llm_result

  def _llm_classify(
    self,
    label: str,
    context: str,
    *,
    options: GroundingOptions,
  ) -> GroundingResult:
    raw = f"{label} {context}".strip() if context else str(label or "").strip()
    norm = normalize_label(label or "")
    from .prompt_registry import render_prompt

    catalog = self.concept_catalog()
    prompt = render_prompt(
      "grounding.classify_label",
      label=label,
      context=context or "(없음)",
      catalog_json=json.dumps(catalog, ensure_ascii=False, indent=2),
    )
    parsed, err = generate_json(
      prompt,
      options.model,
      options.ollama_url,
      temperature=0.1,
      num_predict=256,
      num_ctx=8192,
      timeout=60,
    )
    if err or not isinstance(parsed, dict):
      return GroundingResult(
        raw_text=raw,
        normalized_text=norm,
        concept_id=None,
        confidence=0.0,
        method="none",
      )

    cid = parsed.get("concept_id")
    if cid in ("null", "none", ""):
      cid = None
    if cid and cid not in self.concepts:
      cid = None

    try:
      conf = float(parsed.get("confidence", LLM_GROUNDING_CONFIDENCE))
    except (TypeError, ValueError):
      conf = LLM_GROUNDING_CONFIDENCE

    if cid is None:
      return GroundingResult(
        raw_text=raw,
        normalized_text=norm,
        concept_id=None,
        confidence=0.0,
        method="none",
      )

    return GroundingResult(
      raw_text=raw,
      normalized_text=norm,
      concept_id=str(cid),
      confidence=min(0.95, max(0.5, conf)),
      method="llm",
    )

  def has_concept(self, concept_id: str, label: str, context: str = "",
                  *, min_confidence: float = MIN_GROUNDING_CONFIDENCE) -> bool:
    result = self.ground(label, context)
    return (
      result.concept_id == concept_id
      and result.confidence >= min_confidence
    )

  def column_concept(self, column_name: str) -> GroundingResult:
    return self.ground(str(column_name or ""))


@lru_cache(maxsize=1)
def get_concept_resolver() -> ConceptResolver:
  return ConceptResolver()


def reload_concept_resolver() -> ConceptResolver:
  """ontology YAML 변경 후 인덱스 재적재."""
  get_concept_resolver.cache_clear()
  return get_concept_resolver()


def compute_grounding_stats(
  facts: list,
  *,
  min_confidence: float = MIN_GROUNDING_CONFIDENCE,
) -> GroundingStats:
  stats = GroundingStats()
  seen_unmatched: set[str] = set()

  for f in facts:
    stats.total += 1
    conf = getattr(f, "concept_confidence", 0.0) or 0.0
    concept = getattr(f, "concept", None)
    method = getattr(f, "grounding_method", "") or ""
    if concept and conf >= min_confidence:
      stats.grounded += 1
      if method == "llm":
        stats.llm_grounded += 1
      continue

    raw = getattr(f, "raw_label", "") or ""
    ctx = getattr(f, "context", "") or getattr(f, "column", "") or ""
    if raw and raw not in seen_unmatched:
      seen_unmatched.add(raw)
      stats.unmatched_labels.append(raw)
      stats.unmatched_hints.append({
        "label": raw,
        "context": str(ctx)[:80],
        "yaml_hint": f'      - "{raw}"  # TODO: 적절한 concept synonyms에 추가',
      })

  return stats
