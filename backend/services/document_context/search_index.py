"""Local SQLite FTS5 search over spec pages and sections."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from services.document_context.models import DocumentIndex, DocumentPage


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def build_fts_index(
    db_path: Path,
    *,
    pages: list[DocumentPage],
    index: DocumentIndex,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS pages_fts")
        conn.execute("DROP TABLE IF EXISTS sections_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE pages_fts USING fts5(
                page_num UNINDEXED,
                body,
                tokenize='porter'
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE sections_fts USING fts5(
                section_title UNINDEXED,
                page_start UNINDEXED,
                page_end UNINDEXED,
                summary,
                tokenize='porter'
            )
            """
        )
        for page in pages:
            conn.execute(
                "INSERT INTO pages_fts(page_num, body) VALUES (?, ?)",
                (page.page_num, page.text or ""),
            )
        for section in index.sections:
            conn.execute(
                "INSERT INTO sections_fts(section_title, page_start, page_end, summary) "
                "VALUES (?, ?, ?, ?)",
                (
                    section.section_title,
                    section.page_start,
                    section.page_end,
                    f"{section.summary} {' '.join(section.keywords)}",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def search_fts(db_path: Path, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
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
            SELECT page_num, snippet(pages_fts, 1, '[[', ']]', '...', 32) AS snippet,
                   bm25(pages_fts) AS score
            FROM pages_fts
            WHERE pages_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (safe_query, limit),
        ):
            results.append(
                {
                    "kind": "page",
                    "page_num": int(row["page_num"]),
                    "page_start": int(row["page_num"]),
                    "page_end": int(row["page_num"]),
                    "snippet": row["snippet"],
                    "score": float(row["score"]),
                }
            )

        remaining = max(0, limit - len(results))
        if remaining:
            for row in conn.execute(
                """
                SELECT section_title, page_start, page_end,
                       snippet(sections_fts, 3, '[[', ']]', '...', 24) AS snippet,
                       bm25(sections_fts) AS score
                FROM sections_fts
                WHERE sections_fts MATCH ?
                ORDER BY score
                LIMIT ?
                """,
                (safe_query, remaining),
            ):
                results.append(
                    {
                        "kind": "section",
                        "section_title": row["section_title"],
                        "page_start": int(row["page_start"]),
                        "page_end": int(row["page_end"]),
                        "snippet": row["snippet"],
                        "score": float(row["score"]),
                    }
                )
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    return results
