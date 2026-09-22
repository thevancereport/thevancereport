"""Offline tests for daily_alert.py. Standard library only; no network."""

import datetime as dt
import re
import unittest

import daily_alert as da


def screen(as_of="2026-09-18", n=300, generated="2026-09-18T23:00:00+00:00", order=None):
    syms = order or [f"S{i:03d}" for i in range(n)]
    ranked = [
        {"rank": i + 1, "symbol": s, "name": f"{s} HOLDINGS INC", "sector": "Industrials",
         "score": 90 - i * 0.1}
        for i, s in enumerate(syms)
    ]
    return {"_meta": {"as_of": as_of, "generated_at": generated}, "ranked": ranked}


NOW = dt.datetime(2026, 9, 19, 1, 0, tzinfo=dt.timezone.utc)


class Guards(unittest.TestCase):
    def test_fresh_weekday_passes(self):
        self.assertIsNone(da.check(screen(), NOW))

    def test_thin_file_skips(self):
        self.assertIn("thin", da.check(screen(n=50), NOW))

    def test_weekend_session_skips(self):
        self.assertIn("weekend", da.check(screen(as_of="2026-09-20"), NOW))

    def test_stale_file_skips(self):
        self.assertIn("stale", da.check(screen(generated="2026-09-17T23:00:00+00:00"), NOW))


class Words(unittest.TestCase):
    def test_top_ten_and_count(self):
        text = da.body(screen(), None)
        self.assertIn("**300 US companies ranked** on the close of Friday, 18 September 2026.", text)
        self.assertEqual(len(re.findall(r"^\| \d+ \|", text, re.M)), 10)
        self.assertNotIn("Changes in the top ten", text)

    def test_movers(self):
        base = [f"S{i:03d}" for i in range(300)]
        before = screen(order=base)
        today_order = ["NEW"] + base[:9] + base[10:] + [base[9]]
        text = da.body(screen(order=today_order), before)
        self.assertIn("**In:** [NEW]", text)
        self.assertIn("**Out:** [S009]", text)
        self.assertIn("now #301", text)

    def test_every_number_is_from_the_file(self):
        s = screen()
        text = da.body(s, None)
        allowed = {str(r["rank"]) for r in s["ranked"]} | {f"{r['score']:.1f}" for r in s["ranked"]}
        allowed |= {"300", "18", "2026", "40", "30", "20", "10"}
        found = set(re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", text))
        self.assertEqual(found - allowed, set())

    def test_no_forecast_words(self):
        text = da.body(screen(), None).lower()
        for w in ["will ", "should ", "expect", "target", "upside", "buy now", "surge", "soar"]:
            self.assertNotIn(w, text)

    def test_new_reader_link(self):
        self.assertIn("/vance-value-screener.html)", da.body(screen(), None))

    def test_subject(self):
        self.assertEqual(da.subject(screen()), "The screen, Fri 18 Sep: 300 companies ranked")

    def test_pretty_name(self):
        self.assertEqual(da.pretty_name("YELP INC"), "Yelp Inc")
        self.assertEqual(da.pretty_name("3M CO"), "3M Co")
        self.assertEqual(da.pretty_name("NerdWallet, Inc."), "NerdWallet, Inc.")
        self.assertEqual(da.pretty_name("WEATHERFORD INTERNATIONAL PLC"), "Weatherford International PLC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
