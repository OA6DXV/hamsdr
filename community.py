# SPDX-License-Identifier: GPL-3.0-only
"""Durable community history. Database work runs off the audio event loop."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import unicodedata


def plain_text(value, limit, allow_empty=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Texto: máximo {limit} caracteres")
    value = " ".join(unicodedata.normalize("NFC", value).split())
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise ValueError("El texto contiene caracteres de control")
    if not value and not allow_empty:
        raise ValueError("Escribe un texto antes de enviar")
    return value


class History:
    def __init__(self, path, max_size_mb=50):
        self.path = Path(path)
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_pages = 0
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history")
        self.db = None

    async def call(self, method, *args):
        return await asyncio.get_running_loop().run_in_executor(self.worker, method, *args)

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, timeout=3)
        self.db.row_factory = sqlite3.Row
        # PERSIST keeps the journal file allocated, so a hardened service can
        # grant write access to only the database and journal, not its entire
        # configuration directory.
        self.db.execute("PRAGMA journal_mode=PERSIST")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise RuntimeError("Unsupported history schema")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL CHECK(kind IN ('chat','log')),
                time INTEGER NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL,
                frequency INTEGER NOT NULL, mode TEXT NOT NULL, call TEXT NOT NULL,
                client_key TEXT NOT NULL, request_id TEXT NOT NULL,
                UNIQUE(client_key, request_id)
            );
            CREATE INDEX IF NOT EXISTS events_kind_id ON events(kind,id);
            PRAGMA user_version=1;
        """)
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        self.max_pages = self.max_size_bytes // page_size
        if self.max_pages < 16:
            raise ValueError("Community database size must allow at least 16 SQLite pages")
        current_pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        while current_pages > self.max_pages:
            rows = self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            if not rows:
                raise ValueError("Community database maximum is smaller than its schema")
            proportional = (rows * (current_pages - self.max_pages) + current_pages - 1) // current_pages
            delete_count = min(rows, max(1, proportional + max(1, rows // 20)))
            with self.db:
                self.db.execute(
                    "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT ?)",
                    (delete_count,))
            self.db.execute("VACUUM")
            current_pages = self.db.execute("PRAGMA page_count").fetchone()[0]
        configured_pages = self.db.execute(f"PRAGMA max_page_count={self.max_pages}").fetchone()[0]
        if configured_pages != self.max_pages:
            raise RuntimeError("Could not apply community database size limit")
        self._make_room()

    def _make_room(self):
        """Keep free pages for the next event, removing the oldest rows first."""
        reserve = min(8, max(2, self.max_pages // 20))
        target = self.max_pages - reserve
        while True:
            pages = self.db.execute("PRAGMA page_count").fetchone()[0]
            free = self.db.execute("PRAGMA freelist_count").fetchone()[0]
            if pages - free <= target:
                return
            with self.db:
                deleted = self.db.execute(
                    "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT 256)"
                ).rowcount
            if not deleted:
                return

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "kind", "time", "name", "text", "frequency", "mode", "call")}

    def _append(self, event):
        self._make_room()
        with self.db:
            cursor = self.db.execute("""INSERT OR IGNORE INTO events
                (kind,time,name,text,frequency,mode,call,client_key,request_id)
                VALUES (?,?,?,?,?,?,?,?,?)""", tuple(event[k] for k in (
                    "kind", "time", "name", "text", "frequency", "mode", "call", "client_key", "request_id")))
            created = cursor.rowcount == 1
            row = self.db.execute("SELECT * FROM events WHERE client_key=? AND request_id=?",
                                  (event["client_key"], event["request_id"])).fetchone()
        self._make_room()
        return self.public(row), created

    def _history(self, kind, before):
        rows = self.db.execute("SELECT * FROM events WHERE kind=? AND id < ? ORDER BY id DESC LIMIT 201",
                               (kind, before or 9223372036854775807)).fetchall()
        return {"type": "history", "kind": kind, "events": [self.public(r) for r in reversed(rows[:200])],
                "more": len(rows) > 200, "before": before}

    async def open(self):
        await self.call(self._open)

    async def append(self, event):
        return await self.call(self._append, event)

    async def history(self, kind, before=0):
        return await self.call(self._history, kind, before)

    async def close(self):
        if self.db:
            await self.call(self.db.close)
        self.worker.shutdown(wait=True)
