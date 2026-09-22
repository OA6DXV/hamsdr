# Third-party notices

HamSDR source code is GPL-3.0-only. It uses the following dependencies at
build or runtime; they are not copied into this repository.

- FFTW3f, GPL-2.0-or-later, for shared spectrum FFT processing.
- aiohttp 3.14.3, Apache-2.0 or MIT, for HTTP and WebSocket transport.
- libopus, BSD-3-Clause, for optional Opus audio encoding.
- mfsk-core, GPL-3.0, for browser WebAssembly FT8/FT4 decoding when digital
  modes are enabled.

Python standard-library modules, SQLite and browser Web APIs are provided by
the operating system or browser. Distributors that bundle any dependency must
also provide that dependency's required license notices and source as
applicable.
