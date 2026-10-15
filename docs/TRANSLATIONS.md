<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Interface translations

The selector beside the listener count offers Spanish (`es`), English (`en`)
and Brazilian Portuguese (`pt-BR`). The first visit defaults to Spanish; the
selection persists in the browser's `hamsdr-language` localStorage key. If
storage is unavailable, the choice still applies to the current page. No
cookies, translation service or receiver reconnection are involved.

`web/translations.js` contains the complete UI catalog: Spanish source text,
English, Brazilian Portuguese, and optional normalized Spanish for older
English labels. Named placeholders must match across all translations.
`web/i18n.js` translates static text and accessibility attributes and observes
changed UI subtrees for asynchronous server/worker notices. Original strings
are retained in weak maps so switching languages is reversible. Numeric
telemetry and decoder/log content are not translated by text matching.

`web/countries-i18n.js` contains offline labels for all 346 CTY/DXCC entities.
ISO region labels follow CLDR, with explicit translations for radio entities.
The displayed labels and alphabetical ordering follow the selected language;
canonical country values and received messages remain intact in memory/export.
Country translations do not depend on a browser translation API or network call.

Callsigns, chat/logbook submissions, received decoder text, saved memory names,
and station name/description/logo customization are application data, excluded
from automatic translation. These retain their configured/original language.
Runtime diagnostics supplied by the browser itself retain the browser's own
language; HamSDR's surrounding error messages are translated.

To add UI text, add its catalog row with coherent translations in all three
languages. Use textContent and plain-text placeholders; translated strings
must never be inserted as HTML. Explicitly translate content in a data-only
area with `HamI18n.t()` when it is an application notice rather than user data.
Country labels use `HamI18n.country()`. A `languagechange` window event lets
data renderers rebuild labels without resetting their DSP/worker state.

Run `.venv/bin/python tests/i18n_browser_test.py` for Chromium and WebKit, or
append `chromium` to select that engine only (as CI does). The isolated demo
checks catalog placeholders, live audio/recording continuity, decoder state,
country data, session dialogs, mobile layout, reload and denied localStorage.
