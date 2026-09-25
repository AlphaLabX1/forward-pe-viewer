import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import fetch
from build_html import compute_5y
from fetch import Series, SeriesError, check_series, drop_spikes


def daily(start: str, values) -> list[list]:
    d0 = date.fromisoformat(start)
    return [[(d0 + timedelta(days=i)).isoformat(), v] for i, v in enumerate(values)]


class CheckSeriesTest(unittest.TestCase):
    def test_accepts_sane_reply(self):
        check_series("pe", daily("2026-01-01", [20.0, 21.0, 22.0]), 0.01, 250, "2026-01-02", 2)

    def test_rejects_out_of_range_value(self):
        with self.assertRaisesRegex(SeriesError, "outside"):
            check_series("pe", daily("2026-01-01", [20.0, 321.7, 22.0]), 0.01, 250, None, 0)

    def test_rejects_zero_price(self):
        with self.assertRaisesRegex(SeriesError, "outside"):
            check_series("px", daily("2026-01-01", [700.0, 0.0]), 0.01, 1e5, None, 0)

    def test_rejects_reply_ending_before_stored_data(self):
        with self.assertRaisesRegex(SeriesError, "ends 2026-01-03"):
            check_series("fg", daily("2026-01-01", [40, 41, 42]), 0, 100, "2026-01-10", 0)

    def test_rejects_short_reply(self):
        with self.assertRaisesRegex(SeriesError, "at least 90"):
            check_series("fg", daily("2026-01-01", [40] * 10), 0, 100, None, 90)

    def test_rejects_empty_reply(self):
        with self.assertRaises(SeriesError):
            check_series("fg", [], 0, 100, None, 0)

    def test_blank_cells_are_not_values(self):
        check_series("breadth", [["2026-01-01", "", "55.1"], ["2026-01-02", "48.0", ""]],
                     0, 100, None, 0)

    def test_string_values_are_parsed(self):
        with self.assertRaises(SeriesError):
            check_series("fg", [["2026-01-01", "101.0000"]], 0, 100, None, 0)


class DropSpikesTest(unittest.TestCase):
    def test_drops_isolated_spike_that_reverts(self):
        rows = [["1997-03-27", "77"], ["1997-03-31", "757.1200"], ["1997-04-01", "75.859375"]]
        self.assertEqual(drop_spikes(rows), [rows[0], rows[2]])

    def test_drops_isolated_dip(self):
        rows = daily("2026-01-01", [100.0, 10.0, 101.0])
        self.assertEqual([r[1] for r in drop_spikes(rows)], [100.0, 101.0])

    def test_keeps_real_crash_day(self):
        rows = daily("2020-03-11", [274.36, 248.11, 269.32])
        self.assertEqual(drop_spikes(rows), rows)

    def test_keeps_level_shift(self):
        rows = daily("2026-01-01", [10.0, 10.0, 30.0, 30.0])
        self.assertEqual(drop_spikes(rows), rows)

    def test_keeps_endpoints(self):
        rows = daily("2026-01-01", [500.0, 50.0, 51.0])
        self.assertEqual(drop_spikes(rows), rows)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "s.csv"
        self.path.write_text("date,price\n1990-01-02,350\n2026-01-01,700\n2026-01-02,701\n")

    def tearDown(self):
        self.dir.cleanup()

    def spec(self, **kw):
        return {"s": Series(self.path, ("price",), 0.01, 1e5, **kw)}

    def test_merge_filters_valid_from_and_keeps_prior_dates(self):
        with mock.patch.dict(fetch.CATALOG, self.spec(merge=True, valid_from="1993-01-29")):
            fetch.store("s", [["2026-01-02", 702.0], ["2026-01-05", 703.0]])
        self.assertEqual(self.path.read_text().splitlines(),
                         ["date,price", "2026-01-01,700", "2026-01-02,702.0", "2026-01-05,703.0"])

    def test_failed_check_leaves_file_untouched(self):
        before = self.path.read_text()
        with mock.patch.dict(fetch.CATALOG, self.spec()):
            with self.assertRaises(SeriesError):
                fetch.store("s", [["2026-01-01", 700.0]])
        self.assertEqual(self.path.read_text(), before)

    def test_blank_fresh_cell_keeps_prior_value(self):
        self.path.write_text("date,a,b\n2026-01-01,40,60\n")
        spec = {"s": Series(self.path, ("a", "b"), 0, 100, merge=True)}
        with mock.patch.dict(fetch.CATALOG, spec):
            fetch.store("s", [["2026-01-01", "", "61"], ["2026-01-02", "41", "62"]])
        self.assertEqual(self.path.read_text().splitlines(),
                         ["date,a,b", "2026-01-01,40,61", "2026-01-02,41,62"])


class Compute5yTest(unittest.TestCase):
    def test_needs_a_year_of_history(self):
        self.assertIsNone(compute_5y(daily("2026-01-01", [10.0] * 300)))

    def test_ranks_within_five_years(self):
        pts = daily("2020-01-01", [100.0] * 400 + list(range(1, 2000)))
        five = compute_5y(pts)
        self.assertEqual(five["n"], 1826)
        self.assertAlmostEqual(five["rank"], 100.0)


if __name__ == "__main__":
    unittest.main()
