<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Audio transport and low-latency playback

This document describes HamSDR's audio path as implemented in the current
preview and the parts that can be reused in another realtime browser project.
“Raw” here means **demodulated audio PCM**, not raw RF/IQ samples from the SDR.

## End-to-end path

```text
shared RF/IQ input
  -> per-listener tuning, demodulation and channel filter (native engine)
  -> mono signed PCM16LE chunks
  -> framed native-engine stdout
  -> same-origin WebSocket binary message (aiohttp gateway)
  -> browser PCM decode / optional Opus decode
  -> bounded AudioWorklet ring buffer
  -> gain node -> AudioContext output
```

The browser opens one persistent WebSocket for control, waterfall and audio.
Audio is opt-in and starts only after the listener presses the audio button.
Pausing audio tells the server to stop sending audio and removes queued audio
packets; it does not merely mute playback locally. Waterfall and control remain
on the connection. Binary data is not base64-encoded, JSON-wrapped, or carried
in a WAV/MP3 container. Recording creates a local WAV or Ogg Opus container.

The native engine writes frames to its stdout bridge as:

```text
type: u8 | client_id: u32 little-endian | payload_length: u32 little-endian | payload
```

The gateway routes each audio frame to its listener. Browser WebSocket audio
messages use these payloads (one leading kind byte followed by the data):

| WebSocket kind | Payload after kind | Use |
| --- | --- | --- |
| `2` | mono signed PCM16LE, 16 kHz | Raw listening profile |
| `10` | sequence `u32` LE + Opus packet | Balanced Opus, 32 kbit/s |
| `11` | sequence `u32` LE + Opus packet | Low-bandwidth Opus, 12 kbit/s |
| `12` | mode `u8`, sequence `u32` LE, Unix timestamp in microseconds `u64` LE, sample count `u16` LE, then mono signed PCM16LE at 12 kHz | Digital decoders and digital-mode monitoring (`digiraw`) |

Opus is encoded server-side with system `libopus`, mono 16 kHz input and 20 ms
frames (320 samples). The browser decodes it with WebCodecs and feeds the same
playback worklet. If server or browser Opus support is unavailable, the client
falls back to raw PCM16. Digital modes do not pass through Opus.

## Data rate and trade-offs

Uncompressed PCM payload rate is predictable:

```text
bytes/second = sample_rate * 2        # one 16-bit mono sample
bits/second  = sample_rate * 16
```

| Profile | Audio format | Nominal audio rate | Main trade-off |
| --- | --- | ---: | --- |
| `raw` | PCM16LE, mono, 16 kHz | 32,000 B/s = 256 kbit/s | No added lossy-codec delay or artifacts; highest network use |
| `digiraw` | PCM16LE, mono, 12 kHz | 24,000 B/s = 192 kbit/s | Uncompressed decoder input; 0–5 kHz passband; selected automatically for digital modes |
| `balanced` | Opus, mono, 16 kHz input | 32 kbit/s | Much less network use; lossy and requires encode/decode |
| `mobile` | Opus, mono, 16 kHz input, narrowband limit | 12 kbit/s | Lowest network use; most bandwidth/fidelity reduction |

The PCM figures are exact sample-data rates; Opus rates are encoder targets
(constrained VBR), not a fixed per-second packet size. These are not the page's
total bandwidth: WebSocket, TCP/IP and (when used) TLS add some overhead, and
the waterfall is a separate traffic source. Thus PCM16 is low-overhead and
avoids codec work, but it is not the lowest-bandwidth option. The 12 kHz
`digiraw` rate is lower than 16 kHz
because the decoder path does not need to transport audio above its configured
0–5 kHz passband. For ordinary voice, use Opus when network consumption matters;
use PCM when preserving samples and avoiding codec artifacts/delay matters.

## Digital PCM preparation

The receiver first tunes, demodulates and channel-filters the selected signal.
Digital mode then bypasses listening AGC, squelch, autonotch and noise reduction
so those effects cannot erase or reshape decoder evidence. A dedicated peak-hold
gain stage handles level variation without pumping up short pauses. The 16 kHz
float stream is converted to 12 kHz by a causal 3:4 polyphase antialias FIR
(129 taps per phase, 5.5 kHz cutoff, approximately 4 ms group delay). It is
quantized once to PCM16LE after resampling. The gateway forwards these samples;
it does not resample or encode them again. Sequence number, sample count and
timestamp let browser workers identify gaps and align digital receive windows.

## Realtime browser buffer

In a secure context, the browser uses an `AudioWorkletProcessor`. Each incoming
PCM16LE payload is decoded explicitly as little-endian signed 16-bit samples,
converted to float and appended to a fixed circular buffer. The worklet starts
playback after about 100 ms of queued audio, targets about 125 ms, corrects
small producer/device clock drift by adjusting the read step (bounded to ±0.4%),
and discards stale backlog rather than playing increasingly old audio. At
16 kHz its 32,768-sample ring has 2.048 seconds of capacity; if queued audio
exceeds 500 ms, it jumps forward to a short fresh buffer. Rate changes reset
the read position.

The gateway also bounds each client's live-media queue to 32 frames and drops
the oldest media frame under congestion. This favors current audio over stale
audio and prevents a slow browser from creating an ever-growing latency queue.
Reliable text/control messages use a separate queue. The worklet reports
underruns for diagnostics. The client does not claim that an observed gap
identifies whether Wi-Fi, cellular service, CPU scheduling or another layer
caused it.

