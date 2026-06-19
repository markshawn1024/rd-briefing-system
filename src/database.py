"""SQLite persistence for news sources and crawled articles."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rd_news.db"

ARTICLE_EXTRA_COLUMNS: dict[str, str] = {
    "canonical_url": "TEXT",
    "published_time": "TEXT",
    "published_time_raw": "TEXT",
    "summary": "TEXT",
    "raw_text": "TEXT",
    "raw_text_length": "INTEGER",
    "raw_text_cleaned": "INTEGER",
    "extraction_status": "TEXT",
    "extraction_error": "TEXT",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_connection(db_path: Union[Path, str] = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            language TEXT,
            notes TEXT,
            last_checked_at TEXT,
            last_status TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_articles_source_id ON articles(source_id);
        CREATE INDEX IF NOT EXISTS idx_articles_first_seen ON articles(first_seen_at);
        """
    )
    _migrate_articles_columns(conn)
    conn.commit()


def _migrate_articles_columns(conn: sqlite3.Connection) -> None:
    """Add missing article columns without breaking existing databases."""
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(articles)").fetchall()
    }
    for column, column_type in ARTICLE_EXTRA_COLUMNS.items():
        if column in existing:
            continue
        conn.execute(f"ALTER TABLE articles ADD COLUMN {column} {column_type}")


def upsert_source(
    conn: sqlite3.Connection,
    name: str,
    url: str,
    language: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    now = _utc_now_iso()
    row = conn.execute("SELECT id FROM sources WHERE url = ?", (url,)).fetchone()
    if row:
        conn.execute(
            """
            UPDATE sources SET name = ?, language = ?, notes = ?
            WHERE id = ?
            """,
            (name, language, notes, row["id"]),
        )
        conn.commit()
        return row["id"]

    cursor = conn.execute(
        """
        INSERT INTO sources (name, url, language, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, url, language, notes, now),
    )
    conn.commit()
    return cursor.lastrowid


def update_source_check(
    conn: sqlite3.Connection,
    source_id: int,
    status: str,
) -> None:
    conn.execute(
        """
        UPDATE sources SET last_checked_at = ?, last_status = ?
        WHERE id = ?
        """,
        (_utc_now_iso(), status, source_id),
    )
    conn.commit()


def insert_article_if_new(
    conn: sqlite3.Connection,
    source_id: int,
    title: str,
    url: str,
) -> bool:
    """
    Insert an article if its URL is not already in the database.

    Returns True when a new row was created, False if the URL already exists
    (in which case last_seen_at is updated).
    """
    now = _utc_now_iso()
    existing = conn.execute(
        "SELECT id FROM articles WHERE url = ?", (url,)
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE articles SET last_seen_at = ? WHERE id = ?",
            (now, existing["id"]),
        )
        conn.commit()
        return False

    conn.execute(
        """
        INSERT INTO articles (source_id, title, url, first_seen_at, last_seen_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source_id, title, url, now, now, now),
    )
    conn.commit()
    return True


def update_article_extraction(
    conn: sqlite3.Connection,
    url: str,
    *,
    title: str,
    canonical_url: str = "",
    published_time: str = "",
    published_time_raw: str = "",
    summary: str = "",
    raw_text: str = "",
    raw_text_length: int = 0,
    raw_text_cleaned: bool = False,
    extraction_status: str = "",
    extraction_error: str = "",
) -> None:
    """Persist detail-page extraction results for an article identified by URL."""
    conn.execute(
        """
        UPDATE articles
        SET title = ?,
            canonical_url = ?,
            published_time = ?,
            published_time_raw = ?,
            summary = ?,
            raw_text = ?,
            raw_text_length = ?,
            raw_text_cleaned = ?,
            extraction_status = ?,
            extraction_error = ?
        WHERE url = ?
        """,
        (
            title,
            canonical_url,
            published_time,
            published_time_raw,
            summary,
            raw_text,
            raw_text_length,
            int(bool(raw_text_cleaned)),
            extraction_status,
            extraction_error,
            url,
        ),
    )
    conn.commit()


def get_articles_since(
    conn: sqlite3.Connection,
    since_iso: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT a.id, a.title, a.url, a.first_seen_at, a.last_seen_at,
               s.name AS source_name, s.url AS source_url
        FROM articles a
        JOIN sources s ON s.id = a.source_id
        WHERE a.first_seen_at >= ?
        ORDER BY a.first_seen_at DESC
        """,
        (since_iso,),
    ).fetchall()
    return [dict(row) for row in rows]
