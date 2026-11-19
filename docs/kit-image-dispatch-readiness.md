# Laia image-job dispatch â€” verified boundary

## Installed code, inactive runtime

Laia's `kit_visual_job` structured action now dispatches only an existing exact
image job. It contains a packet ID/digest, email ID and saved revision; no paths,
selectors, browser commands, credentials, audience or send/schedule fields.
The host replaces speculative success text with the verified service outcome.
Showing an email remains read-only. A mixed request containing another mutation
does not run the job. No application restart was performed.

An authenticated owner must first approve the insertion packet, then explicitly
allow Laia to run that one job through a separate owner-review control. The grant
expires after 30 minutes and is consumed before execution. Agent API tokens,
model-supplied approval dictionaries, cross-origin requests and replayed nonces
cannot grant it. Nothing in this change enables a grant automatically.

When Josh subsequently asks to run the approved image job for its exact email,
the host validates revision/content/images/alt/placement, delegation and existing
guarded-writer ownership, then invokes `KitVisualInsertions.execute`. That service
retains private-draft/baseline checks, reservations, single-use upload/insertion
phases and saved/reloaded verification. Uncertain dispatch is not retried.
The ordinary worker and Laia's general browser/shell permissions are unchanged.

## Supported local browser experiment

The documented Chrome integration uses the CLI session's file-read permissions.
Files in additional directories can be admitted with session `--add-dir`; this
is distinct from persistent settings. No custom transfer endpoint is required.

The actual installed CLI and Chrome extension were exercised against a unique
loopback mock page, in a dedicated new session/tab. Only eight byte-identical
original PNG copies were placed in its temporary working directory. The mock
server rejects any other filenames/bytes and has no external network destination.
The supported `read_page` returned a scoped Media Gallery dialog and:

```
button "Choose File" [ref_6] type="file"
```

The first attempt was denied because session file-read permission was absent.
The authorized retry used `--add-dir` for the isolated asset directory plus eight
exact `Read(./<sha256>.png)` rules, supplied only in that CLI session. It uploaded
all eight original PNGs successfully and clicked the local gallery insertion
control. No global settings, bypass mode or credential access was used.

The next exact-call guard stopped alt entry because the actual tool included an
`action_summary` field absent from the host policy. The field was explicitly bound
in a second local fixture seeded from the verified PNGs; the upload was not
repeated. All eight full alt strings were saved exactly in server readback, and
browser reload displayed all eight image-name prefixes. `read_page` truncates
individual accessible names, so the original full-substring assertion failed;
that failure receipt is retained. Full saved strings and truncated browser names
are distinct evidence. This was a two-phase local test, not a live Kit test.

Evidence is in task-6/local-browser-upload-test/verified-two-phase-evidence.json,
with the original raw tool receipts, saved JSON, and reloaded browser tree.
Both owned tabs were closed and both loopback servers stopped.

## Bounded session file access

`stage_session_assets` creates independent hash-named copies from one approved
packet. The upload worker admits only that directory and those exact PNG Read
rules. Its hook checks the active upload call, exact path and SHA256, rejects
symlinks/multiple hardlinks, and blocks reads outside the active upload permit.
The ordinary read-only worker still receives no Read tool or additional directory.
No new owner confirmation is required for these already-authorized local copies.

The production adapter still needs verified Kit gallery asset identity, selection,
exact paragraph insertion, full alt entry and saved/reloaded checks. Local mock
controls do not establish those Kit-specific controls. `KitChromeBrowser` remains
unavailable and the delegation button remains disabled with it. No live Kit
changes, automatic adoption, restart or production activation occurred.

## Email-specific audience

An explicit audience selection is stored in each exact email revision's approved
draft proposal and transferred unchanged. Successful readback now also persists
`verified_subscriber_filter`; a mismatch remains uncertain. Tests prove separate
emails retain separate selections and an unspecified selection never inherits
segment 607863. No planned date is transferred as a send or schedule instruction.
The source email's audience remains unresolved and blank pending Josh's answer.

## Manual-broadcast intake review

Broadcast mapping is evidence of a link, not guarded-writer ownership. A future
intake must be a separate owner-confirmed operation binding the exact local
revision, broadcast ID/account, private unsent baseline and all existing content,
images, audience, template and footer. It must detect already-present images and
avoid appending duplicates. Adopted ownership should have its own provenance;
never fabricate a successful `KitDrafts` creation row. Conflicting, scheduled,
sent, publicly published, locked, changed or ambiguous drafts must fail closed.

No intake/adoption was implemented or performed for broadcast 27089829. Existing
ownership checks remain intact, and a regression test proves that a local mapping
cannot authorize dispatch.

References: [Chrome file uploads](https://code.claude.com/docs/en/chrome),
[working-directory permissions](https://code.claude.com/docs/en/permissions),
[CLI flags](https://code.claude.com/docs/en/cli-reference).