The worklet has no codec or container of its own: it consumes PCM samples,
performs sample-rate/clock correction and writes the browser output buffer. A
gain node controls listening volume/mute independently of decoder input, so
muting does not stop digital decoding; pausing does.

## iOS and secure-context behavior

Audio playback is started from the user's button gesture: `AudioContext.resume()`
is called before awaiting worklet loading. When supported, the client requests
`navigator.audioSession.type = "playback"`. These browser playback mechanisms,
not raw PCM or WebSocket by themselves, are the likely reason audio can continue
with the screen locked on tested iOS/browser combinations. This is not a
background-execution guarantee: the operating system, browser version, power
policy, tab lifecycle and user gesture rules can still suspend it. Validate the
target browser/device rather than assuming that PCM itself keeps an app alive.

`AudioWorklet` requires a secure context in the supported browser path (HTTPS,
or localhost for development). The current code has a `ScriptProcessorNode`
fallback for insecure contexts, but that API is deprecated and should not be
the basis of a new implementation. Prefer HTTPS/WSS and a user-initiated start.

## Browser-local recording

Recording branches off the transport before playback, mute, volume adjustment
or browser output resampling:

- Raw: copy received PCM16LE directly into mono 16 kHz WAV.
- Digiraw: copy received PCM16LE directly into mono 12 kHz WAV.
- Balanced / mobile: copy the original compressed Opus packets into Ogg
  Opus (.opus). Do not record the 48 kHz WebCodecs output or encode again.

Ogg uses CRC-protected pages, OpusHead/OpusTags, a 48 kHz granule clock and an
EOS marker on finalization. Capture begins inside a running live stream, so
Opus pre-skip is zero; brief decoder startup artifacts are possible. Duration
counts received samples/packets, not connection gaps or time spent paused.
WAV reserves a ds64 chunk and becomes RF64 if the file exceeds the RIFF 4 GiB
limit. RF64 support varies between external players.

recorder-worker.js keeps container and storage work off the playback thread.
It detects OPFS synchronous access in a dedicated worker and atomic metadata
writes. Fallbacks use IndexedDB chunk transactions, then memory. The memory
fallback explicitly warns that navigation/closure loses the recording.
Pending data is checkpointed every **5 seconds or 256 KiB**, whichever comes
first. OPFS retains one data file plus small checkpoint metadata per recording;
IndexedDB stores ordered binary chunks and metadata in the same transaction.
The recording is not uploaded to HamSDR.

After reload, navigation or reopening the site, **Recuperar y Guardar** exports
the last durable checkpoint and repairs WAV sizes/Ogg EOS as necessary.
Active recordings in other tabs are excluded through browser Web Locks.
Without that API, a recent checkpoint is treated as active for 15 seconds.
Local recordings are retained until the user selects **Eliminar**. Exporting
does not delete them. Recovery is limited to the same origin/browser profile;
clearing site data, private browsing or browser eviction can remove it.
OPFS exports a file-backed Blob; IndexedDB/memory export assembles a Blob, so
very large exports can still create browser memory pressure.

There is no application time/size cap. Browser quota and device capacity still
apply: storage failure stops capture and preserves the previous checkpoint.
The display is MM:SS • MiB, changing to HH:MM:SS • MiB and blinking red
above one hour. Audio profile and digital-mode transitions are blocked during
capture; server-forced profile changes finalize the existing recording first.
Muting remains independent, and pausing audio finishes capture.

Validation: node tests/recorder_core_tests.mjs checks containers, CRC,
timestamps and RF64; .venv/bin/python tests/recording_browser_test.py runs
an isolated local demo and verifies Chromium/WebKit exports against the
received payload, interrupted capture, tab isolation and storage fallbacks.

## Porting checklist

For another project that wants the same low-latency PCM approach:

1. Produce **already selected and demodulated** mono audio at the lowest sample
   rate that still preserves the signal bandwidth. Apply an antialias filter
   before downsampling; do not send IQ unless clients really need to demodulate.
2. Quantize once to signed 16-bit little-endian PCM. Send it as binary chunks on
   a persistent WebSocket, not JSON/base64 or repeated HTTP requests. Put a small
   versioned kind/header before the payload if several stream types share it.
3. Include sequence numbers (and timestamps when clock alignment matters), so
   clients can detect loss, reset stale decoder state and align blocks.
4. Use bounded per-client queues. On congestion, drop old media rather than
   accumulating latency; keep reliable control messages on a separate path.
5. Let the browser opt into audio. Pause should stop network delivery and clear
   queued media; mute should be a local gain control when decoding must continue.
6. Start/resume `AudioContext` directly from a user gesture. Use an AudioWorklet
   and a bounded ring buffer, prebuffer only enough to absorb normal jitter, and
   trim excessive backlog. Keep sample-rate/clock correction bounded.
7. Offer Opus as a separate bandwidth-saving option where lossy audio is okay;
   do not substitute it for a decoder path that requires original PCM samples.
8. Measure end-to-end delay and payload bandwidth on target devices. The PCM
   rate formula above is exact for payload, but it excludes network framing and
   any audio/video/spectrum traffic sharing the connection.

The concrete HamSDR wire protocol is private to this application; reuse the
design principles, not the kind numbers, without versioning and documenting a
new protocol for the other project.
