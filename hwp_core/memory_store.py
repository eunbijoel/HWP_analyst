"""
장기 기억 저장소 (L2).

숫자 판단은 하지 않는다. Q&A Stage2에 참고 문구만 주입.
검색은 키워드 겹침(임베딩 없음) — 건수 적을 때 충분.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB = (
  Path(__file__).resolve().parent.parent / "data" / "memory" / "memories.sqlite"
)

MEM_TYPES = ("note", "fact_confirmed", "term_alias", "preference", "lesson")


@dataclass
class Memory:
  id: int
  mem_type: str
  content: str
  concept_id: str = ""
  document_id: str = ""
  tags: str = ""
  confidence: float = 1.0
  created_at: str = ""


def _utcnow() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tokens(text: str) -> set[str]:
  parts = re.findall(r"[가-힣a-zA-Z0-9]{2,}", (text or "").lower())
  return {p for p in parts if len(p) >= 2}


class MemoryStore:
  def __init__(self, db_path: Path | None = None):
    self.db_path = Path(db_path) if db_path else DEFAULT_DB
    self.db_path.parent.mkdir(parents=True, exist_ok=True)
    self._init_db()

  def _connect(self) -> sqlite3.Connection:
    conn = sqlite3.connect(str(self.db_path))
    conn.row_factory = sqlite3.Row
    return conn

  def _init_db(self) -> None:
    with self._connect() as conn:
      conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          mem_type TEXT NOT NULL,
          content TEXT NOT NULL,
          concept_id TEXT DEFAULT '',
          document_id TEXT DEFAULT '',
          tags TEXT DEFAULT '',
          confidence REAL DEFAULT 1.0,
          created_at TEXT NOT NULL
        )
        """
      )
      conn.commit()

  def add(
    self,
    content: str,
    *,
    mem_type: str = "note",
    concept_id: str = "",
    document_id: str = "",
    tags: str = "",
    confidence: float = 1.0,
  ) -> int:
    text = (content or "").strip()
    if not text:
      raise ValueError("empty memory content")
    if mem_type not in MEM_TYPES:
      mem_type = "note"
    with self._connect() as conn:
      cur = conn.execute(
        """
        INSERT INTO memories
          (mem_type, content, concept_id, document_id, tags, confidence, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
          mem_type,
          text,
          concept_id or "",
          document_id or "",
          tags or "",
          float(confidence),
          _utcnow(),
        ),
      )
      conn.commit()
      return int(cur.lastrowid)

  def delete(self, memory_id: int) -> None:
    with self._connect() as conn:
      conn.execute("DELETE FROM memories WHERE id = ?", (int(memory_id),))
      conn.commit()

  def list_recent(self, limit: int = 20) -> list[Memory]:
    with self._connect() as conn:
      rows = conn.execute(
        "SELECT * FROM memories ORDER BY id DESC LIMIT ?",
        (int(limit),),
      ).fetchall()
    return [self._row(r) for r in rows]

  def count(self) -> int:
    with self._connect() as conn:
      row = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()
    return int(row["n"] if row else 0)

  def retrieve(
    self,
    query: str,
    *,
    limit: int = 5,
    document_id: str | None = None,
  ) -> list[Memory]:
    """질문과 키워드가 겹치는 기억 상위 N개. 겹침 없으면 최신순."""
    q_toks = _tokens(query)
    with self._connect() as conn:
      if document_id:
        rows = conn.execute(
          "SELECT * FROM memories WHERE document_id = '' OR document_id = ?",
          (document_id,),
        ).fetchall()
      else:
        rows = conn.execute("SELECT * FROM memories").fetchall()

    scored: list[tuple[float, Memory]] = []
    for r in rows:
      mem = self._row(r)
      blob = f"{mem.content} {mem.tags} {mem.concept_id} {mem.document_id}"
      m_toks = _tokens(blob)
      overlap = len(q_toks & m_toks) if q_toks else 0
      score = float(overlap) + 0.01 * mem.confidence
      scored.append((score, mem))

    scored.sort(key=lambda x: (x[0], x[1].id), reverse=True)
    if q_toks and any(s > 0.01 for s, _ in scored):
      picked = [m for s, m in scored if s > 0.01][:limit]
      if picked:
        return picked
    # fallback: 최신
    scored.sort(key=lambda x: x[1].id, reverse=True)
    return [m for _, m in scored[:limit]]

  def format_for_prompt(
    self,
    query: str,
    *,
    limit: int = 5,
    document_id: str | None = None,
  ) -> str:
    items = self.retrieve(query, limit=limit, document_id=document_id)
    if not items:
      return ""
    lines = []
    for m in items:
      tag = m.mem_type
      prefix = f"[{tag}] "
      if m.concept_id:
        prefix += f"({m.concept_id}) "
      lines.append(f"- {prefix}{m.content}")
    return "\n".join(lines)

  def remember_qa(
    self,
    question: str,
    answer: str,
    *,
    document_id: str = "",
    max_answer_chars: int = 400,
  ) -> int:
    q = (question or "").strip()
    a = (answer or "").strip()
    if len(a) > max_answer_chars:
      a = a[:max_answer_chars] + "…"
    content = f"Q: {q}\nA: {a}" if q else a
    return self.add(
      content,
      mem_type="lesson",
      document_id=document_id,
      tags="qa",
    )

  @staticmethod
  def _row(r: sqlite3.Row) -> Memory:
    return Memory(
      id=int(r["id"]),
      mem_type=str(r["mem_type"]),
      content=str(r["content"]),
      concept_id=str(r["concept_id"] or ""),
      document_id=str(r["document_id"] or ""),
      tags=str(r["tags"] or ""),
      confidence=float(r["confidence"] or 1.0),
      created_at=str(r["created_at"] or ""),
    )


_default_store: Optional[MemoryStore] = None


def get_memory_store(db_path: Path | None = None) -> MemoryStore:
  global _default_store
  if db_path is not None:
    return MemoryStore(db_path)
  if _default_store is None:
    _default_store = MemoryStore()
  return _default_store
