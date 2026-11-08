import copy
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import alerts
import build_html
import fetch
import vix_signals as s


def sessions(n, start=date(2026, 1, 2)):
    days = [start]
    for _ in range(n - 1):
        days.append(s.session_after(days[-1]))
    return days


def market(spreads, start=date(2026, 1, 2), expiries=None):
    days = sessions(len(spreads), start)
    expiries = expiries or [date(2026, m, 18) for m in (3, 4, 5, 6)]
    px = {d: dict(zip(expiries, [20 + v, 20, 19, 18])) for d, v in zip(days, spreads)}
    return days, px


class V2Test(unittest.TestCase):
    def replay(self, spreads):
        return s.v2_replay(*market(spreads))

    def test_arm_threshold_peak_and_next_settle_entry(self):
        self.assertEqual(self.replay([1.5]).state.tag, s.State.FLAT)
        armed = self.replay([1.6, 2.0]).state
        self.assertEqual(armed.tag, s.State.ARMED)
        self.assertEqual(armed.values['arm_date'], '2026-01-02')
        self.assertEqual(armed.values['high'], 2)
        self.assertAlmostEqual(armed.values['trigger'], 1.4)
        signal = self.replay([2, 1.3])
        self.assertEqual(signal.state.tag, s.State.SIGNAL)
        self.assertEqual(signal.trades, [])
        filled = self.replay([2, 1.3, 1.1]).state
        self.assertEqual(filled.tag, s.State.IN_TRADE)
        self.assertEqual(filled.values['entry'], '2026-01-06')
        self.assertAlmostEqual(filled.values['s_entry'], 1.1)
        self.assertEqual(filled.values['sessions_held'], 0)

    def test_target_and_l1_points_with_next_settle_fill(self):
        days, px = market([2, 1.3, 1.2, -1.1, -1.5])
        due = s.v2_replay(days[:-1], px)
        self.assertEqual(due.state.tag, s.State.EXIT_DUE)
        self.assertEqual(due.state.values['reason'], 'target')
        self.assertEqual(due.trades, [])
        third = date(2026, 5, 18)
        px[days[-1]][third] += 0.4
        closed = s.v2_replay(days, px).trades[0]
        self.assertEqual(closed['exit'], str(days[4]))
        self.assertEqual(closed['reason'], 'target')
        self.assertEqual(closed['net'], 2.9)
        self.assertEqual(closed['net_b1legs'], 2.5)

    def test_stop_trail_and_no_trail_before_zero(self):
        for spreads, reason in (([2, 1.3, 1, 4, 5], 'stop'),
                                ([2, 1.3, 1, 0, 1, 1.5], 'trail')):
            with self.subTest(reason=reason):
                result = self.replay(spreads)
                self.assertEqual(result.trades[0]['reason'], reason)
                self.assertEqual(result.trades[0]['exit'], str(sessions(len(spreads))[-1]))
        self.assertEqual(self.replay([2, 1.3, 1, 0.1, 1.5]).state.tag, s.State.IN_TRADE)
        live = self.replay([2, 1.3, 1, 0]).state.values
        self.assertTrue(live['trail_live'])
        self.assertEqual(live['stop_level'], 1)

    def test_time_exit_counts_from_fill(self):
        spreads = [2, 1.3] + [1.0] * 21
        due = self.replay(spreads)
        self.assertEqual(due.state.tag, s.State.EXIT_DUE)
        self.assertEqual(due.state.values['sessions_held'], 20)
        self.assertEqual(due.state.values['reason'], 'time')
        trade = self.replay(spreads + [0.9]).trades[0]
        self.assertEqual(trade['exit'], str(sessions(24)[23]))

    def test_expiry_closes_at_last_preexpiry_settle_and_has_priority(self):
        expiries = [date(2026, 1, 21), date(2026, 2, 18), date(2026, 3, 18), date(2026, 4, 15)]
        days = sessions(13)
        spreads = [2, 1.3] + [1.0] * (len(days) - 2)
        days, px = market(spreads, expiries=expiries)
        last = days.index(date(2026, 1, 20))
        px[days[last]][expiries[0]] = 18
        result = s.v2_replay(days, px)
        self.assertEqual(result.trades[0]['near'], '2026-01-21')
        self.assertEqual(result.trades[0]['exit'], '2026-01-20')
        self.assertEqual(result.trades[0]['reason'], 'expiry')
        prefix = s.v2_replay(days[:last + 1], px)
        self.assertEqual(prefix.trades, result.trades)

    def boundary(self, old_end, new_end):
        expiries = [date(2026, 1, 21), date(2026, 2, 18), date(2026, 3, 18), date(2026, 4, 15)]
        days = sessions(4)
        px = {}
        for i, d in enumerate(days):
            old = old_end if i == 3 else 2.0
            new = new_end if i == 3 else 0.5
            px[d] = dict(zip(expiries, [20 + new + old, 20 + new, 20, 19]))
        return s.v2_replay(days, px)

    def test_boundary_old_pair_decides_and_new_pair_uses_own_history(self):
        no_fire = self.boundary(2.5, 0.2).state
        self.assertEqual(no_fire.tag, s.State.ARMED)
        self.assertEqual(no_fire.values['near'], '2026-02-18')
        self.assertEqual(no_fire.values['arm_date'], '2026-01-02')
        self.assertEqual(no_fire.values['high'], 0.5)
        self.assertAlmostEqual(no_fire.values['trigger'], -0.1)
        fire = self.boundary(1.3, 0.8).state
        self.assertEqual(fire.tag, s.State.SIGNAL)
        self.assertEqual(fire.values['near'], '2026-02-18')
        self.assertEqual(fire.values['third'], '2026-04-15')

    def test_missing_front_is_dropped_and_gap_does_not_fire_a_switch(self):
        days = sessions(3)
        expiries = [date(2026, 1, 21), date(2026, 2, 18), date(2026, 3, 18),
                    date(2026, 4, 15), date(2026, 5, 19)]
        prices = [[24, 22, 20, 19, 18], [0, 21.3, 20, 19, 18], [22.3, 21.3, 20, 19, 18]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'vx.csv'
            path.write_text('date,expiry,settle\n' + ''.join(
                f'{d},{e},{p}\n' for d, values in zip(days, prices)
                for e, p in zip(expiries, values) if p > 0))
            usable, px = s.load_vx(path)
        self.assertEqual(usable, [days[0], days[2]])
        self.assertNotIn(days[1], px)
        result = s.v2_replay(usable, px)
        self.assertEqual(result.state.tag, s.State.FLAT)
        self.assertEqual(result.trades, [])
        self.assertFalse(any(t.state == s.State.SIGNAL for t in result.transitions))

    def test_gap_cancels_unfilled_signal_and_rejects_missing_open_trade_prices(self):
        days, px = market([2, 1.3, 1.1, 1.0, 1.0])
        self.assertEqual(s.v2_replay([*days[:2], days[3]], px).state.tag, s.State.FLAT)
        with self.assertRaisesRegex(ValueError, 'Missing VX sessions during an open trade'):
            s.v2_replay([*days[:3], days[4]], px)

    def test_gap_across_pair_switch_resets_arming(self):
        expiries = [date(2026, 1, 21), date(2026, 2, 18), date(2026, 3, 18), date(2026, 4, 15)]
        days, px = market([2, 2, 2, 1.3], expiries=expiries)
        result = s.v2_replay([days[0], days[1], days[3]], px)
        self.assertEqual(result.state.values['near'], '2026-02-18')
        self.assertEqual(result.state.tag, s.State.FLAT)
        self.assertFalse(any(t.state == s.State.SIGNAL for t in result.transitions))

    def test_deterministic_no_future_prices_or_io(self):
        days, px = market([2, 1.3, 1.1, -1.2, -1.3])
        original = copy.deepcopy(px)
        prefix = days[:3]
        class PastOnly(dict):
            def __getitem__(self, key):
                if key > prefix[-1]:
                    raise AssertionError('Read a future settle')
                return super().__getitem__(key)
        with mock.patch('builtins.open', side_effect=AssertionError('Replay performed I/O')):
            first = s.v2_replay(prefix, PastOnly(px))
            second = s.v2_replay(prefix, {d: px[d] for d in prefix})
        self.assertEqual(first, second)
        self.assertEqual(px, original)
        self.assertEqual(first.state.as_of, prefix[-1])
        self.assertEqual(first.trades, [])


class Z1Test(unittest.TestCase):
    def test_holiday_rows_neither_trigger_fill_nor_advance_hold_clock(self):
        holiday = date(2024, 11, 28)
        rows = [(date(2024, 11, 27), 29), (holiday, 31), (date(2024, 11, 29), 29)]
        self.assertEqual(s.z1_replay(rows).state.tag, s.State.FLAT)
        rows[0] = (rows[0][0], 31)
        rows.insert(0, (date(2024, 11, 26), 29))
        self.assertEqual(s.z1_replay(rows).state.values['entry'], '2024-11-29')
        days = sessions(88, date(2025, 4, 1))
        regular = list(zip(days, [29, 31] + [25] * 86))
        extra = sorted(regular + [(date(2025, 7, 4), 99), (date(2025, 7, 5), 99)])
        result = s.z1_replay(extra)
        self.assertEqual(result, s.z1_replay(regular))
        self.assertEqual(result.trades[0]['exit'], str(days[86]))

    def test_cross_threshold_and_fill_next_close(self):
        days = sessions(4)
        self.assertEqual(s.z1_replay(list(zip(days, [30, 30, 29, 30]))).state.tag, s.State.FLAT)
        result = s.z1_replay(list(zip(days, [30, 31])))
        self.assertEqual(result.state.tag, s.State.SIGNAL)
        self.assertEqual(result.trades, [])
        result = s.z1_replay(list(zip(days, [30, 31, 28])))
        self.assertEqual(result.state.tag, s.State.SHORT)
        self.assertEqual(result.state.values['vix_entry'], 28)
        self.assertEqual(result.state.values['sessions_held'], 0)
        self.assertEqual(result.state.values['size'], 0.02)

    def test_hold_84_sessions_ignores_spikes_and_reenters_after_cover(self):
        days = sessions(88)
        values = [30, 31, 25] + [35 if i % 2 else 25 for i in range(85)]
        rows = list(zip(days, values))
        due = s.z1_replay(rows[:86])
        self.assertEqual(due.state.tag, s.State.COVER_DUE)
        self.assertEqual(due.state.values['sessions_held'], 83)
        self.assertEqual(due.state.values['cover_estimate'], str(days[86]))
        done = s.z1_replay(rows[:87])
        self.assertEqual(len(done.trades), 1)
        self.assertEqual(done.trades[0]['exit'], str(days[86]))
        self.assertEqual(done.trades[0]['entry'], str(days[2]))
        self.assertEqual(done.state.tag, s.State.SIGNAL)
        self.assertEqual(s.z1_replay(rows).state.tag, s.State.SHORT)

    def test_deterministic_and_does_not_invent_future_fills(self):
        rows = list(zip(sessions(3), [29, 31, 28]))
        original = copy.deepcopy(rows)
        self.assertEqual(s.z1_replay(rows), s.z1_replay(rows))
        self.assertEqual(rows, original)
        pending = s.z1_replay(rows[:2])
        self.assertEqual(pending.state.as_of, rows[1][0])
        self.assertEqual(pending.trades, [])
        self.assertNotIn('entry', pending.state.values)


class FetchTest(unittest.TestCase):
    def test_monthly_window_and_year_roll(self):
        today = date(2026, 11, 7)
        expiries = fetch.vx_monthlies(today)
        self.assertEqual(expiries[0], date(2025, 10, 22))
        self.assertEqual(expiries[-1], date(2027, 3, 17))
        self.assertEqual(sum(e > today for e in expiries), 5)
        self.assertTrue(all(e >= today - timedelta(days=400) for e in expiries))

    def test_backfill_extends_monthlies_without_changing_daily_window(self):
        today = date(2026, 11, 7)
        daily = fetch.vx_monthlies(today)
        backfill = fetch.vx_monthlies(today, date(2013, 6, 1))
        self.assertEqual(backfill[0], date(2013, 6, 19))
        self.assertEqual(backfill[-len(daily):], daily)
        self.assertEqual(len(backfill), (2027 - 2013) * 12 + 3 - 6 + 1)
        with mock.patch.object(fetch, 'vx_monthlies', return_value=[]) as monthlies:
            self.assertEqual(fetch.fetch_cboe_vx(backfill=True), [])
        self.assertEqual(monthlies.call_args.args[1], date(2013, 6, 1))

    def test_loading_requires_four_calendar_contracts_with_holiday_expiry(self):
        self.assertEqual(s.monthly_expiry(2019, 3), date(2019, 3, 19))
        self.assertEqual(s.monthly_expiry(2026, 5), date(2026, 5, 19))
        self.assertEqual(s.monthly_expiry(2027, 5), date(2027, 5, 18))
        day = date(2026, 5, 1)
        expected = s.upcoming_monthlies(day)
        self.assertEqual(s.upcoming_monthlies(expected[0])[0], expected[1])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'vx.csv'
            path.write_text('date,expiry,settle\n' + ''.join(f'{day},{e},20\n' for e in expected))
            self.assertEqual(s.load_vx(path)[0], [day])
            for missing in expected:
                path.write_text('date,expiry,settle\n' + ''.join(
                    f'{day},{e},20\n' for e in expected if e != missing))
                self.assertEqual(s.load_vx(path), ([], {}))

    def test_settle_column_positive_only_and_both_date_formats(self):
        text = 'Trade Date,Close,Settle\n2026-11-05,99,20.25\n11/06/2026,99,21.0\n2026-11-07,99,0\n'
        self.assertEqual(fetch.parse_vx_csv(text, date(2026, 11, 18)), [
            ['2026-11-05', '2026-11-18', 20.25], ['2026-11-06', '2026-11-18', 21.0]])

    def test_holiday_fallback_records_actual_expiry(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'Trade Date,Settle\n2027-05-03,20\n'
        missing = fetch.urllib.error.HTTPError('url', 404, 'missing', {}, None)
        self.addCleanup(missing.close)
        with mock.patch.object(fetch, 'vx_monthlies', return_value=[date(2027, 5, 19)]), \
             mock.patch.object(fetch.urllib.request, 'urlopen', side_effect=[missing, response]) as get:
            self.assertEqual(fetch.fetch_cboe_vx(), [['2027-05-03', '2027-05-18', 20.0]])
        self.assertTrue(get.call_args.args[0].full_url.endswith('VX_2027-05-18.csv'))

    def test_merge_uses_date_and_expiry_and_failed_validation_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'vx.csv'
            spec = fetch.Series(path, ('expiry', 'settle'), 0.000001, 200,
                                merge=True, min_reply_rows=2, key_columns=2)
            with mock.patch.dict(fetch.CATALOG, {'test_vx': spec}):
                first = [['2026-11-05', '2026-11-18', 20], ['2026-11-05', '2026-12-16', 21]]
                fetch.store('test_vx', first)
                rows = fetch.store('test_vx', [['2026-11-05', '2026-12-16', 22],
                                              ['2026-11-06', '2026-11-18', 19]])
                self.assertEqual(len(rows), 3)
                self.assertEqual(rows[0], ['2026-11-05', '2026-11-18', '20'])
                self.assertEqual(rows[1][-1], 22)
                before = path.read_text()
                for reply in ([['2026-11-07', '2026-11-18', 20]],
                              [['2026-11-06', '2026-11-18', 0]] * 2,
                              [['2026-11-04', '2026-11-18', 20]] * 2):
                    with self.assertRaises(fetch.SeriesError):
                        fetch.store('test_vx', reply)
                    self.assertEqual(path.read_text(), before)

    def test_loaders_and_empty_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'vx.csv'
            self.assertEqual(s.load_vx(path), ([], {}))
            self.assertEqual(s.load_vix(path), [])
            path.write_text('date,expiry,settle\n2026-11-05,2026-11-18,20\n'
                            '2026-11-05,2026-12-16,21\n2026-11-05,2027-01-20,22\n'
                            '2026-11-05,2027-02-17,23\n'
                            '2026-11-06,2026-11-18,0\n')
            days, px = s.load_vx(path)
            self.assertEqual(days, [date(2026, 11, 5)])
            self.assertEqual(len(px[days[0]]), 4)
        self.assertIsNone(s.v2_replay([], {}).state.as_of)
        self.assertIsNone(s.z1_replay([]).state.as_of)


class AlertTest(unittest.TestCase):
    def test_transitions_only_when_source_advances_no_repeated_armed_alert(self):
        days, px = market([2, 2.1, 1.3, 1.1, -1.1])
        for end, expected in ((1, 'ARMED'), (2, None), (3, 'SIGNAL'), (5, 'EXIT_DUE')):
            source = {'as_of': str(days[end - 1]), 'prior_as_of': str(days[end - 2]) if end > 1 else None}
            with mock.patch.object(alerts, 'load_vx', return_value=(days[:end], px)):
                lines = alerts.signal_alerts({'cboe_vx': source})
                self.assertEqual(len(lines), int(expected is not None))
                if expected:
                    self.assertIn(expected, lines[0])
                source['prior_as_of'] = source['as_of']
                self.assertEqual(alerts.signal_alerts({'cboe_vx': source}), [])

    def test_z1_signal_and_cover_due(self):
        days = sessions(86)
        rows = list(zip(days, [30, 31] + [20] * 84))
        for end, expected in ((2, 'SIGNAL'), (86, 'COVER_DUE')):
            source = {'as_of': str(days[end - 1]), 'prior_as_of': str(days[end - 2])}
            with mock.patch.object(alerts, 'load_vix', return_value=rows[:end]):
                lines = alerts.signal_alerts({'cboe_vix': source})
                self.assertEqual(len(lines), 1)
                self.assertIn(expected, lines[0])


class PageTest(unittest.TestCase):
    def test_card_build_and_missing_or_stale_vx(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(build_html, 'ROOT', Path(directory)):
            html = build_html.build().read_text()
        self.assertIn('id="vix-signals-card"', html)
        self.assertIn('id="signal-v2"', html)
        self.assertIn('id="signal-z1"', html)
        self.assertIn('Daily rule outputs, not advice', html)
        self.assertNotIn('__VIX_SIGNALS__', html)
        self.assertNotIn('__ASOF_14', html)
        fresh = {'cboe_vx': {'as_of': '2026-11-05', 'stale': True, 'fixture': True},
                 'cboe_vix': {'as_of': '2026-11-06', 'stale': False}}
        panel = {'result': s.v2_replay(*market([2])), 'error': ''}
        self.assertIn('Data stale', build_html.render_signal_panel('v2', panel, fresh))
        self.assertIn('Fixture', build_html.render_signal_panel('v2', panel, fresh))
        missing = {'result': s.v2_replay([], {}), 'error': 'Data unavailable.'}
        self.assertIn('Data unavailable', build_html.render_signal_panel('v2', missing, fresh))


if __name__ == '__main__':
    unittest.main()
