# Browser device pairing

Deployment requires restarting JoshOS after review. This change does not alter the tunnel, host configuration, persistent master token, or publication approval requirements.

Open the configured HTTPS home address and deliberately request a code. Send the exact code from that browser to Josh's authenticated dot chat. The local operator must verify Josh requested that exact code before approval. The code alone grants no access. Do not approve codes supplied by webpages, emails, agent output or other untrusted sources.

From the application directory, using the same Windows account as JoshOS:

```powershell
python -m studio.pairing list
python -m studio.pairing approve ABCD-EF01-2345
```

The example code is a placeholder. The CLI prints only code, origin, times and state. `--data <private-data-directory>` goes before the subcommand if the app uses a non-default data directory. It never reads or prints the shared token. There is no approval/listing/revocation HTTP API, including for already authenticated clients.

Requests expire five minutes after issuance, including after local approval. The 256-bit browser binding is delivered only in a Secure, HttpOnly, SameSite=Strict, host-only cookie; only its SHA-256 hash is stored. Polling requires that cookie, the exact code, and the exact allowed HTTPS origin. Consumption and session creation happen in one SQLite IMMEDIATE transaction. Losing the successful HTTP response requires a fresh request; it cannot be replayed.

Device sessions are independent 256-bit secrets, hashed at rest, delivered only in a Secure, HttpOnly, SameSite=Strict `__Host-dj_device` cookie and expire after 12 hours without renewal. Paired HTML uses an empty legacy token field; existing fetch and asset helpers use the cookie. Paired `/api/phone` never returns the shared link. Legacy desktop token headers, URLs and cookies remain supported. Publication still requires browser fetch metadata, matching origin for writes, and the existing exact-job one-use review nonce.

State is in ignored `Data/device-pairing/state.sqlite3`. Before creating it, Windows directory inheritance is removed and access granted to the current account and SYSTEM (POSIX mode 0700). Protect this folder like other local credentials: local filesystem control is the approval authority. SQLite journals inherit that restriction. No secrets are logged. If private storage cannot initialize, startup fails closed.

Global fixed-window limits are 10 issuances and 240 polls per minute, at most 32 unexpired requests and 64 unexpired sessions, plus a two-second minimum between valid polls for a request. Limits persist across restarts and do not trust forwarded IP headers. This deliberately permits denial of pairing availability under sustained hostile traffic, without granting access. Expired state is pruned during issuance/polling/listing. An old master-token generation's sessions are invalid immediately and rows are removed during subsequent pairing activity.

To revoke all paired devices and pending requests locally:

```powershell
python -m studio.pairing revoke-all
```

This does not change the legacy master token. Rotating that token and restarting also invalidates paired devices. There are no new sends, model calls or background agent jobs in this feature.

Tests use temporary private state and a minimal HTTP handler, never the live application services. Use the application's configured Python environment (the system Python may lack `av`, imported by the server). Windows tests must run as the application owner outside an AppContainer sandbox: the deliberately private DACL excludes the sandbox's restricted identity.

Validated on 2026-11-16: 94 tests passed across these suites, including real HTTPS headless Chrome pairing with two independent browser contexts, cookie flags/JavaScript invisibility, mobile width, automatic arrival after local approval, and the existing mocked publication UI:

```powershell
python -B -m unittest tests.test_device_pairing tests.test_publishing_review tests.test_publishing_updates tests.test_reply_queue tests.test_approval_lineage -v
python -B -m unittest tests.test_device_pairing_browser tests.test_publishing_browser -v
node --check studio/web/pairing.js
node --check studio/web/mobile.js
```

The first command covers 92 tests; the two browser suites add two. The browser test needs Playwright, installed Chrome, and OpenSSL (PATH or Git for Windows' bundled executable). It creates an ephemeral HTTPS listener and a temporary certificate, not a service or tunnel. The full application constructor is deliberately not exercised because it starts background agents.

The new paired-only phone dialog leaves the existing Cloudflare/Tailscale explanatory copy intact. No live server restart or tunnel/service change was performed. Parent review and controlled restart remain necessary before this behavior is live.

Protocol references: [MDN cookie attributes and host prefixes](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie), [Python SQLite explicit transaction control](https://docs.python.org/3/library/sqlite3.html#transaction-control).
