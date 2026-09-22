# Third-party notices

HamSDR source code is GPL-3.0-only. It uses the following dependencies at
build or runtime; they are not copied into this repository.

- FFTW3f, GPL-2.0-or-later, for shared spectrum FFT processing.
- aiohttp 3.14.3, Apache-2.0 or MIT, for HTTP and WebSocket transport.
- libopus, BSD-3-Clause, for optional Opus audio encoding.
- mfsk-core, GPL-3.0, for browser WebAssembly FT8/FT4 decoding when digital
  modes are enabled.
- Big CTY (`web/cty.dat.gz`) by Jim Reisert, AD1C, MIT License, for offline
  DXCC entity and prefix lookups in decoded digital messages. Its copyright
  and permission notice are reproduced below.

## Big CTY notice

BIG cty.dat was released on 23 March 2024.

Copyright © 1994- Jim Reisert, AD1C

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the “Software”), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Python standard-library modules, SQLite and browser Web APIs are provided by
the operating system or browser. Distributors that bundle any dependency must
also provide that dependency's required license notices and source as
applicable.
