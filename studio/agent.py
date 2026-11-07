"""Runs the team members for real, through Claude Code on this PC.

Each request builds the team member's instructions from three places: their profile on
the Team page, the skills they own (in .claude/skills, the same files Claude Code uses)
and the team's lessons learned. Claude Code is called in print mode with read-only access
to the reference library, so it can look things up (the Ed Lawrence movement library,
curiosity lines, audience notes) without being able to change anything. Replies come back
as structured JSON, and nothing is applied to a script until Josh clicks Replace.

Conversations are kept per video and per team member, so they can be picked up from the
phone or the desktop.
"""
import json
import socket
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from .store import now

SKILLS = {'sam': ['script-touch-up'], 'tariq': ['video-editing-prep'], 'laia': [], 'saskia': ['lesson-slides', 'careeros-playbook'], 'max': []}
REFERENCES = 'reference_sources'
TIMEOUT = 300
SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string', 'description': 'What you say back to Josh, in your own voice, short.'},
        'replacement': {'type': ['string', 'null'],
                        'description': 'The rewritten text for the selected passage (or the whole script when asked), '
                                       'ready to paste in. Null when you are only answering.'},
    },
    'required': ['reply', 'replacement'],
}
ALLOWED_TOOLS = 'Read,Grep,Glob'
# Team members who may look things up in Josh's Chrome from their chat (YouTube Studio, community posts, Linklr).
CHROME = {'sam'}
# Linklr (tracked links and stats) through its MCP server, added to Claude Code on this PC as "linklr".
LINKLR_READ = ('get_data_status', 'get_business_summary', 'get_daily_metrics', 'get_acquisition_by_source',
               'get_email_sequence_funnel', 'get_signup_cohorts', 'get_course_buyer_journeys', 'lookup_link_context',
               'preview_tracked_link')
LINKLR_MAKE = ('register_video', 'create_tracked_link')
LINKLR = {'sam': LINKLR_READ + LINKLR_MAKE, 'laia': LINKLR_READ + LINKLR_MAKE, 'max': LINKLR_READ}
CHROME_DEVICE = 'd791a792-0ea7-4d51-90d5-a9fbf4144b3a'  # Chrome on DESKTOP-A4TRTK5
CHROME_TOOLS = ','.join('mcp__claude-in-chrome__' + t for t in (
    'list_connected_browsers', 'select_browser', 'switch_browser', 'tabs_context_mcp', 'tabs_create_mcp',
    'tabs_close_mcp', 'navigate', 'read_page', 'get_page_text', 'find', 'computer', 'browser_batch', 'javascript_tool',
    'form_input', 'file_upload', 'upload_image'))
DESK_SCHEMA = {
    'type': 'object',
    'properties': {'reply': {'type': 'string', 'description': 'What you say back to Josh, in your own voice.'}},
    'required': ['reply'],
}
# Team members who write the emails can save drafts and send dates from chat.
EMAIL_WRITE = ('subject', 'preview', 'body', 'planned_date', 'send_time', 'notes', 'description')
EMAIL_PROPS = {
    'emails': {
        'type': 'array',
        'description': 'Emails to add or change in Josh OS. Leave empty when nothing needs saving.',
        'items': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'Id of an existing email to change. Leave out to add a new draft.'},
            **{k: {'type': 'string'} for k in EMAIL_WRITE}}},
    },
}
# Every chat answer can also note which of Josh's edits it covered, and save lessons.
LEARN_PROPS = {
    'edits_noted': {'type': 'array', 'items': {'type': 'string'},
                    'description': "Ids of Josh's edits you asked about or explained in this reply."},
    'lessons': {'type': 'array', 'description': 'Rules you now know for next time, from his edits or his answers.',
                'items': {'type': 'object', 'properties': {'lesson': {'type': 'string'}, 'evidence': {'type': 'string'},
                                                           'workflow': {'type': 'string'}}, 'required': ['lesson']}},
}
# Team members who keep the carousel tracker can save to it from chat.
CAROUSEL_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string', 'description': 'What you say back to Josh, in your own voice.'},
        'carousels': {
            'type': 'array',
            'description': 'Carousels to add or change in Josh OS. Leave empty when nothing needs saving.',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string', 'description': 'Id of an existing carousel to change. Leave out to add a new one.'},
                    'title': {'type': 'string'}, 'stage': {'type': 'string'}, 'post_date': {'type': 'string'},
                    'hook': {'type': 'string'}, 'slides': {'type': 'string'}, 'caption': {'type': 'string'},
                    'notes': {'type': 'string'}, 'link': {'type': 'string'}, 'folder': {'type': 'string'},
                    'tags': {'type': 'object'}, 'results': {'type': 'object'},
                    'make_design': {'type': 'boolean', 'description': 'True to make the Paper slide images again from the slides.'},
                },
            },
        },
        'inspo': {
            'type': 'array',
            'description': 'Creator posts (Ali Abdaal, Alex Hormozi) to add or change in Josh OS. Leave empty when nothing needs saving.',
            'items': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string', 'description': 'Id of a saved creator post to change. Leave out to add a new one.'},
                    'creator': {'type': 'string'}, 'date': {'type': 'string'}, 'url': {'type': 'string'},
                    'hook': {'type': 'string'}, 'caption': {'type': 'string'}, 'slides': {'type': 'integer'},
                    'likes': {'type': 'integer'}, 'comments': {'type': 'integer'}, 'checked_at': {'type': 'string'},
                    'tags': {'type': 'object'}, 'look': {'type': 'string'}, 'new_thing': {'type': 'boolean'},
                    'new_note': {'type': 'string'}, 'notes': {'type': 'string'},
                    'image_urls': {'type': 'array', 'items': {'type': 'string'},
                                   'description': 'Full-size slide image links, in order, for a new post. Josh OS saves them to the E drive.'},
                },
            },
        },
    },
    'required': ['reply'],
}
DESK = 'desk'  # thread key for the general chat on a team member's page

