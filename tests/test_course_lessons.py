import json, tempfile, time, unittest
from pathlib import Path
from unittest import mock
from studio.store import Store, Conflict
from studio.course_lessons import CourseLessons, STAGES


class FakeAgents:
    """Stands in for Saskia: returns a canned answer per task and remembers what she was asked."""
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def course_job(self, lesson, task, note, content_dir):
        self.calls.append((lesson['id'], task, note))
        answer = self.answers[task]
        answer = answer(lesson) if callable(answer) else answer
        return answer.get('reply', 'Done.'), answer


class CourseLessonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.content = self.root / 'content'
        hatt = self.content / 'Heard_at_the_Top'
        for d in ('Scripts', 'Slides', 'Lesson_Guides', 'Thumbnails'):
            (hatt / d).mkdir(parents=True)
        (hatt / 'Scripts/3.3_2026-10-19_v05_situation-complication-resolution.md').write_text('# 3.3 v05\n\n---\n\n## Hook\nOld hook.', encoding='utf-8')
        (hatt / 'Scripts/3.4_2026-10-19_v02_the-ask-slide.md').write_text('# 3.4\n\n## Hook\nAsk.', encoding='utf-8')
        (hatt / 'Slides/3.4_2026-10-20_v01_the-ask-slide_slides.html').write_text('<html></html>', encoding='utf-8')
        (hatt / 'Lesson_Guides/3.4_2026-10-31_v01_the-ask-slide_lesson-guide.pdf').write_bytes(b'%PDF-1.4')
        (hatt / 'Thumbnails/3.4_2026-10-31_v03_the-ask-slide_thumb.jpg').write_bytes(b'jpg')
        self.drive = self.root / 'drive'
        part3 = self.drive / 'Communication Track' / 'Part 3 · Building the presentation'
        part3.mkdir(parents=True)
        (part3 / '3.4 Ask slide old.mp4').write_bytes(b'old')  # an earlier upload under another name
        (part3 / '3.40 Another lesson.pdf').write_bytes(b'keep')
        patcher = mock.patch('studio.course_lessons.PUBLISH_ROOT', '')
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.root / 'settings.json').write_text(json.dumps({'course_content_dir': str(self.content)}), encoding='utf-8')
        (self.root / 'Data').mkdir()
        (self.root / 'Data/course-import.json').write_text(json.dumps({'courses': {
            'hatt': {'parts': {'3': 'Building the presentation'}, 'lessons': [
                ['3.3', 'Situation, complication, resolution', 'Lesson', 11, 'Slides', 'aaa'],
                ['3.4', 'The ask slide', 'Lesson', 9, 'Slides', 'bbb'],
                ['3.10', 'The appendix that keeps persuading', 'Lesson', 9, 'Not started', 'ccc']]},
            'data': {'parts': {'1': 'Why good numbers'}, 'lessons': [['1.2', 'The prompt pack', 'Lesson', 8, 'Not started', '']]}}}),
            encoding='utf-8')
        self.store = Store(self.root / 'Data')
        self.c = CourseLessons(self.store, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def wait(self, lid):
        for _ in range(200):
            t = self.c.jobs.get(lid)
            if not t or not t.is_alive():
                return
            time.sleep(0.02)

    def test_import_reads_stages_from_the_files(self):
        items = {l['number']: l for l in self.c.all()['items']}
        self.assertEqual(items['3.3']['stage'], 'Approved')           # script, no slides, Notion said Slides
        self.assertEqual(items['3.4']['stage'], 'Ready to record')    # slides built, nothing recorded
        self.assertEqual(items['3.10']['stage'], 'Planned')           # no script yet
        self.assertEqual(items['1.2']['id'], 'data-1.2')
        self.assertTrue(items['3.4']['has']['slides'] and items['3.4']['has']['guide'] and items['3.4']['has']['thumbnail'])
        self.assertEqual([l['number'] for l in self.c.all()['items']][:3], ['3.3', '3.4', '3.10'])
        l = self.c.get('aaa')
        self.assertIn('Old hook.', l['script'])
        self.assertEqual(l['approved']['by'], 'import')
        self.assertEqual(l['neighbours']['next']['number'], '3.4')
        # Importing runs once. A second start keeps Josh OS as the record.
        CourseLessons(self.store, self.root)
        self.assertEqual(len(self.c.all()['items']), 4)

    def test_changed_script_goes_back_for_approval_and_approving_writes_a_new_version(self):
        l = self.c.get('aaa')
        l = self.c.save('aaa', {'script': l['script'] + '\nNew line.'}, l['revision'])
        self.assertEqual(l['stage'], 'Script review')
        with self.assertRaises(ValueError):
            self.c.move('aaa', 'Slides', l['revision'])
        with self.assertRaises(ValueError):
            self.c.start('aaa', 'slides')  # no agents yet, but approval comes first either way
        with self.assertRaises(Conflict):
            self.c.save('aaa', {'notes': 'x'}, l['revision'] - 1)
        l = self.c.approve('aaa', l['revision'])
        self.assertEqual(l['stage'], 'Approved')
        scripts = self.content / 'Heard_at_the_Top/Scripts'
        new = [f.name for f in scripts.iterdir() if f.name.startswith('3.3_')]
        self.assertEqual(len(new), 1)
        self.assertIn('_v06_situation-complication-resolution.md', new[0])
        self.assertTrue((self.content / 'Heard_at_the_Top/Archive/Scripts/3.3_2026-10-19_v05_situation-complication-resolution.md').is_file())
        self.assertIn('New line.', (scripts / new[0]).read_text(encoding='utf-8'))
        # Approving again with nothing changed writes nothing new.
        self.c.approve('aaa', l['revision'])
        self.assertEqual(len([f for f in scripts.iterdir() if f.name.startswith('3.3_')]), 1)

    def test_stage_gates(self):
        l = self.c.get('aaa')
        with self.assertRaises(ValueError) as e:
            self.c.move('aaa', 'Ready to record', l['revision'])
        self.assertIn('slide file', str(e.exception))
        with self.assertRaises(ValueError):
            self.c.move('aaa', 'Published', l['revision'])
        b = self.c.get('bbb')
        with self.assertRaises(ValueError) as e:
            self.c.move('bbb', 'Ready to publish', b['revision'])
        self.assertIn('the video', str(e.exception))
        self.assertEqual(self.c.move('bbb', 'Approved', b['revision'])['stage'], 'Approved')  # going back is always fine
        self.assertEqual(set(STAGES[:3]), {'Planned', 'Preparing', 'Drafting'})

    def test_publish_copies_video_guide_and_thumbnail_with_details_for_lovable(self):
        b = self.c.get('bbb')
        with self.assertRaises(ValueError):
            self.c.publish('bbb')  # no Drive folder yet
        with self.assertRaises(ValueError):
            self.c.set_publish_root(str(self.root / 'nowhere'))
        self.c.set_publish_root(str(self.drive))
        with self.assertRaises(ValueError) as e:
            self.c.publish('bbb')
        self.assertIn('the video', str(e.exception))
        video = self.root / 'take01.mp4'
        video.write_bytes(b'video')
        b = self.c.save('bbb', {'video': str(video)}, b['revision'])
        self.c.move('bbb', 'Ready to publish', b['revision'])
        self.c.publish('bbb')
        self.wait('bbb')
        b = self.c.get('bbb')
        self.assertEqual((b['stage'], b['publish']['status']), ('Published', 'done'))
        folder = self.drive / 'Communication Track' / 'Part 3 · Building the presentation'
        files = sorted(f.name for f in folder.iterdir() if f.is_file())
        self.assertEqual(files, ['3.4 The ask slide.mp4', '3.4 The ask slide.pdf', '3.40 Another lesson.pdf'])
        archived = [f for f in folder.iterdir() if f.is_dir()]
        self.assertEqual([f.name for f in archived[0].iterdir()], ['3.4 Ask slide old.mp4'])
        self.assertTrue((self.drive / 'Communication Track' / 'Thumbnails' / '3.4_2026-10-31_v03_the-ask-slide_thumb.jpg').is_file())
        self.assertEqual(b['publish']['folder'], str(folder))

    def test_saskia_prepares_drafts_and_builds_slides(self):
        slides = self.content / 'Heard_at_the_Top/Slides/3.3_2026-11-08_v01_situation-complication-resolution_slides.html'
        def build(lesson):
            slides.write_text('<html></html>', encoding='utf-8')
            return dict(slides_path=str(slides), beats=40, cued_script='## Hook\n**CHANGE 1** Old hook.')
        self.c.agents = FakeAgents({'prepare': dict(prep='## The one key point\nEach part has a job.'),
                                    'draft': dict(script='# 3.3 v06\n\n---\n\n## Hook\n' + 'A new hook line. ' * 40),
                                    'slides': build})
        self.c.check_slides = lambda lid, path=None: dict(ok=True, beats=40, problems=[], errors=[], sheets=[])
        l = self.c.get('ccc')
        with self.assertRaises(ValueError):
            self.c.start('ccc', 'draft')  # nothing to draft from yet
        self.assertEqual(self.c.start('ccc', 'prepare')['stage'], 'Preparing')
        self.wait('ccc')
        l = self.c.get('ccc')
        self.assertEqual((l['stage'], l['job']['status']), ('Drafting', 'done'))
        self.assertIn('Each part has a job', l['prep'])
        self.c.start('ccc', 'draft', 'Keep it short')
        self.wait('ccc')
        l = self.c.get('ccc')
        self.assertEqual(l['stage'], 'Script review')
        self.assertIn('A new hook line.', l['script'])
        self.assertEqual(self.c.agents.calls[-1], ('ccc', 'draft', 'Keep it short'))
        # Slides for 3.3: approved by the import, so Saskia can build them.
        self.c.start('aaa', 'slides')
        self.wait('aaa')
        a = self.c.get('aaa')
        self.assertEqual((a['stage'], a['job']['status'], a['cues']), ('Ready to record', 'done', True), a['job'])
        self.assertIn('CHANGE 1', self.c.cues('aaa'))

    def test_saskia_adds_renames_and_starts_work_from_chat(self):
        import threading
        from studio.agent import Agents
        gate = threading.Event()
        def slow(lesson):
            gate.wait(5)
            return dict(prep='## Notes. The whole offer.')
        self.c.agents = FakeAgents({'prepare': slow})
        agents = Agents.__new__(Agents)
        done = agents.apply_lessons(self.c, [
            dict(course='hatt', number='3.5', title='Negotiating the offer', start='prepare', start_note='Cover the whole offer'),
            dict(lesson_id='aaa', title='Situation, complication and resolution'),
            dict(lesson_id='bbb', start='prepare'),
            dict(lesson_id='ccc', start='prepare'),
            dict(lesson_id='bbb', stage='Published'),
            dict(course='hatt', number='3.4', title='Twice')])
        actions = [(d['action'], d['title']) for d in done]
        new = done[0]['id']
        self.assertEqual(actions[0], ('added', '3.5 Negotiating the offer'))
        self.assertIn(('started', '3.5 Negotiating the offer'), actions)
        self.assertIn(('updated', '3.3 Situation, complication and resolution'), actions)
        self.assertEqual([d['action'] for d in done[-2:]], ['failed', 'failed'])
        added = self.c.get(new)
        self.assertEqual((added['part_name'], added['stage']), ('Building the presentation', 'Preparing'))
        # Two jobs run at a time, so the third waits its turn.
        for _ in range(100):
            if any((self.c.get(x)['job'] or {}).get('waiting') for x in (new, 'bbb', 'ccc')):
                break
            time.sleep(0.02)
        self.assertEqual(sum(bool(self.c.get(x)['job'].get('waiting')) for x in (new, 'bbb', 'ccc')), 1)
        gate.set()
        for x in (new, 'bbb', 'ccc'):
            self.wait(x)
        self.assertEqual({self.c.get(x)['job']['status'] for x in (new, 'bbb', 'ccc')}, {'done'})
        self.assertIn((new, 'prepare', 'Cover the whole offer'), self.c.agents.calls)

    def test_a_lesson_already_in_the_course_app_can_be_marked_live(self):
        from studio.agent import Agents
        with self.assertRaises(ValueError):
            self.c.move('aaa', 'Published', None)
        done = Agents.__new__(Agents).apply_lessons(self.c, [dict(lesson_id='aaa', stage='Published')])
        self.assertEqual([(d['action'], d.get('stage')) for d in done], [('moved', 'Published')])
        l = self.c.get('aaa')
        self.assertEqual((l['stage'], l['publish']['status'], l['publish']['by']), ('Published', 'elsewhere', 'Saskia'))
        self.assertIn('Already live', l['history'][-1]['why'])
        self.assertEqual(self.c.mark_live('aaa')['stage'], 'Published')
        with self.assertRaises(ValueError):
            self.c.open_path('aaa', 'published')

    def test_a_slide_file_outside_the_course_folder_is_refused(self):
        stray = self.root / '3.3_2026-11-08_v01_x_slides.html'
        stray.write_text('<html></html>', encoding='utf-8')
        self.c.agents = FakeAgents({'slides': dict(slides_path=str(stray), cued_script='x')})
        self.c.start('aaa', 'slides')
        self.wait('aaa')
        a = self.c.get('aaa')
        self.assertEqual((a['job']['status'], a['stage']), ('failed', 'Approved'))
        self.assertIn('should be in', a['job']['message'])


if __name__ == '__main__':
    unittest.main()
