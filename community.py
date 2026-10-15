# SPDX-License-Identifier: GPL-3.0-only
"""Durable community history. Database work runs off the audio event loop."""
import asyncio
import hashlib
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time
import unicodedata


CALLSIGN_SYMBOLS = frozenset("\\|/@-_")
IDENTITY_TTL = 90 * 86400


class IdentityError(ValueError):
    """An admission failure safe to report without exposing credentials."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize_callsign(value, allow_empty=False):
    """Trim and uppercase a callsign while enforcing its wire format."""
    if not isinstance(value, str):
        raise ValueError("El indicativo debe ser texto")
    value = value.strip()
    if not value:
        if allow_empty:
            return ""
        raise ValueError("Escribe un indicativo antes de publicar")
    if not value.isascii():
        raise ValueError("Indicativo inválido: usa letras A-Z, números 0-9 y los símbolos \\ | / @ - _")
    value = value.upper()
    if len(value) > 10:
        raise ValueError("El indicativo admite como máximo 10 caracteres")
    if any(not (char.isalnum() or char in CALLSIGN_SYMBOLS) for char in value):
        raise ValueError("Indicativo inválido: usa letras A-Z, números 0-9 y los símbolos \\ | / @ - _")
    return value


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
        if version > 3:
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
            CREATE TABLE IF NOT EXISTS posting_limits (
                scope TEXT NOT NULL, identity TEXT NOT NULL, period INTEGER NOT NULL,
                count INTEGER NOT NULL, updated REAL NOT NULL,
                PRIMARY KEY(scope,identity,period)
            );
            CREATE INDEX IF NOT EXISTS posting_limits_expiry ON posting_limits(scope,updated);
        """)
        if version < 2:
            # Existing logbook entries count toward today's allowance too.
            day = int(time.time()) // 86400
            rows = self.db.execute(
                "SELECT call,time FROM events WHERE kind='log' AND time>=? AND time<?",
                (day * 86400, (day + 1) * 86400)).fetchall()
            with self.db:
                for row in rows:
                    self.db.execute("""INSERT INTO posting_limits VALUES ('log',?,?,1,?)
                        ON CONFLICT(scope,identity,period) DO UPDATE SET count=count+1""",
                        (self.callsign_key(row['call']), day, row['time']))
                self.db.execute("PRAGMA user_version=2")
        # Keep the migration transactional and preserve historical authors/keys.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("""CREATE TABLE IF NOT EXISTS browser_identities (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                created REAL NOT NULL, last_seen REAL NOT NULL,
                expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0
            )""")
            self.db.execute("CREATE INDEX IF NOT EXISTS browser_identity_expiry ON browser_identities(expires)")
            self.db.execute("PRAGMA user_version=3")
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

    def _identity(self, token):
        """Resolve/issue a credential on the SQLite worker, never on the AV loop."""
        now = time.time()
        if token is not None:
            if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{64}", token):
                raise IdentityError("identity-invalid", "Identificador inválido; vuelve a conectar")
            digest = hashlib.sha256(token.encode("ascii")).hexdigest()
            with self.db:
                row = self.db.execute("SELECT * FROM browser_identities WHERE token_hash=?", (digest,)).fetchone()
                if row is None or row['revoked'] or row['expires'] <= now:
                    raise IdentityError("identity-invalid", "El identificador venció o fue revocado; vuelve a conectar")
                self.db.execute("UPDATE browser_identities SET last_seen=?,expires=? WHERE id=?",
                                (now, now + IDENTITY_TTL, row['id']))
            return row['id'], None
        # Expiry cleanup is bounded by the registry's independent row ceiling.
        # Never evict valid identities to make room for an issuance flood.
        with self.db:
            self.db.execute("DELETE FROM browser_identities WHERE expires<=?", (now,))
            capacity = min(10000, max(1, self.max_size_bytes // 2048))
            if self.db.execute("SELECT COUNT(*) FROM browser_identities").fetchone()[0] >= capacity:
                raise IdentityError("identity-capacity", "Registro de identidades completo; intenta más tarde")
            token = secrets.token_hex(32)
            identity = secrets.token_hex(16)
            self.db.execute("INSERT INTO browser_identities VALUES (?,?,?,?,?,0)",
                            (identity, hashlib.sha256(token.encode("ascii")).hexdigest(), now, now, now + IDENTITY_TTL))
        return identity, token

    async def identity(self, token=None):
        return await self.call(self._identity, token)

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "kind", "time", "name", "text", "frequency", "mode", "call")}

    @staticmethod
    def callsign_key(call):
        return unicodedata.normalize("NFKC", call).strip().casefold()

    def _append(self, event, enforce_limits=False):
        # Retries must acknowledge the original event without spending allowance.
        row = self.db.execute("SELECT * FROM events WHERE client_key=? AND request_id=?",
                              (event['client_key'], event['request_id'])).fetchone()
        if row is not None:
            return self.public(row), False
        self._make_room()
        with self.db:
            if enforce_limits:
                now = time.time()
                day = int(now) // 86400
                # Retain only active cooldowns and the current UTC day's quota.
                self.db.execute("DELETE FROM posting_limits WHERE scope='chat' AND updated<=?", (now - 1,))
                self.db.execute("DELETE FROM posting_limits WHERE scope='log' AND updated<?", (day * 86400,))
                scope = event['kind']
                identity = event['client_key'] if scope == 'chat' else self.callsign_key(event['call'])
                period = 0 if scope == 'chat' else day
                limit = self.db.execute(
                    "SELECT count,updated FROM posting_limits WHERE scope=? AND identity=? AND period=?",
                    (scope, identity, period)).fetchone()
                if limit is not None:
                    if scope == 'chat' and now - limit['updated'] < 1:
                        raise ValueError("Espera un segundo antes de volver a enviar un mensaje")
                    if scope == 'log' and limit['count'] >= 2:
                        raise ValueError("Máximo dos entradas diarias por indicativo en el logbook (UTC)")
                self.db.execute("""INSERT INTO posting_limits VALUES (?,?,?,1,?)
                    ON CONFLICT(scope,identity,period) DO UPDATE SET count=count+1,updated=excluded.updated""",
                    (scope, identity, period, now))
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

    async def append(self, event, enforce_limits=False):
        return await self.call(self._append, event, enforce_limits)

    async def history(self, kind, before=0):
        return await self.call(self._history, kind, before)

    async def close(self):
        if self.db:
            await self.call(self.db.close)
        self.worker.shutdown(wait=True)
