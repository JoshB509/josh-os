"""Tariq's AutoCut driver against real AutoCut screens (tests/fixtures/autocut, read from AutoCut 4.60.38 on 12 Nov 2026)
with a fake engine and a fake Premiere bridge. Nothing here touches AutoCut or Premiere."""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import autocut_driver as ad

FX = Path(__file__).with_name('fixtures') / 'autocut'
load = lambda n: json.loads((FX / f'{n}.json').read_text())
T = 254016000000


class Clock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeAutoCut:
    """Moves between the real screens the way AutoCut does when its buttons are pressed."""

    def __init__(self, settings_override=None, finish=True):
        self.screens = {n: load(n) for n in ('home', 'silences-sections', 'silences-settings', 'repeat-sections', 'repeat-language')}
        if settings_override:
            settings_override(self.screens['silences-settings'])
        self.at = 'home'
        self.pressed = []
        self.finish = finish
        self.cuts = 0

    def snap(self):
        return copy.deepcopy(self.screens[self.at]) if isinstance(self.at, str) else self.at

    def __call__(self, args):
        cmd = args[0]
        if cmd == 'snapshot':
            s = self.snap()
            return s if isinstance(s, dict) else dict(ok=True, items=s)
        name = args[args.index('-Name') + 1]
        self.pressed.append((cmd, name))
        if cmd == 'track':
            for scr in self.screens.values():
                it = scr['items']
                for k, i in enumerate(it[:-1]):
                    if i['type'] == 'CheckBox' and it[k + 1]['name'] == name:
                        i['toggle'] = 'On' if args[args.index('-State') + 1] == 'on' else 'Off'
            return dict(ok=True)
        moves = {('home', 'AutoCut Silences'): 'silences-sections', ('home', 'AutoCut Repeat'): 'repeat-sections',
                 ('silences-sections', 'Validate sections'): 'silences-settings', ('repeat-sections', 'Validate sections'): 'repeat-language',
                 ('silences-settings', 'Back: Sections'): 'silences-sections', ('repeat-language', 'Back: Sections'): 'repeat-sections',
                 ('silences-sections', 'Back to home'): 'home', ('repeat-sections', 'Back to home'): 'home'}
        if isinstance(self.at, str) and (self.at, name) in moves:
            self.at = moves[(self.at, name)]
        elif name == 'Cut and Delete Silences':
            self.cuts += 1
            self.at = [dict(type='Window', name='AutoCut Silences finished'), dict(type='Button', name='Close')] if self.finish else self.at
        elif name == 'Close' and isinstance(self.at, list):
            self.at = 'home'
        elif name not in ('Entire timeline',):
            return dict(ok=False, error=f'No Button called {name!r} on AutoCut\'s screen now.')
        return dict(ok=True)


class Driver(ad.Driver):
    def __init__(self, fake, open_seq='AUTOCUT_RAW_r07', length_s=892.53):
        super().__init__(runner=fake, clock=Clock(), log=lambda *a: None)
        self.fake, self.open_seq, self.length_s = fake, open_seq, length_s
        self.calls = []

    def bridge_status(self):
        return dict(connected=True, info=dict(sequence=self.open_seq))

    def premiere_ops(self, ops, timeout=300):
        end = str(int(self.length_s * T))
        return [dict(items=[dict(kind='a', track=0, start='0', end=end), dict(kind='v', track=0, start='0', end=end)])]

    def host_calls(self):
        return [dict(intervals=[[0, 1]])] * self.fake.cuts


class ProgressDialogs(unittest.TestCase):
    def test_normal_tool_progress_is_waited_for_not_recut(self):
        from unittest.mock import Mock
        for tool in ('AutoCut Silences', 'AutoCut Repeat'):
            d = ad.Driver(clock=Clock())
            d.snapshot = Mock(side_effect=[dict(items=[dict(type='Window', name=tool)]), dict(items=[dict(type='Window', name=tool+' finished'), dict(type='Button', name='Close')])])
            d.host_calls = Mock(return_value=[dict(intervals=[])])
            d.ui = Mock()
            d._finish(tool, 'AUTOCUT_RAW_r01', 0, 30)
            self.assertEqual(d.snapshot.call_count, 2)
            d.ui.assert_called_once_with('press', '-Type', 'Button', '-Name', 'Close', '-Index', '0')


