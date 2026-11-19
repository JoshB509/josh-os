# Source attribution for agent-created links

Reviewed and corrected 18 November 2026. The final change is shared agent guidance plus regression tests. It does not enforce backend attribution and is not a deployed Linklr fix.

## Root cause and supported contract

The installed MCP preview/create contract accepts `asset_id` OR `destination_url`, `campaign`, `label`, `placement`, `source_type`, sequence name/position and `video_id`. It does not accept `creative_id`, `posted_at`, a carousel instance ID or `email_key`, and exposes no independent saved-link readback tool. Labels are not structured source association.

The parent investigation confirmed that Linklr's resolver ignores labels and matches source type, placement, destination, campaign, video, asset and email sequence/key fields. Creation can return the oldest ambiguous match. Its insert does not populate creative/post fields. The creative report excludes rows with a null creative ID. The legacy carousel planner does write creative/post metadata and paired instance link IDs, but also retires/flags campaigns and creatives; it is not a safe repair endpoint.

The previous local prompt said to reuse any preview match. It now tells agents to check the intended source and follow shared attribution guidance. No new fields are sent to the connector by local code.

## Final local behavior

- Sam and Laia retain their pre-task `create_tracked_link` and `register_video` allowances. Other roles retain their pre-task access. There is no new Linklr denial in the shared runner.
- All runs receive guidance to verify Linklr `video_id` for video/pinned-comment sources, use channel-wide identity intentionally for channel links, and supply sequence name plus position for sequence emails.
- Broadcasts should use a stable per-email `email_key` only when the installed schema supports it. Otherwise the agent must report that missing capability, never invent fields or substitute labels.
- A different post label is not a different source. Reuse requires the same intended source, asset/destination, placement, campaign and applicable video/email identity. Agents must not silently reuse another post's URL or accept an ambiguous match.
- Carousel caption/QR placements must remain separate. Agents must explain the missing structured creative/post association instead of claiming that a label, date, placement name or saved URL establishes it.
- Existing published URLs, destinations, campaigns and historical events must remain unchanged. Creation/import timestamps do not establish publication.

These are instructions, not runtime validation. Existing model-driven connector execution and local content-save paths remain as before this task. No local code proves association, prevents all cross-source reuse, or validates readback.

The attempted blanket creation hold, active `link_requests` chat action and permanent blocked-request ledger have been removed. The unused `studio/link_attribution.py` prototype has been removed. There is no future-helper code in the production path and no local blocked-request lifecycle to mistake for a finished link. The old prototype tests were replaced with tests that assert restored tool access, absence of the ledger action/table, shared guidance and removal of unconditional preview reuse advice.

## Smallest remaining contracts

All cases need authoritative source/destination results or independent saved-link readback, ambiguity rejection and race-safe idempotency. A local trusted adapter would then validate those results. Merely changing prompts cannot enforce this.

| Source | Minimum source identity |
|---|---|
| YouTube video / pinned comment | Existing verified `video_id` plus placement. Verify its mapping to the intended video; no creative field is needed. |
| YouTube channel | Explicit channel scope; account scope can suffice if one channel is guaranteed, otherwise channel ID. Channel-wide reuse can be legitimate. |
| Sequence email | Existing sequence name and position, required and verified as the intended stable step within the account. |
| Broadcast or distinct behaviour email | Stable per-email key. Backend `email_key` reportedly exists; expose and require it in the installed tool schema. |
| Carousel caption / QR | Creative plus publication-instance association, stable local-card mapping, separate slots, and independent link/instance readback. Publication time remains separate from link creation. |
| Social / ad / document / other / non-carousel QR | Stable source namespace/key plus placement, unless a validated existing key already identifies that particular source. |

Server-side uniqueness must include the intended source and placement, reject ambiguous/cross-source reuse, and survive concurrent retries. On uncertain outcomes, reconcile before retrying. Do not change campaigns or destinations to manufacture uniqueness. Do not use legacy `planWeek` to repair historical associations. A later implementation should allow revision/cancellation of unsent proposals without remapping live URLs or permanently locking mistaken proposals.

## Historical mapping candidates, not backfills

These mappings were checked by a read-only query of `Data/studio.sqlite` on 18 November. They identify local evidence for manual reconciliation, not newly verified remote creative/instance IDs or exact publish timestamps.

| Local card ID | Recorded post date | Link evidence | Limitation |
|---|---|---|---|
| `499a09b7-718d-448f-a2f4-52e38016cc81` Pushback phrases | 2026-11-10 | caption/tag `s72u23sh`; notes say the same link was encoded in QR | Caption and QR events cannot be separated retroactively by inventing a second link. |
| `34fecea1-b8c3-4c2f-9a74-5374f4de8a1c` Fix these 4 slides | 2026-11-11 | caption/tag `4aznrm47`; notes identify caption-only placement | No QR association should be inferred. |
| `34c82861-067f-4857-b9cc-3f18f1e96a39` Manager backing promotion | 2026-11-12 | caption/tag `9h24nmf2`; notes document reuse of an unused Nov 7 link, shared with QR | Actual card is Nov 12; creation date is not publication evidence. |
| `bd2c3c3c-c45b-43fd-9fcf-d018ca3fc30e` Slides sound senior | 2026-11-16 | caption `ch4cfu28`; QR `53cr9v8e`; asset `db7cafee-9dc9-40fa-82b3-c80500681e60` in notes | Old SCR `w44fgafc` is unused. Notes record observation at 19:18 Dubai, not an exact publication timestamp. |

`qqn96k` belongs to card `f0530868-bdcd-4dfa-a944-b6959af260c1`, recorded Nov 7; its notes say Linklr groups it with Five mistakes, so attribution may be shared. `6beem8` belongs to card `9672913e-b60a-41c0-8435-17cf1c4e1341`, recorded Nov 9. Both folders mention Nov 2 and remote timestamps reportedly also do; do not use those dates to backfill. Several local `posted_at` values are Nov 13 import times. No history was edited.

## Validation and activation

Focused regression command:

`python -B -m unittest tests.test_link_attribution tests.test_agent tests.test_team_carousels tests.test_carousel_save_atomic tests.test_careeros_integration tests.test_gmail_access tests.test_laia_emails tests.test_lead_magnets`

The tests use mocked subprocesses/connectors and temporary databases. They check guidance and backward compatibility, not real backend attribution or live Claude tool enforcement. The tests package blocks external networking and Claude execution. No real outbound links are used as tests.

No server restart, job termination, credential/security configuration change, campaign edit or historical backfill is part of this work. The shared prompt changes require a later approved JoshOS restart to affect the existing process. `studio/business.py` and `studio/learning_activity.py` are untouched.
