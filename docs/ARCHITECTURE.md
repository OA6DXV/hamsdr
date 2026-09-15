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
`set ID OFFSET MODE LOW HIGH SQUELCH NOTCH NR` or `del ID` lines on stdin. Its stdout
is binary: one type byte, little-endian uint32 client ID, little-endian uint32
payload length, then payload. ID zero broadcasts. Types: 1 spectrum uint8,
2 mono signed PCM16LE at 16 kHz, 3 ASCII dBFS, 4 ASCII source state.

The browser uses a same-origin `/ws` connection. Text messages are JSON
`tune`, `tuned`, `status`, `error`, `identify`, `identified`, `presence`,
`chat`, `log`, `event`, `ack`, and `history`. Binary frames have one type byte followed
by spectrum, PCM or meter payload. Browser kind 7 carries a
versioned, independently decodable max-pooled and bit-packed spectrum row;
protocol v2 carries every profile with its frequency window and sequence. A shared
65536-bin FFT lets the gateway project a client's zoom to at most 1024, 2048 or
4096 directly drawable bins without increasing packet size. PCM delivery is separately
negotiated and defaults off; disabling it purges queued PCM before the server
acknowledges the state. The private protocol is experimental;
waterfall cadence is negotiated per client at 7.8, 3.9 or 1.3 rows/s, so a
slower display setting reduces transport instead of discarding browser frames.
version negotiation is required before any stable release. Opus is not yet
implemented.

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

## Community persistence

Each WebSocket has an ephemeral public listener ID and a private random
browser session key (not an authentication credential). Callsigns are
self-declared; no password, verified identity, or IP address is broadcast.
Presence includes name, frequency, mode and color, updated after a tune,
rename, connection or disconnection.

History is SQLite schema version 1, WAL + synchronous FULL. A single executor
thread owns the database connection, keeping fsync and queries off the audio
event loop. Chat/log events are committed before acknowledgement; the unique
(client_key, request_id) pair deduplicates reconnect retries. Snapshot delivery
and inserts share an async lock so a live event cannot race ahead of a stale
history snapshot. History pages have 200 entries and a before-ID cursor.

AV has a discard-oldest queue of 32 frames. JSON control/history events use a
separate queue of 64 messages, prioritized for delivery. A stalled reliable
queue closes the connection and the browser reloads persisted history. Chat
is limited per connection and globally; only plain text is rendered. Records
expire at the configured retention (default 90 days) with a 10,000-row cap per
category. Browser history maps are capped at 10,000 entries too.

Runtime data belongs outside Git. Backups must use SQLite's online backup API
rather than copying an active WAL file alone.
