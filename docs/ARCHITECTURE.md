<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Architecture

HamSDR is an original implementation informed by observed radio behavior,
public SDR techniques and independently documented digital-signal-processing
methods. The server, protocol and browser client are implemented in this tree.

## Data plane

```text
rtl_tcp -> IQ source -> shared complex-sample buffer
                         |-> shared spectrum FFT -> waterfall broadcaster
                         |-> receiver N -> demodulator -> audio broadcaster
```

One source and one panorama are shared. Each connected listener owns only the
channel selector, narrow filter, demodulator, AGC/squelch state, and audio
encoder required for their tuned frequency.

## Failure boundaries

- Source disconnects do not terminate the process.
- Reconnection uses bounded exponential backoff.
- Web clients never control the SDR hardware directly.
- Development defaults to the read-only IQ mirror.
- Slow clients receive current spectrum/audio instead of creating unbounded queues.
- TLS is delegated to a maintained reverse proxy.

## Implemented preview protocol

One aiohttp gateway supervises one native engine. The engine receives numeric
`set ID OFFSET MODE LOW HIGH SQUELCH NOTCH NR DIGITAL` or `del ID` lines on stdin.
The optional DIGITAL flag defaults to zero for older command producers. Its stdout
is binary: one type byte, little-endian uint32 client ID, little-endian uint32
payload length, then payload. ID zero broadcasts. Types: 1 spectrum uint8,
2 mono signed PCM16LE at 16 kHz, 3 ASCII dBFS, 4 ASCII source state,
5 mono signed PCM16LE at 12 kHz for the dedicated digital path. Distinct
audio kinds prevent interpreting stale frames at the wrong sampling rate
when changing profiles. The
gateway sends audio to the browser as kind 2 raw PCM16/16 kHz, kind 10 balanced
Opus wideband at 32 kbit/s, or kind 11 low-bandwidth Opus narrowband at 12 kbit/s. Opus uses 20 ms mono
input frames at 16 kHz and is decoded through WebCodecs before entering the
same AudioWorklet output path.

Digital audio uses a separate peak-hold gain stage instead of listening AGC,
bypasses squelch/notch/noise reduction, and resamples the demodulated float
audio with a 3:4 polyphase antialias FIR before one PCM16 quantization. The
129 taps per phase have a 5.5 kHz cutoff and approximately 4 ms group delay.
The gateway only wraps native 12 kHz frames as browser kind 12; it does not
resample them again. Network audio remains 192 kbit/s payload, with no second
playback stream. Profile/resource-policy changes coalesce into bounded engine
settings updates and restore normal 16 kHz processing on digital exit.

The browser uses a same-origin `/ws` connection. Text messages are JSON
`hello`, `tune`, `tuned`, `status`, `error`, `identify`, `identified`, `presence`,
`chat`, `log`, `event`, `ack`, and `history`. Binary frames have one type byte followed
by spectrum, PCM or meter payload. Browser kind 7 carries a
versioned, independently decodable max-pooled and bit-packed spectrum row;
protocol v2 carries every profile with its frequency window and sequence. A shared
65536-bin FFT lets the gateway project a client's zoom to at most 1024, 2048 or
4096 directly drawable bins without increasing packet size. Audio delivery is separately
negotiated and defaults off; disabling it purges queued PCM or Opus before the server
acknowledges the state. The private protocol is versioned. The client must
negotiate application protocol version 2 and waterfall protocol version 2
before sending controls;
waterfall cadence is negotiated per client from a 15.625 rows/s source. High
definition can use 11.7 rows/s; normal, slow and very slow use 7.8, 3.9 or 1.3,
so slower display settings reduce transport instead of discarding browser frames.
Browsers without a
compatible WebCodecs Opus decoder fall back to raw PCM16.

Raw audio kind 2 is demodulated mono signed PCM16LE at 16 kHz (256 kbit/s
payload); it is not RF/IQ and has no WAV/container or base64 layer. Digital kind
12 carries lossless mono PCM16LE at 12 kHz (192 kbit/s), preceded by mode,
sequence, microsecond timestamp and sample count. The 12 kHz stream is prepared
by a native causal 3:4 polyphase antialias FIR before one PCM16 quantization.
Balanced and mobile profiles instead use 20 ms server-encoded Opus frames at
32 and 12 kbit/s. The browser routes PCM or WebCodecs-decoded Opus through one
bounded AudioWorklet ring buffer. See
[`AUDIO_TRANSPORT.md`](AUDIO_TRANSPORT.md) for framing, bandwidth,
buffering, iOS caveats and a porting checklist.

Client-side diagnostics observe AudioWorklet underruns, gaps or large delays
in waterfall sequence numbers, and unexpected WebSocket reconnects while the
page is visible. They recommend lower-bandwidth profiles without overriding
the listener's choices. This measures experienced interruption; it does not
claim to identify a particular Wi-Fi or cellular cause.

