"""Offline tests (no ISIS, no Ollama, no jeff): python -m unittest discover -s tests"""
import base64
import hashlib
import os
import tempfile
import unittest

os.environ["ISIS_STATE"] = tempfile.mkdtemp(prefix="isis-test-")
os.environ["ISIS_DATA"] = os.environ["ISIS_STATE"]

from app.isis import ics, moodle  # noqa: E402
from app.isis.api import fts_query  # noqa: E402
from app.isis.knowledge import chunk, merge  # noqa: E402
from app.isis.knowledge.extract import norm, verify  # noqa: E402


class Login(unittest.TestCase):
    def _blob(self, passport, token="0123456789abcdef0123456789abcdef"):
        sig = hashlib.md5((moodle.WWWROOT + passport).encode()).hexdigest()
        return "moodlemobile://token=" + base64.b64encode(f"{sig}:::{token}:::PRIVATE".encode()).decode()

    def test_only_ws_token_is_kept(self):
        url = moodle.make_login_url()
        passport = url.split("passport=")[1].split("&")[0]
        self.assertEqual(moodle.redeem(self._blob(passport)), "0123456789abcdef0123456789abcdef")
        self.assertEqual(moodle.TOKEN_FILE.read_text(), "0123456789abcdef0123456789abcdef")
        self.assertEqual(moodle.TOKEN_FILE.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("PRIVATE", moodle.TOKEN_FILE.read_text())

    def test_foreign_signature_is_rejected(self):
        moodle.make_login_url()
        with self.assertRaises(moodle.MoodleError):
            moodle.redeem(self._blob("not-my-passport"))

    def test_flatten(self):
        self.assertEqual(moodle._flatten({"courseids": [5], "options": [{"name": "x", "on": True}]}),
                         {"courseids[0]": 5, "options[0][name]": "x", "options[0][on]": 1})


class Calendar(unittest.TestCase):
    def test_fold_keeps_utf8_and_75_octets(self):
        line = "SUMMARY:" + "Übungsblatt " * 20
        folded = ics._fold(line)
        self.assertTrue(all(len(p.encode()) <= 75 for p in folded.split("\r\n")))
        self.assertEqual(folded.replace("\r\n ", ""), line)

    def test_escape(self):
        self.assertEqual(ics._esc("a;b,c\nd\\"), r"a\;b\,c\nd\\")


class Knowledge(unittest.TestCase):
    PAGE = ("Eine Datenstruk-\ntur organisiert Werte im Speicher. Die Laufzeit 𝑇(𝑛) = 𝑂(𝑛 log 𝑛) gilt für "
            "Mergesort und Heapsort. Abgabe am 05.11.2026.")

    def _chunk(self):
        return chunk.chunks([(3, self.PAGE * 3)])[0]

    def test_norm_joins_hyphenation_and_nfkc(self):
        self.assertIn("datenstruktur", norm(self.PAGE))
        self.assertIn("t(n) = o(n log n)", norm(self.PAGE))

    def test_verify_keeps_real_quote_and_finds_page(self):
        item = {"type": "begriff", "title": "Datenstruktur", "content": "Organisiert Werte im Speicher.",
                "quote": "Eine Datenstruktur organisiert Werte im Speicher"}
        kept, dropped = verify([item], self._chunk())
        self.assertEqual((len(kept), kept[0]["page"]), (1, 3))

    def test_verify_drops_made_up_quote_and_title(self):
        bad_quote = {"type": "begriff", "title": "Datenstruktur", "content": "x", "quote": "steht nirgends auf der Folie"}
        bad_title = {"type": "begriff", "title": "Quantenchromodynamik", "content": "x", "quote": "Mergesort und Heapsort"}
        no_date = {"type": "termin", "title": "Mergesort", "content": "x", "quote": "Mergesort und Heapsort"}
        kept, dropped = verify([bad_quote, bad_title, no_date], self._chunk())
        self.assertEqual(kept, [])
        self.assertEqual([w for w, _ in dropped], ["Zitat nicht im Text", "Titel nicht im Text", "Termin ohne Datum"])

    def test_chunks_respect_size(self):
        pages = [(i, "Wort " * 400) for i in range(1, 30)]
        out = chunk.chunks(pages)
        self.assertTrue(all(len(c["text"]) <= chunk.MAX_CHARS + 30 * 12 for c in out))
        self.assertEqual(sum(len(c["pages"]) for c in out), 29)

    def test_merge_keys(self):
        self.assertEqual(merge.key("Die Datenstruktur"), merge.key("datenstruktur"))
        self.assertTrue(merge._near(merge.key("Rekursive Datenstruktur"), merge.key("Rekursive Datenstrukturen")))

    def test_fts_query(self):
        self.assertEqual(fts_query("Was ist eine Datenstruktur?"), '"datenstrukt"*')
        self.assertEqual(fts_query("und oder 42"), "")


if __name__ == "__main__":
    unittest.main()
