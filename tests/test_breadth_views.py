import unittest
from datetime import date, timedelta

from build_html import (
    advance_decline_payload,
    equal_weight_ratio_stats,
    mcclellan_series,
    ratio_series,
)


class McClellanTest(unittest.TestCase):
    def test_hand_computed_values_and_cumulative_series(self):
        rows = [
            ("2026-01-01", 3, 0, 1),
            ("2026-01-02", 1, 0, 3),
            ("2026-01-03", 4, 0, 0),
        ]
        points = mcclellan_series(rows)
        self.assertEqual([p["net"] for p in points], [2, -2, 4])
        self.assertEqual([p["ad_line"] for p in points], [2, 0, 4])
        self.assertAlmostEqual(points[0]["rana"], 500.0)
        self.assertAlmostEqual(points[1]["oscillator"], -50.0)
        self.assertAlmostEqual(points[2]["oscillator"], -17.5)
        self.assertAlmostEqual(points[2]["summation"], -67.5)
        self.assertTrue(all(p["warmup"] for p in points))

    def test_first_39_sessions_are_masked_in_chart_payload(self):
        d0 = date(2026, 1, 1)
        rows = [((d0 + timedelta(days=i)).isoformat(), 300 + i % 3, 0, 200) for i in range(40)]
        spy = [[(d0 + timedelta(days=i)).isoformat(), 100 + i] for i in range(40)]
        payload = advance_decline_payload(rows, spy)
        self.assertIsNotNone(payload)
        self.assertTrue(all(p[2] is None for p in payload["points"][:39]))
        self.assertIsNotNone(payload["points"][39][2])


class EqualWeightRatioTest(unittest.TestCase):
    def test_common_dates_only(self):
        equal = [["2026-01-01", 50], ["2026-01-02", 55], ["2026-01-03", 60]]
        cap = [["2026-01-01", 100], ["2026-01-03", 120]]
        self.assertEqual(ratio_series(equal, cap), [["2026-01-01", 0.5], ["2026-01-03", 0.5]])

    def test_changes_and_five_year_percentile(self):
        d0 = date(2020, 1, 1)
        points = [[(d0 + timedelta(days=i)).isoformat(), 100 + i] for i in range(2000)]
        stats = equal_weight_ratio_stats(points)
        self.assertEqual(stats["changes"], {"1m": 1.4, "3m": 4.5, "1y": 21.0})
        self.assertEqual(stats["percentile_5y"], 100.0)


if __name__ == "__main__":
    unittest.main()
