"""Observe Premiere's supported automatic source transcription; never operate its UI.

Premiere starts this work on import/use in a sequence when its existing preference
is enabled. CareerOS only saves, decodes the exact mic and verifies the cut. A
preference alone is readiness evidence, never proof transcription succeeded.
"""
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import premiere_transcript as pt
from .transcript_worker import WorkerBlocked, file_digest


def readiness():
    """Public status contains no local profile path or claim of completed work."""
    caps = capabilities()
    return dict(version='native-background-v1', automatic_enabled=caps.get('enabled', False),
                route='premiere-background' if caps.get('enabled') else 'native-api-or-explicit-worker',
                reason=caps.get('reason', ''))


def capabilities(preferences=None):
    if preferences is None:
        root = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Documents/Adobe/Premiere Pro/25.0'
        candidates = list(root.glob('Profile-*/Adobe Premiere Pro Prefs'))
        if len(candidates) != 1:
            return dict(enabled=False, reason='Premiere 25 transcription preferences could not be identified uniquely.')
        preferences = candidates[0]
    try:
        root = ET.fromstring(Path(preferences).read_bytes())
        def value(name):
            return root.findtext('.//' + 'BE.Prefs.Transcription.' + name)
        enabled = value('RunBackgroundTranscription') == 'true'
        return dict(enabled=enabled, route='premiere-background', preferences=str(preferences),
                    language=value('BackgroundTranscriptionLanguage'),
                    separate_speakers=value('BackgroundTranscriptionDiarisationEnabled'),
                    scope=value('BackgroundTranscriptionPreference'),
                    reason='' if enabled else 'Premiere automatic transcription is disabled; no preference was changed.')
    except (OSError, ET.ParseError) as exc:
        return dict(enabled=False, reason='Premiere transcription preferences could not be read: ' + str(exc)[:160])


def wait(store, identity, info, driver, read_info, decode, progress, cancelled,
         timeout=900, clock=time):
    """Bounded observation only. A resume cannot reimport, recut or click Transcribe."""
    store.observe_background(identity, info)
    deadline = clock.monotonic() + max(0, min(timeout, 900))
    while True:
        cancelled()
        opened = driver.premiere_ops([dict(op='project')])[0]
        if pt.media_key(opened.get('project')) != identity['project']:
            raise WorkerBlocked('The active Premiere project changed; background transcript observation stopped before saving.')
        store.check(identity, read_info())
        saved = driver.premiere_ops([dict(op='save')])
        if not any(r.get('saved') is True for r in saved):
            raise WorkerBlocked('Premiere did not confirm saving its native transcript.')
        selected = pt.microphone_transcript(decode(), identity['mic'])
        after = read_info()
        store.check(identity, after)
        if selected and any(w.get('word', '').strip() for w in selected.get('words', [])):
            if file_digest(identity['mic']) != identity['mic_sha256']:
                raise WorkerBlocked('The microphone file changed during transcription; nothing was accepted.')
            store.verify(identity, after, selected, saved=True)
            progress('Premiere native microphone transcript saved and verified against the unchanged AutoCut sequence.')
            return selected
        remaining = deadline - clock.monotonic()
        if remaining <= 0:
            raise WorkerBlocked('Waiting for Premiere automatic transcription timed out. The cut and request are preserved; resume only checks for the saved exact-microphone transcript, without repeating AutoCut or importing media again.')
        progress('Waiting for Premiere automatic microphone transcription; AutoCut is complete. Checking the saved native words and unchanged cut.')
        clock.sleep(min(5, remaining))
