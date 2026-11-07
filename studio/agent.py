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
    'tabs_close_mcp', 'navigate', 'read_page', 'get_page_text', 'find', 'computer', 'browser_batch',
    'form_input', 'file_upload', 'upload_image'))
DESK_SCHEMA = {
    'type': 'object',
    'properties': {'reply': {'type': 'string', 'description': 'What you say back to Josh, in your own voice.'}},
    'required': ['reply'],
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
            *(['- You cannot edit files or Josh OS cards from this chat yet. When a card needs changing, tell Josh what to change.']
              if agent['id'] in CHROME else
              ['- From this chat you can read but not change anything. Your scheduled tasks on this PC do the making, scheduling and tracking.',
               '  When Josh asks for something to be done, say what you will do on your next run, or what he needs to change.']),
            '- When Josh gives you a rule or a preference worth keeping, say you will add it to your lessons and suggest the exact wording.',
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
        ]

    def desk_snapshot(self, agent_id, carousels):
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
            learnings = [l for l in self.team.all()['learnings'] if l['agent_id'] in ('', agent_id)][:30]
            reply, replacement = self.run(self.system_prompt(agent, learnings), self.user_prompt(project, data, history))
        finally:
            lock.release()
        mine = dict(id=str(uuid.uuid4()), role='josh', text=message, at=now(),
                    selection={'start': sel['start'], 'end': sel['end'], 'text': sel['text']} if sel else None)
        theirs = dict(id=str(uuid.uuid4()), role='agent', text=reply, at=now(), replacement=replacement,
                      selection=mine['selection'])
        return dict(messages=self._append(project_id, agent_id, mine, theirs), reply=theirs)

    def chat(self, data, carousels, tracker=None):
        """The general chat on a team member's own page."""
        if not isinstance(data, dict):
            raise ValueError('Invalid request.')
        agent_id = data.get('agent_id', '')
        message = str(data.get('message', '')).strip()
        if not message or len(message) > 4000:
            raise ValueError('Write a message, up to 4,000 characters.')
        agent = next((a for a in self.team.all()['agents'] if a['id'] == agent_id), None)
        if not agent:
            raise KeyError('Team member not found.')
        lock = self.locks.setdefault((agent_id, DESK), threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError(f"{agent['name']} is still working on your last message.")
        try:
            history = self.thread(DESK, agent_id)
            learnings = [l for l in self.team.all()['learnings'] if l['agent_id'] in ('', agent_id)][:30]
            _, dirs = self.training(agent_id)
            parts = [self.desk_snapshot(agent_id, carousels)]
            if history:
                parts += ['', 'Earlier in this conversation:']
                for m in history[-12:]:
                    parts.append(f"{'Josh' if m['role'] == 'josh' else 'You'}: {m['text']}")
            parts += ['', f'Josh: {message}']
            keeps = tracker is not None and 'carousels' in (agent.get('workflows') or [])
            reply, answer = self.run(self.desk_system_prompt(agent, learnings), '\n'.join(parts),
                                     CAROUSEL_SCHEMA if keeps else DESK_SCHEMA, dirs, chrome=agent_id in CHROME,
                                     extra_tools=['mcp__linklr__' + t for t in LINKLR.get(agent_id, ())], full=True)
            todo = answer.get('carousels') if isinstance(answer, dict) else None
            saved = tracker.apply(agent_id, todo) if keeps and isinstance(todo, list) else []
        finally:
            lock.release()
        mine = dict(id=str(uuid.uuid4()), role='josh', text=message, at=now())
        theirs = dict(id=str(uuid.uuid4()), role='agent', text=reply, at=now())
        if saved:
            theirs['saved'] = saved
        return dict(messages=self._append(DESK, agent_id, mine, theirs), reply=theirs)
