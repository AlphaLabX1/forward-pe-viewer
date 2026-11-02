import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import alerts
import build_html
import fetch
from build_html import (
    freshness,
    render_asof,
    render_vix_readout,
    render_vix_stats,
    vix_episodes,
    vix_return_stats,
    vix_term_payload,
)


def days(n):
    return [(date(2010, 1, 1) + timedelta(days=i)).isoformat() for i in range(n)]


class CboeTest(unittest.TestCase):
    def test_close_and_iso_dates(self):
        sample = ('DATE,OPEN,HIGH,LOW,CLOSE\n'
                  '01/02/1990,17.24,17.24,17.24,17.240000\n'
                  '09/18/2009,25.91,26.66,25.91,26.540000\n'
                  '10/30/2026,19.00,20.00,18.00,19.420000\n')
        self.assertEqual(fetch.parse_cboe_csv(sample), [
            ['1990-01-02', 17.24], ['2009-09-18', 26.54], ['2026-10-30', 19.42],
        ])

    def test_retries_and_browser_header(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = (
            b'DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,17,17,17,17.24\n')
        with mock.patch.object(fetch.urllib.request, 'urlopen', side_effect=[OSError('offline'), response]) as get, \
             mock.patch.object(fetch.time, 'sleep') as sleep:
            self.assertEqual(fetch.fetch_cboe('VIX'), [['1990-01-02', 17.24]])
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args.args[0].get_header('User-agent'), 'Mozilla/5.0')
        sleep.assert_called_once_with(fetch.RETRY_BACKOFF_SEC)

    def test_full_history_registry(self):
        self.assertEqual(set(fetch.SOURCES['cboe_vix']), {'vix', 'vix3m', 'vix9d', 'vix6m'})
        for name in fetch.SOURCES['cboe_vix']:
            self.assertFalse(fetch.CATALOG[name].merge)
            self.assertEqual(fetch.CATALOG[name].columns, ('close',))


class EpisodeTest(unittest.TestCase):
    def test_short_run_has_no_exit_long_run_exits_at_equality(self):
        dates = days(10)
        ratios = list(zip(dates, [0.9, 1.1, 1.2, 1, 1.1, 1.2, 1.3, 1.1, 1, 1.2]))
        self.assertEqual(vix_episodes(ratios), [
            {'start': '2010-01-02', 'end': '2010-01-03', 'sessions': 2, 'exit': None},
            {'start': '2010-01-05', 'end': '2010-01-08', 'sessions': 4, 'exit': '2010-01-09'},
            {'start': '2010-01-10', 'end': '2010-01-10', 'sessions': 1, 'exit': None},
        ])

    def test_readout_snapshot_alignment_and_precision(self):
        dates = days(253)
        vix = list(zip(dates, [20.0] * 251 + [22.001, 22.001]))
        vix3m = list(zip(dates, [22.0] * 253))
        vix9d = [(dates[-1], 18.126)]
        vix6m = [(dates[-6], 23.456)]
        payload = vix_term_payload(vix, vix3m, vix9d, vix6m, [])
        today = payload['today']
        self.assertEqual((today['state'], today['run'], today['window']), ('Backwardation', 2, 252))
        self.assertAlmostEqual(today['backwardation_share'], 100 / 126)
        self.assertEqual(payload['points'][-1], ['2010-09-10', 1.0])
        self.assertEqual(payload['episodes'][-1]['sessions'], 2)
        self.assertEqual(payload['snapshots'], [
            {'date': '2010-09-10', 'points': [['9D', 18.13], ['30D', 22.0], ['3M', 22.0]]},
            {'date': '2010-09-05', 'points': [['30D', 20.0], ['3M', 22.0], ['6M', 23.46]]},
            {'date': '2010-08-20', 'points': [['30D', 20.0], ['3M', 22.0]]},
        ])
        self.assertIn('Backwardation', render_vix_readout(payload))
        self.assertIsNone(vix_term_payload([], [], [], [], []))
        self.assertIn('unavailable', render_vix_stats(None))


