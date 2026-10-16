<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Persistent community storage

Both SQLite files live directly in the TOML `working_directory`, outside the
source checkout:

```toml
[storage]
database = "community.sqlite3"
history_database = "chat-logbook.sqlite3"
max_size_mb = 50
```

- `database`: browser identities/token hashes, posting quotas, identity-scoped
  chat moderation strikes/sanctions in `posting_limits`, and a bounded
  interrupted-publication recovery marker. No chat/logbook bodies remain here.
- `history_database`: only chat/logbook events, indexes and SQLite's sequence.
- `max_size_mb`: independent ceiling for each database, in MiB. Oldest events
  can be removed from history to reuse space; valid identities are not evicted.

Normal restarts and source updates do not erase these files. Names must differ
and be direct filenames, not absolute paths or subdirectories. If `--database`
overrides the TOML on the command line without `--history-database`, the second
file is derived beside that override as `<stem>-history<suffix>`; this keeps
demo/test instances out of an installation's persistent files.

## Migration

Back up the combined database before the first start with this change. Startup
creates history schema 1 and upgrades state to schema 4. It copies existing
events, verifies every original row and preserves IDs and the AUTOINCREMENT
sequence before removing the old table. An interruption after the history
commit is safe to retry without duplicates; conflicting rows fail startup and
leave the originals intact. The old binary must not be restarted against the
migrated pair. For rollback, restore the pre-migration backup together with the
old code; doing so discards any later entries, so decide explicitly.

Both connections use PERSIST journals and synchronous FULL on one worker
thread. Quota reservation and a small recovery marker in state precede the
history commit. Recovery refunds an interrupted reservation only when its
history event is absent; a committed event retains its quota and retry ID.
Messages are acknowledged only after durable storage/finalization. The two
commits are not claimed to be an atomic cross-database SQLite transaction.

## Service permissions and backups

A hardened service must have write permission to **both database files and
both `-journal` files**. Pre-create them with service-user ownership and grant
file-specific `ReadWritePaths`; keep configuration, word lists, certificates
and the parent directory protected. No cross-file write transaction requiring
super-journal creation in that directory is used.

`tools/history_backup.py DATABASE BACKUP_DIRECTORY` backs up one file using
SQLite's backup API. Pass the history file to export chat/logbook alone. For a
consistent restorable pair, stop the corresponding service, back up each file,
then restart it. Never copy a live database/journal independently or assume a
backup of state contains chat. Backups may contain private browser identity
hashes or user messages; keep them outside Git and restrict access.

Checks: `tests/history_tests.py` covers split migration, conflicts, interrupted
copy/finalization, failed writes, quota recovery, retry IDs, sequence retention,
size limits and backup. Identity, integration and browser tests cover restart,
reconnection and the public protocol.
