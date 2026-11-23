"""Lossless archive of every message the compactor has seen.

Folding never deletes anything: originals live here (SQLite, in-memory by default)
and can be searched or recalled by the model through tools. Pointing `path` at a file
lets you open the archive after a run and inspect exactly what was folded and how.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

_WORD = re.compile(r"[\w./:@#-]{2,}")


@dataclass
class Hit:
    idx: int
    ref: str
    role: str
    snippet: str
    score: float


class Archive:
    def __init__(self, path: str = ":memory:"):
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages(
                idx INTEGER PRIMARY KEY, ref TEXT, role TEXT, text TEXT, tokens INTEGER);
            CREATE TABLE IF NOT EXISTS blocks(
                id TEXT PRIMARY KEY, data TEXT);
            """
        )

    def clear(self) -> None:
        self.db.executescript("DELETE FROM messages; DELETE FROM blocks;")
        self.db.commit()

    def put(self, idx: int, ref: str, role: str, text: str, tokens: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?)", (idx, ref, role, text, tokens)
        )
        self.db.commit()

    def put_block(self, block_id: str, data: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO blocks VALUES (?,?)", (block_id, json.dumps(data, ensure_ascii=False))
        )
        self.db.commit()

    def get(self, idx: int) -> tuple[str, str, str, int] | None:
        row = self.db.execute("SELECT ref, role, text, tokens FROM messages WHERE idx=?", (idx,)).fetchone()
        return tuple(row) if row else None

    def search(self, query: str, only: set[int], limit: int = 5, window: int = 220) -> list[Hit]:
        """Keyword search restricted to message indices in `only` (the folded ones)."""
        terms = [t.lower() for t in _WORD.findall(query)]
        if not terms or not only:
            return []
        hits: list[Hit] = []
        marks = ",".join("?" * len(only))
        rows = self.db.execute(
            f"SELECT idx, ref, role, text FROM messages WHERE idx IN ({marks})", tuple(only)
        ).fetchall()
        for idx, ref, role, text in rows:
            low = text.lower()
            counts = [low.count(t) for t in terms]
            matched = sum(1 for c in counts if c)
            if not matched:
                continue
            score = matched * 10 + min(sum(counts), 20)
            # Centre the snippet on the first occurrence of the rarest matched term.
            term = min((t for t, c in zip(terms, counts) if c), key=lambda t: low.count(t))
            pos = low.find(term)
            lo, hi = max(0, pos - window // 2), min(len(text), pos + window // 2)
            snippet = " ".join(text[lo:hi].split())
            hits.append(Hit(idx, ref, role, ("…" if lo else "") + snippet + ("…" if hi < len(text) else ""), score))
        hits.sort(key=lambda h: (-h.score, h.idx))
        return hits[:limit]