The engine translates each user's frequency with a complex oscillator. Two
127-tap FIR stages decimate 8x each; a 257-tap complex bandpass selects the
channel at 16 kHz. USB/LSB/CW extract the real part, AM uses the envelope, and
NFM uses consecutive phase difference with de-emphasis. DC removal, adaptive
gain, limiting and squelch precede PCM output. Hann FFT power is averaged
linearly before logarithmic conversion. Waterfall dB levels are relative and
not calibrated to antenna input power.

The gateway maps CW carrier frequency to a 700 Hz beat note. Optional 64-tap
normalized LMS delayed predictors subtract correlated tones (autonotch) and
blend predicted audio with the input (noise reduction). State is per receiver.
Narrow presets reuse the existing demodulators with narrower complex filters.

The current engine processes receivers serially in its acquisition callback.
It does not yet have the planned shared ring buffer/worker pool; the gateway
does have bounded per-client output queues. Overload can delay input and must
be benchmarked for each deployment. Maximum preview listeners: 10 by default,
20 hard limit. No claim of Raspberry Pi capacity has been established.

## Frontend contract

The UI places status above the waterfall, users on their own frequency strip,
then frequency/modes/memories, waterfall options, and signal/volume columns.
Filter/recording and DSP occupy the next row, followed by chat and logbook.
Responsive CSS reflows these same controls into a mobile column.

The browser localizes UI text in Spanish, English or Brazilian Portuguese,
using a local catalog and persistent browser preference. It translates only
UI subtrees and attributes; received content and station customization remain
application data. Language changes retain receiver/audio/decoder state. See
[`TRANSLATIONS.md`](TRANSLATIONS.md) for the catalog and DXCC label contract.

## Community persistence

Each WebSocket has an ephemeral public listener ID. Application protocol 2
also requires a server-issued browser credential: `hello` carries `protocol:2`,
`waterfall:2` and an optional 64-character hex `token`. Omitting the token
requests issuance; malformed, unknown, expired or revoked tokens are rejected.
The admitted `hello` returns a 32-character internal `identity` and includes
`token` only for a new issuance. Tokens never appear in URLs, presence,
community events or health. They are bearer credentials, not proof of callsign
ownership. Callsigns are self-declared; no IP address is broadcast.

Admission reserves an identity slot before creating a DSP receiver. The default
is one socket per identity, configurable with `max_connections_per_identity`.
Pending sockets count against the existing total/per-IP connection ceilings,
time out after five seconds, and receive no AV or broadcast status/history.
Orderly reconnects may wait up to two seconds for the old socket to close;
duplicates receive `session-error` / `identity-active`, close code 4409, and
explicit Retry. Other identity failures report a generic code, never a token.
Duplicate admission now opens a centered modal with a dimmed/blurred backdrop.
The listener can explicitly close the other session and continue here. The
authenticated `POST /session` route accepts `action: status|transfer` plus the
existing token in the JSON body (never the URL), and requires the same Origin
policy as WebSockets. It never issues credentials or targets a callsign/IP.
Transfer notifies only the validated identity's sockets with `session-error` /
`identity-replaced`, closes them with code 4410, and waits for sender/snapshot
cleanup and DSP deletion before acknowledging. The application notice also
works through proxies that normalize WebSocket close codes. The old page
pauses audio/recording and shows a moved-session modal without reconnecting.
Taking it back always requires another explicit click.

The control route remains available when receiver slots are full, without
relaxing WebSocket/DSP caps. It shares the connection-attempt limiter, allows
at most eight concurrent requests, has a five-second timeout, and serializes
transfers with a one-second per-identity cooldown (4096 retained entries).
Status only reports whether that authenticated identity is active, so CGNAT
neighbours and different browser identities cannot disconnect one another.
HTTP upgrade rejection on a new page (including total/per-IP capacity) also
offers explicit Retry because browser WebSockets do not expose the HTTP status.
An established session retains automatic backoff across transient upgrade
failures after interruption; explicit identity rejection still stops retries.
Cleanup removes only the departing socket's reservation. The existing 20-second
server heartbeat detects dead peers; background audio timing is unchanged.

The browser stores the token in localStorage and serializes initial issuance
with Web Locks, or an IndexedDB transactional lease on insecure/older clients.
Permission denial or an unusable Web Locks API falls back to IndexedDB too;
a lock-wait timeout does not bypass coordination.
Leases expire and release on admission, error, navigation or timeout. Storage
denial falls back to a page-local token and a persistence warning. Same-origin
tabs share credentials; different origins/profiles/devices do not. HTTP works
but exposes bearer credentials to network interception. Identity/issuance
limits supplement rather than replace existing IP and global controls.
Presence includes name, frequency, mode and color, updated after a tune,
rename, connection or disconnection.

