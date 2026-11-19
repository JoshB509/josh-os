# JoshOS chat capability audit and implementation

Date: 15 November 2026. Shared handler: studio/agent.py. Surfaces: Team, dock, script chat, Discord. Connector connectivity is separate from action availability.

## Implemented in this pass

- Sam/Tariq: separate new video cards, atomic validation, stable delivered-message identity, no reuse of an old idea. Existing card edits reject stale project revisions. Local field clearing, archive/restore, title selection/removal, thumbnail selection/reordering/reference removal, packaging and lead-magnet briefs. Existing title/thumbnail locks remain enforced; no file deletion or YouTube changes from these fields.
- Sam: existing carousel/design/thumbnail actions remain shared across channels.
- Laia: planner audience/type/tags/archive fields; saves explicitly disable remote synchronization. Showing exact saved email remains nonmutating. Anouk cannot edit the newsletter planner merely by having an email workflow.
- Anouk: local customer archive/restore and read-only learner lookup/cache refresh. Course workflow membership no longer grants Saskia's authoring actions.
- Max: task links and existing read-only scorecard refresh. Existing Max off state is unchanged.
- Saskia: bounded cue save, recording date, latest-take selection and audio recheck. Fresh lesson/revision/source/state fingerprints; exact unique title can bind to its ID. Shared cue lock prevents a UI cue save racing the chat write. Existing validated lesson text fields can now be cleared.
- Tariq: bounded source registration/cancel, word remove/restore/undo, cleanup proposals, clean transcript, approved transcript handoff, archive prepare/copy/verify. Exact project, current request and revision required. Quoted transcript phrase accepted only when uniquely contiguous and selected IDs match exactly. Unique source basename accepted only among project candidates; source containment still enforced. Word mutations check state digest atomically inside the backend lock.

## Still guarded or missing

This is NOT a claim of complete natural-language parity.

- Native Premiere rebuild/final extraction: backend does not yet prove the open Premiere project matches saved project identity. Not exposed through new adapter.
- Editing proposal revision: backend currently attributes revisions to Josh; actor correction is required before exposing it as Tariq work.
- Exact edit/transcript/script/Kit/publication approvals: existing protected owner flows remain; model-generated approval flags are never accepted by new helpers.
- Saskia slide check, done/session association/import/handover and other take actions remain UI-only pending source/session-bound interfaces.
- Sam release chapters/regeneration and some release-packaging operations remain UI-bound.
- Kit scheduling/sending is deliberately unavailable. Exact draft approval/save remains its protected workflow.
- Some local helper operations still use optimistic whole-context version checks rather than a transaction shared with every UI action. Do not claim complete concurrency coverage.
- Current request matching is conservative. Ambiguous repeated transcript phrases, implicit targets, incomplete source selections or unsupported phrasings can need clarification. No need to open a particular window when the exact video/lesson is named.

## Verification

230 affected regression tests passed on final code: card creation/metadata, shared handler, course/edit helpers, clean edit, conversation context, approval lineage, Discord, Kit drafts, YouTube updates, email, courses, people, tasks and business. Models and external services were mocked or isolated in temporary fixtures. No publication, send, schedule, credential change or actual editing action was used as a test. Live natural-language model behavior remains distinct from these routing/backend checks.

Read-only runtime contract: GET /api/agents/capabilities (authenticated as normal). Version 2026-11-15-parity-2. This is routing inventory, not proof of connector access or task execution.

## Recovery already performed

The misplaced 6,538-character draft and three title ideas were preserved in separate card 995c7649-f97a-5a54-872e-95bb75693cf2 named Communication. I'll improve it 86%. Original idea d346142f-3fb1-59dc-b31e-795f32f779b0 was restored against verified historical fields. Original had no script/title-option/thumbnail assets; its notes and thumbnail concept were restored/preserved. Draft wording was not rewritten. Recovery backups remain under Backups/chat-card-recovery-20261115.

## Deployment verification

Second pass activated by server-only hidden restart on unchanged port 64233 after job-idle checks. Authenticated live endpoint returned 2026-11-15-parity-2. Five configured Discord bots connected; Max remained intentionally off. Live API re-read both restored/new cards and exact preserved draft SHA256 03cc223126d5fa8d294901e2f78912b634b5d032beffe0ba384d333e11468786. Existing rendering jobs were terminal; publication and update queues empty. No outbound messages, publication, actual media edits or real model tasks were exercised for verification.

## Remaining native workflow contracts

Native rebuild remains guarded: CleanEdit.build does not compare active Premiere project with saved prproj, and EditFlow.build_again opens expected project but probes/builds in separate calls. Required: bridge-side project identity assertions at every mutation/save, stable sequence/media/source identity, globally serialized native operations, fresh-sequence/no-overwrite/readback guards. The plugin command implementation was not located in this repository, so the full bridge contract is unverified. No native operation was enabled or exercised.

Chat approvals must be deterministic verified-owner requests tied to an exact pending artifact/revision with expiry, stale rejection and replay protection. Never expose from_josh/confirmed booleans to model tools. Claims of hearing/reviewing a take need actual owner confirmation, not automatic audio checks. These are implementable in chat; they are not a reason to permanently require a particular window.

Course recording gaps: Done releases an exact stopped binding and is not reviewed-take completion. Session preparation currently also prepares OBS and opens sources, requiring its own explicit scope. Recovery imports need immutable lesson/session/file proposals and genuine owner confirmation; take selection needs atomic state/hash checks. Upload/access endpoints log manual actions, not delivery. Guarded actions remain unavailable through model actions pending these contracts.

