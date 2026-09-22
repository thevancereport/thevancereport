"""Offline tests for screen_history.py. Standard library only; no network."""

import json
import os
import tempfile
import unittest

import screen_history as sh


def screen(as_of="2026-09-22", n=300, order=None, prices=None):
    syms = order or ["AAA"] + [f"S{i:03d}" for i in range(n - 1)]
    ranked = []
    for i, s in enumerate(syms):
        price = (prices or {}).get(s, 10 + i)
        ranked.append({"rank": i + 1, "symbol": s, "name": f"{s} INC",
                       "sector": "Industrials", "score": 90 - i * 0.1, "price": price})
    return {
        "_meta": {"as_of": as_of, "prices_through": as_of, "universe": len(syms),
                  "generated_at": as_of + "T23:30:00+00:00"},
        "ranked": ranked,
    }


def run(history, scr):
    """One evening: record the session, then follow the top ten."""
    record, why = sh.entry(scr)
    assert record is not None, why
    sh.merge(history, record)
    return sh.follow(history, scr, record["session"])


class Entry(unittest.TestCase):
    def test_keeps_top_n_with_the_closes(self):
        record, why = sh.entry(screen(), top_n=25)
        self.assertIsNone(why)
        self.assertEqual(len(record["top"]), 25)
        self.assertEqual(record["top"][0]["symbol"], "AAA")
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
        history = sh.blank()
        base = [f"S{i:03d}" for i in range(300)]
        run(history, screen(as_of="2026-09-22", order=base))
        run(history, screen(as_of="2026-09-22", order=["BBB"] + base[:299]))
        self.assertEqual(len(history["sessions"]), 1)
        self.assertEqual(history["sessions"][0]["top"][0]["symbol"], "BBB")

    def test_sessions_stay_in_date_order(self):
        history = sh.blank()
        for day in ["2026-09-23", "2026-09-21", "2026-09-22"]:
            run(history, screen(as_of=day))
        self.assertEqual([s["session"] for s in history["sessions"]],
                         ["2026-09-21", "2026-09-22", "2026-09-23"])


class Following(unittest.TestCase):
    def test_only_the_top_ten_are_followed(self):
        history = sh.blank()
        new, _ = run(history, screen(as_of="2026-09-22"))
        self.assertEqual(len(history["entries"]), 10)
        self.assertEqual(len(new), 10)
        self.assertEqual(history["entries"]["AAA"]["entered"], "2026-09-22")
        self.assertEqual(history["entries"]["AAA"]["entry_rank"], 1)

    def test_entry_price_and_date_never_change(self):
        history = sh.blank()
        base = [f"S{i:03d}" for i in range(299)]
        run(history, screen(as_of="2026-09-22", order=["AAA"] + base, prices={"AAA": 20}))
        run(history, screen(as_of="2026-09-23", order=["AAA"] + base, prices={"AAA": 25}))
        self.assertEqual(history["entries"]["AAA"]["entered"], "2026-09-22")
        self.assertEqual(history["entries"]["AAA"]["entry_price"], 20)

    def test_price_line_continues_after_it_drops_out_of_the_top_25(self):
        history = sh.blank()
        base = [f"S{i:03d}" for i in range(299)]
        run(history, screen(as_of="2026-09-22", order=["AAA"] + base, prices={"AAA": 20}))
        # AAA falls to 41st: out of the top 25, still in the ranked universe.
        fallen = base[:40] + ["AAA"] + base[40:]
        run(history, screen(as_of="2026-09-23", order=fallen, prices={"AAA": 18}))
        self.assertEqual(history["prices"]["AAA"],
                         {"2026-09-22": 20, "2026-09-23": 18})
        self.assertNotIn("AAA", [r["symbol"] for r in history["sessions"][1]["top"]])

    def test_a_company_that_leaves_the_universe_leaves_a_gap(self):
        history = sh.blank()
        base = [f"S{i:03d}" for i in range(299)]
        run(history, screen(as_of="2026-09-22", order=["AAA"] + base, prices={"AAA": 20}))
        run(history, screen(as_of="2026-09-23", order=base + ["ZZZ"]))
        self.assertEqual(list(history["prices"]["AAA"]), ["2026-09-22"])
        self.assertIn("AAA", history["entries"])


class EndToEnd(unittest.TestCase):
    def test_writes_and_appends(self):
        with tempfile.TemporaryDirectory() as tmp:
            sh.SCREEN_FILE = os.path.join(tmp, "value_screen.json")
            sh.HISTORY_FILE = os.path.join(tmp, "screen_history.json")
            for day in ["2026-09-22", "2026-09-23"]:
                with open(sh.SCREEN_FILE, "w", encoding="utf-8") as fh:
                    json.dump(screen(as_of=day), fh)
                self.assertEqual(sh.main(), 0)

            with open(sh.HISTORY_FILE, encoding="utf-8") as fh:
                history = json.load(fh)
            self.assertEqual([s["session"] for s in history["sessions"]],
                             ["2026-09-22", "2026-09-23"])
            self.assertEqual(len(history["entries"]), 10)
            self.assertEqual(sorted(history["prices"]["AAA"]),
                             ["2026-09-22", "2026-09-23"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
