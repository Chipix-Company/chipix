"""SQLite FTS5 search over RTL chunks."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from services.rtl_context.chunker import RtlChunk


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def build_rtl_fts_index(db_path: Path, chunks: list[RtlChunk]) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS rtl_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE rtl_fts USING fts5(
                filepath UNINDEXED,
                module_name UNINDEXED,
                start_line UNINDEXED,
                body,
                tokenize='porter'
            )
            """
        )
        for chunk in chunks:
            conn.execute(
                "INSERT INTO rtl_fts(filepath, module_name, start_line, body) VALUES (?, ?, ?, ?)",
                (chunk.filepath, chunk.module_name, chunk.start_line, chunk.body),
            )
        conn.commit()
    finally:
        conn.close()


def search_rtl_fts(db_path: Path, query: str, *, limit: int = 8) -> list[dict[str, Any]]:
    if not db_path.exists() or not query.strip():
        return []

    safe_query = " ".join(
        f'"{tok}"' if " " in tok else tok
        for tok in query.strip().split()
        if tok
    )
    if not safe_query:
        return []

    conn = _connect(db_path)
    results: list[dict[str, Any]] = []
    try:
        for row in conn.execute(
            """
            SELECT filepath, module_name, start_line,
                   snippet(rtl_fts, 3, '[[', ']]', '...', 32) AS snippet,
                   bm25(rtl_fts) AS score
            FROM rtl_fts
            WHERE rtl_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (safe_query, limit),
        ):
            results.append(
                {
                    "filepath": row["filepath"],
                    "module_name": row["module_name"],
                    "start_line": int(row["start_line"]),
                    "snippet": row["snippet"],
                    "score": float(row["score"]),
                }
            )
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return results
