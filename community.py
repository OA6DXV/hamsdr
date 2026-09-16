"""Durable community history. Database work runs off the audio event loop."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time
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
    def __init__(self, path, retention_days=90, max_rows=10000):
        self.path = Path(path)
        self.retention_days, self.max_rows = retention_days, max_rows
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
        self._prune()

    def _prune(self):
        with self.db:
            self.db.execute("DELETE FROM events WHERE time < ?", (int(time.time())-self.retention_days*86400,))
            for kind in ("chat", "log"):
                self.db.execute("DELETE FROM events WHERE kind=? AND id NOT IN (SELECT id FROM events WHERE kind=? ORDER BY id DESC LIMIT ?)",
                                (kind, kind, self.max_rows))

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "kind", "time", "name", "text", "frequency", "mode", "call")}

    def _append(self, event):
        with self.db:
            cursor = self.db.execute("""INSERT OR IGNORE INTO events
                (kind,time,name,text,frequency,mode,call,client_key,request_id)
                VALUES (?,?,?,?,?,?,?,?,?)""", tuple(event[k] for k in (
                    "kind", "time", "name", "text", "frequency", "mode", "call", "client_key", "request_id")))
            created = cursor.rowcount == 1
            row = self.db.execute("SELECT * FROM events WHERE client_key=? AND request_id=?",
                                  (event["client_key"], event["request_id"])).fetchone()
        self._prune()
        return self.public(row), created

    def _history(self, kind, before):
        self._prune()
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
