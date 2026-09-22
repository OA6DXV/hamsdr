# HamSDR

Copyright (C) 2026 OA6DXV

HamSDR is a lightweight, multi-user software-defined radio receiver with a
responsive browser interface and server-side DSP.

The current `dev` branch is a development preview. The `main` branch is
reserved for stable releases.

## 0.5.0-unstable

- Client-side FT8/FT4 decoding with the bundled `mfsk-core` WebAssembly module.
- Digital operation is gated by `server.digimodes`. Entering FT8 or FT4 selects
  USB, the exclusive 12 kHz PCM `digiraw` profile and the connection-slow
  waterfall preset. Demodulation and waterfall quality remain user-selectable.
- Selecting another audio profile stops the decoder until the digital mode is
  toggled off and on again.

## 0.4.1-stable

- Shareable URLs preserve frequency, mode, filter and zoom.
- The station flag is used as the browser icon.
- The squelch threshold remains visible and is disabled until squelch is enabled.
- Project source is now GPL-3.0-only.

## Features

- Native C++20 IQ acquisition, shared spectrum FFT and independent receivers.
- AM, USB, LSB, CW and narrow FM demodulation.
- Adjustable filters, AGC, squelch, autonotch and noise reduction.
- Responsive HTML, CSS and framework-free JavaScript.
- Canvas spectrum and waterfall with independent quality and speed controls.
- AudioWorklet playback with bounded buffering and clock correction.
- Native Opus transport with browser capability fallback.
- Client-side interruption detection with low-bandwidth recommendations.
- Listener identification, live chat and persistent SQLite logbook.
- Same-origin WebSockets, bounded queues and automatic DSP recovery.
- Site identity and optional logo configured outside the frontend source.
- The configured station flag is also used as the favicon and touch icon.

The current preview accepts 1.024 Msps unsigned 8-bit IQ and produces 16 kHz
mono PCM internally. The center frequency and source endpoint are selected by
the enabled `[band.N]` section. Each browser can tune and demodulate
independently without changing the shared hardware.

The spectrum source contains 65,536 bins at 15.625 rows per second. Connection
slow uses 1,024 bins at 6 bits and 5 fps. The remaining profiles preserve 8-bit
levels with lossless differential encoding: low definition uses 1,024 bins,
balanced uses 2,048 bins and high definition uses 4,096 bins. Balanced is the
default on every device. High definition unlocks an 11.7 fps high-speed mode.
Slower waterfall settings reduce transmission at the server.

Audio and waterfall quality are selected independently. Audio offers:

- Raw: PCM16 mono at 16 kHz.
- Balanced: Opus at 32 kbit/s, wideband mono input at 16 kHz.
- Low bandwidth: Opus at 12 kbit/s, narrowband mono input at 16 kHz.

Balanced Opus is the default for a new browser. Opus is encoded by system
`libopus`, decoded through browser WebCodecs and passed into the same
AudioWorklet. Unsupported clients fall back to raw PCM16. Audio is opt-in: pausing
it stops its network frames instead of applying only a local mute. WAV
recording follows the decoded audio sample rate.

The current tuning state is reflected in a shareable URL. `freq` is expressed
in kHz; `mode` accepts `LSB`, `USB`, `AM`, `CW` or `NFM`; optional `low` and
`high` values describe the audio filter in Hz, and `zoom` accepts 1 through 64.
For example: `/?freq=7100&mode=LSB&zoom=4`. Values outside the configured band
or control ranges are ignored. The Copy link button writes the canonical
current URL to the clipboard. Audio remains user-initiated to comply with
browser autoplay policies.

The client observes actual audio buffer underruns, missing or late waterfall
sequence numbers and unexpected WebSocket reconnects. It displays a temporary
red recommendation only after three interruption samples within 30 seconds,
avoiding warnings for isolated jitter, and does not change either selection
automatically.

## Build

Requirements are Linux, a C++20 compiler, CMake, pkg-config, FFTW3f, Python
3.12 or newer, libopus and Python virtual-environment support. On Debian or Ubuntu:

```sh
sudo apt install g++ cmake pkg-config libfftw3-dev libopus0 python3-venv
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py --demo --database /tmp/hamsdr-demo.sqlite3
```

The repository includes the browser decoder artifacts. To rebuild them after
changing the Rust adapter, install Rust and `wasm-pack`, then run
`tools/build_mfsk_wasm.sh`.

Open <http://127.0.0.1:18093/>. The demo source provides a 1 kHz tone on USB
7100, LSB 7090 and AM 7108 kHz.

For a real source, configure and enable one `[band.N]` entry as described below,
then start HamSDR with that installation file:

```sh
.venv/bin/python server.py --site-config /etc/hamsdr/site.toml
```

Use HTTPS for remote access because AudioWorklet requires a secure context
outside localhost. Set `--origin` to the public HTTPS origin when deploying
behind a reverse proxy.

