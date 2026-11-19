# CareerOS read-only MCP connection

Verified on 2026-11-11 on DESKTOP-A4TRTK5 using Claude Code 2.1.319.

## Configuration and authentication

The `careeros` HTTP server is configured privately for the JoshOS project, not committed in the repository. Endpoint: https://yswttrnfwgfiahpetjyw.supabase.co/functions/v1/mcp. Claude manages OAuth credentials; do not copy tokens into the app, source code, chat, or logs.

Public client ID: `a7018fcc-b53f-41d0-b5b1-7dbc4c414c1f`. Callback: `http://localhost:3118/callback`. Public PKCE S256 client, no client secret.

The standard discovery flow selected an invalid Supabase `/authorize` fallback. The supported Claude `oauth.authServerMetadataUrl` override is set to `https://yswttrnfwgfiahpetjyw.supabase.co/auth/v1/.well-known/oauth-authorization-server`. This verified document names the issuer `/auth/v1`, authorization endpoint `/auth/v1/oauth/authorize`, and token endpoint `/auth/v1/oauth/token`.

Configured `oauth.scopes` is `openid email profile`; Claude additionally requests `offline_access` for refresh access. Consent was approved for the intended account at the CareerOS consent page, requested by localhost:3118. The installed client confirmed successful authentication. To reconnect, run `claude mcp login careeros` from this repository and verify the account and consent.

## JoshOS access boundary

Only Anouk receives the seven named CareerOS read tools, independently of the general `team_live` setup gate, under the explicit CareerOS integration request. Her general gate was not changed, and she remains in setup mode for unrelated integrations. Other roles were not given CareerOS access. No Gmail, Calendly, Kit, Linklr, or Chrome activation was performed. After other desk work was confirmed idle, JoshOS was restarted using its configured video-env Python, without browser interaction. Runtime PID changed from 59652 to 77600 on the same port 62493. Process liveness and five focused permission tests passed after restart. Browser UI end-to-end validation was not performed.

Allowlist: `find_learners`, `get_learner`, `get_learner_admin_history`, `get_learner_progress`, `get_learner_timeline`, `list_questions`, `list_reviews`. The server descriptions and schemas identify read-only learner support; formal read-only annotations were not supplied. Never automatically allow future tools or wildcards.

## Verification

Live authenticated checks succeeded for unique Detlef and Angela searches, Angela access provenance, Detlef progress/timeline, and bounded questions visibility/replies. Admin-history and reviews were discovered but not called. No learner mutations or outgoing messages were requested. Server-side logging may occur for reads; read-only does not mean no audit writes.

Core progress must remain separate from Data Storytelling and course downloads. Lesson opens are not completions or evidence of video playback. Tracking started 2026-11-11 06:12 UTC; earlier records have partial backfill, and missing history means not tracked rather than zero. Email handoff and queued notifications do not establish delivery. Legacy membership labels must not override current access-grant provenance.

Focused tests: `python -m unittest discover -s tests -p test_careeros_integration.py -v`. Existing agent regression tests: `python -m unittest discover -s tests -p test_agent.py -v`. These use temporary/mock data, not live learners.
