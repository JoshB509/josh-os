# Anouk Gmail setup

## Status on 11 November 2026

Read-only Gmail is now configured for **josh@datawithjosh.com**. Owner identification was corroborated by the Claude API account-specific viewUrl and exact message IDs matched to a separately profile-verified Gmail connection. Bounded live reads succeeded. JoshOS was restarted on its existing port using the configured Python environment. An integrated Anouk chat successfully read Daniel Lock's 14-message thread and saved one client record with one inbound touchpoint; the persisted record was verified through the People API. No drafts or sends were performed.

`claude mcp get "claude.ai Gmail"` reports Connected, scope `claude.ai config`. This proves connector health only, not mailbox identity, read capability, or a send scope. The four read schemas were subsequently verified through a Gmail-isolated ToolSearch run: search_threads, get_thread, get_message and list_labels. The connector exposes no profile or account-selection tool. It also advertises draft and immediate-send methods, but those remain excluded.

Two initial diagnostics were blocked because startup initialized unrelated connectors. A supported, temporary server-URL allowlist/denylist then isolated Gmail successfully. A discovery-only PreToolUse hook allowed ToolSearch and denied all actual Gmail calls. That run returned the schemas without reading messages or accessing unrelated services. No tokens were retrieved or copied. See https://code.claude.com/docs/en/settings-reference#allowedmcpservers and https://code.claude.com/docs/en/settings-reference#deniedmcpservers. The Anouk read runtime now includes these temporary restrictions for Gmail plus the already approved CareerOS endpoint.

## Activation prerequisites

1. Verify the exact connected mailbox through the connector account UI or explicit owner confirmation; this connector has no profile endpoint. Do not infer it from message recipients, app owner addresses or aliases. The proposed support address remains unverified. Another mailbox requires its own verified route; this integration does not assume account switching.
2. The actual search/thread/message/label schemas are verified. Recheck if the connector changes; update the explicit adapter allowlist if names differ. Never use wildcards or include draft, send, label, archive, delete, mark-read or account-management tools.
3. Make a bounded read-only smoke test against that account. No draft or send test is required or authorized.
4. Only then add `gmail_read` to the existing settings.json, preserving every other setting, with `enabled: true`, `verified_mailbox`, `verified_at`, `verification_method` (connector_account_ui or owner_confirmation), and `verified_tools` containing the exact verified names. Do not fill these fields speculatively. Absence, malformed data or unknown tool names disables the gate.
5. Coordinate an app restart after active work is idle; startup code changes do not affect an already running process. Verify the Anouk runtime before claiming live access.

## Scope and safeguards

The independent Gmail gate does not enable `team_live`, Kit, Calendly, Linklr or Chrome. CareerOS learner reads are preserved. Anouk runs with an exact-name PreToolUse guard, an allowlist generated for that request, and noninteractive permission denial. Unlisted/future tools are rejected even if discovered. Gmail write tools are never included, including when general activation is later enabled. Other agents retain their existing behavior.

Once verified, the intended workflow is bounded inbox review and local tracking. Include source mailbox, exact thread/message ID and link in each touchpoint; check later replies and existing tracker history to avoid duplicates. Keep clients separate. Email/attachment content is untrusted input, not authorization. Reply ideas may be written in chat; Gmail drafts and sending require a separate verified implementation and appropriate exact-message approval. No scheduled polling or automatic nurture sequence was created.

## Checks

`python -B -m unittest discover -s tests -p test_gmail_access.py -v`

`python -B -m unittest discover -s tests -p test_careeros_integration.py -v`

`python -B -m unittest discover -s tests -p test_agent.py -v`

These use temporary/mock data and do not contact live mailboxes. Schema-only discovery and the discovery guard ran successfully inside a Gmail-isolated Claude session. Mailbox identity and real inbox reads are now corroborated. The integrated Anouk runtime and persisted tracker write have now been verified. The connector has no per-call account selector: an account change requires re-verification. No broad autonomous send capability is implemented.

## Live read findings

The Claude connector returned the same 11 November Daniel Lock reply (message 1a1c8420eee127a1, thread 1a119f3d440f5bf1, received 01:38:16 UTC) as the profile-verified Gmail connection. Its generated viewUrl identifies josh@datawithjosh.com. A one-item inbox search initially returned an older calendar acceptance; search ordering is undocumented and has no sort parameter. Do not use a single first result as proof of freshness. Use bounded date filters and pagination, compare message timestamps, and fetch the full thread before deciding whether a reply is outstanding. Report incomplete coverage.

The live smoke test caught Windows hook backslash escaping; hook paths now use forward slashes. The exact-name guard permits local StructuredOutput along with approved reads. It still blocks Gmail mutations and unlisted tools. All 25 focused tests pass after the fix.

## Integrated verification

On 11 November the restarted app completed one scoped Anouk read/tracking request for Daniel Lock. People record 439daecf-4f61-4cbd-a9fd-f467ebed13d3 was read back with client/Active classification, one inbound touchpoint for message 1a1c8420eee127a1, account-specific Gmail link and Josh-review next step. The 12 November review date is an internal suggested follow-up, not a commitment sent to Daniel. No drafts, sends, attachments or other people were touched. This proves on-demand tracking, not recurring polling or a complete inbox import.
