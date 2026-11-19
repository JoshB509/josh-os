"""Offline adapter tests. No credentials, Google requests or live app writes."""
import unittest
from unittest.mock import Mock
from studio.youtube_analytics import AnalyticsError, YouTubeAnalytics, TIMEZONE


def report(rows=None):
    return {'columnHeaders': [{'name': k} for k in ('day', 'views', 'subscribersGained', 'subscribersLost')],
            'rows': [] if rows is None else rows}


class YouTubeAnalyticsTest(unittest.TestCase):
    def fetch(self, body):
        return YouTubeAnalytics(lambda: 'test-only', lambda *_: body).daily('2026-11-01', '2026-11-03')

    def test_read_only_parameters(self):
        transport = Mock(return_value=report())
        YouTubeAnalytics(lambda: 'test-only', transport).daily('2026-11-01', '2026-11-03')
        params, token = transport.call_args.args
        self.assertEqual(params['ids'], 'channel==MINE')
        self.assertEqual(params['metrics'], 'views,subscribersGained,subscribersLost')
        self.assertEqual(params['dimensions'], 'day')
        self.assertEqual(token, 'test-only')

    def test_net_subscribers_and_timezone(self):
        result = self.fetch(report([['2026-11-02', 100, 3, 5]]))
        self.assertEqual(result['daily'][0]['net_subscribers'], -2)
        self.assertEqual(result['timezone'], TIMEZONE)
        self.assertEqual(result['data_through'], '2026-11-02')
        self.assertEqual(result['missing_days'], 2)
        self.assertEqual(len(result['daily']), 1)

    def test_empty_report_is_not_zero(self):
        result = self.fetch(report())
        self.assertIsNone(result['data_through'])
        self.assertEqual(result['daily'], [])
        self.assertEqual(result['missing_days'], 3)

    def test_actual_zero_preserved(self):
        self.assertEqual(self.fetch(report([['2026-11-01', 0, 0, 0]]))['daily'][0]['views'], 0)

    def test_invalid_reports_rejected(self):
        for rows in ([['2026-10-31', 1, 1, 0]], [['2026-11-01', -1, 0, 0]],
                     [['2026-11-01', True, 0, 0]], [['2026-11-01', 0.5, 0, 0]],
                     [['2026-11-01', 1, 0, 0]] * 2, [['2026-11-01', 1]]):
            with self.subTest(rows=rows), self.assertRaises(AnalyticsError):
                self.fetch(report(rows))
        with self.assertRaises(AnalyticsError):
            self.fetch({})

    def test_missing_connection_makes_no_request(self):
        transport = Mock()
        with self.assertRaises(AnalyticsError):
            YouTubeAnalytics(lambda: None, transport).daily('2026-11-01', '2026-11-03')
        transport.assert_not_called()

    def test_token_failure_redacted(self):
        def provider():
            raise RuntimeError('secret-example')
        with self.assertRaises(AnalyticsError) as caught:
            YouTubeAnalytics(provider).daily('2026-11-01', '2026-11-03')
        self.assertNotIn('secret-example', str(caught.exception))

    def test_transport_failure_redacted(self):
        transport = Mock(side_effect=RuntimeError('secret-example'))
        with self.assertRaises(AnalyticsError) as caught:
            YouTubeAnalytics(lambda: 'test-only', transport).daily('2026-11-01', '2026-11-03')
        self.assertNotIn('secret-example', str(caught.exception))

    def test_invalid_window_precedes_connection(self):
        provider = Mock()
        with self.assertRaises(ValueError):
            YouTubeAnalytics(provider).daily('2026-11-03', '2026-11-01')
        provider.assert_not_called()


if __name__ == '__main__':
    unittest.main()
