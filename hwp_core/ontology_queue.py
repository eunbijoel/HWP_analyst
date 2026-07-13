"""
ontology 흡수 큐 — term_alias / 미매칭 라벨 후보.

승인 시 budget_concepts.yaml synonyms에 추가 후 ConceptResolver 리로드.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

_ONTOLOGY_PATH = (
  Path(__file__).resolve().parent / "ontology" / "budget_concepts.yaml"
)
_QUEUE_PATH = (
  Path(__file__).resolve().parent.parent / "data" / "memory" / "ontology_candidates.json"
)


@dataclass
class OntologyCandidate:
  id: str
  label: str
  concept_id: str = ""  # 비어 있으면 승인 시 선택 필요
  source: str = ""      # unmatched | term_alias | manual
  status: str = "pending"  # pending | approved | rejected
  created_at: str = ""
  note: str = ""


def _utcnow() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class OntologyCandidateQueue:
  def __init__(self, path: Path | None = None, ontology_path: Path | None = None):
    self.path = Path(path) if path else _QUEUE_PATH
    self.ontology_path = Path(ontology_path) if ontology_path else _ONTOLOGY_PATH
    self.path.parent.mkdir(parents=True, exist_ok=True)
    if not self.path.is_file():
      self._save([])

  def _load(self) -> list[OntologyCandidate]:
    try:
      raw = json.loads(self.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
      return []
    items = []
    for row in raw if isinstance(raw, list) else []:
      if not isinstance(row, dict):
        continue
      items.append(OntologyCandidate(
        id=str(row.get("id") or ""),
        label=str(row.get("label") or ""),
        concept_id=str(row.get("concept_id") or ""),
        source=str(row.get("source") or ""),
        status=str(row.get("status") or "pending"),
        created_at=str(row.get("created_at") or ""),
        note=str(row.get("note") or ""),
      ))
    return items

  def _save(self, items: list[OntologyCandidate]) -> None:
    payload = [asdict(c) for c in items]
    self.path.write_text(
      json.dumps(payload, ensure_ascii=False, indent=2),
      encoding="utf-8",
    )

  def list_pending(self) -> list[OntologyCandidate]:
    return [c for c in self._load() if c.status == "pending" and c.label.strip()]

  def list_all(self, limit: int = 50) -> list[OntologyCandidate]:
    items = self._load()
    items.sort(key=lambda c: c.created_at, reverse=True)
    return items[:limit]

  def propose(
    self,
    label: str,
    *,
    concept_id: str = "",
    source: str = "manual",
    note: str = "",
  ) -> Optional[OntologyCandidate]:
    text = (label or "").strip()
    if not text:
      return None
    items = self._load()
    # 동일 pending 라벨 중복 방지
    for c in items:
      if c.status == "pending" and c.label.strip() == text:
        if concept_id and not c.concept_id:
          c.concept_id = concept_id
          self._save(items)
        return c
    cand = OntologyCandidate(
      id=uuid.uuid4().hex[:10],
      label=text,
      concept_id=(concept_id or "").strip(),
      source=source,
      status="pending",
      created_at=_utcnow(),
      note=note,
    )
    items.append(cand)
    self._save(items)
    return cand

  def propose_many(self, labels: list[str], *, source: str = "unmatched") -> int:
    n = 0
    for lab in labels:
      if self.propose(lab, source=source):
        n += 1
    return n

  def reject(self, candidate_id: str) -> bool:
    items = self._load()
    for c in items:
      if c.id == candidate_id and c.status == "pending":
        c.status = "rejected"
        self._save(items)
        return True
    return False

  def set_concept(self, candidate_id: str, concept_id: str) -> bool:
    items = self._load()
    for c in items:
      if c.id == candidate_id and c.status == "pending":
        c.concept_id = (concept_id or "").strip()
        self._save(items)
        return True
    return False

  def approve(self, candidate_id: str, concept_id: str | None = None) -> str:
    """synonym을 ontology YAML에 추가. 성공 메시지 또는 에러 문자열."""
    items = self._load()
    cand = next((c for c in items if c.id == candidate_id), None)
    if not cand or cand.status != "pending":
      return "후보를 찾을 수 없습니다."
    cid = (concept_id or cand.concept_id or "").strip()
    if not cid:
      return "concept_id를 선택하세요."
    try:
      msg = append_synonym(cid, cand.label, ontology_path=self.ontology_path)
    except Exception as e:
      return f"YAML 반영 실패: {e}"
    cand.concept_id = cid
    cand.status = "approved"
    self._save(items)
    try:
      from .concept_resolver import reload_concept_resolver
      reload_concept_resolver()
    except Exception:
      pass
    return msg


def append_synonym(
  concept_id: str,
  synonym: str,
  *,
  ontology_path: Path | None = None,
) -> str:
  path = Path(ontology_path) if ontology_path else _ONTOLOGY_PATH
  syn = (synonym or "").strip()
  if not syn:
    raise ValueError("empty synonym")
  with path.open(encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}
  concepts = data.get("concepts") or {}
  if concept_id not in concepts:
    raise KeyError(f"unknown concept_id: {concept_id}")
  spec = concepts[concept_id]
  if not isinstance(spec, dict):
    raise TypeError(f"invalid concept spec: {concept_id}")
  synonyms = list(spec.get("synonyms") or [])
  # 대소문자·공백 무시 중복 체크
  norm = syn.lower().replace(" ", "")
  for existing in synonyms:
    if str(existing).lower().replace(" ", "") == norm:
      return f"이미 있음: {concept_id} ← {syn}"
  synonyms.append(syn)
  spec["synonyms"] = synonyms
  concepts[concept_id] = spec
  data["concepts"] = concepts
  with path.open("w", encoding="utf-8") as f:
    yaml.safe_dump(
      data,
      f,
      allow_unicode=True,
      sort_keys=False,
      default_flow_style=False,
    )
  return f"반영됨: {concept_id} ← {syn}"


_default_queue: Optional[OntologyCandidateQueue] = None


def get_ontology_queue() -> OntologyCandidateQueue:
  global _default_queue
  if _default_queue is None:
    _default_queue = OntologyCandidateQueue()
  return _default_queue
