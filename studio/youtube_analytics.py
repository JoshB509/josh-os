"""Read-only YouTube Analytics adapter; connection and OAuth storage are not configured.

A caller must supply an already-authorized access-token provider. This module never
creates credentials, requests consent, stores tokens, or performs channel writes.
YouTube daily reports use America/Los_Angeles, not the business scorecard's UTC days.
"""
import json
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

ENDPOINT = 'https://youtubeanalytics.googleapis.com/v2/reports'
SCOPE = 'https://www.googleapis.com/auth/yt-analytics.readonly'
TIMEZONE = 'America/Los_Angeles'
METRICS = ('views', 'subscribersGained', 'subscribersLost')


class AnalyticsError(RuntimeError):
    """Safe, credential-free error suitable for display."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the bearer credential to another destination.


def _request(params, token):
    request = Request(ENDPOINT + '?' + urlencode(params), headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/json'})
    try:
        with build_opener(_NoRedirect).open(request, timeout=30) as response:
            return json.loads(response.read(4 * 1024 * 1024).decode('utf-8'))
    except HTTPError as exc:
        if exc.code == 401:
            raise AnalyticsError('YouTube authorization expired. Reconnect the channel.') from None
        if exc.code == 403:
            raise AnalyticsError('YouTube access was refused. Check API enablement, read-only consent and quota.') from None
        raise AnalyticsError('YouTube Analytics request failed (HTTP %s).' % exc.code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise AnalyticsError('YouTube Analytics could not be reached or returned an invalid response.') from None


def normalize_report(report, start, end):
    """Preserve reported days only: absent/lagging days are not fabricated zeros."""
    try:
        headers = [c['name'] for c in report['columnHeaders']]
        required = ('day',) + METRICS
        if len(headers) != len(set(headers)) or not all(k in headers for k in required):
            raise ValueError()
        days = {}
        for row in report.get('rows', []):
            if len(row) != len(headers):
                raise ValueError()
            raw = dict(zip(headers, row))
            day = date.fromisoformat(raw['day'])
            if not start <= day <= end or day.isoformat() in days:
                raise ValueError()
            values = {}
            for key in METRICS:
                val = raw[key]
                if isinstance(val, bool) or not isinstance(val, (int, float)) or val < 0 or int(val) != val:
                    raise ValueError()
                values[key] = int(val)
            days[day.isoformat()] = dict(date=day.isoformat(), views=values['views'],
                subscribers_gained=values['subscribersGained'], subscribers_lost=values['subscribersLost'],
                net_subscribers=values['subscribersGained'] - values['subscribersLost'])
        return dict(source='YouTube Analytics', timezone=TIMEZONE,
                    requested_start=start.isoformat(), requested_end=end.isoformat(),
                    data_through=max(days) if days else None,
                    daily=[days[key] for key in sorted(days)],
                    missing_days=(end - start).days + 1 - len(days))
    except (KeyError, TypeError, ValueError, OverflowError):
        raise AnalyticsError('YouTube Analytics returned an unexpected daily report.') from None


class YouTubeAnalytics:
    def __init__(self, token_provider, transport=None):
        self.token_provider = token_provider
        self.transport = transport or _request

    def daily(self, start, end):
        start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
        if start > end or (end - start).days >= 366:
            raise ValueError('Request between 1 and 366 days, in date order.')
        try:
            token = self.token_provider()
        except Exception:
            raise AnalyticsError('YouTube is not connected or its authorization could not be refreshed.') from None
        if not isinstance(token, str) or not token.strip() or '\r' in token or '\n' in token:
            raise AnalyticsError('YouTube is not connected.')
        params = dict(ids='channel==MINE', startDate=start.isoformat(), endDate=end.isoformat(),
                      metrics=','.join(METRICS), dimensions='day', sort='day', maxResults=366)
        try:
            report = self.transport(params, token)
        except AnalyticsError:
            raise
        except Exception:
            raise AnalyticsError('YouTube Analytics request failed.') from None
        return normalize_report(report, start, end)