Persistence uses two SQLite files, both with PERSIST journal + synchronous FULL.
The state database (`storage.database`, schema 4) contains browser identities,
posting limits and a single interrupted-publication recovery marker. The history
database (`storage.history_database`, schema 1) contains only chat/logbook events
and their indexes. Both are filename-only references inside working_directory,
outside the source tree, and survive restarts/updates. One executor thread owns
both connections, keeping fsync and queries off the audio event loop.
Chat/log events are committed before acknowledgement; the unique
(client_key, request_id) pair deduplicates reconnect retries. New `client_key`
values are server-assigned internal identity IDs; historical rows remain intact.
`browser_identities` stores SHA-256 token hashes, independent IDs, timestamps,
expiry and revocation, never raw tokens. The registry expires after 90 days
without a successful token lookup, and caps at 10,000 rows (or one row per
2 KiB of the database budget, whichever is smaller). It shares the state SQLite
size ceiling and rejects issuance if full instead of evicting valid identities.
Issuance uses a global 30-token bucket refilling at one per second, plus a
per-IP six-token bucket refilling at one per ten seconds; its IP map caps at
4096 addresses. Existing token lookups do not consume issuance allowance.
Database errors fail admission closed. Snapshot delivery
and inserts share an async lock so a live event cannot race ahead of a stale
history snapshot. History pages have 200 entries and a before-ID cursor.

AV has a discard-oldest queue of 32 frames. JSON control/history events use a
separate queue of 64 messages, prioritized for delivery. A stalled reliable
queue closes the connection and the browser reloads persisted history. Chat
allows one new message per second per browser session, plus the global publication
bucket. Logbook submissions allow two entries per normalized callsign per UTC day.
SQLite persists these limits across reloads, reconnects and server restarts.
Publication reserves its quota in the state file before committing history.
A bounded recovery marker stores only request identity and the previous quota,
not message text. On success it is finalized; on write failure/restart it refunds
the quota only if no matching history event exists. A committed event retains
its allowance and deduplicated acknowledgement even after interruption. This
avoids pretending two separate SQLite commits are an atomic transaction and
does not require super-journal creation in the protected configuration directory.
Legacy combined databases are migrated by committing and verifying an exact
copy of events/IDs/sequence before removing the old table. An interrupted copy
can safely repeat; conflicting data fails startup with originals preserved.
Idempotent retries do not consume additional allowance. Callsign changes retain
the same identity/cooldown; old messages keep their original authors. Pending
messages retain identity and author metadata: only matching, acknowledged
identity/name pairs are retried. Legacy/mismatching submissions become drafts
for explicit review. Held drafts retain a persistent `review` marker in
sessionStorage across reloads, and explicit Send creates a fresh request ID.
These limits are not verified-person authentication.
Publishing requires a nonempty callsign of at most 10 ASCII characters,
containing only letters, digits, or `\ | / @ - _`; it is normalized to
uppercase. Anonymous listeners can still receive streams and read history.
Logbook callsigns come from that session identification. The logbook form has
no separate callsign field.
Only plain text is rendered. New submissions are moderated before publication:
an offline whole-word/phrase filter checks
Spanish, English and Brazilian Portuguese together, including callsign changes.
Rejections precede persistence, broadcast and posting quotas; a private
`error` with code `offensive-language` identifies the field and, for messages,
the kind/request ID. The client retains a held editable draft and does not
retry it on reconnect. Historical records are not retroactively filtered.
See [`MODERATION.md`](MODERATION.md) for vocabulary and matching limitations.
Reviewed source additions and bounded ASCII-gesture detection use the same
Python path. A small set of exact known-callsign collisions is exempt only
in the identification field, after its normal syntax validation. Message
bodies remain moderated. Complete input is checked without a result cache;
no Node worker or browser moderation library is required. More than ten
consecutive identical NFC characters in the original submission are rejected
with private code `repeated-character`, before whitespace normalization for
storage; the same draft-holding behavior applies. Each gateway loads its own
word/phrase and exact-callsign exception lists at startup. Optional UTF-8
`moderation-words.txt` and `callsigns.txt` files directly in the
configured working directory each replace their packaged `data/` default,
including when intentionally empty; there is no process-global policy mutation
between instances. Invalid files fail startup. These lists are not web assets.
Records do not expire by age. Each database has an independent 50 MiB ceiling; when history space
must be reused, the oldest chat or logbook records are removed first. Browser
history maps remain capped at 10,000 entries.

Runtime data belongs outside Git. Backups must use SQLite's online backup API
rather than copying an active database and its journal independently.
