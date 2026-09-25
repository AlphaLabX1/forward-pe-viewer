import unittest
from unittest import mock

import alerts
from build_html import freshness, render_asof, render_stale_note


def status(**overrides):
    base = {
        source: {"ok": True, "as_of": "2026-09-24", "prior_as_of": "2026-09-23", "error": None}
        for source in ("koyfin_pe", "koyfin_prices", "us10y", "fear_greed", "breadth")
    }
    for source, entry in overrides.items():
        base[source] = {**base[source], **entry}
    return {"generated_at": "2026-09-24T22:10:00+00:00", "sources": base}


FG_FAILED = {"ok": False, "as_of": "2026-09-23", "prior_as_of": "2026-09-23",
             "error": "HTTP 423: Locked", "missing": ["fear_greed"]}


class FreshnessTest(unittest.TestCase):
    def test_all_current(self):
        fresh = freshness(status(), "2026-09-24")
        self.assertFalse(any(f["stale"] for f in fresh.values()))
        self.assertEqual(render_stale_note(fresh), "")
        self.assertEqual(render_asof("05", fresh), '<span class="asof">as of 2026-09-24</span>')

    def test_failed_source_is_stale_on_its_cards(self):
        fresh = freshness(status(fear_greed=FG_FAILED), "2026-09-24")
        self.assertEqual(fresh["fear_greed"]["why"], "fetch failed")
        self.assertIn("stale", render_asof("05", fresh))
        self.assertIn("as of 2026-09-23", render_asof("06", fresh))
        self.assertNotIn("stale", render_asof("02", fresh))
        self.assertIn("Fear &amp; Greed (fetch failed, last 2026-09-23)", render_stale_note(fresh))

    def test_source_behind_page_without_error_is_stale(self):
        fresh = freshness(status(breadth={"as_of": "2026-09-22"}), "2026-09-24")
        self.assertEqual(fresh["breadth"]["why"], "no new data")

    def test_missing_sector_is_named(self):
        pe = {"ok": False, "missing": ["forward/20520"], "error": "HTTP 500"}
        fresh = freshness(status(koyfin_pe=pe), "2026-09-24")
        self.assertEqual(fresh["koyfin_pe"]["why"], "missing Financials forward")


class AlertsTest(unittest.TestCase):
    def run_alerts(self, st, fg):
        with mock.patch.object(alerts, "_load_csv_points", return_value=fg), \
             mock.patch.object(alerts, "load_pe", return_value={}):
            return alerts.collect_alerts(st)

    def test_failed_source_alerts_with_error(self):
        lines = self.run_alerts(status(fear_greed=FG_FAILED), [])
        self.assertEqual(lines, ["Fear & Greed not current: fetch failed, last 2026-09-23 (HTTP 423: Locked)"])

    def test_crossing_reported_when_source_advanced(self):
        lines = self.run_alerts(status(), [("2026-09-23", 26.0), ("2026-09-24", 24.0)])
        self.assertEqual(lines, ["Fear & Greed fell below 25: 26 → 24 (2026-09-24)"])

    def test_crossing_not_repeated_when_source_did_not_advance(self):
        st = status(fear_greed={"prior_as_of": "2026-09-24"})
        self.assertEqual(self.run_alerts(st, [("2026-09-23", 26.0), ("2026-09-24", 24.0)]), [])


if __name__ == "__main__":
    unittest.main()
