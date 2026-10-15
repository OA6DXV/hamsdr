# Third-party notices

HamSDR source code is GPL-3.0-only. It uses the following dependencies at
build or runtime. Compiled browser decoders and the CTY database are included.

- FFTW3f, GPL-2.0-or-later, for shared spectrum FFT processing.
- aiohttp 3.14.3, Apache-2.0 or MIT, for HTTP and WebSocket transport.
- libopus, BSD-3-Clause, for optional Opus audio encoding.
- Fldigi receive-only RTTY adaptation, GPL-3.0-or-later, by Dave Freese,
  W1HKJ, Stefan Fendt, DL1SMF and contributors, with ancestry in Tomi
  Manninen's gmfsk. Source: https://github.com/w1hkj/fldigi at revision
  `61b97f4133c488063f3de1795c894d22d5032e8a`. Unmodified FFT-filter sources
  and the reference RTTY receiver are in `wasm/rtty-fldigi/upstream/`;
  the receive-only HamSDR adapter is GPL-3.0-only. Fldigi's g_fft template
  is LGPL-3.0-or-later and based on public-domain John Green FFT code.
  License notices: `licenses/Fldigi-GPL-3.0.txt` and `licenses/LGPL-3.0.txt`.
  The bundled browser module is built with Emscripten 3.1.74; runtime
  notices are in `licenses/Emscripten.txt`, `licenses/LLVM-libcxx.txt`
  and `licenses/musl.txt`. Compiler source:
  https://github.com/emscripten-core/emscripten/tree/3.1.74.
- mfsk-core, GPL-3.0, for browser WebAssembly FT8/FT4 decoding when digital
  modes are enabled.
- cw-dit (`cwdit-dsp` and `cwdit-morse`), MIT OR Apache-2.0, by swilcox and
  contributors, for browser WebAssembly CW decoding. HamSDR selects Apache-2.0.
  Source: https://github.com/swilcox/cw-dit at revision
  `153fc247ce6e4934c94e0cd2dcbf7887e368ec29`. The HamSDR adapter is GPL-3.0-only;
  the upstream crates remain under their original license. The Apache license
  is included in `licenses/Apache-2.0.txt`. Rust runtime dependencies of this
  adapter (wasm-bindgen, rustfft, num-traits, num-integer, num-complex,
  primal-check, transpose, strength_reduce, cfg-if and once_cell) also offer
  Apache-2.0; exact versions and sources are in `wasm/cw-decoder/Cargo.lock`.
  Build dependencies also include unicode-ident; its Unicode data notice is
  reproduced in `licenses/Unicode-3.0.txt`.
- Big CTY (`web/cty.dat.gz`) by Jim Reisert, AD1C, MIT License, for offline
  DXCC entity and prefix lookups in decoded digital messages. Its copyright
  and permission notice are reproduced below.
- LDNOOBW, Copyright 2012–2020 Shutterstock, Inc., CC-BY-4.0, for selected
  Spanish, English and Portuguese moderation vocabulary in `data/moderation-words.txt`.
  Source: https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words
  at revision `5faf2ba42d7b1c0977169ec3611df25a3c08eb13`. HamSDR selects terms
  and adds regional insults/plurals; it does not use the unmodified full lists.
  Attribution license: `licenses/CC-BY-4.0.txt`. The matching code is GPL-3.0-only.
- Selected moderation vocabulary in `data/moderation-words.txt` also uses
  Surge AI's Obscenity List, FlacSy/BadWords and jfreddypuentes/spanlp under
  their MIT notices, reproduced in `licenses/Moderation-MIT.txt`. Pinned
  revisions, original sources and the selected terms are recorded in text-file comments.
  Ordinary words and ambiguous entries from the full lists are not imported.
  Additional explicitly derived plurals are HamSDR contributions.
- Selected Portuguese moderation entries also come from kyriosdata/palavrao,
  Apache-2.0, revision `87004212ab63ffbdf465bc24b7cb8f2fe9e1932e`.
  Source: https://github.com/kyriosdata/palavrao, `fontes/palavroes.txt`.
  HamSDR selects a subset, deduplicates and combines it with the other reviewed
  lists. The license is included in `licenses/Apache-2.0.txt`; no runtime code
  from that project is used. Real social comments/forum corpora used to guide
  review and evaluate false positives are not distributed with HamSDR.

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
