"""Tariq's driver for Josh's installed AutoCut (Silences, then Repeat), with no screenshots and no blind clicks.

AutoCut has no supported command for its tools (see the evidence index from 12 Nov 2026). Its Premiere panel is only a
bridge that AutoCut's own app drives, so Tariq works AutoCut's window through Windows UI Automation instead
(studio/autocut_uia.ps1). Every action names a control by type and exact name and uses that control's own pattern.

Before anything is pressed, the driver checks:
- which AutoCut screen is showing, from AutoCut's own page route and the controls on it. Any screen it doesn't know
  (a sign-in, an update, a payment page, an error) stops the run.
- that Premiere's open sequence is the one Tariq expects (asked through the Josh Cut Pro bridge), and that the timeline
  length AutoCut shows matches that sequence.
- that only A1 is ticked, read back after setting it.
- for Silences, that the settings still match the defaults Josh chose. Any difference stops the run.

A cut button is pressed only when cut=True, and only on one of Tariq's own AUTOCUT_RAW_rNN copies. After it, the driver
waits for AutoCut's finished dialog and for a new /batch-cut-and-delete in AutoCut's host log, with a timeout, then has
Premiere read the sequence back. Each pass's record (screens, settings, log calls, read-back) is returned for the evidence.
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

ENGINE = Path(__file__).with_name('autocut_uia.ps1')
HOST_DIR = Path(os.environ.get('TEMP', '')) / 'com.autocut' / 'com.autocut.aea'
T = 254016000000
OWN = re.compile(r'AUTOCUT_RAW_r\d{2}')

ROUTES = {
    'home': '/homepage',
    'silences.sections': '/legacy/selection',
    'silences.settings': '/legacy/customization',
    'repeat.sections': '/repeat/selection',
    'repeat.language': '/repeat/language',
}
# What Josh chose on 12 Nov 2026: AutoCut Silences as it comes. A change here is a change to Josh's recipe.
SILENCES_DEFAULTS = dict(auto_threshold='On', remove_longer_than_ms='350', keep_talks_longer_than_ms='350',
                         margin_before_ms='350', margin_after_ms='350', minimum_time_between_cuts='Off')
# Repeat settings are read only in its review step; Josh's choice is the default mode with the 100 ms margin.
REPEAT_DEFAULTS = dict(margin_ms='100')


class AutoCutError(RuntimeError):
    pass


def mmss(seconds):
    s = int(round(seconds))
    return f'{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}'


class Driver:
    def __init__(self, bridge='http://127.0.0.1:47820', host_dir=HOST_DIR, runner=None, clock=time, log=print):
        self.bridge = bridge.rstrip('/')
        self.host_log = Path(host_dir) / 'autocut.host.log'
        self._run = runner or self._powershell
        self.clock = clock
        self.say = log
        self.trail = []          # every screen seen and every action taken, for the evidence

    # ---------------------------------------------------------------- engine and bridge
    @staticmethod
    def _powershell(args, timeout=60):
        p = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ENGINE)] + args,
                           capture_output=True, text=True, timeout=timeout)
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise AutoCutError(f'The AutoCut engine gave no answer: {(p.stderr or p.stdout)[:300]}') from None

    def ui(self, *args):
        r = self._run(list(args))
        if not r.get('ok'):
            raise AutoCutError(r.get('error') or 'AutoCut refused the step.')
        if args[0] != 'snapshot':
            self.trail.append(dict(at=self.clock.time(), did=list(args)))
        return r

    def bridge_status(self):
        return json.loads(urllib.request.urlopen(self.bridge + '/bridge/status', timeout=5).read())

    def native_transcript(self, args):
        # Fixed bridge command; never drives a Premiere window or AutoCut control.
        req = urllib.request.Request(self.bridge + '/bridge/call',
                                     data=json.dumps(dict(cmd='tq_native_transcript', args=args)).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        try:
            result = json.loads(urllib.request.urlopen(req, timeout=30).read()).get('result')
        except Exception as exc:
            raise AutoCutError('Native transcript bridge did not answer. No retry was sent. ' + str(exc)[:150]) from exc
        if not isinstance(result, dict) or result.get('error'):
            raise AutoCutError('Native transcript bridge is unavailable. Load the updated Josh Cut Pro panel. ' + str(result)[:150])
        return result

    def premiere_ops(self, ops, timeout=300):
        req = urllib.request.Request(self.bridge + '/bridge/call', data=json.dumps(dict(cmd='tq_ops', args=dict(ops=ops))).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read()).get('result') or {}
        if not r.get('ok'):
            bad = (r.get('steps') or [{}])[-1]
            raise AutoCutError(f"Premiere refused {bad.get('op')}: {bad.get('error')}")
        return [s.get('result') for s in r['steps']]

    # ---------------------------------------------------------------- reading AutoCut's screen
    def ensure_open(self, timeout=60):
        """Open the installed Premiere extension as part of Start processing."""
        try:
            return self.snapshot()
        except AutoCutError as error:
            if 'AutoCut is not open.' not in str(error):
                raise
        self.ui('open')
        end = self.clock.time() + timeout
        while self.clock.time() < end:
            try:
                return self.snapshot()
            except AutoCutError as error:
                if 'AutoCut is not open.' not in str(error):
                    raise
                self.clock.sleep(1)
        raise AutoCutError('AutoCut did not open within 60 seconds. The fresh project was retained.')

    def snapshot(self):
        return self.ui('snapshot')

    @staticmethod
    def screen(snap):
        items = snap['items']
        windows = [i['name'] for i in items if i['type'] == 'Window']
        finished = [w for w in windows if w.endswith(' finished')]
        if finished:
            return 'finished:' + finished[0][:-len(' finished')]
        if windows:
            return 'dialog:' + windows[0]
        route = next((i.get('value') or '' for i in items if i['type'] == 'Document'), '')
        buttons = {i['name'] for i in items if i['type'] == 'Button'}
        for name, path in ROUTES.items():
            if route.endswith(path):
                return name
        if 'Cut sequence' in buttons and '/repeat/' in route:
            return 'repeat.review'
        if '/repeat/' in route and not ({'Cut sequence', 'Generate transcript', 'Validate sections'} & buttons):
            return 'repeat.working'
        return 'unknown:' + route

    def wait(self, wanted, timeout, poll=1.0, allow=()):
        """Wait until AutoCut shows one of the wanted screens. Any other screen, apart from allow, stops the run."""
        end = self.clock.time() + timeout
        last = None
        while True:
            snap = self.snapshot()
            name = self.screen(snap)
            if name != last:
                self.trail.append(dict(at=self.clock.time(), screen=name))
                last = name
            if name in wanted:
                return snap
            if name not in allow:
                raise AutoCutError(f'AutoCut is showing {name}, which this step does not expect. Nothing more was pressed.')
            if self.clock.time() > end:
                raise AutoCutError(f'AutoCut did not reach {", ".join(wanted)} within {timeout:.0f} s. Nothing more was pressed.')
            self.clock.sleep(poll)

    @staticmethod
    def timeline_end(snap):
        """The timeline length AutoCut shows on its sections and settings screens, as HH:MM:SS."""
        ts = [i['name'] for i in snap['items'] if i['type'] == 'Text' and re.fullmatch(r'\d\d:\d\d:\d\d', i['name'])]
        return ts[1] if len(ts) >= 2 else None

    @staticmethod
    def tracks(snap):
        it = snap['items']
        return {it[k + 1]['name']: i.get('toggle') for k, i in enumerate(it[:-1])
                if i['type'] == 'CheckBox' and it[k + 1]['type'] == 'Text' and re.fullmatch(r'A\d+', it[k + 1]['name'])}

    @staticmethod
    def silences_settings(snap):
        it = snap['items']
        boxes = [i.get('toggle') for i in it if i['type'] == 'CheckBox']
        spins = [i.get('value') for i in it if i['type'] == 'Spinner']
        if len(boxes) != 2 or len(spins) != 4:
            raise AutoCutError(f'The Silences settings screen has {len(boxes)} switches and {len(spins)} number fields, not 2 and 4. '
                               'AutoCut may have changed, so nothing was pressed.')
        threshold = next((i.get('value') for i in it if i['type'] == 'Slider' and i['name'] == 'Noise Threshold'), None)
        detected = next((i['name'] for i in it if i['type'] == 'Text' and i['name'].startswith('Detected by AI')), None)
        return dict(auto_threshold=boxes[0], remove_longer_than_ms=spins[0], keep_talks_longer_than_ms=spins[1], margin_before_ms=spins[2],
                    margin_after_ms=spins[3], minimum_time_between_cuts=boxes[1], threshold=threshold, detected=detected,
                    not_readable='Silence Management and Transitions choices are not exposed to UI Automation')

    # ---------------------------------------------------------------- guards
    def guard_sequence(self, expected, snap=None):
        st = self.bridge_status()
        if not st.get('connected'):
            raise AutoCutError('The Josh Cut Pro panel in Premiere is not connected, so the open sequence cannot be checked.')
        open_seq = (st.get('info') or {}).get('sequence')
        if open_seq != expected:
            raise AutoCutError(f'Premiere has {open_seq!r} open, not {expected!r}. Nothing was pressed.')
        info = self.premiere_ops([dict(op='info', seq=expected)])[0]
        a1 = [i for i in info['items'] if i['kind'] == 'a' and i['track'] == 0]
        if not a1:
            raise AutoCutError(f'{expected} has nothing on A1.')
        length = max(int(i['end']) for i in info['items']) / T
        if snap is not None:
            shown = self.timeline_end(snap)
            if shown is None or abs(_secs(shown) - length) > 1.0:
                raise AutoCutError(f'AutoCut shows a {shown} timeline but {expected} is {mmss(length)}. Nothing was pressed.')
        return dict(sequence=expected, length_s=length, a1_items=len(a1), items=len(info['items']))

    def a1_only(self, snap):
        have = self.tracks(snap)
        if 'A1' not in have:
            raise AutoCutError('AutoCut shows no A1 track to tick.')
        for name in have:
            self.ui('track', '-Name', name, '-State', 'on' if name == 'A1' else 'off')
        now = self.tracks(self.snapshot())
        if now.get('A1') != 'On' or any(v != 'Off' for k, v in now.items() if k != 'A1'):
            raise AutoCutError(f'Tracks did not end up as A1 only: {now}. Nothing was pressed.')
        return now

    def host_calls(self):
        try:
            text = re.sub(r'\x1b\[[0-9;]*m', '', self.host_log.read_text(encoding='utf-8', errors='replace'))
        except OSError:
            return []
        out = []
        for m in re.finditer(r'/batch-cut-and-delete with data: (\{.*?\})\s*\n', text, re.S):
            try:
                out.append(json.loads(m.group(1)))
            except ValueError:
                out.append(dict(unreadable=True))
        return out

    def home(self):
        """Back to AutoCut's home screen from any known screen, without pressing anything that edits."""
        for _ in range(5):
            snap = self.snapshot()
            name = self.screen(snap)
            if name == 'home':
                return
            backs = [i['name'] for i in snap['items'] if i['type'] == 'Button' and i['name'].startswith('Back')]
            if name.startswith('finished:'):
                self.ui('press', '-Type', 'Button', '-Name', 'Close', '-Index', '0')
            elif name in ('silences.sections', 'silences.settings', 'repeat.sections', 'repeat.language', 'repeat.review') and len(backs) == 1:
                self.ui('press', '-Type', 'Button', '-Name', backs[0])
            else:
                raise AutoCutError(f'AutoCut is showing {name}, so Tariq stops rather than guess how to leave it.')
            self.clock.sleep(1.5)
        raise AutoCutError('AutoCut did not get back to its home screen.')

    def _cut_allowed(self, sequence, cut):
        if cut and not OWN.fullmatch(sequence):
            raise AutoCutError(f'Tariq only lets AutoCut cut his own AUTOCUT_RAW_rNN copies, not {sequence}.')

    def _finish(self, tool, sequence, calls_before, timeout):
        snap = self.wait({f'finished:{tool}'}, timeout, poll=2.0, allow={'repeat.review', 'silences.settings', 'repeat.working', 'dialog:Premiere Pro is busy', f'dialog:{tool}'})
        calls = self.host_calls()
        if len(calls) != calls_before + 1:
            raise AutoCutError(f'AutoCut says {tool} finished, but its log has {len(calls) - calls_before} new cut calls, not 1. Check the sequence by hand.')
        self.ui('press', '-Type', 'Button', '-Name', 'Close', '-Index', '0')
        return dict(finished=[i['name'] for i in snap['items'] if i['type'] == 'Text'][:12], call=calls[-1])

    # ---------------------------------------------------------------- the two passes
    def silences(self, sequence, cut=False, timeout=900):
        """AutoCut Silences on the whole timeline, A1 only, on Josh's defaults. cut=False stops on the settings screen and goes home."""
        self._cut_allowed(sequence, cut)
        self.home()
        self.ui('press', '-Type', 'Button', '-Name', 'AutoCut Silences', '-Index', '0')
        snap = self.wait({'silences.sections'}, 30, allow={'home'})
        self.ui('press', '-Type', 'Button', '-Name', 'Entire timeline')
        tracks = self.a1_only(self.snapshot())
        guard = self.guard_sequence(sequence, self.snapshot())
        self.ui('press', '-Type', 'Button', '-Name', 'Validate sections')
        snap = self.wait({'silences.settings'}, 60, allow={'silences.sections'})
        # the screen draws first and works out the threshold just after, so wait for AutoCut's own reading
        end = self.clock.time() + 60
        while not self.silences_settings(snap)['detected']:
            if self.clock.time() > end:
                raise AutoCutError('AutoCut did not show its detected noise threshold within 60 s. Nothing was cut.')
            self.clock.sleep(1.0)
            snap = self.wait({'silences.settings'}, 10)
        settings = self.silences_settings(snap)
        diff = {k: (settings[k], v) for k, v in SILENCES_DEFAULTS.items() if settings[k] != v}
        rec = dict(tool='Silences', sequence=sequence, guard=guard, tracks=tracks, settings=settings, differs_from_defaults=diff, cut=False)
        if diff:
            raise AutoCutError(f'AutoCut Silences settings differ from Josh\'s defaults: {diff}. Nothing was cut.')
        if not cut:
            self.home()
            return rec
        guard2 = self.guard_sequence(sequence, snap)            # checked again right before the cut
        before = len(self.host_calls())
        self.ui('press', '-Type', 'Button', '-Name', 'Cut and Delete Silences')
        rec.update(cut=True, guard_at_cut=guard2, result=self._finish('AutoCut Silences', sequence, before, timeout))
        return rec

    def repeat(self, sequence, generate=False, cut=False, timeout=1800):
        """AutoCut Repeat on the whole timeline, A1 only, English. generate=False stops on the language screen (no cloud
        call). generate=True makes AutoCut's transcript and stops on its review. cut=True also presses Cut sequence."""
        self._cut_allowed(sequence, cut)
        if cut and not generate:
            raise AutoCutError('Repeat can only cut after its transcript is made.')
        self.home()
        self.ui('press', '-Type', 'Button', '-Name', 'AutoCut Repeat', '-Index', '0')
        self.wait({'repeat.sections'}, 30, allow={'home'})
        self.ui('press', '-Type', 'Button', '-Name', 'Entire timeline')
        tracks = self.a1_only(self.snapshot())
        guard = self.guard_sequence(sequence, self.snapshot())
        self.ui('press', '-Type', 'Button', '-Name', 'Validate sections')
        snap = self.wait({'repeat.language'}, 60, allow={'repeat.sections'})
        lang = [i.get('value') for i in snap['items'] if i['type'] == 'ComboBox' and i.get('value') not in ('EN',)]
        rec = dict(tool='Repeat', sequence=sequence, guard=guard, tracks=tracks, language=lang, generated=False, cut=False)
        if lang != ['English']:
            raise AutoCutError(f'AutoCut Repeat language is {lang}, not English. Nothing was sent.')
        if not generate:
            self.home()
            return rec
        self.ui('press', '-Type', 'Button', '-Name', 'Generate transcript')
        snap = self.wait({'repeat.review'}, timeout, poll=3.0, allow={'repeat.language', 'repeat.working', 'dialog:Premiere Pro is busy'})
        rec.update(generated=True, review_text=[i['name'] for i in snap['items'] if i['type'] == 'Text'])
        if not cut:
            return rec          # left on the review screen for Josh or Tariq to look at
        rec['guard_at_cut'] = self.guard_sequence(sequence)
        before = len(self.host_calls())
        self.ui('press', '-Type', 'Button', '-Name', 'Cut sequence')
        rec.update(cut=True, result=self._finish('AutoCut Repeat', sequence, before, timeout))
        return rec


def _secs(hms):
    h, m, s = (int(x) for x in hms.split(':'))
    return h * 3600 + m * 60 + s
