# SPDX-License-Identifier: GPL-3.0-only
"""Small, offline, multilingual word filter for public community submissions."""
from pathlib import Path
import re
import unicodedata


class ModerationError(ValueError):
    """A rejected submission; never include the matched word in a response."""

    code = "offensive-language"

    def __init__(self, field, message=None):
        self.field = field
        super().__init__(
            message or (
                "El indicativo contiene lenguaje ofensivo; elige otro."
                if field == "name" else
                "El mensaje contiene lenguaje ofensivo. Revisa el texto antes de enviarlo."
            )
        )


class RepeatedCharacterError(ModerationError):
    """Reject excessive consecutive repetitions without echoing the input."""

    code = "repeated-character"

    def __init__(self, field):
        super().__init__(field, "No repitas el mismo carácter más de 10 veces seguidas.")


_REPEATED_CHARACTER = re.compile(r"(.)\1{10}", re.DOTALL)
_LEET = str.maketrans("01345789@$!", "oieastbgasi")
# A deliberately small visual-confusable map. It is applied only inside a
# token that already contains ASCII, so ordinary text written wholly in
# another script is not transliterated into the moderation vocabulary.
_CONFUSABLES = str.maketrans({
    # Cyrillic characters commonly substituted into Latin-looking words.
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x",
    "у": "y", "і": "i", "ј": "j", "к": "k", "м": "m", "т": "t",
    "в": "b", "н": "h",
    # Greek lookalikes found in mixed-script text.
    "α": "a", "ε": "e", "ο": "o", "ρ": "p", "χ": "x", "ι": "i",
    "κ": "k", "τ": "t", "ν": "v", "υ": "u",
    # Latin phonetic/small-cap forms that NFKD does not reduce to ASCII.
    "ɑ": "a", "ɡ": "g", "ı": "i", "ɩ": "i", "ⅼ": "l", "ᴏ": "o",
    "ᴘ": "p", "ᴛ": "t", "ᴜ": "u", "ᴠ": "v", "ᴡ": "w", "ᴢ": "z",
})
_UNICODE_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# These are ordinary adjacent words whose joined spelling collides with a
# blocked term. They are exempt only from split-word joining; literal matches
# and configured multi-word rules remain active.
_BENIGN_JOINED_WORD_BOUNDARIES = frozenset({
    ("put", "a"),  # English phrase must not collide with Spanish "puta".
    ("put", "on"), # Common English phrasal verb / hyphenated noun.
    ("con", "o"), # Common Spanish conjunction sequence.
    ("con", "a"), # Common Spanish preposition sequence.
    ("we", "on"), # Ordinary English phrase.
    ("desgraca", "do"), # Portuguese preposition boundary.
    ("mari", "con"), # Name plus preposition in Spanish/Portuguese text.
    ("s", "pic"), ("s", "pic", "s"), # English possessive + "pictures".
    ("who", "re"), # English contraction.
})
_COMMON_WORDS = frozenset(" ".join((
    "a an and are as at be been being but by can could did do does for from had has have he her here hers him his how i if in into is it its me my of on or our ours she so than that the their theirs them then there these they this those to too us was we were what when where which who why will with you your",
    "a al algo algunos algunas ante antes bajo cada como con contra cual cuando de del desde donde dos el ella ellas ellos en entre era es esa ese eso esta este esto fue ha hacia hasta hay la las le les lo los mas me mi mis mucho muy ni no o otra otras otro otros para pero por porque que quien se si sin sobre su sus te tu tus un una unas uno unos y ya",
    "a ao aos as até com como da das de dela delas dele deles do dos e ela elas ele eles em entre era essa esse esta este eu foi há isso isto já lhe lhes mais mas me meu minha muito na nas nem no nos nossa nossas nosso nossos num numa o os ou para pela pelas pelo pelos por porque pra pro que se sem seu sua suas também tem um uma umas uns você vocês",
)).split())
_ROOT = Path(__file__).resolve().parent
# Require an explicit shaft: the ordinary glasses/smile emoticon "8-D" is not
# prohibited. Work is bounded by the protocol's 500-character input limit.
_ASCII_GESTURE = re.compile(
    r"(?<![a-z0-9])[8b](?:\s*=\s*|(?:\s*[-=~_]){2,500}\s*)[dp](?![a-z0-9])"
    r"|\(_!_\)|╭∩╮|┌∩┐|🖕"
)


def _fold(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in text if not unicodedata.category(char).startswith(("M", "C")))


def _tokens(text):
    return _tokenize(text)[0]


def _tokenize(text):
    """Return normalized tokens and the text between adjacent tokens."""
    normalized = _fold(text).translate(_LEET)
    tokens = []
    separators = []
    previous_end = 0
    for match in _UNICODE_WORD.finditer(normalized):
        token = match.group(0)
        # Preserve native-script words. Confusable folding is intended for
        # mixed-script Latin spellings, not general transliteration.
        if any("a" <= char <= "z" for char in token):
            token = token.translate(_CONFUSABLES)
        tokens.append(token)
        if len(tokens) > 1:
            separators.append(normalized[previous_end:match.start()])
        previous_end = match.end()
    return tokens, separators


