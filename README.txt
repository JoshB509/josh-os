DaJoshiResolve 0.1 alpha
Local Windows editing assistant for Josh

START
Open DaJoshiResolve.exe or the DaJoshiResolve (Alpha) desktop shortcut.
Drop a video onto the Video field. Add a better microphone WAV if available.
For a silent approved composite, also provide the original camera file in Reference.
Choose optional shot-list CSV/JSON, HTML beat deck, plain-text script and cached
word-timestamp transcript. Click Build editable draft. Each run gets its own folder.
No cloud inference, paid tokens, uploads, model downloads, keying or grading.

THIS RELEASE IS AN ALPHA, NOT A VERIFIED PREMIERE DELIVERY
The worker produces legacy Final Cut Pro XML using Premiere's supported XML
import route. It does not manufacture .prproj files. Import EDITABLE_DRAFT.xml
into a NEW Premiere project during an idle review window, then save from Premiere.
Native import, Basic Motion translation, trim editing, native playback and full
listening have NOT been verified. Do not replace an existing approved project.

OUTPUTS
EDITABLE_DRAFT.xml: original footage and original selected audio references.
  V1 contains separate rendered animation clips. V2 contains camera clips.
  Full-camera, PiP and side layouts use editable Basic Motion scale/center.
  In graphic-only shots the camera clip remains present but disabled.
  Linked audio clips remain separately editable. Original source handles remain.
REVIEW_PREVIEW.mp4: a review render, not the editable deliverable.
timing_map.json: authoritative source/output frame map and audio alignment.
retained_ranges.csv: inspectable source clip ranges.
exceptions.csv/json: uncertainty report with source seconds and output frames.
decisions.json: automatic removals, override ranges and acoustic thresholds.
sync.json: measured alignment, confidence, frame-grid residual and visual checks.
animations/: one movable clip per mapped shot/beat, plus 30 seconds of frozen
  tail handles. Trim its out-point to extend the hold; extend source duration
  in the renderer if more than 30 extra seconds are needed.
status.json, process.log, error.txt: worker state and diagnostics.

AUTOMATIC EDITING
Word timestamps plus acoustic energy identify quiet gaps. Bounds are padded
outside recognized speech to protect consonants and tails. Strong short repeated
prefixes can be removed when a later take completes them and quiet separation
supports the decision. Low-confidence words, ambiguous repetition and audible
untranscribed sounds are retained and reported. This will miss some short false
starts, laughs and breaths; it is not a semantic sound classifier or flawless editor.
Script input checks vocabulary coverage; it does not silently rewrite speech.

OVERRIDES
Always keep / Always remove accept original VIDEO seconds, e.g. 12.4-15.8, 32-34.
Keep overrides restore material removed automatically. Remove overrides are applied
last, so they win conflicts. Start a new run after changes. For CLI use, see
examples/overrides.json. You do not have to approve every automatic cut.

SHOT LIST CONTRACT
CSV columns: source_start,source_end,layout,beat,phrase
Times are seconds on the selected VIDEO, before automatic cuts.
layout: full (camera), pip (camera bottom right), side (camera right/graphic left),
graphic (graphic full frame, camera retained disabled).
beat: integer passed to window.go(beat). Required except for full.
phrase: optional exact, uniquely occurring spoken phrase replacing source_start.
source_end is still required. Ambiguous matches stop instead of guessing.
Rows must be ordered and non-overlapping. Cuts split shots through the same map.
Examples demonstrate the format, not editorial choices suitable for a full video.
DOCX/PDF/freeform shot-list parsing is not implemented.

HTML CONTRACT AND LIMITS
Local HTML must expose window.go(integer). The renderer blocks HTTP/HTTPS requests,
disables service workers, waits for local/embedded fonts, and uses an isolated
headless Chrome process. It does not touch the user's browser, Premiere or OBS.
Timers, RAF, Date/performance clocks, seeded randomness and CSS animation time are
controlled. Up to three seconds of motion are recorded per beat, then the final
state is frozen. Cyclic content intentionally stops during the hold. Decks needing
longer animated sequences, video/WebGL/audio clocks or external assets require an
adapter and validation; they are not universally supported by this contract.
HTML is executable code. Use your own trusted local decks.

FRAME RATES / SYNC
Rational timing arithmetic supports any positive rational rate up to 1000fps.
XML export supports integer rates and N*1000/1001 rates. Other fractional rates
are explicitly rejected by the exporter; general arbitrary-rate Premiere assembly
is unfinished. Variable-frame-rate source timing is not certified.
Audio correlation searches for the lag rather than using 2.44 seconds as a default.
Three independent checks must agree. A silent composite is aligned visually to
its original reference within a +/-5 second search. Uncertain sync stops export.
XMEML audio is frame-aligned: the measured offset is rounded to the frame grid and
the residual is reported. Sample-accurate Premiere subframe assembly is unfinished.
Original-camera and approved-composite time are different in Promotion Rule.

RESUME / RESOURCE USE
Cancel safely writes a cancellation marker. The worker checks between frames,
FFmpeg polling intervals and Whisper segments. A Whisper segment can take time
before cancellation is observed. Resume selects request.json inside that run.
Completed stages are reused only with matching inputs, code, settings and request.
For changed settings/overrides/app versions, start a new run.
The app refuses media work while an OBS process is present, even if merely idle.
Close OBS when you are finished with it; the app never closes it for you.
Worker CPU threads are limited to two for FFmpeg/Whisper. It does not use GPU ASR.

DEPENDENCIES / SOURCE
This release uses installed C#/.NET Windows Forms for its native app and Python for
the media pipeline. No Rust toolchain was found, so a Rust implementation is pending.
No software was downloaded or installed. settings.json explicitly identifies the
existing Python environment and cached Faster Whisper model in the older local
work folder. KEEP THAT ENVIRONMENT/MODEL: removing it will break this alpha until
settings are updated. FFmpeg is copied locally when packaging succeeds.
Legacy scripts are copied under reference_sources with a provenance manifest for
audit only; they are not executed. Hand-authored cut choices, fixed 25fps slots,
native .prproj object-graph builders, greenscreen and grading were not reused.
Source files and older projects are read-only inputs and are never rewritten.

DEVELOPER COMMANDS (from app folder, using settings.json's Python)
python -m unittest discover -s tests -v
python worker.py --request examples/fixture.json --job Runs/a-new-unique-name
python worker.py --job Runs/interrupted-run --resume
build.ps1 uses the installed .NET Framework csc.exe; no packages are fetched.

PREMIERE ACCEPTANCE CHECK REQUIRED
1. During an idle window, import EDITABLE_DRAFT.xml into a new empty project.
2. Confirm no offline media/import errors, correct duration and frame rate.
3. Play every fixture join with the selected mic; check lip sync and clipped words.
4. Extend both ends of a camera/audio clip into original source handles.
5. Change PiP to full by resetting Motion; disable/enable the retained camera.
6. Move an animation independently; extend its tail into the 30-second hold.
7. Save/reopen the native project and play again. Record outcomes, do not assume.
Promotion Rule v03 joins 09:17.28 and 09:20.16 remain unresolved regression notes.

Adobe XML import reference:
https://helpx.adobe.com/premiere/desktop/organize-media/import-files/migrate-from-final-cut-pro-x.html
Adobe's supported project-format list includes FCP XML:
https://helpx.adobe.com/premiere/desktop/organize-media/import-files/supported-file-formats.html
