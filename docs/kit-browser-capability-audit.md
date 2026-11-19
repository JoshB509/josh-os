# CareerOS browser capability audit — 16 November 2026

## Verified inventory

| Surface | Installed capability | Laia access / limit |
| --- | --- | --- |
| `studio/agent.py`, `Agents.run` | Starts installed Claude Code as a hidden subprocess; supports `--chrome`, exact allowlists and tool hooks. Installed executable reports **2.1.319**. | `CHROME={'sam'}`; Laia gets no general browser tools. Chat cannot directly write Kit broadcasts. |
| Claude Code Chrome tools | Metadata-only ToolSearch returned registered `mcp__claude-in-chrome__file_upload` and `upload_image`. No browser tool was invoked. | `file_upload` takes `paths`, `ref`, `tabId`; help requires session-shared files, under 10 MB total. `upload_image` uploads recent screenshots, so is unsuitable for preserving original PNG bytes. |
| `studio/kit_chrome_worker.py` | Existing persistent dedicated Chrome session, owned tabs, immutable exact-call permits, raw PostToolUse receipts and single-use tool IDs. | Ordinary worker remains inspection-only. Its general click/typing permissions have not expanded. |
| `studio/kit_chrome_upload.py` | Concrete subclass dispatches the supported `file_upload` through the same worker and hooks. Uses an approved/reserved insertion job, original PNG hash, exact local revision/alt/placement checks, dedicated tab, fresh receipt-bound URL/control and durable attempt reservation. | Host-only; not wired to Laia, server or live UI adapter. Requires an already session-shared directory. Does not create shares, open file pickers, select uploaded assets or assert insertion success. |
| `KitChromeBrowser` / `KitVisualReview` | Owner review and dedicated supported inspection adapter are already wired into CareerOS. | `available:false`; `insert_image` still raises. The review controller requires owner session/nonces and rejects agent API tokens. |
| `KitVisualInsertions` | Revision/hash-bound proposals, owner approval, reservation, private-draft checks and recovery evidence. | Currently requires a draft made by CareerOS's guarded writer. Linking manually created broadcast 27089829 does not satisfy that ownership rule. Do not fabricate ownership. |
| `studio/tasks.py` | Local task/goal records. | Not an executable desktop-task queue. |
| `studio/notices.py` / `oversight.py` | Queue notices and agent-generated work/nudges. | Not a Codex/ChatGPT desktop delegation API; no corresponding dispatcher found. |
| `studio/jobs.py` | Launches local video-edit worker using `settings['python']`. | Video pipeline only, not a browser-job transport. |
| Installed Codex computer-use plugin | Plugin manifest and docs describe ChatGPT's managed computer-use runtime. | No documented CareerOS-callable service/CLI or app integration was found. This task's private tools and the separate cloud browser are not available to Laia. |
| Local Playwright | Installed for isolated visual rendering. | Does not establish access to the authenticated user browser or a supported Kit upload adapter. No raw CDP, profile/cookie reuse or alternate channel was added. |

## Smallest legitimate deployment

No new browser platform needs to be invented: extend the existing dedicated
Claude-in-Chrome worker through the existing owner-reviewed insertion workflow.
The bounded transfer bridge is implemented and mock-tested, but remains inactive.

Before activation:

1. Obtain/verify a supported way to share only the approved immutable PNG files
   with that Claude session. A filesystem path or `--add-dir` alone is not proof
   of the upload tool's sharing permission. This audit did not connect a folder
   or change any permission. If a new folder/site grant is required, report the
   exact limited grant to Josh before configuring it.
2. Observe the local supported read-page representation of Media Gallery's file
   input, then verify resource identity, alt entry, placement and saved/reloaded
   content. The bridge accepts one unambiguous fresh `Choose File` reference;
   it relies on `file_upload` to validate the target input and never clicks it.
   Cloud-browser observations do not prove local extension behavior. No live
   upload or current-broadcast test was performed here.
3. Complete that UI sequence in `KitChromeBrowser.insert_image` and wire the
   subclass only inside the approved host job. A raw upload tool receipt is
   explicitly **not** a Kit asset identity or insertion-completion receipt.
4. For chat dispatch, add a narrow host request that resolves an existing exact
   job and authenticated authorization, then invokes this worker. Existing
   `Agents.run` can launch the supported CLI worker; Laia currently has no action
   that queues such a job. Do not add Laia to the general `CHROME` set or expose
   tool names, selectors, paths, approval records or observations to model JSON.
5. Reuse exact mapped broadcast IDs; a manually created draft needs a separately
   reviewed existing-draft intake, not a new broadcast or fabricated ownership.

No Chrome/Kit permission, service, credential, hosting setting or runtime was
changed. No restart, foreground action, Kit read/write or upload occurred during
this audit; the sole connected operation was tool-schema discovery.

## Source documentation and evidence limits

[Anthropic's Chrome integration documentation](https://code.claude.com/docs/en/chrome)
documents CLI browser automation, session tab groups, local file uploads and
extension-managed site permissions. It does not make this task's cloud browser
available to CareerOS.

Installed ToolSearch's raw result confirms the two registered tool names. The
discovery session's answer transcribed the exposed help/schema; it did not execute
either tool. Session file acceptance and browser connectivity were not live-tested.

Tests use fake services, original local PNG fixtures and the actual exact-call
permit/receipt code. They verify dispatch shape and failure boundaries, not live
upload completion.
