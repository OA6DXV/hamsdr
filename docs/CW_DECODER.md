# CW decoding

CW-Decode is enabled by `[server] digimodes = true` and appears beside RTTY.
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
incompatible-profile message until CW-Decode is toggled off and back on.
Shared links use `digital=CW` with optional `tone`, `wpm` and `mute` parameters;
reloaded links require the central audio-start button.

Rebuild: `bash tools/build_cw_wasm.sh` (Rust and wasm-pack required).
Tests: `cargo test --locked --manifest-path wasm/cw-decoder/Cargo.toml` and
`.venv/bin/python tests/cw_browser_test.py`.
The pinned upstream source and dependency licenses are documented in
`THIRD_PARTY_NOTICES.md` and `wasm/cw-decoder/Cargo.lock`.
