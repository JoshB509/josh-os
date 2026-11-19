"""Independent last-good YouTube report cache, never containing credentials."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .youtube_analytics import YouTubeAnalytics, AnalyticsError, TIMEZONE
from .youtube_auth import YouTubeAuth


class YouTubeCache:
    def __init__(self, data, auth=None, client=None):
        self.path = Path(data) / 'youtube'
        self.auth = auth or YouTubeAuth(data)
        self.client = client or YouTubeAnalytics(self.auth.token)

    def _read(self, name):
        try:
            return json.loads((self.path / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def snapshot(self):
        return self._read('snapshot.json')

    def attempt(self):
        return self._read('attempt.json')

    def _write(self, name, body):
        self.path.mkdir(parents=True, exist_ok=True)
        path = self.path / name
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(body), encoding='utf-8')
        temp.replace(path)

    def refresh(self, start, end):
        if not self.auth.connected:
            return
        now = datetime.now(timezone.utc)
        attempt = dict(at=now.isoformat(), state='failed')
        try:
            # Never ask for an incomplete Pacific day even when UTC is already tomorrow.
            end = min(date.fromisoformat(str(end)), now.astimezone(ZoneInfo(TIMEZONE)).date() - timedelta(days=1))
            result = self.client.daily(start, end)
            if not result['daily']:
                raise AnalyticsError('YouTube returned no completed daily figures. Keeping the last good report.')
            result['fetched_at'] = now.isoformat()
            self._write('snapshot.json', result)
            attempt['state'] = 'ok'
        except AnalyticsError as exc:
            attempt['error'] = str(exc)
        except Exception:
            attempt['error'] = 'YouTube could not refresh. Keeping the last good report.'
        self._write('attempt.json', attempt)
