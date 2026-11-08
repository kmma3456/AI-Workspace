"""대화 상태 저장소. 구글 시트는 사람이 보는 기록, SQLite는 AI가 쓰는 작업 메모리."""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.utils import normalize_phone

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    role TEXT NOT NULL,
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone, id);

CREATE TABLE IF NOT EXISTS processed_events (
    key TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flags (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Source:
    CUSTOMER = "customer"
    AI = "ai"
    OWNER = "owner"
    TEMPLATE = "template"


ROLE_BY_SOURCE = {Source.CUSTOMER: "user", Source.AI: "assistant", Source.OWNER: "assistant", Source.TEMPLATE: "assistant"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def add_message(self, phone: str, source: str, content: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO messages (phone, role, source, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (phone, ROLE_BY_SOURCE[source], source, content, _now()),
            )

    def history(self, phone: str, limit: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, source, content, created_at FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?",
                (phone, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def conversations(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.phone, m.source AS last_source, m.content AS last_content, m.created_at AS last_at, c.total
                FROM messages m
                JOIN (SELECT phone, MAX(id) AS last_id, COUNT(*) AS total FROM messages GROUP BY phone) c
                  ON m.id = c.last_id
                ORDER BY m.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, phone: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM messages WHERE phone = ?", (phone,))

    def count_outgoing_since(self, phone: str, since: datetime) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE phone = ? AND role = 'assistant' AND created_at >= ?",
                (phone, since.astimezone(timezone.utc).isoformat()),
            ).fetchone()
        return row[0]

    def last_outgoing_at(self, phone: str) -> datetime | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at FROM messages WHERE phone = ? AND role = 'assistant' ORDER BY id DESC LIMIT 1",
                (phone,),
            ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def mark_processed(self, key: str) -> bool:
        """처음 보는 key면 True. 웹훅 재전송, 리마인드 중복 발송 방지용."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO processed_events (key, created_at) VALUES (?, ?)", (key, _now())
            )
        return cur.rowcount == 1

    def get_flag(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM flags WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_flag(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO flags (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def executescript(self, sql: str) -> None:
        with self._lock, self._conn:
            self._conn.executescript(sql)

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(sql, params)
            return cur.rowcount

    def execute_returning_id(self, sql: str, params: tuple = ()) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute(sql, params)
            return int(cur.lastrowid)

    def fetchone(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def recent_messages(self, limit: int = 40) -> list[dict]:
        return self.search_messages(limit=limit, offset=0)["items"]

    def search_messages(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        since: datetime | None = None,
        until: datetime | None = None,
        source: str | None = None,
        q: str | None = None,
    ) -> dict:
        clauses: list[str] = []
        params: list[object] = []
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since.astimezone(timezone.utc).isoformat())
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until.astimezone(timezone.utc).isoformat())
        if source:
            clauses.append("source = ?")
            params.append(source)
        if q:
            raw = q.strip()
            content_term = f"%{raw}%"
            digits = normalize_phone(raw)
            if len(digits) >= 4:
                phone_term = f"%{digits}%"
                clauses.append("(phone LIKE ? OR content LIKE ?)")
                params.extend([phone_term, content_term])
            else:
                clauses.append("content LIKE ?")
                params.append(content_term)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        with self._lock:
            total = self._conn.execute(f"SELECT COUNT(*) FROM messages {where}", params).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT phone, source, content, created_at FROM messages {where} "
                "ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}

    def message_stats_since(self, since: datetime) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, COUNT(*), COUNT(DISTINCT phone) FROM messages WHERE created_at >= ? GROUP BY source",
                (since.astimezone(timezone.utc).isoformat(),),
            ).fetchall()
        return {source: {"messages": count, "customers": customers} for source, count, customers in rows}

    def message_stats_between(self, start: datetime, end: datetime) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, COUNT(*), COUNT(DISTINCT phone) FROM messages "
                "WHERE created_at >= ? AND created_at < ? GROUP BY source",
                (start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()),
            ).fetchall()
        return {source: {"messages": count, "customers": customers} for source, count, customers in rows}

    def close(self) -> None:
        self._conn.close()
