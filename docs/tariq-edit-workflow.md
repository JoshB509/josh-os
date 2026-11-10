# Tariq's editing workflow: build status

Built 10 November 2026 from the handover at `C:\Users\Anigma PC\Desktop\Tariq-Editing-Handover-2026-11-10`. This page says what is built, what was tested and at what level, and what still needs Premiere or Josh.

## How it works

1. **Sources.** On a video's Edit piece, "Tariq's clean cut" opens the workflow. Josh picks the camera file and the good mic from the video's `Recorded\Input` folder. If there is more than one candidate, nothing is guessed. The originals are only ever read.
2. **Proposal.** Josh OS fingerprints both files, decodes them with the bundled FFmpeg, and checks sync at three points (start, middle, end). It transcribes the mic locally with faster-whisper medium.en on the CPU, with downloads off. If the model is missing it stops rather than using a cloud service. Then it measures pauses and sounds, and Tariq reads the transcript for retakes and long pauses. Tariq has read-only tools and a fixed answer shape, and Josh OS checks every word id and pause id he sends back.
3. **Review.** The whole transcript shows, with cut words struck through and pauses, sounds and throat clears inline. Josh selects words to cut or keep, clicks any tag to change it, and has undo and redo. The "To check" list holds the uncertain moments. Each card plays the original moment and the proposed join from the good mic, with 2, 5 or 10 seconds of context. Every change makes a new revision, and old ones are never edited.
4. **Approval.** Josh approves one revision as a whole. Unverified sync or a missing source blocks it. Approval is tied to that revision's digest, so any later change makes it stale.
5. **Premiere cut.** Josh OS checks the approval again, then asks the Josh Cut Pro panel to build `CLEAN_CUT_rNN` as a new sequence. Its pictures are pieces of `GREEN_MASTER`, and the good mic goes on A1 using the same map. The panel reads every track back and Josh OS compares it with the plan. It never overwrites a sequence, and it never edits Josh's own sequences or media.
6. **Proof.** "Prove a key change reaches every piece" changes one presenter Ultra Key value in the master and exports real Premiere frames from three distant pieces. It checks that each one changed and that the shadow's key did not, then puts the value back.
7. **Animations and final.** Josh's notes are kept with the cut. Approving the storyboard ties it to the current cut and to the slides as they were, and a new cut or new slides make it stale. The final step runs as a job:
   - It copies the cut to `FINAL_ASSEMBLY_rNN`.
   - It finds each press of Tariq's approved deck in the approved transcript and maps it through the cut's own map.
   - It renders each press on this PC, at the sequence's size. PiP and half and half come out as ProRes with alpha, into `video\tariq-animations`.
   - The panel places them on V5, with copies of the GREEN_MASTER pieces on V4 under PiP and half and half shots. If that copy fails, it stops rather than placing the raw camera file.
   - Then it exports with Premiere's own encoder into `video\exports` as a new file, saves, and checks the file's length, picture and sound.
8. **Setting up the master.** "Set up Premiere" on the review screen copies Josh's keyed, uncut sequence to `GREEN_MASTER`, never changing his:
   - It takes the audio off the copy.
   - It can start the presenter's Ultra Key from the Data Storytelling or Executive Questions values, leaving the key colour as Josh had it.
   - It can add a rough shadow copy under the presenter, using Drop Shadow with Shadow Only and the Data Storytelling settings.
   - Masks come from Josh's sequence, because the plugin API has no way to make them.
9. **The Premiere test on a copy.** "Run the Premiere test on a copy" copies a `.prproj` into Josh OS's own test folder, checks the original's hash before and after, and opens only the copy. Then it:
   - makes the master and imports the mic
   - builds the approved cut and reads it back
   - proves a key change reaches three pieces
   - renders and places a few animations
   - exports, saves, closes, reopens and checks the sequences, the nest and the media links
   It closes the copy at the end and writes `receipt.json` in the test folder.

## The files

