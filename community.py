# SPDX-License-Identifier: GPL-3.0-only
"""Durable community history. Database work runs off the audio event loop."""
import asyncio
import hashlib
import math
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import time
import unicodedata
from moderation import ModerationError, require_clean


CALLSIGN_SYMBOLS = frozenset("\\|/@-_")
IDENTITY_TTL = 90 * 86400
CHAT_MODERATION_RESET = 600
CHAT_MODERATION_THRESHOLD = 5
CHAT_MODERATION_MAX_SECONDS = 512


class ChatCooldownError(ModerationError):
    """A private, identity-scoped restriction on new chat submissions."""

    code = "chat-cooldown"

    def __init__(self, cooldown):
        super().__init__("text", "Chat temporalmente deshabilitado por intentos de envío bloqueados.")
        self.chat_cooldown = cooldown


class IdentityError(ValueError):
    """An admission failure safe to report without exposing credentials."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize_callsign(value, allow_empty=False, *, moderator=None):
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
    (moderator.require_clean if moderator is not None else require_clean)(value, "name")
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
    def __init__(self, path, max_size_mb=50, *, history_path=None):
        self.path = Path(path)
        self.history_path = (Path(history_path) if history_path is not None else
                             self.path.with_name(self.path.stem + '-history' + self.path.suffix))
        if (self.path.resolve() == self.history_path.resolve() or
                self.path.exists() and self.history_path.exists() and self.path.samefile(self.history_path)):
            raise ValueError("State and history databases must be different files")
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_pages = 0
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history")
        self.db = None
        self.events_db = None

    async def call(self, method, *args):
        return await asyncio.get_running_loop().run_in_executor(self.worker, method, *args)

    @staticmethod
    def _connect(path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db = sqlite3.connect(path, timeout=3, uri=True)
        db.row_factory = sqlite3.Row
        # PERSIST keeps the journal file allocated, so a hardened service can
        # grant write access to only the database and journal, not its entire
        # configuration directory.
        try:
            db.execute("PRAGMA journal_mode=PERSIST")
            db.execute("PRAGMA synchronous=FULL")
        except BaseException:
            db.close()
            raise
        return db

    def _open(self):
        self.db = self._connect(self.path)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 4:
            raise RuntimeError("Unsupported state schema")
        self.events_db = self._connect(self.history_path)
        if self.events_db.execute("PRAGMA user_version").fetchone()[0] > 1:
            raise RuntimeError("Unsupported history schema")
        self.events_db.executescript("""
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
        legacy_events = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone() is not None
        self.db.executescript("""
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
            rows = (self.db if legacy_events else self.events_db).execute(
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
            self.db.execute("""CREATE TABLE IF NOT EXISTS pending_publication (
                id INTEGER PRIMARY KEY CHECK(id=1),
                client_key TEXT NOT NULL, request_id TEXT NOT NULL,
                scope TEXT NOT NULL, identity TEXT NOT NULL, period INTEGER NOT NULL,
                previous_count INTEGER, previous_updated REAL
            )""")
        if legacy_events:
            self._migrate_events()
        with self.db:
            self.db.execute("PRAGMA user_version=4")
        self._recover_pending()
        self.max_pages = self._configure_size(self.events_db, evict=True)
        self._configure_size(self.db, evict=False)
        self._make_room()

    def _migrate_events(self):
        """Commit and verify the copy before removing the old table.

        A restart between these independent commits safely repeats the copy.
        The source attachment is read-only; no cross-file write transaction or
        super-journal in the protected customization directory is needed.
        """
        self.events_db.execute("ATTACH DATABASE ? AS legacy", (self.path.resolve().as_uri() + '?mode=ro',))
        try:
            with self.events_db:
                self.events_db.execute("INSERT OR IGNORE INTO events SELECT * FROM legacy.events")
                if self.events_db.execute(
                        "SELECT * FROM legacy.events EXCEPT SELECT * FROM events LIMIT 1").fetchone():
                    raise RuntimeError("History migration conflict; original events preserved")
                old_sequence = self.events_db.execute(
                    "SELECT seq FROM legacy.sqlite_sequence WHERE name='events'").fetchone()
                if old_sequence:
                    current = self.events_db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
                    if current:
                        self.events_db.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='events'", (old_sequence[0],))
                    else:
                        self.events_db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('events',?)", (old_sequence[0],))
        finally:
            self.events_db.execute("DETACH DATABASE legacy")
        with self.db:
            self.db.execute("DROP TABLE events")

    def _configure_size(self, db, *, evict):
        page_size = db.execute("PRAGMA page_size").fetchone()[0]
        max_pages = self.max_size_bytes // page_size
        if max_pages < 16:
            raise ValueError("Community database size must allow at least 16 SQLite pages")
        current_pages = db.execute("PRAGMA page_count").fetchone()[0]
        if current_pages > max_pages and not evict:
            db.execute("VACUUM")
            current_pages = db.execute("PRAGMA page_count").fetchone()[0]
            if current_pages > max_pages:
                raise ValueError("State database exceeds its size maximum")
        while current_pages > max_pages:
            rows = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            if not rows:
                raise ValueError("Community database maximum is smaller than its schema")
            proportional = (rows * (current_pages - max_pages) + current_pages - 1) // current_pages
            delete_count = min(rows, max(1, proportional + max(1, rows // 20)))
            with db:
                db.execute(
                    "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id LIMIT ?)",
                    (delete_count,))
            db.execute("VACUUM")
            current_pages = db.execute("PRAGMA page_count").fetchone()[0]
        configured_pages = db.execute(f"PRAGMA max_page_count={max_pages}").fetchone()[0]
        if configured_pages != max_pages:
            raise RuntimeError("Could not apply community database size limit")
        return max_pages

    def _make_room(self):
        """Keep free pages for the next event, removing the oldest rows first."""
        reserve = min(8, max(2, self.max_pages // 20))
        target = self.max_pages - reserve
        while True:
            pages = self.events_db.execute("PRAGMA page_count").fetchone()[0]
            free = self.events_db.execute("PRAGMA freelist_count").fetchone()[0]
            if pages - free <= target:
                return
            with self.events_db:
                deleted = self.events_db.execute(
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

    def _chat_moderation(self, identity, rejected=False):
        """Persist strikes in the existing identity-scoped quota table.

        Requests during a sanction neither advance strikes nor extend it.
        Read and update run on the single database executor, including when
        more than one connection per identity is configured.
        """
        now = time.time()
        row = self.db.execute("SELECT count,updated FROM posting_limits "
                              "WHERE scope='chat-moderation' AND identity=? AND period=0",
                              (identity,)).fetchone()
        if row is not None and now - row['updated'] >= CHAT_MODERATION_RESET:
            with self.db:
                self.db.execute("DELETE FROM posting_limits WHERE scope='chat-moderation' AND identity=?",
                                (identity,))
            row = None
        count = row['count'] if row is not None else 0
        duration = (min(2 ** min(count - CHAT_MODERATION_THRESHOLD, 9), CHAT_MODERATION_MAX_SECONDS)
                    if count >= CHAT_MODERATION_THRESHOLD else 0)
        remaining = min(duration, max(0, duration - (now - row['updated']))) if row is not None else 0
        if rejected and not remaining:
            count = min(count + 1, CHAT_MODERATION_THRESHOLD + 9)
            duration = 2 ** (count - CHAT_MODERATION_THRESHOLD) if count >= CHAT_MODERATION_THRESHOLD else 0
            remaining = duration
            with self.db:
                self.db.execute("DELETE FROM posting_limits WHERE scope='chat-moderation' AND updated<=?",
                                (now - CHAT_MODERATION_RESET,))
                self.db.execute("""INSERT INTO posting_limits VALUES ('chat-moderation',?,0,?,?)
                    ON CONFLICT(scope,identity,period) DO UPDATE SET count=excluded.count,updated=excluded.updated""",
                    (identity, count, now))
        return {"remaining_ms": math.ceil(remaining * 1000), "duration_ms": duration * 1000}

    async def chat_moderation(self, identity, rejected=False):
        return await self.call(self._chat_moderation, identity, rejected)

    @staticmethod
    def public(row):
        return {key: row[key] for key in ("id", "kind", "time", "name", "text", "frequency", "mode", "call")}

    @staticmethod
    def callsign_key(call):
        return unicodedata.normalize("NFKC", call).strip().casefold()

    def _recover_pending(self):
        """Repair a quota reservation interrupted before/after history commit."""
        pending = self.db.execute("SELECT * FROM pending_publication WHERE id=1").fetchone()
        if pending is None:
            return
        saved = self.events_db.execute("SELECT 1 FROM events WHERE client_key=? AND request_id=?",
                                       (pending['client_key'], pending['request_id'])).fetchone()
        with self.db:
            if saved is None:
                key = (pending['scope'], pending['identity'], pending['period'])
                if pending['previous_count'] is None:
                    self.db.execute("DELETE FROM posting_limits WHERE scope=? AND identity=? AND period=?", key)
                else:
                    self.db.execute("INSERT OR REPLACE INTO posting_limits VALUES (?,?,?,?,?)",
                                    (*key, pending['previous_count'], pending['previous_updated']))
            self.db.execute("DELETE FROM pending_publication WHERE id=1")

    def _reserve_publication(self, event):
        """Durably reserve allowance before storing an event in the other DB."""
        with self.db:
            now = time.time()
            day = int(now) // 86400
            self.db.execute("DELETE FROM posting_limits WHERE scope='chat' AND updated<=?", (now - 1,))
            self.db.execute("DELETE FROM posting_limits WHERE scope='log' AND updated<?", (day * 86400,))
            scope = event['kind']
            identity = event['client_key'] if scope == 'chat' else self.callsign_key(event['call'])
            period = 0 if scope == 'chat' else day
            limit = self.db.execute("SELECT count,updated FROM posting_limits WHERE scope=? AND identity=? AND period=?",
                                    (scope, identity, period)).fetchone()
            if limit is not None:
                if scope == 'chat' and now - limit['updated'] < 1:
                    raise ValueError("Espera un segundo antes de volver a enviar un mensaje")
                if scope == 'log' and limit['count'] >= 2:
                    raise ValueError("Máximo dos entradas diarias por indicativo en el logbook (UTC)")
            self.db.execute("INSERT INTO pending_publication VALUES (1,?,?,?,?,?,?,?)",
                            (event['client_key'], event['request_id'], scope, identity, period,
                             limit['count'] if limit else None, limit['updated'] if limit else None))
            self.db.execute("""INSERT INTO posting_limits VALUES (?,?,?,1,?)
                ON CONFLICT(scope,identity,period) DO UPDATE SET count=count+1,updated=excluded.updated""",
                            (scope, identity, period, now))

    def _append(self, event, enforce_limits=False):
        self._recover_pending()
        # Retries must acknowledge the original event without spending allowance.
        row = self.events_db.execute("SELECT * FROM events WHERE client_key=? AND request_id=?",
                              (event['client_key'], event['request_id'])).fetchone()
        if row is not None:
            return self.public(row), False
        if enforce_limits and event['kind'] == 'chat':
            cooldown = self._chat_moderation(event['client_key'])
            if cooldown['remaining_ms']:
                raise ChatCooldownError(cooldown)
        self._make_room()
        if enforce_limits:
            self._reserve_publication(event)
        try:
            with self.events_db:
                cursor = self.events_db.execute("""INSERT INTO events
                    (kind,time,name,text,frequency,mode,call,client_key,request_id)
                    VALUES (?,?,?,?,?,?,?,?,?)""", tuple(event[k] for k in (
                        "kind", "time", "name", "text", "frequency", "mode", "call", "client_key", "request_id")))
                row = self.events_db.execute("SELECT * FROM events WHERE id=?", (cursor.lastrowid,)).fetchone()
        except sqlite3.Error:
            self._recover_pending()
            raise
        self._recover_pending()
        self._make_room()
        return self.public(row), True

    def _history(self, kind, before):
        rows = self.events_db.execute("SELECT * FROM events WHERE kind=? AND id < ? ORDER BY id DESC LIMIT 201",
                               (kind, before or 9223372036854775807)).fetchall()
        return {"type": "history", "kind": kind, "events": [self.public(r) for r in reversed(rows[:200])],
                "more": len(rows) > 200, "before": before}

    async def open(self):
        try:
            await self.call(self._open)
        except BaseException:
            await self.close()
            raise

    async def append(self, event, enforce_limits=False):
        return await self.call(self._append, event, enforce_limits)

    async def history(self, kind, before=0):
        return await self.call(self._history, kind, before)

    async def close(self):
        def close_connections():
            for name in ('db', 'events_db'):
                connection = getattr(self, name)
                if connection is not None:
                    connection.close()
                    setattr(self, name, None)
        if self.db is not None or self.events_db is not None:
            await self.call(close_connections)
        self.worker.shutdown(wait=True)