Max audit: credentials/configured channel already exist, but a hardcoded disconnected list suppressed the bot. Narrow source change allows connection for owner chat while keeping proactive DM and notices blocked. Business daily settings remain disabled with no pinned destination. No settings, credentials or other agents' jobs were changed. Channel business answers still require verified private pinned audience; owner DM is distinct. This does not verify daily brief delivery and does not replace any fallback automation.

## Backend hardening pass (parity-3)

Atomic latest-proposal revision insertion now records a trusted actor rather than always claiming Josh. Final transcript schema 2 binds project/sequence/source/cut/evidence/words; approval and handoff recompute content and reject tampering/stale lineage. Legacy transcripts require regeneration and fresh owner approval; no files were migrated or approved automatically. Native project identity remains unverified so native actions remain guarded.

Course approval rejects stale/noninteger revision before writing script files. Slide-check results are stored only after lesson revision/script hash/slide SHA recheck. Handover fallback now runs outside the lock, and stale exact bindings cannot silently fall back. Correction: production uses RLock, so the original fallback was reentrant; this was not a demonstrated production deadlock.

Combined isolated affected suite: 245 passed with configured media-enabled Python and approved temp writes. Max/notice/Discord/business suite: 83 passed. Separate broad legacy recording suite was not clean (33 failures/5 errors, older Done semantics and sandbox fixture issues); no full-repository regression certification is claimed. No source gates or tests were weakened to hide that result.

## Final live verification

Authenticated runtime reports 2026-11-15-parity-3 after idle background restart on unchanged port 64233. All six configured bots connected, including Max; kept_off is empty. Max daily business enabled remains false; proactive DM/notices remain blocked separately. No verification messages sent and no fallback automation changed. Recovery cards and exact draft SHA remain unchanged. Additional media-regression run is pending a deliberate unused-localhost bridge timeout; not included in passing totals above.

## Native plugin investigation follow-up

Actual plugin source found at E:/Videos/DaJoshiCut/panel/index.js; installed UXP 0.9.0 copy matched source hash at inspection. tqBuildCut has no exact-project assertion; tqOps only checks pathname pattern; tq_save reacquires active project separately. Helper bridge timeout removes waiting entry but leaves queued command, permitting late native execution. None of these commands were invoked during audit. Required new contract: capability-negotiated expected canonical project and stable sequence GUID, same captured project object through mutation/readback/save, precise media identities, expired queue cancellation. Existing native chat commands remain unavailable pending tested deployment of the guarded contract.

## Final parity-4 result (supersedes provisional totals)

291 combined affected tests pass. Additional 69 media/backend tests and 83 notices/Discord/business tests pass (overlapping suites, not additive totals). Reviewed recording baseline/current each ran100 with exactly identical31 failures/1 error; no new failures from hardening. Earlier33/5 default-sandbox result is superseded.

Sam now supports stale-protected local description/CTA drafts, chapter drafts, and local planned release time. Exact IANA timezone/time required; no remote scheduling. Live capability endpoint verified parity-4, all six bots connected, recovered cards unchanged. Max proactive DM/notices remain blocked and daily business settings remain disabled. No real model task, external send/publication or Premiere mutation used in verification.

DaJoshiCut source-only guarded native commands and expired-queue handling passed11 mocked JS scenarios and3 isolated queue tests. Installed plugin remains unchanged; no native chat action enabled. Real Premiere integration, active same-project edit races and media integrity need controlled checks before activation. Already-started native work cannot be canceled merely by expiry.

## Native validation plan and correction

Production handover_lock is RLock. Earlier self-deadlock diagnosis was overstated; no production deadlock fix is claimed. Exact-binding stale rejection remains useful. Legacy recording failures largely reflect v1 Done/import assumptions differing from explicit lifecycle_v2 release/recovery semantics; no safety gates were weakened to satisfy them.

Native validation remains blocked under no-install/no-foreground constraints: installed UXP0.9.0 lacks staged guarded commands, and Premiere was not running at feasibility inspection. Chrome harness is a mock host, not Adobe. Plan for a separately authorized maintenance window: preserve installed plugin backup; deploy/reload staged guarded version; create disposable project with synthetic media; verify guarded capability handshake; test exact project+GUID matches and mismatches, expired queue refusal, captured project save/readback, no overwrite of originals; record actual project/sequence identities and artifacts. Only enable chat native adapter after real tests pass. Preserve user project and no live edits; restore old plugin if validation fails. Do not test unsafe legacy commands on user projects.

## Recording worker resilience (final parity-5)

AudioChecks now survives check/storage exceptions and continues subsequent queued work. It best-effort records explicit failure, retains diagnostic error, never marks failed work passed, and does not automatically retry in a loop. Shutdown drains queued bookkeeping without processing pending files; close is idempotent and closed worker rejects new checks. Existing decoder shutdown timeout remains; running decoder is not forcibly killed.

Three new mocked worker tests fail against reconstructed pre-fix source and pass current source. Final combined affected regression suite294/294 passed. This does not fix or conceal the separately documented pre-existing lifecycle-v1 test mismatch. Idle server-only restart activated parity-5; live endpoint and six Discord connections verified. Native installed plugin and foreground apps remain untouched; no actual recording/import/approval action was exercised.
