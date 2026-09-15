# HamSDR

HamSDR is a lightweight, multi-user software-defined radio receiver with a
responsive browser interface and server-side DSP.

The current `dev` branch is a development preview. The `main` branch is
reserved for stable releases.

## Features

- Native C++20 IQ acquisition, shared spectrum FFT and independent receivers.
- AM, USB, LSB, CW and narrow FM demodulation.
- Adjustable filters, AGC, squelch, autonotch and noise reduction.
- Responsive HTML, CSS and framework-free JavaScript.
- Canvas spectrum and waterfall with independent quality and speed controls.
- AudioWorklet playback with bounded buffering and clock correction.
- Listener identification, live chat and persistent SQLite logbook.
- Same-origin WebSockets, bounded queues and automatic DSP recovery.
- Site identity and optional logo configured outside the frontend source.

The current preview accepts 1.024 Msps unsigned 8-bit IQ centered at
7.1005 MHz and produces 16 kHz mono PCM internally. The source is expected on
an `rtl_tcp`-compatible endpoint and must already be configured by its owning
process. Each browser can tune and demodulate independently without changing
the shared hardware.

The spectrum source contains 65,536 bins at approximately 7.8 rows per second.
Waterfall profiles provide up to 1,024 bins/4 bits, 2,048 bins/6 bits or 4,096
bins/8 bits. Slower waterfall settings reduce transmission at the server.

Audio and waterfall quality are selected independently. Audio offers:

- Original: PCM16 mono at 16 kHz.
- Balanced: IMA ADPCM mono at 16 kHz.
- Low bandwidth: IMA ADPCM mono at 8 kHz.

ADPCM is decoded inside the AudioWorklet. Audio is opt-in: pausing it stops its
network frames instead of applying only a local mute. WAV recording follows
the selected audio sample rate.

## Build

Requirements are Linux, a C++20 compiler, CMake, pkg-config, FFTW3f, Python
3.12 or newer and Python virtual-environment support. On Debian or Ubuntu:

```sh
sudo apt install g++ cmake pkg-config libfftw3-dev python3-venv
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j
ctest --test-dir build --output-on-failure
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py --demo
```

Open <http://127.0.0.1:18093/>. The demo source provides a 1 kHz tone on USB
7100, LSB 7090 and AM 7108 kHz.

For a real source:

```sh
.venv/bin/python server.py \
  --source-host 127.0.0.1 \
  --source-port 1231 \
  --bind 127.0.0.1 \
  --port 8080
```

Use HTTPS for remote access because AudioWorklet requires a secure context
outside localhost. Set `--origin` to the public HTTPS origin when deploying
behind a reverse proxy.

## Site customization

Copy the generic configuration and edit the copy:

```sh
cp site.example.json site.json
```

`site.json` is ignored by Git. It defines the receiver name, descriptive text,
language, administrator and optional logo. Logo files may be SVG, PNG, JPEG or
WebP up to 2 MB. An external configuration may be supplied with:

```sh
.venv/bin/python server.py --site-config /etc/hamsdr/site.json
```

## Verification

```sh
.venv/bin/python tests/integration_test.py -v
.venv/bin/python tests/audio_codec_tests.py -v
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

No project license has been selected yet. Public availability alone does not
grant permission to copy, modify or redistribute the source.