- **`dajoshi/edit_plan.py`** is the planner. It works in mic samples, and every setting in `CONFIG` is marked `calibrated: False`.
- **`studio/edit_flow.py`** holds the stages, revisions, approvals, previews, Premiere requests, read-back checks, the native proof and the export check. It uses the existing `workflow_artifacts` and `workflow_approvals` tables, in kinds that start with `edit_`.
- **`studio/agent.py`** has `edit_proposal_job`, `EDIT_JOB_SCHEMA` and `EDIT_BRIEF`.
- **`studio/edit_animations.py`** is the animation hand-off. It finds presses, renders them and builds the layer list.
- **`studio/web/edit-flow.js` and `edit-flow.css`** are the review screen.
- **`E:\Videos\DaJoshiCut\panel\index.js`** (0.8.8) has the commands `tq_probe`, `tq_make_master`, `tq_master_read`, `tq_master_set`, `tq_export_frames`, `tq_import`, `tq_build_cut`, `tq_assemble`, `tq_place_layers`, `tq_export`, `tq_save`, `tq_open_project`, `tq_close_project` and `tq_verify_project`. `tq_open_project` only opens files in a `native-test` folder, and `tq_close_project` only closes the project it was given. The 0.8.7 package is kept in `E:\Videos\DaJoshiCut\Backups`.

## Decisions taken from the evidence (development settings, not calibrated)

- **Pauses** are kept unless Tariq or Josh shortens them. Those of 700 ms and over go to review. The study suggested that gate for review, not as a cut rule.
- **Sounds.** An isolated sound between words is a `[possible cough]`, proposed for cutting and always reviewed. A sound right beside a word is kept. Faint sounds count as breaths and are kept. Nothing is ever called a cough.
- **Throat clears.** When Whisper writes "ahem", it becomes a `[possible throat clear]` and is cut on its own span only.
- **Retakes.** Repeated wording is a candidate and kept by default. Tariq's editorial pass decides, and Josh reviews.
- **Handles** are 100 ms before an incoming word and 150 ms after an outgoing one. The first pilot candidate in the report was 100 ms in and 130 to 150 ms out.
- **Frame rounding** happens once, onto the camera frames, using the sequence's own ticks per frame. Cuts under a frame merge, and pieces under half a frame drop.
- **Room tone** is a separate insert that removes nothing.

## What was tested, by kind

| Check | Kind | Result |
|---|---|---|
| Planner on the Executive Questions mic and study transcript, defaults only | Measurement replay | 0 of 82 retained pauses touched. 0.18 s removed where the editor kept, all of it in the head and tail handles. 5 of 13 sound candidates removed, all 5 ones the editor removed, including event 04. Receipt `tests/fixtures/tariq_edit/exec_replay_receipt.json`. |
| Same, with the editor's removals given as word selections | Measurement replay | 11 retake cuts covered 90 to 100%. 0 kept words clipped. |
| Data Storytelling acoustic replay from doc 10 | Reproduction | Matched exactly: 9 of 210 pauses shortened, 3.55 s lost, 7 of 7 kept and 3 of 6 removed. The receipt now exists at `data_replay_receipt.json`. The planner defaults on the same audio shortened 0 of 210, kept 7 of 7 and removed 4 of 6. |
| Tariq's real editorial pass on the Executive transcript | One real model run | 45 s. 11 removals, covering the editor's 11 retake regions at 88 to 99% each. 139.9 s removed against 144.9 s. 0.58 s removed where the editor kept. Receipt `exec_editorial_receipt.json`. |
| Whole local path on the real Executive recording | End to end, no Premiere | Local transcription 1,509 words on the CPU, 275 s for the whole run. Sync 3.7029 s against the project's 3.7000 s. Revisions, a stale-approval refusal, the restore, cached previews, the plan refused before approval, and the build reported as blocked with Premiere closed. |
| Review screen in Chrome on a throwaway Josh OS | UI | Opened from the Edit piece. Card clips, keep, selecting and cutting words, undo, redo, approval through the button and the Premiere blocker message all worked. |
| Panel commands against the Premiere stub | MOCK | The build was made only of master nests, with the mic in sync on every piece, no camera audio and no overlaps. A second build was refused. The key change reached every piece in the stub's model and the shadow key stayed the same. Receipt `E:\Videos\DaJoshiCut\tests\tq-native-stub-receipt.json`. |
| Josh Cut Pro's existing in-place UI test with the new panel | Regression, mock | Passed in full. |
| Animation hand-off on the real Executive deck and recording | Real renders, no Premiere | 55 of 58 presses placed in order with no overlaps. The 3 left out quote lines that aren't in this transcript, and the run reports them. One PiP press rendered as ProRes with alpha and one full screen press as H.264, both at the sequence's exact rate. Receipt `animation_handoff_receipt.json`. |
| The whole copied-project route through Josh OS, a real Josh Cut Pro helper and the real panel code | MOCK (Premiere stub) | Every step passed: open the copy, make the master (reference key and shadow), import, build, read back, render and place 2 animations, export request, save, close, reopen, check. The frame proof and the export file check failed, as expected, because the stub writes no files. The original project's hash didn't change. Receipt `native_flow_MOCK_receipt.json`. |
| Josh Cut Pro's existing in-place UI test after the nest-aware PiP change | Regression, mock | Passed in full. |
| Unit tests `tests/test_tariq_edit.py` | Unit | 18 pass. |
| The whole copied-project route in real Premiere 25.6.6 with panel 0.8.8, started unattended (`tests/tariq_native_real.py`) | **Native** | All 21 steps passed in about 10 minutes on a copy of `Exec questions.prproj`, with the original's hash unchanged. Josh OS started the helper with no window and launched Premiere, and the panel linked by itself 5 seconds later. GREEN_MASTER was made from the uncut AutoCut backup. `CLEAN_CUT_r01` was 6 master nests with the mic on A1, and the read back matched. A Choke change in the master changed the exported frame at all three pieces and putting it back restored them byte for byte. 3 animations were placed, the 4K export was checked, and Josh shows in every PiP window. The copy saved, closed, reopened with all 6 sequences and no offline media. Receipt `native_flow_REAL_receipt.json`. |
| Master from an already cut sequence (AutoCut, 14 pieces) | MOCK | Rebuilt as one uncut take with the effects copied over. This is in panel 0.8.9, which is packaged but not installed, so it hasn't run in Premiere. |

