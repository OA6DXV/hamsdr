# RTTY Experimental

This optional Digimodos entry is a receive-only adaptation of Fldigi's RTTY
DSP, not a build of its desktop application. The existing RTTY decoder and
multidetection remain available and unchanged in the RTTY entry.

The server sends the same lossless mono PCM16 digiraw stream at 12 kHz.
Decoding takes place in a separate browser worker, not on the server or the
audio rendering thread. The bundled decoder, loader and worker total about
55 KB; no neural model or external decoding API is involved.

## Adaptation

- Unmodified upstream `fftfilt.cxx`, `fftfilt.h`, `gfft.h` and `complex.h`
  implement overlap-add Nyquist filtering, with filters rebuilt for baud.
- `decoder.cpp` adapts optimal Automatic Threshold Correction, asymmetric
  envelope/noise estimates, start/data/stop timing and ITA2 shifts from
  `rtty.cxx`. Desktop TX, sound-card, UI and station dependencies are omitted.
- The sample rate is 12 kHz instead of the desktop default of 8 kHz.
  MARK is the lower audio tone to retain HamSDR's LSB/reverse convention.
- AFC uses filtered tone phase with a bounded correction instead of the
  desktop waterfall-dependent metric. This is an intentional adapter change.
- A spectral-presence gate and a sliding valid-frame ratio suppress output
  until a plausible selected stream is present. This is not a CRC: noisy
  signals can still produce false characters.

The first version decodes one selected stream, with manual baud/shift,
center, reverse and AFC. Multidetection and automatic profile suggestions
are disabled in this experimental entry, and remain in the original RTTY.
Switching back restores the original multidetection preference.

Mute changes playback only. Pause terminates the worker and stops the audio
stream; resume initializes a fresh receiver. PCM backlog is limited to four
packets, and sequence discontinuities or RF/mode changes reset receiver timing. Clearing the
experimental text log does not erase its waterfall or reset decoding.
Shared links use `digital=RTTY-EXPERIMENTAL`; reloading requires pressing the
central audio-start button, as with the other digital modes.

## Source and rebuilding

Upstream revision: `61b97f4133c488063f3de1795c894d22d5032e8a` from
https://github.com/w1hkj/fldigi. The complete RTTY source is retained as
`wasm/rtty-fldigi/upstream/rtty-reference.cxx` for auditing the adaptation.
Licenses and runtime notices are recorded in `THIRD_PARTY_NOTICES.md`.

Use Emscripten 3.1.74 and run:

```sh
bash tools/build_rtty_fldigi_wasm.sh
node tests/rtty_fldigi_tests.mjs
.venv/bin/python tests/rtty_fldigi_browser_test.py
BROWSER=webkit .venv/bin/python tests/rtty_fldigi_browser_test.py
```

`EMXX` may point to an absolute `em++` path. Tests exercise the actual browser
module at 45.45/50/75 baud, shifts, reverse, frequency offset and asymmetric
tone amplitude with noise. Browser tests cover stream sharing, the output
gate, responsive layout, mute, pause, mode switching and shared links.
Better real-world decoding than the original is not assumed until comparison
against known-text recordings or manually verified transmissions.
