# CW decoding

Morse is enabled by `[server] digimodes = true` and appears beside FT8.
The Digimodos selector provides FT4, RTTY and CW Experimental. Selecting
the Digimodos placeholder disables the mode selected through this menu.
Activating it selects CW, a 450--950 Hz passband and the internal `digiraw`
mono PCM16 stream at 12 kHz. The demodulation and filter controls remain usable.

The browser sends copies of the same PCM packets to playback and a dedicated
worker. The classic decoder uses narrow quadrature filters, quantile-tracked
envelope slicing and speed-adaptive glitch rejection in WebAssembly. Envelope
sampling stays at 600 Hz independently of the receiver filter bandwidth.
The narrow-filter width follows learned speed (35--80 Hz per pole), avoiding
the same broad noise admission for slow and fast operators.
Adjacent-frequency guards help reject noise without letting one strong
neighbour block the selected carrier. Timing is fitted jointly to marks and
gaps over a bounded rolling window, rather than trusting each noisy pulse.
The initial speed defaults to 20 WPM; acquisition requires at least twelve
marks with plausible dit/dah/gap cadence and supports 5--60 WPM. Mark-plus-gap
periods reduce errors from eroded edges; a substantially better timing fit
can recover from a stale speed estimate. The tone
defaults to 700 Hz and can be changed numerically or by tapping the waterfall.
Automatic acquisition/AFC is enabled by default. Up to six bounded candidate
decoders validate actual Morse cadence before tone selection, even in single
mode. Optional multidetection displays confirmed streams separately. Selection
survives pauses and follows validated nearby tone steps, with level/speed
guards against jumping to fast noise. Tapping the waterfall selects a new
tone explicitly. Clearing the log preserves detection evidence and timing;
cached acquisition text is not replayed after clearing or reacquisition.

A 4096-point overlapping Hann FFT in the worker supplies six waterfall rows
per second. The panel displays the receiver's audio passband with adaptive
contrast and the same purple/yellow palette as the receiver. It adds no
network stream. Worker backlog is bounded; sequence gaps discard partial
characters but preserve learned speed. A four-second audio pre-roll, 64 timing
intervals, at most 128 acquisition intervals and bounded per-stream logs keep
memory independent of listening duration.

Mute affects playback only. Pausing audio terminates the worker and disables
audio transmission; starting it again creates a fresh decoder. Clearing the
text changes only the browser log. Changing the audio profile shows the
incompatible-profile message until Morse is toggled off and back on.
Shared links use `digital=CW` with optional `tone`, `wpm` and `mute` parameters;
reloaded links require the central audio-start button.

Rebuild: `bash tools/build_cw_wasm.sh` (Rust and wasm-pack required).
Tests: `cargo test --locked --manifest-path wasm/cw-decoder/Cargo.toml` and
`.venv/bin/python tests/cw_browser_test.py`, plus `tests/cw_detector_tests.mjs`
and `tests/cw_tracking_browser_test.py` (Chromium and WebKit).
The pinned upstream source and dependency licenses are documented in
`THIRD_PARTY_NOTICES.md` and `wasm/cw-decoder/Cargo.lock`.

## Real recordings and limitations

`digiraw` generation uses a dedicated peak-hold gain stage (one-second hold,
ten-second release) and a native 16-to-12 kHz polyphase antialias FIR. Listening
AGC and audio effects are bypassed in digital mode; PCM16 quantization occurs
only after resampling. Added FIR delay is approximately 4 ms and network
payload remains 24 kB/s. These DSP changes improve transport preparation,
not proof of correct Morse transcription. Existing 12 kHz WAV recordings
cannot undo their earlier AGC/resampling; use original IQ captures to test
the new generation path.

An offline tool accepts external unsigned 8-bit interleaved IQ without an
rtl_tcp header: `build/hamsdr-digital-iq-replay INPUT CENTER_HZ FREQUENCY_HZ
MODE LOW HIGH SECONDS DIGITAL_0_OR_1 OUTPUT_PCM`. It produces little-endian
PCM16 at 12 kHz (digital=1) or 16 kHz (digital=0) and reports sample counts,
RMS/peak and clipped samples. It never connects to or controls the live SDR.
Keep recordings outside tracked source; they are not bundled or uploaded.

DSP regressions: `ctest --test-dir build --output-on-failure` includes passband
flatness, out-of-band alias rejection, uneven block continuity, gain hold/release
and sample-rate switching. `tests/digital_audio_browser_test.py SERVER_URL`
checks live FT8/FT4/RTTY/Morse audio, native packet sizes and pause in Chromium
and WebKit without posting community messages. Integration tests also cover
rate restoration after automatic shared-IP resource downgrades.

`tests/cw_recording_browser_test.py WAV` replays external mono PCM16/12 kHz
recordings through the shipped browser worker without touching rtl_tcp. It
reports audio-time tracking traces; optional `--expect-text` and
`--max-tone-offset` assertions support local regressions. A faster Rust-only
check is `cargo run --release --locked --manifest-path wasm/cw-decoder/Cargo.toml
--example replay -- WAV TONE SEED_WPM`. Recordings are not bundled or uploaded.

The October 4 recordings exposed noise-driven tone selection and timing
failures missed by clean synthetic tests. The revised decoder tracks the
first recording's approximately 744/643/683 Hz tone changes and recovers more
recognizable fragments (including CP4NET/73); the second recovers EZEIZA but
still has damaged letters and spacing. These are raw algorithm outputs, not
independently verified complete transcriptions. No dictionary, AI completion
or callsign substitution is applied. Strong fading, overlapping signals and
irregular hand keying still require further work; confidence is a timing-fit
score, not a probability that the text or callsign is correct.

## CW Experimental

CWformer v0.2.0 FP32 runs in a dedicated browser worker via ONNX Runtime
Web 1.22.0. It is loaded only when this mode starts audio; no inference runs
on the server. The first use downloads approximately 90 MiB of model/runtime
assets, served locally and cached by the browser. Mobile CPU/memory usage
can be substantially higher than Morse. No GPU or external API is required.

The network audio remains uncompressed `digiraw` PCM16 at 12 kHz. The worker
resamples to 16 kHz, applies the upstream 400-sample Hann/40-bin log-mel
frontend with its released exact tables, and processes 500 ms chunks using
the causal streaming model. Attention history is bounded to five seconds;
CTC collapse spans chunk boundaries. Tone and initial-WPM inputs are disabled
because the model estimates characters directly. It is trained for tones
200--1400 Hz and roughly 15--40 WPM; it is not a multistream decoder.
Like other neural decoders, it can invent characters in noise. Experimental
output is not proof of a valid signal or callsign. Browser inference/lifecycle
is tested in Chromium and WebKit, including a clean CQ/DE/W1AW test signal;
optional WAV arguments exercise previously captured 12 kHz receiver audio.

The focused waterfall, mute, pause, incompatible-profile guard and clearing
behavior are shared with Morse. Share links use `digital=CWFORMER`. Reloading
never starts audio automatically. Tests: `tests/cwformer_browser_test.py`.

Pinned assets (SHA-256):

- FP32 model: `cbc241311f55074f3bb522ddf2823f0bc97f26b30cfb28203d042031c7187279`
- mel basis: `fb5389c9b11b9f559858362a311278922e6f8a95ca6864c0f21a2c01dbcc3183`
- mel window: `138de18ad784bd866e7ebe9807f1713b69cebe99ebf43dd85dd85abab294eb88`
