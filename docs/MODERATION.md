<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Community moderation

New callsigns, renames, chat and logbook text pass through `moderation.py` on
the server. Spanish, English and Portuguese vocabularies apply together,
independently of interface language or client-side form checks. Rejected text
is neither stored nor broadcast and does not consume a posting quota. The
original form remains editable; a rejected pending message is held as a draft
instead of automatically retried after reconnect. Only an explicit Send
submits it again. Rejected renames preserve the previous server-side identity.
Feedback is available in Spanish, English and Brazilian Portuguese.

## Repeated rejected chat submissions

Chat counts `offensive-language` and `repeated-character` rejections per
server-issued browser identity, regardless of the text or current callsign.
The first four rejections show feedback; the fifth starts a one-second chat
sanction. Each later rejected submission after that sanction expires doubles
the duration: 2, 4, 8 seconds, up to 512 seconds. During a sanction all new chat
submissions are rejected, including clients that bypass HTML controls. Attempts
during the sanction do not extend it or advance the counter. Audio, waterfall,
reading history, and the separate logbook quota remain available. Logbook and
callsign rejections do not increment this chat-specific counter.

Ten minutes without a moderation rejection reset both the counter and its
escalation. A successful message does not reset strikes. The existing state
database stores this information in `posting_limits` with scope
`chat-moderation`, independently of the normal chat cooldown and logbook quota.
There is no schema change or message-body storage. Reloading, reconnecting,
renaming, transferring a session, or restarting the server retains the sanction;
a different browser identity is independent, even behind the same public IP.

The private `chat-cooldown` packet reports `remaining_ms` and `duration_ms` on
admission and after a moderated chat attempt. Correlated moderation errors carry
the same `chat_cooldown` state; requests during a sanction use error code
`chat-cooldown`. Only that identity's connections receive its state. The button
fills blue against a pale background as time passes and is enabled again on
expiry if the connection and callsign are ready. Client timing uses a monotonic
clock; the server remains authoritative. Rejected drafts never auto-retry.
Feedback appears below the corresponding form in bold red and is not duplicated
in the global receiver notice.

Chat and logbook submissions also reject more than ten consecutive identical
Unicode characters after NFC normalization, including punctuation and spaces.
This checks the original input before storage whitespace normalization, not the
total occurrences of a character throughout a message. Ten repetitions remain
valid. The private `repeated-character` error preserves the editable held draft
and posting quota just like a vocabulary rejection. Callsigns already have a
ten-character total limit. This rule limits simple repeated-character art; it
does not detect every possible ASCII drawing.

## Editable text lists

The shipped defaults are `data/moderation-words.txt` (blocked words/phrases)
and `data/callsigns.txt` (exact identifier-only exceptions). Copy
either or both files directly into the TOML `working_directory`, keeping
their filenames, to customize an installation. No additional TOML keys or
subdirectories are needed. Each present local file **replaces**, rather than
extends, its respective default. Adding or deleting an entry therefore takes
effect without a hidden built-in copy restoring it. A missing local file uses
the shipped default independently of the other list. A present empty file
means an empty list, not fallback. Keep customized files outside the source
checkout so `git pull` does not overwrite them; HamSDR never writes them.

Use UTF-8, one word/phrase or callsign per line. Blank lines and full lines
starting with `#` are ignored. There are no inline comments, regular
expressions or wildcard entries. Callsigns are case-insensitive and must obey
the existing ten-character ASCII identifier syntax; they do not need to be
listed to use the receiver. Comments/source notices can remain in each file.
For example, adding a word to the words file blocks it; adding an exact
callsign to the callsigns file bypasses vocabulary matching only for that
identifier, not for its messages, length or syntax checks.
Because matching ignores case, accents and common substitutions, remove any
equivalent variants too when allowing a previously blocked term.

Restart the corresponding HamSDR service after editing. Lists are read and
compiled once per server instance, not on every message. Malformed/unreadable
files, symlinks and files larger than 1 MiB fail startup instead of silently
disabling moderation. The repetition and explicit-gesture rules are independent
of both lists and remain active even when the words file is empty. This change
does not install the full external research/reference database.

Python module changes also require restarting the corresponding server.
Development serves static assets from disk, so a refreshed interface can show
new JavaScript while an old process still runs the previous moderation logic.

## Vocabulary provenance and behavior