# Training files each team member works from. The folder can be moved with "training_dir" in settings.json.
TRAINING_DIR = r'E:\CareerOS\_Agent-Training'
TRAINING = {
    'sam': {
        'embed': ['README.md', '01_house-style.md', '07_carousel-queue.md', '06_tracking-and-learning.md'],
        'read': ['02_carousels_lead-magnet-engine.md', '03_carousels_value-posts.md', '04_youtube-scheduling.md', '05_thumbnails.md'],
        'dirs': ['03_Community_Posts', '12_YouTube'],  # folders next to the training folder Sam may read
    },
    # Saskia reads the course content and programme downloadables, and any saskia_*.md training files Josh adds.
    'saskia': {
        'embed': ['01_house-style.md'],
        'read': [],
        'dirs': ['04_Programme', '08_Strategy'],
    },
}


PERSONALITIES = Path('team') / 'personalities.md'
SHARED_SECTIONS = ('purpose', '5.', '6.', '7.')


def personality(root, agent):
    """The Purpose, this person's own section and the shared sections of team/personalities.md."""
    file = Path(root) / PERSONALITIES
    if not file.is_file():
        return ''
    sections, head, body = [], None, []
    for line in file.read_text(encoding='utf-8').splitlines():
        if line.startswith('## '):
            if head is not None:
                sections.append((head, body))
            head, body = line[3:].strip(), []
        elif head is not None:
            body.append(line)
    if head is not None:
        sections.append((head, body))
    names = {agent['id'].lower(), str(agent.get('name', '')).lower()}
    def wanted(h):
        h = h.lower()
        title = h.split('.', 1)[-1].strip() if h[:1].isdigit() and '.' in h else h
        return title in names or h.startswith(SHARED_SECTIONS)
    picked = [f'## {h}\n' + '\n'.join(b).strip() for h, b in sections if wanted(h)]
    return '\n\n'.join(picked)


class AgentError(RuntimeError):
    pass


def find_claude(root):
    """Claude Code on this PC: settings.json "claude" wins, then PATH, then the usual installs."""
    try:
        configured = json.loads((Path(root) / 'settings.json').read_text(encoding='utf-8-sig')).get('claude')
    except (OSError, ValueError):
        configured = None
    candidates = [configured] if configured else []
    candidates += [shutil.which('claude')]
    home = Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or '~').expanduser()
    candidates += [home / '.local' / 'bin' / 'claude.exe', home / '.local' / 'bin' / 'claude',
                   Path(os.environ.get('APPDATA', '')) / 'npm' / 'claude.cmd']
    # The copy bundled with the Claude desktop app, newest version first.
    data = Path(os.environ.get('LOCALAPPDATA', '')) / 'Claude-Data'
    if data.is_dir():
        candidates += sorted(data.glob('*/claude-code/*/*/claude.exe'), key=lambda f: f.stat().st_mtime, reverse=True)
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    return None


