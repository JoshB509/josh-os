# YouTube Analytics connection

JoshOS can read daily channel views and net subscribers (gained minus lost) directly from YouTube Analytics. The connection requests only `https://www.googleapis.com/auth/yt-analytics.readonly`; it cannot upload, edit or delete videos. Linklr remains the source for email attribution and business revenue.

## Connect on this Windows PC

1. In Google Cloud, use a separate Desktop OAuth client for JoshOS, with YouTube Analytics API enabled. Do not alter an existing web application's client.
2. Download that Desktop client's JSON locally. Do not paste it, a client secret, an authorization code or a token into chat or source files.
3. From the JoshOS repository, run `python -m studio.youtube_auth --connect "C:\path\to\downloaded-client.json"`.
4. Google's browser flow asks for the Google account and, where applicable, the correct YouTube brand account. Confirm the intended channel and only the read-only Analytics permission. The consent label follows the Google project's existing branding, which may say Linklr.
5. Return to the command. Success is reported only after Google's callback and token exchange complete. Start/restart JoshOS when convenient to load the updated Python code, then refresh the scorecard.

The setup listener is bound only to 127.0.0.1, expires after three minutes and validates a random state and PKCE challenge. It does not log callback codes. The refresh token and OAuth client configuration are saved encrypted with Windows DPAPI for the current Windows user under the ignored Data directory. Access tokens remain in memory. A downloaded client JSON remains at its download location; keep it private and remove it yourself when no longer needed. Do not commit credentials.

## Reporting behavior

YouTube report dates use America/Los_Angeles (Pacific Time), while business dates remain UTC. The same displayed date ranges are interpreted in each metric's reporting timezone. Incomplete Pacific days are not requested. Missing days stay gaps, not zeros, and period comparisons are suppressed when either period is incomplete. YouTube's reporting lag is shown by the actual last returned date. Refresh failures keep the last good analytics report and do not replace business figures.

## Verification

Offline tests: `python -B -m unittest discover -s tests -p "test_youtube*.py"` and `python -B -m unittest discover -s tests -p test_scorecard.py`.

Google sign-in, correct brand-channel selection and a successful live report still need end-to-end verification. Google may delay availability of a newly created client. An OAuth app left in external Testing status can require periodic reconnection; review Google's current consent configuration before relying on unattended refresh indefinitely.