The words file contains a curated selection from LDNOOBW plus regional terms
and explicit variants. Upstream revision, attribution and license are recorded
in file comments and THIRD_PARTY_NOTICES.md.
Ambiguous vocabulary (colors, sexual orientation, ordinary objects and radio
abbreviations) is deliberately excluded. Match whole words and phrases rather
than substrings, so a normal callsign such as K4ASS remains valid. Comparison
handles case, accents, compatibility forms, common numeric/symbol substitutions,
elongated words, invisible format characters, a small mixed-script homoglyph map,
and punctuation/space inserted within words, including fragments
such as `mie rda` and `ca raj o`. When whitespace separates fragments, common
function-word sequences are protected to reduce cross-language collisions;
ambiguous boundaries such as `put on` have explicit benign exceptions. Literal
blocked terms and configured multi-word rules remain checked.
The mixed-script map is deliberately limited and does not transliterate wholly
Cyrillic/Greek text. It is not a complete Unicode-confusables implementation;
structural changes such as transposition or deletion may still evade it. The
submitted text is never rewritten. No runtime downloads, API keys, remote
moderation service, neural model or additional Python package is required.

The same words file includes individually selected explicit insults, compound
words and regional/spelling variants from pinned MIT sources. Each source
retains its licence notice in comments. The external research corpora are not
installed into the product; anatomical/identity words and ordinary objects
in public lists do not automatically become prohibitions.

The 2026-10-15 expansion adds 462 normalized word/phrase entries after reviewing
the retained social-comment/forum reference and its unreviewed lexical
candidates. Only selected vocabulary from the pinned MIT lists and the
Apache-2.0 kyriosdata/palavrao list is shipped, not source comments or user
records. Comments in the words file preserve source/revision/license notices.
Examples include regional Spanish insults, English evasion spellings and
Brazilian Portuguese profanity/abbreviations. Neutral names, identities and
objects such as Dick, Peter, negro, gay, mesa, sheet and BBC remain unblocked.
The existing lists now contain 886 distinct normalized entries. Exact F4GET
and M0FOS identifier exceptions address two newly observed LoTW collisions.

Broader vocabulary is stricter even when profanity is not directed at anyone.
For example, `pqp` is rejected even when used as an expression of surprise;
social-dataset labels may call that non-offensive. Do not interpret lexical
coverage as contextual-abuse accuracy. Remove unwanted entries/variants from
the installation's text file to adjust this policy.

An additional bounded detector handles explicit ASCII gestures such as
`8---D`, `8===D`, `(_!_)` and raised-middle-finger glyphs. Ordinary `8-D`,
smileys and `8 dB` remain valid. Decisions always examine the complete input;
there is no prefix-based result cache or edit-distance fuzzy matching.

The shipped `data/callsigns.txt` contains a small reviewed set of exact
identifier exceptions for collisions observed in the public LoTW reference
snapshot. For example, numeric substitution makes the real W4NK look like a
prohibited word. The exception applies only after the existing ASCII/length
validation, only in the identification field and only to the whole listed
identifier. It does not exempt that user's chat/logbook text or arbitrary
prefixes/suffixes. A listed call is not proof of ownership or current licence
status, and an unlisted call is not rejected merely because it is absent.
The complete research whitelist stays outside Git rather than adding the
entire registry to every installation's RAM/dependencies.

This is a vocabulary filter, not semantic abuse detection or authentication.
It cannot reliably detect sarcasm, every regional insult or deliberate evasions,
and some legitimate uses of a listed word may be rejected. Extend/review the
dictionary and benign examples together when adjusting policy. Existing stored
history, operator customization and digital decoder output remain unchanged.
The existing connection, message-rate, length and daily-log limits still apply.
Moderation does not replace plain-text rendering or XSS protections.

Checks: `tests/moderation_tests.py`, the moderation case in
`tests/integration_test.py`, `tests/community_render_tests.mjs`,
`tests/moderation_cooldown_browser_test.py` (Chromium/WebKit), and the
moderation browser scenario in `tests/i18n_browser_test.py`. All server/browser
tests use an isolated demo receiver and a temporary database.

`tools/evaluate_python_moderation.py --reference SNAPSHOT --output NEW_DIRECTORY`
compares current Python decisions with the frozen earlier Python results in
the external reference SQLite database. It preserves that database read-only
and records new per-case results separately. Once consulted during development,
this is a regression reference, not an untouched blind accuracy benchmark.
Offence/toxicity labels also include contextual insults without profane words;
they are broader than this vocabulary filter's policy.

The 2026-10-15 development comparison covered 426,265 records against the
frozen prior implementation. It resolved the 14 previously observed real
callsign collisions. Detection increased from 48.6% to 50.0% on OffendES and
41.9% to 47.0% on the majority-labelled ToLD-Br view, with corresponding
source-labelled false-positive rates increasing from 3.1% to 3.3% and
25.2% to 26.3%. These are policy tradeoffs on external corpora, not guaranteed
accuracy on HamSDR traffic or proof that every rejected example is abusive.
