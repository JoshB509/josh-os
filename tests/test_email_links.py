import json
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

from studio.agent import Agents, EMAIL_PROPS
from studio.kit import body_html
from studio.kit_drafts import GET, UPDATE
from studio.kit_draft_guard import decision
from studio.team import Team
from tests.test_kit_drafts import Base, AUD, confirmed

URL = 'https://linklr.app/r/fxxvmuga?ksid={{ subscriber.id }}'
BODY = "You can see what's inside here."
LINKS = [{'label': "see what's inside", 'href': URL}]


class Parsed(HTMLParser):
    def __init__(self, html):
        super().__init__();self.links=[];self.text=[];self.active=None;self.feed(html)
    def handle_starttag(self, tag, attrs):
        if tag=='a':self.active=[dict(attrs)['href'],''];self.links.append(self.active)
    def handle_endtag(self, tag):
        if tag=='a':self.active=None
    def handle_data(self, data):
        self.text.append(data)
        if self.active is not None:self.active[1]+=data


class EmailLinkTests(Base):
    def test_saved_source_chat_and_kit_roundtrip(self):
        agents=Agents(self.store,self.root,Team(self.store))
        result=agents.apply_emails(self.emails,[dict(id=self.email['id'],body=BODY,links=LINKS)])
        e=self.fresh()
        self.assertEqual(e['links'],LINKS,result)
        parsed=Parsed(body_html(e))
        self.assertEqual(parsed.links,[[URL,"see what's inside"]])
        self.assertEqual(''.join(parsed.text),BODY)
        self.assertNotIn(URL,e['body'])
        self.assertEqual(EMAIL_PROPS['emails']['items']['properties']['links']['type'],'array')
        self.assertEqual(max(self.emails.history(e['id']),key=lambda row:row['revision'])['links'],LINKS)

    def test_multiple_labels_on_one_line_and_safe_escaping(self):
        e=dict(body='A & <B> and another link.',links=[{'label':'A & <B>','href':'https://example.test/?x=1&y=2'},
                                                    {'label':'another link','href':URL}])
        parsed=Parsed(body_html(e))
        self.assertEqual(parsed.links, [['https://example.test/?x=1&y=2','A & <B>'],[URL,'another link']])
        self.assertEqual(''.join(parsed.text),e['body'])
        self.assertNotIn('<B>',body_html(e))

    def test_missing_ambiguous_overlapping_or_unsafe_links_are_rejected(self):
        for e in (dict(body='Other text',links=LINKS),dict(body=BODY+' '+BODY,links=LINKS),
                  dict(body=BODY,links=[dict(LINKS[0],href='javascript:alert(1)')]),
                  dict(body=BODY,links=[LINKS[0],{'label':"what's inside",'href':URL}])):
            with self.assertRaises(ValueError):body_html(e)

    def test_saving_changed_wording_requires_matching_label(self):
        e=self.emails.save(self.email['id'],dict(body=BODY,links=LINKS),self.email['revision'])
        with self.assertRaises(ValueError):self.emails.save(e['id'],{'body':'Different CTA'},e['revision'])
        self.assertEqual(self.fresh()['revision'],e['revision'])

    def test_bare_urls_are_not_given_invented_labels(self):
        parsed=Parsed(body_html({'body':'Read '+URL}))
        self.assertEqual(parsed.links,[[URL,URL]])

    def test_browser_preview_and_export_preserve_label_destination(self):
        source=Path(__file__).resolve().parents[1]/'studio/web/emails.js'
        js=source.read_text(encoding='utf-8')
        part=js[js.index('function emailValidHref'):js.index('function emailSubjectAlternatives')]
        program="const esc=s=>String(s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));\n"+part
        program+='\nconst p='+json.dumps(dict(body=BODY,links=LINKS,subject='S',preview='P'))+';console.log(JSON.stringify({preview:emailLinkedBody(p),export:emailExportHTML(p)}));'
        out=subprocess.run(['node','-e',program],capture_output=True,text=True,check=True)
        data=json.loads(out.stdout)
        self.assertTrue(data['preview']['valid'])
        for rendered in (data['preview']['html'],data['export']):
            self.assertEqual(Parsed(rendered).links,[[URL,"see what's inside"]])
        self.assertIn("'links'",js.split('const emailFields=')[1].split(';')[0])

    def test_content_only_update_guards_baseline_and_preserves_metadata(self):
        self.live();d=self.drafts.propose(self.fresh(),AUD);self.drafts.approve(d['id'],d['hash'],self.fresh())
        self.drafts.write(d['id'],self.fresh(),lambda *a,**k:('ok',confirmed(k['guard'])))
        baseline=dict(d['payload'],id=555,status='draft',send_at=None,published_at=None,public_url=None,
                      email_address='sender@example.test',email_template={'id':42})
        e=self.fresh();e=self.emails.save(e['id'],dict(body=BODY,links=LINKS),e['revision'])
        edit=self.drafts.propose(e,AUD,baseline=baseline,content_only=True)
        self.drafts.approve(edit['id'],edit['hash'],e)
        policy=self.drafts.guard_policy(edit)
        self.assertEqual(set(policy['write']['input']),{'broadcast_id','content','public'})
        event=dict(tool_name=UPDATE,tool_input=policy['write']['input'])
        receipt=dict(tool_name=GET,tool_input={'id':555},tool_response={'broadcast':baseline})
        self.assertTrue(decision(event,policy,receipts=[receipt])[0])
        changed=dict(receipt,tool_response={'broadcast':dict(baseline,preview_text='Concurrent edit')})
        self.assertFalse(decision(event,policy,receipts=[changed])[0])
        def run(*a,**k):
            b=dict(baseline,content=k['guard']['write']['input']['content'])
            return 'ok',dict(_kit_receipts=[dict(tool_name=UPDATE,tool_input=k['guard']['write']['input'],tool_response={'broadcast':b}),
                                          dict(tool_name=GET,tool_input={'id':555},tool_response={'broadcast':b})])
        self.assertEqual(self.drafts.write(edit['id'],e,run)['status'],'drafted')


if __name__=='__main__':unittest.main()