`[secure].mode` accepts three values: `"insecure"` for direct HTTP and classic
audio, `"native"` for native HTTPS/WSS with the configured PEM certificate and
private key, or `"proxy"` when an HTTPS reverse proxy terminates TLS. Native TLS
and a reverse proxy should not be enabled on the same listener.

When `[secure].mode` is `"insecure"` and the page is opened directly over HTTP,
HamSDR falls back to the deprecated but widely compatible ScriptProcessor Web
Audio path. It keeps the PCM/Opus stream interactive with a short buffer, but
is not expected to survive iOS background suspension. Secure pages continue to
use AudioWorklet, including the existing background behavior.

Public deployments should set `--trusted-proxy` to the proxy's literal IP and
leave the browser's `Origin` header unchanged. `--max-clients-per-ip` defaults
to three. If a CDN is present, normalize its authenticated client address in
the reverse proxy before setting `X-HamSDR-Client-IP`; never trust that header
from arbitrary peers.

Shared-address resource policy is configured with
`full_quality_sessions_per_ip` and `max_bandwidth_kbps_per_ip`. By default, at
most two sessions behind one address may select high-definition waterfall or
raw PCM audio. The server measures application payload egress every five
seconds. Three consecutive samples above 1,000 kb/s progressively reduce
waterfall and audio profiles; three samples below 80% of the threshold release
each reduction stage. TLS, WebSocket, TCP, reverse-proxy and CDN overhead are
not included, so this is an adaptive application limit rather than an exact
network shaper. The connection ceiling remains an independent last-resort
control.

Each WebSocket starts with an explicit protocol handshake. The current
application protocol is version 1 and negotiates waterfall protocol version 2
before the server accepts tuning, profile, chat or logbook controls. An
incompatible or silent client is closed rather than receiving binary streams.

## Site customization

Create one installation directory, copy the generic configuration into it and
edit the copy:

```sh
sudo install -d -m 0755 /etc/hamsdr
sudo install -m 0644 site.example.toml /etc/hamsdr/site.toml
sudo editor /etc/hamsdr/site.toml
```

`site.toml` is ignored by Git. It defines the HTTP listener and connection
limits under `[server]`, browser identity under `[html]`, public station metadata
under `[station]`, numbered receiver sources under `[band.N]`, ReceiverBook
discovery under `[receiverbook]`, and persistence under `[storage]`. Band indexes
must be contiguous from zero. The current engine accepts exactly one enabled
band, `receiver_type = "rtltcp"`, and `sample_rate_khz = 1024.0`.

On every connection and reconnection HamSDR sends the standard `rtl_tcp`
commands for sample rate, center frequency and gain. Set
`receiver_gain = "auto"` (also the default when omitted) for tuner AGC, or use
any finite numeric dB value for manual gain, including negative values. HamSDR
does not impose tuner-specific limits; it converts the value internally and
lets `rtl_tcp` or the SDR handle unsupported levels. A passive port of a
multi-client relay may ignore these commands; a classic or controlling
`rtl_tcp` connection applies them before HamSDR consumes the IQ stream.

`working_directory` must be absolute. Certificates, the station flag and the
SQLite database are filename-only references and must live directly inside
that directory; subdirectories, absolute per-file paths and traversal are
rejected. Flags may be SVG, PNG, JPEG or WebP up to 2 MB. Equivalent
command-line options override the file when supplied. An external configuration
may be supplied with:

`[storage].max_size_mb` limits the main SQLite community database and defaults
to 50 MB. Events do not expire by age. When space is needed, HamSDR removes the
oldest chat or logbook entries first and SQLite reuses the released pages.

```sh
.venv/bin/python server.py --site-config /etc/hamsdr/site.toml
```

When ReceiverBook is enabled, `tag` must contain the complete 64-hex-character
confirmation `<meta>` tag. HamSDR injects it into the document head and exposes
the legacy-compatible `/~~orgstatus` endpoint using `[station]` plus all enabled
bands. `mobile_page = "/"` identifies the responsive root page.

Legacy `.json` configuration files and the former single `[receiver]` section
remain readable for migration compatibility.
Using `server.origin = "*"` disables browser Origin protection and is intended
only for controlled diagnostics.

## Verification

```sh
.venv/bin/python tests/integration_test.py -v
.venv/bin/python tests/site_config_tests.py -v
.venv/bin/python tests/opus_codec_tests.py -v
.venv/bin/python tests/history_tests.py -v
.venv/bin/python tests/waterfall_codec_tests.py -v
.venv/bin/pip install playwright==1.62.0
.venv/bin/playwright install --with-deps chromium
# Start a separate demo server on port 18094 first.
.venv/bin/python tests/browser_test.py
```

## Branch policy

- `dev`: active implementation and integration testing.
- `main`: stable releases only.

## License

HamSDR is licensed under the GNU General Public License, version 3
(GPL-3.0-only). See [LICENSE](LICENSE).

The source tree includes no receiver-specific configuration, credentials,
certificates, databases, or bundled third-party binaries. Installation-specific
files belong outside the repository.

Third-party runtime dependencies and redistribution notes are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
