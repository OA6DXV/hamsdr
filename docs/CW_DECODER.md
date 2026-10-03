# CW decoding

Morse is enabled by `[server] digimodes = true` and appears beside FT8.
The Digimodos selector provides FT4, RTTY and CW Experimental. Selecting
the Digimodos placeholder disables the mode selected through this menu.
Activating it selects CW, a 450--950 Hz passband and the internal `digiraw`
mono PCM16 stream at 12 kHz. The demodulation and filter controls remain usable.

The browser sends copies of the same PCM packets to playback and a dedicated
worker. The worker runs cw-dit's Goertzel, envelope slicing, debouncing and
adaptive Morse decoding in WebAssembly. Two adjacent-frequency detectors
reject broadband noise. The initial speed defaults to 20 WPM; the decoder
estimates timing from the first eight marks and adapts thereafter. The tone
defaults to 700 Hz and can be changed numerically or by tapping the waterfall.
This first implementation decodes one selected tone.

A 4096-point overlapping Hann FFT in the worker supplies six waterfall rows
per second. The panel displays the receiver's audio passband with adaptive
contrast and the same purple/yellow palette as the receiver. It adds no
network stream. Worker backlog is bounded; sequence gaps reset timing so old
audio cannot be joined to a new fragment.

Mute affects playback only. Pausing audio terminates the worker and disables
audio transmission; starting it again creates a fresh decoder. Clearing the
text changes only the browser log. Changing the audio profile shows the
incompatible-profile message until Morse is toggled off and back on.
Shared links use `digital=CW` with optional `tone`, `wpm` and `mute` parameters;
reloaded links require the central audio-start button.

Rebuild: `bash tools/build_cw_wasm.sh` (Rust and wasm-pack required).
Tests: `cargo test --locked --manifest-path wasm/cw-decoder/Cargo.toml` and
`.venv/bin/python tests/cw_browser_test.py`.
The pinned upstream source and dependency licenses are documented in
`THIRD_PARTY_NOTICES.md` and `wasm/cw-decoder/Cargo.lock`.

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
