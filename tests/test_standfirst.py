import unittest

from build_html import render_standfirst, standfirst


def row(name, rank, latest=20.0, d1w=None, is_index=False):
    return {"name": name, "rank_5y": rank, "latest": latest, "d1w": d1w, "isIndex": is_index}


class StandfirstTest(unittest.TestCase):
    def test_no_superlative_below_95(self):
        rows = [
            row("S&P 500", 99.0, is_index=True),
            row("Health Care", 82.4, 19.04, d1w=5.0),
            row("Industrials", 73.0, 24.1, d1w=2.0),
            row("Utilities", 10.2, 16.1, d1w=-1.0),
            row("Real Estate", 18.0, 34.4, d1w=-13.2),
        ]
        self.assertEqual(
            standfirst(rows, ("2026-09-24", 36.0)),
            "Health Care is the richest sector against its own five years, at the 82nd "
            "percentile and 19.0 times forward earnings. Utilities is the cheapest, at the "
            "10th percentile. Real Estate moved most this week, 13 percentile points cheaper. "
            "Fear & Greed reads 36, fear.",
        )

    def test_superlatives_at_95_and_5(self):
        rows = [
            row("Industrials", 94.6, 26.0, d1w=1.0),
            row("Energy", 4.8, 12.3, d1w=-1.0),
            row("Materials", 50.0, d1w=0.9),
        ]
        text = standfirst(rows, ("2026-09-24", 80.0))
        self.assertTrue(text.startswith("Industrials trades at its richest in five years, "
                                        "at the 95th percentile and 26.0 times forward earnings."))
        self.assertIn("Energy trades at its cheapest in five years, at the 5th percentile.", text)
        self.assertIn("Fear & Greed reads 80, extreme greed.", text)

    def test_just_below_threshold_and_single_point(self):
        rows = [
            row("Industrials", 94.4, 26.0, d1w=0.6),
            row("Energy", 5.6, 12.3, d1w=None),
        ]
        text = standfirst(rows, None)
        self.assertIn("richest sector against its own five years, at the 94th percentile", text)
        self.assertIn("Energy is the cheapest, at the 6th percentile.", text)
        self.assertIn("Industrials moved most this week, 1 percentile point richer.", text)
        self.assertNotIn("Fear", text)
        self.assertNotIn("—", text)

    def test_empty(self):
        self.assertEqual(render_standfirst(standfirst([], None)), "")

    def test_escaped(self):
        self.assertIn("S&amp;P", render_standfirst("S&P"))


if __name__ == "__main__":
    unittest.main()
