# SPDX-License-Identifier: GPL-3.0-only
"""Offline moderation, evasions, and benign radio vocabulary regressions."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from community import normalize_callsign
from moderation import ModerationError, ModerationFilter, require_clean


class ModerationTests(unittest.TestCase):
    def test_editable_lists_replace_defaults_and_reload_at_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            words = root / 'moderation-words.txt'
            callsigns = root / 'callsigns.txt'
            words.write_text('\ufeff# Words\n\nidiot\nradio blocked\n', encoding='utf-8')
            callsigns.write_text('# Exact exceptions\nid1ot\n', encoding='utf-8')
            policy = ModerationFilter.from_directory(root)
            with self.assertRaises(ModerationError):
                policy.require_clean('radio blocked')
            self.assertEqual(normalize_callsign('id1ot', moderator=policy), 'ID1OT')
            with self.assertRaises(ModerationError):
                policy.require_clean('id1ot')
            # Shipped words absent from the replacement file stay removed.
            policy.require_clean('fuck')
            words.write_text('# Now empty\n', encoding='utf-8')
            callsigns.write_text('', encoding='utf-8')
            with self.assertRaises(ModerationError):
                policy.require_clean('idiot')
            changed = ModerationFilter.from_directory(root)
            changed.require_clean('idiot')
            self.assertFalse(changed.callsign_exceptions)
            self.assertNotEqual(changed.words, policy.words)
            # Structural protections remain independent of the word list.
            for text in ('8===D', '=' * 11):
                with self.assertRaises(ModerationError):
                    changed.require_clean(text)
            words.write_text('idiot\n', encoding='utf-8')
            with self.assertRaises(ModerationError):
                normalize_callsign('ID1OT', moderator=ModerationFilter.from_directory(root))
            # No mutable policy is shared with another server or tool.
            require_clean('radio blocked')

    def test_each_missing_list_falls_back_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            defaults = ModerationFilter.from_directory(root)
            (root / 'moderation-words.txt').write_text('', encoding='utf-8')
            custom = ModerationFilter.from_directory(root)
            custom.require_clean('fuck')
            self.assertEqual(custom.callsign_exceptions, defaults.callsign_exceptions)
            (root / 'moderation-words.txt').unlink()
            (root / 'callsigns.txt').write_text('', encoding='utf-8')
            custom = ModerationFilter.from_directory(root)
            self.assertFalse(custom.callsign_exceptions)
            self.assertEqual(custom.phrases, defaults.phrases)

    def test_invalid_operator_lists_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            words = root / 'moderation-words.txt'
            callsigns = root / 'callsigns.txt'
            for content in (b'\xff', b'A' * (1024 * 1024 + 1), b'\x00bad', b'=' * 20):
                words.write_bytes(content)
                with self.assertRaises(ValueError):
                    ModerationFilter.from_directory(root)
            words.write_text('radio\n', encoding='utf-8')
            for call in ('TOO-LONG-1234', 'A B', 'Ä1BC', 'ID<1>'):
                callsigns.write_text(call, encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'Invalid moderation callsign'):
                    ModerationFilter.from_directory(root)
            callsigns.write_text('name\nA\\|/@-_1\n', encoding='utf-8')
            policy = ModerationFilter.from_directory(root)
            self.assertEqual(policy.callsign_exceptions, {'NAME', 'A\\|/@-_1'})
            words.unlink()
            words.symlink_to(callsigns)
            with self.assertRaisesRegex(ValueError, 'non-symlink'):
                ModerationFilter.from_directory(root)
            words.unlink()
            words.mkdir()
            with self.assertRaisesRegex(ValueError, 'regular'):
                ModerationFilter.from_directory(root)

    def test_three_languages_are_checked_together(self):
        for text in ("eres un pendejo", "what an asshole", "vai tomar no cu",
                     "hijo de puta", "filha da puta", "motherfucker",
                     "you are an idiot", "cuzão", "estúpido"):
            with self.subTest(text=text), self.assertRaises(ModerationError):
                require_clean(text)

    def test_simple_obfuscation(self):
        for text in ("P3ND3J0", "PÚTÁ", "ＰＵＴＡ", "p.u.t.a", "f u c k",
                     "miiiieeerdaaa", "p0rr@", "FÜCK", "f\u200buck", "🖕"):
            with self.subTest(text=text), self.assertRaises(ModerationError):
                require_clean(text)

    def test_expanded_regional_vocabulary_and_online_evasions(self):
        for text in ("eres un joputa", "aweonao", "mamaguevo", "me llegas al pincho",
                     "reconchesumare", "careverga", "chupapija", "wepota",
                     "fcking", "fvck", "phucking", "STFU", "wanker", "jackass",
                     "niggers", "groid", "sonofabitch", "scumbag", "c*nt",
                     "pqp", "vagabundo", "safada", "mocreia", "bucetinha",
                     "piroca", "punheta", "boquete", "fodido"):
            with self.subTest(text=text), self.assertRaises(ModerationError):
                require_clean(text)
        # High corpus frequency is not sufficient to ban neutral identities,
        # ordinary names/objects, radio abbreviations or technical vocabulary.
        for text in ("El cable negro conecta la antena a la computadora",
                     "Soy gay y escucho la radio desde Perú",
                     "La señal tiene un pico fuerte y un ancho de banda estable",
                     "Boa escuta, coragem para ajustar a torneira e a mesa",
                     "O frango assado ficou no saco perto da cerveja",
                     "BBC radio, Dick and Peter, data sheet, forked code, CW FT8 RTTY",
                     "La campana y el martillo están al lado del mapache",
                     "New users: K4ASS, PU2ABC, AA9900JHFR, OA6DXV"):
            with self.subTest(text=text):
                require_clean(text)

    def test_reviewed_vocabulary_and_compound_boundaries(self):
        for text in ("you fucking cocksucker", "what a fuckface", "mothafucking shitbag",
                     "eres un pelotudo", "hijueputa", "conchatumare", "chupapinga",
                     "comemierda", "fodeu", "vai se fuder", "buceta", "fodendo"):
            with self.subTest(text=text), self.assertRaises(ModerationError):
                require_clean(text)
        for text in ("Mucho ruido, pero ya está hecho", "Tenho um banho de sinal forte",
                     "Ajusta el espacio y atornilla la antena", "The cockatoo is outside",
                     "Tenemos un mapache, una campana y un martillo",
                     "Negro, gay, lesbiana, bissexual, frango assado, 73"):
            with self.subTest(text=text):
                require_clean(text)

    def test_ascii_gestures_and_benign_emoticons(self):
        for text in ("8---D", "8=D", "8===D", "B===D", "8 - - - D", "8==p",
                     "(_!_)", "╭∩╮", "┌∩┐", "8" + "=" * 498 + "D"):
            with self.subTest(text=text), self.assertRaises(ModerationError):
                require_clean(text)
        for text in ("8-D", ":-)", "8D", "8 dB", "8dBm", "B--DX", "8===DB",
                     "Medición: 8 --- dB"):
            with self.subTest(text=text):
                require_clean(text)

    def test_exact_callsign_exceptions_do_not_exempt_messages_or_suffixes(self):
        for call in ("W4NK", "W4NK/0", "SP1C", "LA/SP1C/P", "VE3RGA", "N1GGA", "F4GET", "M0FOS"):
            with self.subTest(call=call):
                self.assertEqual(normalize_callsign(call.lower()), call)
                with self.assertRaises(ModerationError):
                    require_clean("fuck", "text")
        for call in ("W4NK/FUCK", "W4NK_SH1T", "X/W4NK", "8---D"):
            with self.subTest(call=call), self.assertRaises(ModerationError):
                normalize_callsign(call)
        with self.assertRaises(ModerationError):
            require_clean("W4NK", "text")
        for call in ("F4GET", "M0FOS"):
            with self.subTest(call=call), self.assertRaises(ModerationError):
                require_clean(call, "text")

    def test_shared_prefixes_never_reuse_another_messages_decision(self):
        prefix = "Radio reception is stable and the antenna is connected correctly. " * 3
        for first, second in ((prefix + "73", prefix + "fuck"),
                              (prefix + "fuck", prefix + "73")):
            for text in (first, second):
                if text.endswith("fuck"):
                    with self.assertRaises(ModerationError):
                        require_clean(text)
                else:
                    require_clean(text)

    def test_plain_radio_discussion_and_word_boundaries(self):
        for text in ("CQ de OA6DXV, 73!", "Paso por la computadora y ajusto el filtro",
                     "The classic receiver has a brass chassis in Scunthorpe",
                     "Boa escuta, o fio preto está no saco ao lado da torneira",
                     "Piso de ruido negro, concha de antena, 10 dB de ruido",
                     "Soy gay, ella es lesbiana", "I am gay", "Sou gay e bissexual",
                     "AS IS, CW, RTTY, FT8, AM, USB", "Q S O en A B C",
                     "📻📡" * 250, "AB" * 250):
            with self.subTest(text=text):
                self.assertIsNone(require_clean(text))

    def test_consecutive_repetitions_boundary(self):
        for char in ("A", "=", "📻", "é", "e\u0301", " "):
            with self.subTest(char=char):
                require_clean("CQ " + char * 10 + " 73" if char != " " else "CQ" + char * 10 + "73")
                with self.assertRaises(ModerationError) as rejected:
                    require_clean("CQ" + char * 11 + "73")
                self.assertEqual(rejected.exception.code, "repeated-character")
                self.assertEqual(rejected.exception.field, "text")
                self.assertNotIn("CQ", str(rejected.exception))
        for text in ("Aa" * 250, "=~" * 250, "A " * 250):
            require_clean(text)
        self.assertEqual(normalize_callsign("A" * 10), "A" * 10)

    def test_callsigns_are_whole_tokens(self):
        for call in ("OA6DXV", "AA9900JHFR", "K4ASS", "PU2ABC", "SH1T/P"):
            if call == "SH1T/P":
                with self.assertRaises(ModerationError):
                    normalize_callsign(call)
            else:
                self.assertEqual(normalize_callsign(call), call)
        with self.assertRaises(ModerationError) as rejected:
            normalize_callsign("PENDEJO")
        self.assertEqual(rejected.exception.field, "name")

    def test_no_offending_text_in_rejection(self):
        with self.assertRaises(ModerationError) as rejected:
            require_clean("secret-phrase fuck")
        self.assertEqual(rejected.exception.code, "offensive-language")
        self.assertEqual(rejected.exception.field, "text")
        self.assertNotIn("secret-phrase", str(rejected.exception))
        self.assertNotIn("fuck", str(rejected.exception))


if __name__ == "__main__":
    unittest.main()