def _entries(path):
    """Read a bounded UTF-8 list; invalid operator files fail startup closed."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Moderation file must be a regular non-symlink file: {path}")
    try:
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError(f"Moderation file exceeds 1 MiB: {path}")
        lines = raw.decode("utf-8-sig").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"Cannot read UTF-8 moderation file: {path}") from error
    for number, line in enumerate(lines, 1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if len(entry) > 500 or any(unicodedata.category(c).startswith("C") for c in entry):
            raise ValueError(f"Invalid moderation entry: {path}:{number}")
        yield number, entry


class ModerationFilter:
    """An installation-local snapshot of two independent editable text lists."""

    def __init__(self, words_path=None, callsigns_path=None):
        self.words_path = Path(words_path) if words_path is not None else _ROOT / "data/moderation-words.txt"
        self.callsigns_path = Path(callsigns_path) if callsigns_path is not None else _ROOT / "data/callsigns.txt"
        phrases = set()
        for number, entry in _entries(self.words_path):
            phrase = tuple(_tokens(entry))
            if not phrase:
                raise ValueError(f"Moderation word needs letters or digits: {self.words_path}:{number}")
            phrases.add(phrase)
        callsigns = set()
        for number, entry in _entries(self.callsigns_path):
            call = entry.upper()
            if not entry.isascii() or not re.fullmatch(r"[A-Z0-9\\|/@_-]{1,10}", call):
                raise ValueError(f"Invalid moderation callsign: {self.callsigns_path}:{number}")
            callsigns.add(call)
        self.callsign_exceptions = frozenset(callsigns)
        self.phrases = frozenset(phrases)
        self.max_words = max(map(len, phrases), default=1)
        self.words = frozenset(phrase[0] for phrase in phrases if len(phrase) == 1)
        self.max_word_length = max(map(len, self.words), default=1)
        # Never reduce a short word like "ass" to "as".
        self.elongated = frozenset(re.sub(r"(.)\1+", r"\1", word) for word in self.words if len(word) >= 4)

    @classmethod
    def from_directory(cls, directory):
        """A present local list replaces its default; an empty list stays empty."""
        paths = []
        for filename in ("moderation-words.txt", "callsigns.txt"):
            local = Path(directory) / filename
            paths.append(local if local.exists() or local.is_symlink() else _ROOT / "data" / filename)
        return cls(*paths)

    def require_clean(self, text, field="text"):
        """Check bounded input without rewriting it or reading disk per message."""
        if _REPEATED_CHARACTER.search(unicodedata.normalize("NFC", text)):
            raise RepeatedCharacterError(field)
        # The caller enforces identifier syntax; exceptions never cover messages.
        if field == "name" and text.upper() in self.callsign_exceptions:
            return
        folded = _fold(text)
        if _ASCII_GESTURE.search(folded):
            raise ModerationError(field)
        tokens, separators = _tokenize(text)
        for start, word in enumerate(tokens):
            if (word in self.words or
                    len(word) >= 4 and re.sub(r"(.)\1+", r"\1", word) in self.elongated):
                raise ModerationError(field)
            for count in range(2, min(self.max_words, len(tokens) - start) + 1):
                if tuple(tokens[start:start + count]) in self.phrases:
                    raise ModerationError(field)
            # Join adjacent fragments as well as single-character sequences:
            # "mie rda" -> "mierda", and "ca raj o" -> "carajo". Only a
            # whole configured word can match, never an arbitrary substring.
            joined = ""
            for end in range(start, min(len(tokens), start + self.max_word_length)):
                joined += tokens[end]
                if len(joined) > self.max_word_length:
                    break
                if end > start:
                    boundary = tuple(tokens[start:end + 1])
                    separator_text = "".join(separators[start:end])
                    ordinary_spaced_words = (
                        any(char.isspace() for char in separator_text) and
                        all(token in _COMMON_WORDS for token in boundary)
                    )
                    if (boundary not in _BENIGN_JOINED_WORD_BOUNDARIES and
                            not ordinary_spaced_words and joined in self.words):
                        raise ModerationError(field)
            # Recognize simple letter-by-letter evasions ("p.u.t.a", "f u c k").
            if len(word) == 1:
                joined = word
                for following in tokens[start + 1:start + 20]:
                    if len(following) != 1:
                        break
                    joined += following
                    if len(joined) >= 4 and joined in self.words:
                        raise ModerationError(field)


# Standalone tools use the shipped defaults, never another installation's lists.
_DEFAULT = ModerationFilter()


def require_clean(text, field="text"):
    _DEFAULT.require_clean(text, field)