class ReturnStatsTest(unittest.TestCase):
    def test_spy_sessions_and_independent_horizon_samples(self):
        dates = days(65)
        # Day 0 returns +10% at 21 sessions and +20% at 63; day 1 is flat,
        # then -10%. An extra ratio date without SPY must not shift either horizon.
        closes = [100.0, 200.0] + [100.0] * 19 + [110.0, 200.0] + [100.0] * 40 + [120.0, 180.0]
        spy = list(zip(dates, closes))
        ratios = [(dates[0], 0.9), (dates[1], 1.1), (dates[-1], 1.2), ('2011-01-01', 1.2)]
        rows = vix_return_stats(ratios, [], spy)
        self.assertEqual(rows[0], {
            'key': 'all', 'label': 'All sessions', 'n': 3,
            '21': {'n': 2, 'mean': 5.0, 'positive': 50.0},
            '63': {'n': 2, 'mean': 5.0, 'positive': 50.0},
        })
        self.assertEqual(rows[1]['21'], {'n': 1, 'mean': 10.0, 'positive': 100.0})
        self.assertEqual(rows[2]['63'], {'n': 1, 'mean': -10.0, 'positive': 0.0})
        self.assertEqual(rows[3]['21'], {'n': 0, 'mean': None, 'positive': None})

    def test_exits_split_by_current_inclusive_200_session_average(self):
        dates = days(265)
        spy = list(zip(dates, [100.0] * 199 + [50.0, 200.0] + [100.0] * 64))
        ratios = [(dates[199], 1.0), (dates[200], 0.9)]
        episodes = [{'exit': dates[199]}, {'exit': dates[200]}]
        rows = vix_return_stats(ratios, episodes, spy)
        self.assertEqual(rows[3]['n'], 2)
        self.assertEqual(rows[4]['21'], {'n': 1, 'mean': 100.0, 'positive': 100.0})
        self.assertEqual(rows[4]['63'], {'n': 1, 'mean': 100.0, 'positive': 100.0})
        self.assertEqual(rows[5]['21'], {'n': 1, 'mean': -50.0, 'positive': 0.0})
        self.assertEqual(rows[5]['63'], {'n': 1, 'mean': -50.0, 'positive': 0.0})


class VixAlertTest(unittest.TestCase):
    def collect(self, values, advanced=True, ratio_date='2010-01-02'):
        status = {'sources': {
            'koyfin_pe': {'ok': True, 'as_of': '2010-01-02', 'prior_as_of': '2010-01-02'},
            'fear_greed': {'ok': True, 'as_of': '2010-01-02', 'prior_as_of': '2010-01-02'},
            'cboe_vix': {'ok': True, 'as_of': '2010-01-02',
                         'prior_as_of': '2010-01-01' if advanced else '2010-01-02'},
        }}
        def load(path):
            if path == fetch.VIX_CSV:
                return [('2010-01-01', values[0]), (ratio_date, values[1])]
            if path == fetch.VIX3M_CSV:
                return [('2010-01-01', 20), (ratio_date, 20)]
            return []
        with mock.patch.object(alerts, '_load_csv_points', side_effect=load), \
             mock.patch.object(alerts, 'load_pe', return_value={}):
            return alerts.collect_alerts(status)

    def test_crossing_both_ways_including_equality(self):
        self.assertEqual(self.collect([20, 22]),
                         ['VIX / VIX3M entered backwardation: 1.00 → 1.10 (2010-01-02)'])
        self.assertEqual(self.collect([22, 20]),
                         ['VIX / VIX3M returned to contango: 1.10 → 1.00 (2010-01-02)'])
        self.assertEqual(self.collect([18, 20]), [])
        self.assertEqual(self.collect([22, 24]), [])

    def test_no_repeat_or_partial_source_date_alert(self):
        self.assertEqual(self.collect([20, 22], advanced=False), [])
        self.assertEqual(self.collect([20, 22], ratio_date='2010-01-01'), [])


class PageTest(unittest.TestCase):
    def test_committed_data_builds_card_13(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(build_html, 'ROOT', Path(directory)):
            html = build_html.build().read_text()
        for identifier in ('vix-card', 'vix-chart', 'vix-curve-chart', 'vix-readout', 'vix-stats'):
            self.assertIn(f'id="{identifier}"', html)
        self.assertIn('VIX (Cboe)', html)
        self.assertIn('window starts 2009-09', html)
        self.assertNotIn('__ASOF_13__', html)
        self.assertNotIn('__VIX_', html)

    def test_cboe_one_session_lag_and_failed_fetch(self):
        status = {'sources': {'cboe_vix': {'ok': True, 'as_of': '2026-10-30'}}}
        fresh = freshness(status, '2026-11-02')
        self.assertFalse(fresh['cboe_vix']['stale'])
        self.assertIn('as of 2026-10-30', render_asof('13', fresh))
        self.assertTrue(freshness(status, '2026-11-03')['cboe_vix']['stale'])
        status['sources']['cboe_vix']['ok'] = False
        self.assertTrue(freshness(status, '2026-11-02')['cboe_vix']['stale'])


if __name__ == '__main__':
    unittest.main()
