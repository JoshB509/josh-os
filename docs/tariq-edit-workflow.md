# Tariq's editing workflow: build status

Built 10 November 2026 from the handover at `C:\Users\Anigma PC\Desktop\Tariq-Editing-Handover-2026-11-10`. This page says what is built, what was tested and at what level, and what still needs Premiere or Josh.

## How it works

1. **Sources.** On a video's Edit piece, "Tariq's clean cut" opens the workflow. Josh picks the camera file and the good mic from the video's `Recorded\Input` folder. If there is more than one candidate, nothing is guessed. The originals are only ever read.
2. **Proposal.** Josh OS fingerprints both files, decodes them with the bundled FFmpeg, and checks sync at three points (start, middle, end). It transcribes the mic locally with faster-whisper medium.en on the CPU, with downloads off. If the model is missing it stops rather than using a cloud service. Then it measures pauses and sounds, and Tariq reads the transcript for retakes and long pauses. Tariq has read-only tools and a fixed answer shape, and Josh OS checks every word id and pause id he sends back.
3. **Review.** The whole transcript shows, with cut words struck through and pauses, sounds and throat clears inline. Josh selects words to cut or keep, clicks any tag to change it, and has undo and redo. The "To check" list holds the uncertain moments. Each card plays the original moment and the proposed join from the good mic, with 2, 5 or 10 seconds of context. Every change makes a new revision, and old ones are never edited.
4. **Approval.** Josh approves one revision as a whole. Unverified sync or a missing source blocks it. Approval is tied to that revision's digest, so any later change makes it stale.
5. **Premiere cut.** Josh OS checks the approval again, then asks the Josh Cut Pro panel to build `CLEAN_CUT_rNN` as a new sequence. Its pictures are pieces of `GREEN_MASTER`, and the good mic goes on A1 using the same map. The panel reads every track back and Josh OS compares it with the plan. It never overwrites a sequence, and it never edits Josh's own sequences or media.
6. **Proof.** "Prove a key change reaches every piece" changes one presenter Ultra Key value in the master and exports real Premiere frames from three distant pieces. It checks that each one changed and that the shadow's key did not, then puts the value back.
7. **Animations and final.** Josh's notes are kept with the cut. Approving the storyboard ties it to the current cut, and a new cut makes it stale. The final step copies the cut to `FINAL_ASSEMBLY_rNN`, renders it with Premiere's own encoder into `video\exports` as a new file, and checks that file's length, picture and sound.

## The files

- **`dajoshi/edit_plan.py`** is the planner. It works in mic samples, and every setting in `CONFIG` is marked `calibrated: False`.
- **`studio/edit_flow.py`** holds the stages, revisions, approvals, previews, Premiere requests, read-back checks, the native proof and the export check. It uses the existing `workflow_artifacts` and `workflow_approvals` tables, in kinds that start with `edit_`.
- **`studio/agent.py`** has `edit_proposal_job`, `EDIT_JOB_SCHEMA` and `EDIT_BRIEF`.
- **`studio/web/edit-flow.js` and `edit-flow.css`** are the review screen.
- **`E:\Videos\DaJoshiCut\panel\index.js`** (0.8.8) has the commands `tq_probe`, `tq_master_read`, `tq_master_set`, `tq_export_frames`, `tq_build_cut`, `tq_assemble`, `tq_export` and `tq_save`. The 0.8.7 package is kept in `E:\Videos\DaJoshiCut\Backups`.

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
| Unit tests `tests/test_tariq_edit.py` | Unit | 16 pass. |

No listening pass was done. Sound and cough labels are unverified. Both recordings are development examples, so no accuracy claim follows.

## What needs Premiere or Josh (not done)

1. **Native proof in Premiere.** Nothing has run in real Premiere. Install the 0.8.8 panel by double-clicking `E:\Videos\DaJoshiCut\DaJoshiCut_premierepro.ccx`, then save a copy of a project as a test project and open it. In that copy, duplicate the keyed, uncut sequence, name it `GREEN_MASTER`, and remove its audio. Import the good mic. Then in Josh OS run Check Premiere, Build, and Prove. After that, save, close and reopen the project and check the media links. Only that counts as proof of nesting, propagation and save and reopen.
2. **Making GREEN_MASTER.** Premiere's plugin API documents effect parameters, frame export and sequences, but no way to create masks. So the master starts from a sequence Josh has already keyed, and Tariq reads and changes the Ultra Key values on it rather than inventing masks.
3. **Inserting a sequence as a nest** is not documented for the plugin API. `tq_build_cut` relies on `createOverwriteItemAction` accepting a sequence's project item. If Premiere refuses it, the build stops with Premiere's message. It never falls back to raw camera media.
4. **Animations onto the assembly.** `FINAL_ASSEMBLY_rNN` starts as a copy of the cut. Placing the rendered animations on it uses Josh Cut Pro's existing Animations tool, and the automatic hand-off from Tariq's deck to that renderer is not connected yet.
5. **Export preset.** `tq_export` passes no preset, so Premiere uses the sequence's own export settings. Choose a preset once the first real export has been checked.
6. **Labels for sounds.** The 26 review WAVs and the annotation sheets in the handover still need Josh's labels before any sound default changes.
7. **A held-out recording** is needed before anyone can say how reliable this is.
8. **Discord and Frame.io** were left out on purpose in this pass.