**Correction.** The earlier animation hand-off row said a PiP press rendered "with alpha". It did, but the alpha was solid everywhere, because Josh's house deck has a white background and only a grey placeholder where the camera goes. In Premiere the slide covered Josh. Only the real export showed it. The renderer now cuts a see-through window at the deck's `#pip` and `#half` boxes, adds the 6px gold border and shadow for PiP, and refuses a render whose window isn't clear. The native test also checks the exported frames for a picture in every window.

**Fixed from the real run.**
- Premiere reloads the panel when a project opens, so the bridge waits for it to come back.
- Premiere adds `.png` to frame names itself.
- Frames arrive after the call returns, so Josh OS waits for them, and asks again if one never arrives.
- `exportSequence` rejects an empty preset, so Josh OS passes Adobe's own `H264 Match Source - High bitrate.epr`.
- After a save Premiere reports the project path with a `\\?\` prefix.

No listening pass was done. Sound and cough labels are unverified. Both recordings are development examples, so no accuracy claim follows.

## What needs Josh (blocked here)

1. **Installing panel 0.8.9.** 0.8.8 is installed and passed the native test. 0.8.9 adds the master rebuild from an already cut sequence and the path fix for closing. Installing goes through Adobe's plugin installer, which asks Josh to confirm, so he has to double-click `E:\Videos\DaJoshiCut\DaJoshiCut_premierepro.ccx` himself. After that, Josh OS starts the helper and Premiere on its own.
2. **Still unproven in Premiere.**
   - Drop Shadow's settings and the shadow copy. Josh's sequences have no spare track above the presenter, and whether Premiere adds one when asked is unknown.
   - Copying Ultra Key settings onto a freshly placed clip (0.8.9).
3. **Josh's simpler PiP layout.** Josh suggested putting the slide underneath, with its PiP box drawn on it, and the cropped camera copy on the track above. That needs no transparency. It would change the panel, so it would ship in the next package.
4. **Restart.** Josh OS needs a restart to load this, and it restarts the Discord bridge too. Do it once the Discord setup is finished, or when Josh is ready. The restart is `Restart DaJoshiResolve.bat`.
5. **Listening and labels.** The 26 review WAVs and the annotation sheets in the handover still need Josh's labels before any sound default changes. A held-out recording is still needed before anyone can say how reliable this is.
6. **Frame.io** is deferred, and Discord wasn't touched.