class Agents:
    def __init__(self, store, root, team):
        self.store, self.root, self.team = store, Path(root), team
        self.locks = {}
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS agent_threads(project_id TEXT NOT NULL,agent_id TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(project_id,agent_id))')

    # Conversation history ---------------------------------------------------
    def thread(self, project_id, agent_id):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM agent_threads WHERE project_id=? AND agent_id=?', (project_id, agent_id)).fetchone()
        return json.loads(row['body']) if row else []

    def _append(self, project_id, agent_id, *messages):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM agent_threads WHERE project_id=? AND agent_id=?', (project_id, agent_id)).fetchone()
            items = (json.loads(row['body']) if row else []) + list(messages)
            items = items[-80:]
            c.execute('INSERT OR REPLACE INTO agent_threads VALUES(?,?,?)', (project_id, agent_id, json.dumps(items)))
        return items

    def status(self):
        path = find_claude(self.root)
        return dict(available=bool(path), path=path or '',
                    help='' if path else 'Install Claude Code on this PC and sign in, or set "claude" in settings.json to its full path.')

    # Building the prompt ----------------------------------------------------
    def chrome_device(self):
        """The Claude in Chrome device ID for this PC's Chrome. "chrome_device" in settings.json can change it."""
        try:
            configured = json.loads((self.root / 'settings.json').read_text(encoding='utf-8-sig')).get('chrome_device')
        except (OSError, ValueError):
            configured = None
        return configured or CHROME_DEVICE

    def training_dir(self):
        try:
            configured = json.loads((self.root / 'settings.json').read_text(encoding='utf-8-sig')).get('training_dir')
        except (OSError, ValueError):
            configured = None
        return Path(configured or TRAINING_DIR)

    def training(self, agent_id):
        """The text of the team member's training files, and the folders they may read."""
        spec = TRAINING.get(agent_id)
        if not spec:
            return '', []
        folder = self.training_dir()
        parts = []
        for name in spec['embed']:
            f = folder / name
            if f.is_file():
                parts += ['', f'=== Training: {name} ===', f.read_text(encoding='utf-8')]
        extra = [n for n in spec['read'] if (folder / n).is_file()]
        extra += sorted(f.name for f in folder.glob(f'{agent_id}_*.md') if f.name not in extra) if folder.is_dir() else []
        if extra:
            parts += ['', f'More training files in {folder} (read them when the question needs them): ' + ', '.join(extra)]
        dirs = [str(folder)] + [str(folder.parent / d) for d in spec['dirs'] if (folder.parent / d).is_dir()]
        return '\n'.join(parts), [d for d in dirs if Path(d).is_dir()]

    def skill_text(self, name):
        file = self.root / '.claude' / 'skills' / name / 'SKILL.md'
        return file.read_text(encoding='utf-8') if file.is_file() else ''

    def roster(self, agent):
        """Who else is on the team, so people refer work to each other by name."""
        others = [a for a in self.team.all()['agents'] if a['id'] != agent['id']]
        return 'The rest of the team: ' + '; '.join(f"{a['name']} ({a['role']}{', starts later' if a['status'] == 'Coming later' else ''})" for a in others) + '.'

    def system_prompt(self, agent, learnings):
        lines = [
            f"You are {agent['name']}, {agent['role']} on Josh's team at CareerOS. You work inside Josh OS, his studio app.",
            f"Your mission: {agent['mission']}",
            f"Your personality: {agent['personality']}",
            'What you are responsible for:', *[f'- {r}' for r in agent['responsibilities']],
            self.roster(agent),
            '',
            'How to work with Josh:',
            '- Be yourself, as described in "Who you are" below. Talk like his colleague, keep it short, and give your view.',
            '  Only ask a question when you need a decision or a missing piece to move the work on.',
            '- Write chat replies the way you would message a colleague. Plain sentences, no headings and no bold.',
            '  Use a short list only when you are naming three or more separate things. Most replies are under 120 words.',
            '- When something belongs to someone else on the team, name them. Never claim you have spoken to them.',
            '- When he selects a passage and asks for a change, put the full rewritten passage in "replacement" so he can drop it in.',
            '  Keep anything he did not ask you to change. Match his formatting, including any # headings.',
            '- When he asks a question or wants your view, answer in "reply" and leave "replacement" null.',
            '- Follow your skills below exactly. They are Josh\'s own rules and they override general writing habits.',
            f'- Reference files the skills mention live in ./{REFERENCES}/ (for example ed-lawrence-movement-library.md,',
            '  curiosity lines, audience-communication.md, magnet-index.md, parker-checkout-and-bumps.md). Read them when you need them.',
            '- Never claim a client result, number or fact you cannot see in the material. Put it in [square brackets] for Josh to check.',
            '- Most of Josh\'s feedback comes in chat. When he asks you to change your work ("make the hook punchier",',
            '  "cut that line", "less salesy"), make the change, then work out the preference behind it. If it would apply to',
            '  future work, save it in lessons as a rule. If you can\'t tell whether it was a one-off, ask in one short line, like',
            '  "Want me to do that every time, or just here?" or "Was that to keep it shorter?". Save the rule once he answers.',
            '- When a rule is clear, save it in lessons as one short instruction, with his words as evidence, and tell him you noted it.',
        ]
        persona = personality(self.root, agent)
        if persona:
            lines += ['', '=== Who you are (team/personalities.md) ===', persona]
        if learnings:
            lines += ['', 'What the team has learned so far (apply it):', *[f"- {l['lesson']}" + (f" ({l['evidence']})" if l.get('evidence') else '') for l in learnings]]
        for name in SKILLS.get(agent['id'], []):
            text = self.skill_text(name)
            if text:
                lines += ['', f'=== Skill: {name} ===', text]
        style = self.training_dir() / '01_house-style.md'
        if agent['id'] in TRAINING and style.is_file():
            lines += ['', '=== Training: 01_house-style.md ===', style.read_text(encoding='utf-8')]
        return '\n'.join(lines)

    def desk_system_prompt(self, agent, learnings):
        training, _ = self.training(agent['id'])
        lines = [
            f"You are {agent['name']}, {agent['role']} on Josh's team at CareerOS. You work inside Josh OS, his studio app.",
            f"Your mission: {agent['mission']}",
            f"Your personality: {agent['personality']}",
            'What you are responsible for:', *[f'- {r}' for r in agent['responsibilities']],
            f"Your check-in: {agent['check_in']}",
            self.roster(agent),
            '',
            'This is the chat on your own page in Josh OS. Josh comes here to ask what you are working on, talk through plans and ideas,',
            'ask about results, and give you direction.',
            '- Be yourself, as described in "Who you are" below. Keep replies short and plain, and give your view.',
            '- Write chat replies the way you would message a colleague. Plain sentences, no headings and no bold.',
            '  Use a short list only when you are naming three or more separate things. Most replies are under 120 words.',
            '- When something belongs to someone else on the team, name them. Never claim you have spoken to them.',
            '- Use the snapshot of your work below and your training. Read files when you need detail. Never make up a number,',
            '  a post or a result you cannot see. Say what you would need to check instead.',
            *(["- You can use Josh's Chrome to do your work, such as YouTube Studio, posting and scheduling carousels on the community tab,",
               '  scheduling videos, and reading stats on YouTube and Linklr. Use the carousel files on the E drive for uploads.',
               f"  Use the Chrome on this PC ({socket.gethostname()}), which is the connected browser with device ID {self.chrome_device()}.",
               '  Select it with select_browser straight away without asking Josh. Open a new tab for your work and close it when you are done.',
               '  When Josh asks you to post or schedule something, do it. If he did not say which post, file or time, ask first.',
               '  Never delete anything or change channel or account settings unless he asks for that exact change. Tell him what you did.',
               '  If a page needs a sign-in, ask Josh to sign in himself.'] if agent['id'] in CHROME else []),
            *(['- You have Linklr through its tools (mcp__linklr__...). Use them for stats such as clicks, new subscribers by source,',
               '  sequence funnels and the business summary. Check get_data_status when freshness matters.',
               *(['- Make tracked links with create_tracked_link, after lookup_link_context and preview_tracked_link.',
                  '  Use an existing link when preview finds one. Register a video with register_video before making its links.']
                 if 'create_tracked_link' in LINKLR.get(agent['id'], ()) else []),
               '- If a Linklr tool says it needs authentication, tell Josh to sign in to Linklr in Claude Code once.']
              if agent['id'] in LINKLR else []),
            *(self.carousel_rules() if 'carousels' in (agent.get('workflows') or []) else []),
            *(['- The Email page in Josh OS is the record for every email. Save to it with the "emails" list in your answer. Each',
               '  entry changes an email when you give its id from the snapshot, or adds a new draft without one. You can change the',
               '  subject, preview, body, planned_date (YYYY-MM-DD), send_time (HH:MM, Dubai), notes and description. Josh approves',
               '  emails himself, so never change their status. Keep {{ subscriber.first_name }} and link wording as they are.',
               '  When you re-plan dates, spread the emails across open days from tomorrow and tell Josh the new plan.',
            '  When Josh asks you to write or rewrite an email, do it now and save it, rather than promising it for a later run.']
              if 'email' in (agent.get('workflows') or []) and getattr(self, 'emails', None) is not None else []),
            *(['- You cannot edit or run files from this chat, but you can change your cards and redo designs as described above.',
               '  Do the work now in this chat rather than promising it for a later run.']
              if agent['id'] in CHROME or 'email' in (agent.get('workflows') or []) else
              ['- From this chat you can read but not change anything. Your scheduled tasks on this PC do the making, scheduling and tracking.',
               '  When Josh asks for something to be done, say what you will do on your next run, or what he needs to change.']),
            "- Josh's edits to your work are how he teaches you. When your prompt lists changes he made, study what he changed",
            '  and match it from now on. If the reason is clear, say in a line what you will do next time. If it is not, ask about it',
            '  in plain words, like "Why did you change this line? Was it to sound less salesy?". Never ask about typo fixes.',
            '  Put the ids of edits you covered in edits_noted.',
            '- Most of Josh\'s feedback comes in chat. When he asks you to change your work ("make the hook punchier",',
            '  "cut that line", "less salesy"), make the change, then work out the preference behind it. If it would apply to',
            '  future work, save it in lessons as a rule. If you can\'t tell whether it was a one-off, ask in one short line, like',
            '  "Want me to do that every time, or just here?" or "Was that to keep it shorter?". Save the rule once he answers.',
            '- When a rule is clear, from an edit or from what Josh tells you, save it in lessons as one short instruction you can',
            '  follow next time, with the edit or his words as evidence. Tell him you have noted it. Do not save the same lesson twice.',
            '- Write at a grade 12 reading level. No em dashes. Never use the words quiet, quietly, land, landed, honest or honestly.',
            '  No "it isn\'t X, it\'s Y" framing, no colons that split a sentence, no fragment lists, and don\'t say why something matters.',
        ]
        persona = personality(self.root, agent)
        if persona:
            lines += ['', '=== Who you are (team/personalities.md) ===', persona]
        if learnings:
            lines += ['', 'What the team has learned so far (apply it):', *[f"- {l['lesson']}" + (f" ({l['evidence']})" if l.get('evidence') else '') for l in learnings]]
        if training:
            lines += ['', 'Your training follows. It is Josh\'s own process and it overrides general habits.', training]
        return '\n'.join(lines)

    def carousel_rules(self):
        from .carousels import TAG_OPTIONS, TAG_TEXT, TAG_FLAGS, RESULT_NUMBERS, STAGES
        return [
            '- The Carousels page in Josh OS is the record for every carousel, its tags and its results. Save to it with the',
            '  "carousels" list in your answer. Each entry adds a carousel, or changes one when you give its id from the snapshot.',
            '  When you draft carousels, save each one there as a Drafting card with hook, slides (a line of three dashes between',
            '  slides), caption and tags. When you post or schedule one, set the stage, post_date and link. When you read stats,',
            '  save them in results. Never invent a number. Leave out anything you did not check.',
            f"  Stages: {', '.join(STAGES)}. Folder is the slide images folder under E:\\CareerOS\\03_Community_Posts.",
            '  Tags use these exact values: ' + '; '.join(f"{k} = {' / '.join(v)}" for k, v in TAG_OPTIONS.items()) + '.',
            f"  Free text tags: {', '.join(sorted(TAG_TEXT))} (post_time like 21:00). True or false tags: {', '.join(sorted(TAG_FLAGS))}. slide_count is a whole number.",
            f"  results has d1 and d7, each with any of {', '.join(RESULT_NUMBERS)} and checked_at (a date).",
            '  Tags and results you send are merged into what is already saved. Tell Josh in your reply what you saved.',
            '- To make or redo a Paper post\'s slide images, set make_design true on that carousel (with any slide changes in the',
            '  same entry). Josh OS draws them with the house template. You cannot edit render.py or any other file. If Josh wants',
            '  the look itself changed (colours, sizes, layout), say what should change and that it needs a change to the template',
            '  in Josh OS, which Josh can ask Claude Code to make.',
            '- The Creators tab on the same page holds the carousels we learn from (Ali Abdaal and Alex Hormozi). Save to it with',
            '  the "inspo" list. Each entry adds a post, or changes one when you give its id from the snapshot.',
            '  To add new posts, open the creator\'s Posts tab in Chrome (youtube.com/@aliabdaal/posts, youtube.com/@AlexHormozi/posts),',
            '  open each carousel post, and send creator, date, url, hook (the first line of the caption), caption, slides, likes',
            '  (as a whole number, so 1.5K is 1500), comments, checked_at, and image_urls with the full-size slide images in order.',
            '  Josh OS downloads the images into the _Inspo folder. Skip posts already in the snapshot, and carousels only.',
            '  Tag each post with the same tag lists as our carousels, and describe its visual look in "look" in a short line.',
            '  Set new_thing true when the format or look is something you have not seen from that creator before, and say what',
            '  is new in new_note. Once it has likes, add whether it worked. Josh OS works out breakouts (at least twice the median',
            '  likes of that creator\'s previous 20 posts), shown as BREAKOUT in the snapshot.',
        ]

    def desk_snapshot(self, agent_id, carousels, inspo=None):
        from datetime import date
        lines = [f'Today is {date.today().isoformat()}.', '', 'Your carousels in Josh OS (newest first). id | date | stage | title | tags | results:']
        mine = [c for c in carousels if c.get('owner') == agent_id and not c.get('archived')][:40]
        if mine:
            for c in mine:
                note = ' '.join((c.get('notes') or '').split())[:200]
                tags = json.dumps(c.get('tags') or {}, ensure_ascii=False) if c.get('tags') else 'no tags'
                res = json.dumps(c.get('results') or {}) if c.get('results') else 'no results'
                lines.append(f"- {c['id']} | {c.get('post_date') or 'no date'} | {c['stage']} | {c['title']} | {tags} | {res}"
                             + (f" | folder {c['folder']}" if c.get('folder') else '') + (f" | {note}" if note else ''))
        else:
            lines.append('- none yet')
        if inspo is not None:
            posts = inspo.all()['items']
            lines += ['', f'Creator posts saved in Josh OS ({len(posts)}, newest first). id | creator | date | likes | comments | hook | tagged:']
            for p in posts[:80]:
                lines.append(f"- {p['id']} | {p['creator']} | {p['date']} | {p.get('likes')} | {p.get('comments')} | "
                             f"{' '.join((p.get('hook') or '').split())[:70]} | {'tagged' if p.get('tags') else 'not tagged'}"
                             + (' | BREAKOUT' if p.get('breakout') else '') + (' | new thing' if p.get('new_thing') else ''))
        mails = getattr(self, 'emails', None)
        agent = next((a for a in self.team.all()['agents'] if a['id'] == agent_id), {})
        if mails is not None and 'email' in (agent.get('workflows') or []):
            from datetime import date as _d
            today = _d.today().isoformat()
            rows = [e for e in mails.all() if not e.get('archived')]
            upcoming = sorted([e for e in rows if e.get('status') != 'Sent'], key=lambda e: e.get('planned_date') or '9999')
            sent = sorted([e for e in rows if e.get('status') == 'Sent'], key=lambda e: e.get('planned_date') or '', reverse=True)[:8]
            lines += ['', 'Emails in Josh OS. id | send date | time | status | subject (Draft = being written or waiting for Josh,',
                      'Ready = Josh approved it, Scheduled or Published = in Kit, Sent = gone out). A Draft dated before today missed its date:']
            for e in upcoming + sent:
                lines.append(f"- {e['id']} | {e.get('planned_date') or 'no date'} | {e.get('send_time') or ''} | {e['status']}"
                             + (' (missed its date)' if e['status'] == 'Draft' and e.get('planned_date') and e['planned_date'] < today else '')
                             + f" | {e.get('subject', '')[:90]}")
        lines += ['', 'Videos in Josh OS:']
        videos = [p for p in self.store.projects() if not p.get('archived')]
        for p in sorted(videos, key=lambda p: p.get('publish_date') or '9999')[:40]:
            lines.append(f"- {p.get('publish_date') or 'no date'} | {p['stage']} | {p.get('chosen_title') or p['name']}")
        return '\n'.join(lines)

    def user_prompt(self, project, data, history):
        sel = data.get('selection') or {}
        parts = [
            f"Video: {project['name']}  (stage: {project['stage']}, planned for {project.get('publish_date') or 'no date yet'})",
            f"Angle and notes: {project.get('notes') or '(none yet)'}",
        ]
        packaging = {k: v for k, v in (project.get('packaging') or {}).items() if v}
        if packaging:
            parts.append('Packaging brief: ' + '; '.join(f'{k.replace("_", " ")}: {v}' for k, v in packaging.items()))
        magnet = project.get('lead_magnet') or {}
        if magnet.get('decision') and magnet['decision'] != 'unknown':
            parts.append('Lead magnet: ' + json.dumps(magnet, ensure_ascii=False))
        parts += ['', '--- Full script ---', project.get('script') or '(empty)', '--- End of script ---']
        if sel.get('text'):
            parts += ['', '--- Josh has selected this passage ---', sel['text'], '--- End of selection ---']
        if history:
            parts += ['', 'Earlier in this conversation:']
            for m in history[-10:]:
                parts.append(f"{'Josh' if m['role'] == 'josh' else 'You'}: {m['text']}")
        parts += ['', f"Josh: {data['message']}"]
        return '\n'.join(parts)

    # Running ----------------------------------------------------------------
    def run(self, system, prompt, schema=SCHEMA, dirs=(), chrome=False, extra_tools=(), full=False):
        claude = find_claude(self.root)
        if not claude:
            raise AgentError('I can\'t reach Claude on this PC yet. Install Claude Code and sign in, then try again.')
        with tempfile.TemporaryDirectory() as tmp:
            sys_file = Path(tmp) / 'system.md'
            sys_file.write_text(system, encoding='utf-8')
            wrapper = claude.lower().endswith(('.cmd', '.bat'))
            cmd = [claude, '-p', '--output-format', 'json', '--no-session-persistence',
                   '--append-system-prompt-file', str(sys_file),
                   '--tools', ALLOWED_TOOLS, '--allowedTools', ','.join([ALLOWED_TOOLS] + ([CHROME_TOOLS] if chrome else []) + list(extra_tools))]
            if chrome:
                cmd.append('--chrome')
            for d in dirs:
                cmd += ['--add-dir', d]
            if not wrapper:
                # npm's .cmd wrapper mangles quoted JSON arguments on Windows, so the schema goes in the prompt there instead.
                cmd += ['--json-schema', json.dumps(schema)]
            else:
                prompt += '\n\nAnswer with only a JSON object: {"reply": "...", "replacement": "..." or null}.'
            try:
                out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding='utf-8',
                                     cwd=str(self.root), timeout=TIMEOUT * 2 if chrome else TIMEOUT,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired:
                raise AgentError('That took too long, so I stopped. Try a smaller selection.') from None
        try:
            result = json.loads(out.stdout)
        except ValueError:
            detail = (out.stderr or out.stdout or '').strip().splitlines()[-1:] or ['no output']
            raise AgentError('Claude Code didn\'t answer properly: ' + detail[0][:300]) from None
        if result.get('is_error') and 'login' in str(result.get('result') or '').lower():
            raise AgentError("Claude Code on this PC isn't signed in yet, so sign in once and try again.")
        if result.get('is_error'):
            raise AgentError('Claude Code reported a problem: ' + str(result.get('result') or result.get('subtype'))[:300])
        answer = result.get('structured_output')
        if answer is None:
            text = result.get('result') or ''
            start, end = text.find('{'), text.rfind('}')
            try:
                answer = json.loads(text[start:end + 1]) if start >= 0 else {'reply': text, 'replacement': None}
            except ValueError:
                answer = {'reply': text, 'replacement': None}
        reply = str(answer.get('reply') or '').strip()
        replacement = answer.get('replacement')
        if replacement is not None and not isinstance(replacement, str):
            replacement = None
        if full:
            return reply, answer
        return reply, replacement

    def ask(self, data):
        if not isinstance(data, dict):
            raise ValueError('Invalid request.')
        agent_id, project_id = data.get('agent_id', ''), data.get('project_id', '')
        message = str(data.get('message', '')).strip()
        if not message or len(message) > 4000:
            raise ValueError('Write a message for the team member, up to 4,000 characters.')
        agent = next((a for a in self.team.all()['agents'] if a['id'] == agent_id), None)
        if not agent:
            raise KeyError('Team member not found.')
        project = self.store.get(project_id)
        sel = data.get('selection') or {}
        if sel:
            start, end, text = sel.get('start'), sel.get('end'), sel.get('text', '')
            if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(project['script'])):
                raise ValueError('The selection no longer matches the script. Select the passage again.')
            if project['script'][start:end] != text:
                raise ValueError('The script changed since you selected that passage. Select it again.')
        lock = self.locks.setdefault((agent_id, project_id), threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError(f"{agent['name']} is still working on your last message.")
        try:
            history = self.thread(project_id, agent_id)
            learnings = [l for l in self.team.all()['learnings'] if l['agent_id'] in ('', agent_id)][:80]
            schema = json.loads(json.dumps(SCHEMA))
            schema['properties']['lessons'] = LEARN_PROPS['lessons']
            reply, answer = self.run(self.system_prompt(agent, learnings), self.user_prompt(project, data, history), schema, full=True)
            answer = answer if isinstance(answer, dict) else {}
            replacement = answer.get('replacement') if isinstance(answer.get('replacement'), str) else None
            learned = self.keep_lessons(agent, answer.get('lessons'))
        finally:
            lock.release()
        mine = dict(id=str(uuid.uuid4()), role='josh', text=message, at=now(),
                    selection={'start': sel['start'], 'end': sel['end'], 'text': sel['text']} if sel else None)
        theirs = dict(id=str(uuid.uuid4()), role='agent', text=reply, at=now(), replacement=replacement,
                      selection=mine['selection'])
        if learned:
            theirs['learned'] = learned
        return dict(messages=self._append(project_id, agent_id, mine, theirs), reply=theirs)

    def focus(self, context, carousels, inspo=None):
        """What Josh has open in Josh OS while he chats, so "this" means that thing."""
        if not isinstance(context, dict):
            return ''
        kind, cid = context.get('kind'), str(context.get('id') or '')
        keep = lambda d, keys: {k: d.get(k) for k in keys if d.get(k) not in (None, '', [], {})}
        if kind == 'carousel':
            c = next((c for c in carousels if c['id'] == cid), None)
            if not c:
                return ''
            body = keep(c, ('id', 'title', 'stage', 'post_date', 'hook', 'slides', 'caption', 'notes', 'link', 'folder', 'tags', 'results'))
            return ('Josh has this carousel open right now, so "this", "it" and "the post" mean this one. To change it, send it in '
                    'the "carousels" list with this id. Its slide images (if any) are in the folder under '
                    'E:\\CareerOS\\03_Community_Posts, and you can look at them with Read.\n' + json.dumps(body, ensure_ascii=False, indent=1))
        if kind == 'inspo' and inspo is not None:
            try:
                p = inspo.get(cid)
            except KeyError:
                return ''
            body = keep(p, ('id', 'creator', 'date', 'url', 'hook', 'caption', 'likes', 'comments', 'folder', 'tags', 'look', 'new_thing', 'new_note', 'notes'))
            return ('Josh has this creator post open right now, so "this" means this one. To change it, send it in the "inspo" '
                    'list with this id. Its slides are in the folder under E:\\CareerOS\\03_Community_Posts\\_Inspo.\n'
                    + json.dumps(body, ensure_ascii=False, indent=1))
        if kind == 'project':
            try:
                p = self.store.get(cid)
            except KeyError:
                return ''
            title = next((t.get('text') for t in p.get('titles') or [] if t.get('id') == p.get('chosen_title')), '') or p['name']
            return ('Josh has this video open right now, so "this video" means this one. You can read it but not change video '
                    'cards from this chat yet, so say what to change.\n'
                    + json.dumps(dict(name=p['name'], title=title, stage=p['stage'], publish_date=p.get('publish_date'),
                                      notes=(p.get('notes') or '')[:2000], script=(p.get('script') or '')[:6000]), ensure_ascii=False, indent=1))
        if kind == 'email' and getattr(self, 'emails', None) is not None:
            try:
                e = self.emails.get(cid)
            except KeyError:
                return ''
            body = keep(e, ('id', 'subject', 'preview', 'body', 'planned_date', 'send_time', 'status', 'type', 'notes', 'links'))
            return ('Josh has this email open right now, so "this" and "it" mean this one. To change it, send it in the "emails" '
                    'list with this id. Keep any link wording exactly as it is unless he asks, since the tracked link is attached to '
                    'those words.\n' + json.dumps(body, ensure_ascii=False, indent=1))
        if kind == 'page' and context.get('name'):
            return f"Josh is on the {str(context['name'])[:40]} page of Josh OS."
        return ''

    def chat(self, data, carousels, tracker=None, inspo=None, proactive=False):
        """The general chat on a team member's own page, and the dock that follows Josh around.

        With proactive=True there is no message from Josh. The team member has just seen his
        edits to their work and writes to him first, asking about what they can't work out.
        """
        if not isinstance(data, dict):
            raise ValueError('Invalid request.')
        agent_id = data.get('agent_id', '')
        message = '' if proactive else str(data.get('message', '')).strip()
        if not proactive and (not message or len(message) > 4000):
            raise ValueError('Write a message, up to 4,000 characters.')
        agent = next((a for a in self.team.all()['agents'] if a['id'] == agent_id), None)
        if not agent:
            raise KeyError('Team member not found.')
        feedback = getattr(self, 'feedback', None)
        edits = feedback.pending(agent_id) if feedback else []
        if proactive and not edits:
            return dict(messages=self.thread(DESK, agent_id), reply=None)
        lock = self.locks.setdefault((agent_id, DESK), threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError(f"{agent['name']} is still working on your last message.")
        try:
            history = self.thread(DESK, agent_id)
            learnings = [l for l in self.team.all()['learnings'] if l['agent_id'] in ('', agent_id)][:80]
            _, dirs = self.training(agent_id)
            keeps = tracker is not None and 'carousels' in (agent.get('workflows') or [])
            parts = [self.desk_snapshot(agent_id, carousels, inspo if keeps else None)]
            if history:
                parts += ['', 'Earlier in this conversation:']
                for m in history[-12:]:
                    parts.append(f"{'Josh' if m['role'] == 'josh' else 'You'}: {m['text']}")
            focus = self.focus(data.get('context'), carousels, inspo)
            if focus:
                parts += ['', focus]
            if edits:
                parts += ['', 'Changes Josh made to your work that you have not raised with him yet (before and after):',
                          feedback.describe(edits)]
            if proactive:
                parts += ['', 'Josh has just finished editing some of your work, listed above, and has not said anything about it. '
                          'Write to him first. Keep it short and natural. For the changes where the reason is clear, say in a line '
                          'what you will do from now on. For the ones you cannot work out, ask about one or two, like "Why did '
                          'you change X to Y? Was it to ...?". Do not ask about typo fixes. Put every edit id you covered in '
                          'edits_noted, and save any rule that is already clear in lessons.']
            else:
                parts += ['', f'Josh: {message}']
            schema = json.loads(json.dumps(CAROUSEL_SCHEMA if keeps else DESK_SCHEMA))
            schema['properties'].update(LEARN_PROPS)
            mails = getattr(self, 'emails', None) if 'email' in (agent.get('workflows') or []) else None
            if mails is not None:
                schema['properties'].update(EMAIL_PROPS)
            reply, answer = self.run(self.desk_system_prompt(agent, learnings), '\n'.join(parts),
                                     schema, dirs, chrome=agent_id in CHROME and not proactive,
                                     extra_tools=['mcp__linklr__' + t for t in LINKLR.get(agent_id, ())], full=True)
            answer = answer if isinstance(answer, dict) else {}
            todo = answer.get('carousels')
            saved = tracker.apply(agent_id, todo) if keeps and isinstance(todo, list) else []
            found = answer.get('inspo')
            if keeps and inspo is not None and isinstance(found, list):
                saved += inspo.apply(found)
            if mails is not None and isinstance(answer.get('emails'), list):
                saved += self.apply_emails(mails, answer['emails'])
            if feedback and isinstance(answer.get('edits_noted'), list):
                known = {e['id'] for e in edits}
                feedback.mark([i for i in answer['edits_noted'] if i in known])
            learned = self.keep_lessons(agent, answer.get('lessons'))
        finally:
            lock.release()
        theirs = dict(id=str(uuid.uuid4()), role='agent', text=reply, at=now())
        if saved:
            theirs['saved'] = saved
        if learned:
            theirs['learned'] = learned
        if proactive:
            theirs['asked'] = True
            return dict(messages=self._append(DESK, agent_id, theirs), reply=theirs)
        mine = dict(id=str(uuid.uuid4()), role='josh', text=message, at=now())
        return dict(messages=self._append(DESK, agent_id, mine, theirs), reply=theirs)

    def reflect(self, data, carousels, tracker=None, inspo=None):
        """Josh just edited a team member's work. They look at the changes and write to him first."""
        return self.chat(data, carousels, tracker, inspo, proactive=True)

    def apply_emails(self, mails, updates):
        """Email changes a team member asked for from chat. Status is left to Josh."""
        done = []
        for u in updates[:40]:
            if not isinstance(u, dict):
                continue
            changes = {k: str(v) for k, v in u.items() if k in EMAIL_WRITE and v is not None}
            eid = str(u.get('id') or '')
            try:
                if eid:
                    current = mails.get(eid)
                    item = mails.save(eid, changes, current['revision'])
                    done.append(dict(id=item['id'], title=item.get('subject', ''), action='updated', kind='email'))
                else:
                    item = mails.create({'id': str(uuid.uuid4()), 'changes': changes})
                    done.append(dict(id=item['id'], title=item.get('subject', ''), action='added', kind='email'))
            except Exception as e:  # noqa: BLE001 - report every failure back to Josh in the chat
                done.append(dict(id=eid, title=changes.get('subject', ''), action='failed', kind='email', error=str(e).strip("'\"")[:200]))
        return done

    def keep_lessons(self, agent, lessons):
        """Save the rules a team member worked out from Josh's edits or answers to the shared lessons log."""
        kept = []
        if not isinstance(lessons, list):
            return kept
        home = {'sam': 'carousels', 'laia': 'email', 'tariq': 'editing', 'saskia': 'courses'}.get(agent['id'], '')
        norm = lambda t: ' '.join(str(t).lower().split()).rstrip('.')
        seen = {norm(l['lesson']) for l in self.team.all()['learnings'] if l['agent_id'] in ('', agent['id'])}
        for l in lessons[:5]:
            if not isinstance(l, dict) or not str(l.get('lesson', '')).strip() or norm(l['lesson']) in seen:
                continue
            seen.add(norm(l['lesson']))
            flow = l.get('workflow') if l.get('workflow') in ('youtube', 'carousels', 'editing', 'email', 'courses') else home
            try:
                item = self.team.learn(dict(agent_id=agent['id'], workflow=flow, lesson=str(l['lesson'])[:2000],
                                            evidence=str(l.get('evidence', ''))[:2000]))
                kept.append(item['lesson'])
            except (ValueError, KeyError):
                continue
        return kept
