import copy,io,json,os,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from studio.agent import Agents
from studio.store import Conflict
from studio.kit import content_hash
from studio.email_visual_anchors import rebase_images
from tests.test_kit_drafts import Base


class BodyVisualEdits(Base):
    def setUp(self):
        super().setUp()
        lines=['Hello,','']
        for i in range(4):
            lines += [f'Problem {i}.','','[add before]','',f'Solution {i}.','','[add after]','']
        lines+=['Keep the original closing.']
        body='\n'.join(lines)
        p=self.fresh();p=self.emails.save(p['id'],dict(body=body),p['revision'],sync_remote=False)
        positions=[i for i,line in enumerate(lines,1) if line.startswith('[add ')]
        def render(slides,folder):
            for i in range(8):Image.new('RGB',(640,360),(i*25,100,140)).save(folder/f'slide_{i+1:02d}.png')
        with patch.object(self.emails.visuals,'render',side_effect=render):
            self.original=self.emails.visuals.generate(dict(email_id=p['id'],revision=p['revision'],visuals=[dict(lines=[f'Original {i}'],width=640,height=360,alt=f'Original image {i}',after_line=n) for i,n in enumerate(positions)]))
        self.pngs=[self.emails.visuals.read_png(p['id'],im['version'],str(im['index'])) for im in self.original['images']]

    def clean(self):
        return '\n'.join(line for line in self.original['body'].split('\n') if line and not line.startswith('[add '))

    def assert_originals(self,p):
        for old,new,png in zip(self.original['images'],p['images'],self.pngs):
            for key in ('version','index','source_hash','png_hash','alt'):self.assertEqual(old[key],new[key])
            self.assertEqual(self.emails.visuals.read_png(p['id'],new['version'],str(new['index'])),png)

    def test_laia_removes_markers_and_compacts_body_preserving_eight_originals(self):
        agent=Agents.__new__(Agents)
        result=agent.apply_emails(self.emails,[dict(id=self.original['id'],body=self.clean())])
        self.assertEqual(result[0]['action'],'updated')
        saved=self.fresh();self.assertEqual(saved['body'],self.clean())
        self.assertEqual([im['after_line'] for im in saved['images']],list(range(2,10)))
        self.assert_originals(saved)
        self.assertNotEqual(content_hash(saved),content_hash(self.original))
        self.assertEqual(saved['approval_status'],'Pending final approval')
        self.assertIn(self.original,self.emails.history(saved['id']))
        self.assertEqual(saved['status'],'Draft');self.assertIsNone(saved['kit'])

    def test_stale_revision_cannot_change_body_or_images(self):
        first=self.emails.save(self.original['id'],dict(subject='A concurrent subject'),self.original['revision'],sync_remote=False)
        with self.assertRaises(Conflict):self.emails.save(first['id'],dict(body=self.clean()),self.original['revision'],sync_remote=False)
        self.assertEqual(first,self.fresh());self.assert_originals(first)

    def test_unchanged_image_payload_rebases_but_explicit_new_positions_win(self):
        p=self.emails.save(self.original['id'],dict(body=self.clean(),images=copy.deepcopy(self.original['images'])),self.original['revision'],sync_remote=False)
        self.assertEqual([im['after_line'] for im in p['images']],list(range(2,10)))
        images=copy.deepcopy(p['images'])
        for im in images:im['after_line']=1
        p=self.emails.save(p['id'],dict(body='A replacement body',images=images),p['revision'],sync_remote=False)
        self.assertEqual([im['after_line'] for im in p['images']],[1]*8);self.assert_originals(p)

    def test_ambiguous_full_rewrite_fails_without_losing_last_good(self):
        with self.assertRaisesRegex(ValueError,'explicit image placements'):
            self.emails.save(self.original['id'],dict(body='Entirely different topic'),self.original['revision'],sync_remote=False)
        self.assertEqual(self.original,self.fresh());self.assert_originals(self.fresh())

    def test_insertion_deletion_and_empty_draft_keep_valid_boundaries(self):
        images=[dict(after_line=n) for n in (0,1,3,5)]
        old='First\n\n[add before]\n\nLast'
        self.assertEqual([i['after_line'] for i in rebase_images(old,'Intro\nFirst\n\nLast',images)],[0,2,2,4])
        self.assertEqual([i['after_line'] for i in rebase_images(old,'',images)],[0]*4)

    def test_clean_preview_keeps_accessible_alt_and_collapsed_actions(self):
        p=self.emails.save(self.original['id'],dict(body=self.clean()),self.original['revision'],sync_remote=False)
        p['images'][0]['alt']='A <clear> image "description"'
        base=Path(os.environ.get('ANCHOR_JS_ROOT',Path(__file__).resolve().parents[1]))
        s=(base/'studio/web/emails.js').read_text(encoding='utf8')
        part=s[s.index('function emailValidHref'):s.index('function emailSubjectAlternatives')]
        program="const document={addEventListener(){}};const esc=s=>String(s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"+part
        program+='\nconst p='+json.dumps(p)+';console.log(JSON.stringify({html:emailLinkedBody(p).html,controls:emailVisualControls(p)}));'
        result=json.loads(subprocess.run(['node','-e',program],capture_output=True,text=True,check=True).stdout)
        self.assertEqual(result['html'].count('<figure'),8)
        for unwanted in ('<figcaption','Download PNG','download=','[add before]','[add after]'):self.assertNotIn(unwanted,result['html'])
        self.assertIn('alt="A &lt;clear&gt; image &quot;description&quot;"',result['html'])
        self.assertTrue(result['controls'].startswith('<details'))
        self.assertNotIn('<details open',result['controls']);self.assertIn('download=',result['controls'])
        for i in range(4):
            before=result['html'].index(f'Problem {i}.');after=result['html'].index(f'Solution {i}.')
            self.assertLess(before,result['html'].index('<figure',before));self.assertLess(result['html'].index('<figure',before),after)


if __name__=='__main__':unittest.main()
