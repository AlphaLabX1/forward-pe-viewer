import unittest
from unittest import mock

import fetch


class FakeResponse:
    def __init__(self, *, text="", payload=None, error=None):
        self.text = text
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise RuntimeError(self._error)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, failing_chart=None, seed_error=None):
        self.failing_chart = failing_chart
        self.seed_error = seed_error
        self.calls = []

    def get(self, url, headers=None, timeout=60):
        self.calls.append((url, headers, timeout))
        if "/charts/data/" not in url:
            if self.seed_error:
                return FakeResponse(error=self.seed_error)
            return FakeResponse(text='window.cfg = {"stk":"shared-token"}')
        chart_id = int(url.rsplit("/", 1)[-1])
        if chart_id == self.failing_chart:
            return FakeResponse(error=f"chart {chart_id} failed")
        return FakeResponse(payload={
            "success": 1,
            "data": {f"c:{chart_id}": {"chart_id": chart_id}},
        })


class SharedSessionTest(unittest.TestCase):
    def tearDown(self):
        fetch._mm_run.reset()

    def test_seed_page_is_fetched_once_for_several_charts(self):
        session = FakeSession()
        with mock.patch.object(fetch, "_MacroMicroSession", return_value=session):
            fetch._mm_run.reset()
            self.assertEqual(fetch.fetch_mm_chart(50108, "one")["chart_id"], 50108)
            self.assertEqual(fetch.fetch_mm_chart(81081, "two")["chart_id"], 81081)
            self.assertEqual(fetch.fetch_mm_chart(96064, "three")["chart_id"], 96064)

        seed_calls = [url for url, _, _ in session.calls if "/charts/data/" not in url]
        self.assertEqual(len(seed_calls), 1)
        data_calls = [call for call in session.calls if "/charts/data/" in call[0]]
        self.assertEqual(len(data_calls), 3)
        self.assertTrue(all(call[1]["Authorization"] == "Bearer shared-token" for call in data_calls))

    def test_failed_data_call_does_not_poison_other_charts(self):
        session = FakeSession(failing_chart=81081)
        with mock.patch.object(fetch, "_MacroMicroSession", return_value=session):
            fetch._mm_run.reset()
            with self.assertRaisesRegex(RuntimeError, "chart 81081 failed"):
                fetch.fetch_mm_chart(81081, "bad")
            self.assertEqual(fetch.fetch_mm_chart(96064, "good")["chart_id"], 96064)

        seed_calls = [url for url, _, _ in session.calls if "/charts/data/" not in url]
        self.assertEqual(len(seed_calls), 1)

    def test_failed_seed_is_shared_by_all_charts(self):
        session = FakeSession(seed_error="seed failed")
        with mock.patch.object(fetch, "_MacroMicroSession", return_value=session):
            fetch._mm_run.reset()
            for chart_id in (50108, 81081):
                with self.assertRaisesRegex(RuntimeError, "seed failed"):
                    fetch.fetch_mm_chart(chart_id, "chart")

        self.assertEqual(len(session.calls), 1)


class StatSelectionTest(unittest.TestCase):
    @staticmethod
    def chart(stats_and_points):
        return {
            "info": {"chart_config": {"seriesConfigs": [
                {"stats": [{"stat_id": stat_id}]} for stat_id, _ in stats_and_points
            ]}},
            "series": [points for _, points in stats_and_points],
        }

    def test_ndx_breadth_selects_series_by_stat_id(self):
        chart = self.chart([
            (25229, [["2026-01-02", 62]]),
            (99999, [["2026-01-02", 7]]),
            (18332, [["2026-01-02", 48]]),
        ])
        with mock.patch.object(fetch, "fetch_mm_chart", return_value=chart):
            self.assertEqual(fetch.fetch_ndx_breadth(), [["2026-01-02", 48, 62]])

    def test_advance_decline_selects_series_by_stat_id(self):
        chart = self.chart([
            (76091, [["2026-01-02", 210]]),
            (76090, [["2026-01-02", 287]]),
            (76092, [["2026-01-02", 6]]),
        ])
        with mock.patch.object(fetch, "fetch_mm_chart", return_value=chart):
            self.assertEqual(fetch.fetch_sp500_ad(), [["2026-01-02", 287, 6, 210]])


if __name__ == "__main__":
    unittest.main()
