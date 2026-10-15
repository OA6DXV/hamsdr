<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Cookie-free browser identity: 0.9.2-dev

Implemented locally on the 0.9.1-dev baseline (`86a0755`). Publication is a
separate action. The protocol contract is in [ARCHITECTURE.md](ARCHITECTURE.md).

## Identity and admission

- The server creates a 256-bit opaque credential; SQLite stores only its
  SHA-256 hash, a separate identity ID, timestamps, expiry and revocation state.
  Callsigns remain editable display metadata, not credentials.
- Tokens persist in localStorage and are shared by tabs on the same origin.
  No cookies, passwords, fingerprints or external identity services are used.
- Web Locks serialize simultaneous first tabs where available. An IndexedDB
  transaction lease is the HTTP-compatible fallback; leases have bounded waits,
  expiry and error/navigation cleanup. Storage denial uses a page-local token
  and warns that persistence is unavailable.
  Denied Web Locks falls back to IndexedDB; only lock-wait timeout remains a
  coordination error rather than bypassing another tab's bootstrap lease.
- Application handshake version 2 validates/issues identity before creating
  a receiver or delivering any AV. Waterfall protocol remains version 2.
- `[server].max_connections_per_identity = 1` is the default. Additional tabs
  show a centered modal, with Retry and an explicit session-transfer action.
  The backdrop dims/blurs the receiver; there is no automatic takeover.
  A two-second admission grace allows an orderly reload to close its old socket.
  Reservations are owned by socket IDs so stale cleanup cannot erase a new owner.
  HTTP upgrade rejection on a new page at a capacity limit also exposes Retry
  without a loop. Established sessions retain automatic backoff across transient
  upgrade failures after interruption; explicit identity rejection stops retries.
- `POST /session` validates the stored token and Origin before status/transfer.
  Explicit transfer closes only this identity's receivers (code 4410), waits
  for DSP cleanup, then the new tab reconnects normally. The old tab pauses
  audio/recording and presents a moved-session notice without auto-reconnecting.
  It works at full receiver capacity without adding a receiver slot. Transfers
  are serialized, rate-limited and bounded; see the architecture contract.
- Pending handshakes count against total/per-IP ceilings and expire after five
  seconds. Existing 20-second server heartbeat and audio buffering are unchanged.
- The registry persists in state SQLite schema 4 on the shared storage executor;
  chat/logbook history now uses a separate SQLite file. State migration is
  transactional; history splitting is restartable and verified. Credential lookups
  renew a 90-day inactivity expiry. The row cap is 10,000 or database budget
  divided by 2 KiB, whichever is smaller. Database failures deny admission.
- Issuance is separately bounded globally (burst 30, refill 1/s) and per IP
  (burst 6, refill 1/10 s); the IP table holds at most 4096 entries. Expired
  identities are removed during issuance; valid identities are never evicted
  to satisfy an issuance flood. The registry uses the state database's SQLite budget.

## Community migration

New cooldown and retry keys use the internal server-assigned identity. Renaming
or reconnecting preserves the chat cooldown. Logbook still permits two entries
per normalized callsign per UTC day. Historical authors are never rewritten.

New pending messages store their identity and acknowledged author alongside
their request ID. Automatic retry requires both to match. Legacy or mismatching
pending submissions remain editable drafts requiring explicit Send; they are
held in sessionStorage across reloads and never silently republished under a
new identity. Explicit Send replaces their request ID after user review.
The token itself is excluded
from pending payloads, URLs, presence, community history, health and logs.

## Verification

Use temporary databases and local `--demo` sources. These commands never need
the live SDR or the stopped audit alternate services:

```sh
.venv/bin/python tests/identity_tests.py -v
.venv/bin/python tests/identity_browser_test.py
.venv/bin/python tests/integration_test.py -v
.venv/bin/python tests/history_tests.py -v
.venv/bin/python tests/security_resource_tests.py -v
.venv/bin/python tests/site_config_tests.py -v
node tests/community_render_tests.mjs
.venv/bin/python tests/recording_browser_test.py
```

The browser test uses Chromium and WebKit with isolated HTTP, native TLS and
an actual local TLS-to-HTTP/WebSocket proxy. It exercises simultaneous first
tabs, IndexedDB fallback, storage denial, duplicate Retry, reload/reconnect,
identity/callsign persistence, PCM streaming and absence of cookies. Unit and
integration checks cover hash-only persistence, expiry/revocation, capacity,
forged credentials, atomic admission, no pre-admission receiver/AV, database
failure, same-IP distinct identities, rename cooldown and retry idempotence.
Regression cases also exercise denied Web Locks, capacity rejection without
automatic retries, held-draft reload and explicit resubmission. Recording tests
recover local checkpoints from a second tab without requiring another receiver.
Transfer regressions cover forged tokens, foreign Origin, concurrent requests,
same-IP isolation, full capacity, modal layout, stopped recording and no reconnect
fight across Chromium/WebKit and all three transports.

## Limits

This identifies stored browser state, not a person or callsign owner. Clearing
storage, private browsing or another profile/device can obtain another token.
Different schemes, hosts and ports have separate storage/identities. IP changes
do not invalidate a token; shared IPs do not imply a shared identity.

HTTP/WS supports the same mechanism but cannot protect bearer credentials from
network interception. HTTPS/WSS protects transport; JavaScript-readable storage
remains vulnerable to token theft if an XSS is introduced. No BroadcastChannel,
cross-origin sharing, silent takeover or tab-to-tab AV multiplexing is required.
Profanity filtering is a separate implementation.
