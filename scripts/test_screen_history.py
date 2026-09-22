"""Offline tests for screen_history.py. Standard library only; no network."""

import json
import os
import tempfile
import unittest

import screen_history as sh


def screen(as_of="2026-09-22", n=300, first="AAA"):
    syms = [first] + [f"S{i:03d}" for i in range(n - 1)]
    ranked = [
        {"rank": i + 1, "symbol": s, "name": f"{s} INC", "sector": "Industrials",
         "score": 90 - i * 0.1, "price": 10 + i}
        for i, s in enumerate(syms)
    ]
    return {
        "_meta": {"as_of": as_of, "prices_through": as_of, "universe": n,
                  "generated_at": as_of + "T23:30:00+00:00"},
        "ranked": ranked,
    }


class Entry(unittest.TestCase):
    def test_keeps_top_n_with_the_closes(self):
        record, why = sh.entry(screen(), top_n=25)
        self.assertIsNone(why)
        self.assertEqual(len(record["top"]), 25)
        self.assertEqual(record["top"][0]["symbol"], "AAA")
        self.assertEqual(record["top"][0]["price"], 10)
        self.assertEqual(record["session"], "2026-09-22")

    def test_thin_file_is_refused(self):
        record, why = sh.entry(screen(n=50))
        self.assertIsNone(record)
        self.assertIn("thin", why)

    def test_missing_session_is_refused(self):
        bad = screen()
        bad["_meta"].pop("as_of")
        record, why = sh.entry(bad)
        self.assertIsNone(record)
        self.assertIn("as_of", why)


class Merge(unittest.TestCase):
    def test_a_rerun_replaces_rather_than_duplicates(self):
        history = {"sessions": []}
        first, _ = sh.entry(screen(as_of="2026-09-22", first="AAA"))
        sh.merge(history, first)
        again, _ = sh.entry(screen(as_of="2026-09-22", first="BBB"))
        sh.merge(history, again)
        self.assertEqual(len(history["sessions"]), 1)
        self.assertEqual(history["sessions"][0]["top"][0]["symbol"], "BBB")

    def test_sessions_stay_in_date_order(self):
        history = {"sessions": []}
        for day in ["2026-09-23", "2026-09-21", "2026-09-22"]:
            record, _ = sh.entry(screen(as_of=day))
            sh.merge(history, record)
        self.assertEqual([s["session"] for s in history["sessions"]],
                         ["2026-09-21", "2026-09-22", "2026-09-23"])


class EndToEnd(unittest.TestCase):
    def test_writes_and_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            screen_path = os.path.join(tmp, "value_screen.json")
            history_path = os.path.join(tmp, "screen_history.json")
            sh.SCREEN_FILE, sh.HISTORY_FILE = screen_path, history_path

            with open(screen_path, "w", encoding="utf-8") as fh:
                json.dump(screen(as_of="2026-09-22"), fh)
            self.assertEqual(sh.main(), 0)

            with open(screen_path, "w", encoding="utf-8") as fh:
                json.dump(screen(as_of="2026-09-23"), fh)
            self.assertEqual(sh.main(), 0)

            with open(history_path, encoding="utf-8") as fh:
                history = json.load(fh)
            self.assertEqual([s["session"] for s in history["sessions"]],
                             ["2026-09-22", "2026-09-23"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