class Screens(unittest.TestCase):
    def test_real_screens_are_recognised(self):
        want = {'home': 'home', 'silences-sections': 'silences.sections', 'silences-settings': 'silences.settings',
                'repeat-sections': 'repeat.sections', 'repeat-language': 'repeat.language'}
        for f, name in want.items():
            self.assertEqual(ad.Driver.screen(load(f)), name, f)

    def test_unknown_page_and_dialogs(self):
        s = load('home')
        for i in s['items']:
            if i['type'] == 'Document':
                i['value'] = 'https://front.static.autocut.com/login'
        self.assertTrue(ad.Driver.screen(s).startswith('unknown:'))
        self.assertEqual(ad.Driver.screen(dict(items=[dict(type='Window', name='AutoCut Repeat finished')])), 'finished:AutoCut Repeat')
        self.assertEqual(ad.Driver.screen(dict(items=[dict(type='Window', name='Update available')])), 'dialog:Update available')

    def test_settings_and_tracks_read_from_real_screen(self):
        st = ad.Driver.silences_settings(load('silences-settings'))
        for k, v in ad.SILENCES_DEFAULTS.items():
            self.assertEqual(st[k], v, k)
        self.assertEqual(st['threshold'], '-44dB')
        self.assertEqual(set(ad.Driver.tracks(load('silences-sections'))), {'A1', 'A2', 'A3'})
        self.assertEqual(ad.Driver.timeline_end(load('silences-sections')), '00:14:53')


class Guards(unittest.TestCase):
    def test_dry_run_presses_no_cut(self):
        f = FakeAutoCut()
        d = Driver(f)
        r = d.silences('AUTOCUT_RAW_r07', cut=False)
        self.assertEqual(r['tracks'], {'A1': 'On', 'A2': 'Off', 'A3': 'Off'})
        self.assertEqual(r['differs_from_defaults'], {})
        self.assertNotIn(('press', 'Cut and Delete Silences'), f.pressed)
        self.assertEqual(f.at, 'home')

    def test_wrong_open_sequence_stops_before_validate(self):
        f = FakeAutoCut()
        d = Driver(f, open_seq='FINAL_ASSEMBLY_r04_v06')
        with self.assertRaises(ad.AutoCutError):
            d.silences('AUTOCUT_RAW_r07', cut=True)
        self.assertNotIn(('press', 'Validate sections'), f.pressed)
        self.assertEqual(f.cuts, 0)

    def test_length_mismatch_stops(self):
        f = FakeAutoCut()
        d = Driver(f, length_s=600.0)      # AutoCut shows 00:14:53
        with self.assertRaises(ad.AutoCutError):
            d.silences('AUTOCUT_RAW_r07', cut=True)
        self.assertEqual(f.cuts, 0)

    def test_only_tariq_copies_can_be_cut(self):
        for name in ('GREEN_MASTER', 'FINAL_ASSEMBLY_r04_v06', 'CLEAN_CUT_r08', 'Josh edit'):
            f = FakeAutoCut()
            with self.assertRaises(ad.AutoCutError):
                Driver(f, open_seq=name).silences(name, cut=True)
            self.assertEqual(f.pressed, [])

    def test_changed_settings_stop_the_cut(self):
        def change(s):
            [i for i in s['items'] if i['type'] == 'Spinner'][2]['value'] = '120'
        f = FakeAutoCut(settings_override=change)
        with self.assertRaises(ad.AutoCutError) as e:
            Driver(f).silences('AUTOCUT_RAW_r07', cut=True)
        self.assertIn('margin_before_ms', str(e.exception))
        self.assertEqual(f.cuts, 0)

    def test_cut_waits_for_finish_and_one_log_call(self):
        f = FakeAutoCut()
        r = Driver(f).silences('AUTOCUT_RAW_r07', cut=True)
        self.assertTrue(r['cut'])
        self.assertEqual(f.cuts, 1)
        self.assertEqual(f.at, 'home')

    def test_cut_that_never_finishes_times_out(self):
        f = FakeAutoCut(finish=False)
        with self.assertRaises(ad.AutoCutError) as e:
            Driver(f).silences('AUTOCUT_RAW_r07', cut=True, timeout=30)
        self.assertIn('within', str(e.exception))

    def test_repeat_dry_run_sends_nothing(self):
        f = FakeAutoCut()
        r = Driver(f).repeat('AUTOCUT_RAW_r07', generate=False)
        self.assertEqual(r['language'], ['English'])
        self.assertNotIn(('press', 'Generate transcript'), f.pressed)
        with self.assertRaises(ad.AutoCutError):
            Driver(FakeAutoCut()).repeat('AUTOCUT_RAW_r07', generate=False, cut=True)


if __name__ == '__main__':
    unittest.main()
