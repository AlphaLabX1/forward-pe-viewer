import unittest
from datetime import date, timedelta

from build_html import breadth_series, divergence_payload, render_divergence_stats


def sessions(n: int) -> list[str]:
    d0 = date(2010, 1, 1)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def build(closes, breadth, start=0):
    """closes: SPY closes per session. breadth: (b50, b200) per session from
    `start`, so the first `start` sessions are price history only."""
    days = sessions(len(closes))
    spy = list(zip(days, closes))
    rows = [(days[start + i], b50, b200) for i, (b50, b200) in enumerate(breadth)]
    return divergence_payload(spy, rows)


def by_date(div):
    return {p[0]: p for p in div["points"]}


class OffHighTest(unittest.TestCase):
    def test_needs_252_prior_sessions(self):
        div = build([100.0] * 300, [(60.0, 60.0)] * 300)
        self.assertEqual(len(div["points"]), 300 - 252)
        self.assertEqual(div["points"][0][0], sessions(300)[252])

    def test_distance_from_rolling_high(self):
        closes = [100.0] * 252 + [110.0, 99.0]
        div = build(closes, [(60.0, 60.0)] * 2, start=252)
        pts = div["points"]
        self.assertEqual(pts[0][1], 0.0)
        self.assertEqual(pts[1][1], -10.0)

    def test_old_high_rolls_out_of_window(self):
        closes = [200.0] + [100.0] * 252
        div = build(closes, [(60.0, 60.0)], start=252)
        self.assertEqual(div["points"][0][1], 0.0)


class ForwardTest(unittest.TestCase):
    def test_fwd63_known_then_null_at_tail(self):
        closes = [100.0] * 252 + [100.0 + i for i in range(100)]
        div = build(closes, [(60.0, 60.0)] * 100, start=252)
        pts = div["points"]
        self.assertEqual(pts[0][4], 63.0)
        self.assertIsNotNone(pts[100 - 64][4])
        self.assertIsNone(pts[100 - 63][4])
        self.assertIsNone(pts[-1][4])

    def test_drawdown_flag(self):
        n_after = 300
        crash = [100.0] * 252 + [100.0] + [95.0] * 100 + [89.0] + [100.0] * (n_after - 101)
        flat = [100.0] * 252 + [100.0] + [95.0] * n_after
        zone = [(40.0, 40.0)] + [(60.0, 60.0)] * n_after
        self.assertEqual(build(crash, zone, start=252)["zones"]["b200"]["zone"]["dd_share"], 100.0)
        self.assertEqual(build(flat, zone, start=252)["zones"]["b200"]["zone"]["dd_share"], 0.0)

    def test_drawdown_unknown_without_a_full_year_after(self):
        closes = [100.0] * 252 + [100.0] * 200
        div = build(closes, [(40.0, 40.0)] * 200, start=252)
        z = div["zones"]["b200"]["zone"]
        self.assertEqual(z["n_dd"], 0)
        self.assertIsNone(z["dd_share"])


class ZoneTest(unittest.TestCase):
    def test_zone_needs_near_high_and_weak_breadth(self):
        closes = [100.0] * 252 + [100.0, 97.1, 96.9, 100.0, 100.0]
        breadth = [(40.0, 40.0), (40.0, 40.0), (40.0, 40.0), (50.0, 49.9), (None, 70.0)]
        div = build(closes, breadth, start=252)
        self.assertEqual(div["zones"]["b200"]["zone"]["days"], 3)
        self.assertEqual(div["zones"]["b50"]["zone"]["days"], 2)
        self.assertEqual(div["zones"]["b50"]["all"]["days"], 4)
        self.assertEqual(div["zones"]["b200"]["all"]["days"], 5)
        self.assertEqual(div["today"]["in_zone"], {"b200": False, "b50": False})

    def test_episode_starts_after_20_quiet_sessions(self):
        pattern = [True, True] + [False] * 19 + [True] + [False] * 20 + [True, False]
        breadth = [(40.0, 40.0) if z else (60.0, 60.0) for z in pattern]
        div = build([100.0] * (252 + len(pattern)), breadth, start=252)
        z = div["zones"]["b200"]["zone"]
        self.assertEqual(z["days"], 4)
        self.assertEqual(z["episodes"], 2)

    def test_empty_zone_has_null_stats(self):
        div = build([100.0] * 400, [(80.0, 80.0)] * 148, start=252)
        for key in ("b200", "b50"):
            z = div["zones"][key]["zone"]
            self.assertEqual((z["days"], z["episodes"], z["n_fwd"], z["n_dd"]), (0, 0, 0, 0))
            self.assertIsNone(z["median_fwd63"])
            self.assertIsNone(z["pos_share"])
            self.assertIsNone(z["dd_share"])
            self.assertIsNone(z["episode_median_fwd63"])
        self.assertIn("–", render_divergence_stats(div))

    def test_stats_against_all_days(self):
        closes = [100.0] * 252 + [100.0] * 5 + [110.0] * 70
        breadth = [(40.0, 40.0)] * 5 + [(60.0, 60.0)] * 70
        div = build(closes, breadth, start=252)
        z, a = div["zones"]["b200"]["zone"], div["zones"]["b200"]["all"]
        self.assertEqual((z["n_fwd"], z["median_fwd63"], z["pos_share"]), (5, 10.0, 100.0))
        self.assertEqual(a["n_fwd"], 12)
        self.assertEqual(a["median_fwd63"], 0.0)

    def test_today(self):
        closes = [100.0] * 252 + [98.0]
        div = build(closes, [(30.0, 48.0)], start=252)
        self.assertEqual(div["today"], {
            "date": sessions(253)[-1], "off_high_pct": -2.0, "b50": 30.0, "b200": 48.0,
            "in_zone": {"b200": True, "b50": True},
        })

    def test_no_overlap_returns_none(self):
        self.assertIsNone(build([100.0] * 10, [(40.0, 40.0)] * 10))


class BreadthSeriesTest(unittest.TestCase):
    def test_splits_rows_into_rounded_series(self):
        rows = [("2026-01-01", 40.04, None), ("2026-01-02", None, 55.55)]
        self.assertEqual(breadth_series(rows), {"ma50": [["2026-01-01", 40.0]], "ma200": [["2026-01-02", 55.5]]})


if __name__ == "__main__":
    unittest.main()
