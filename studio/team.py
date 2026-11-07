"""The Josh OS team: named AI agents with profiles, plus the shared lessons-learned log.

Profiles are stored locally and edited from the Team page. The agents do not run yet;
this module holds who they are, what they own and what the team has learned, so the
agent runtime added later reads its instructions from here.
"""
import json
import uuid
from pathlib import Path
from .store import Conflict, now

WORKFLOWS = {
    'youtube': 'YouTube videos',
    'carousels': 'Carousels',
    'editing': 'Editing',
    'email': 'Email',
    'courses': 'Course lessons',
    'clients': 'Clients',
    'business': 'Business metrics',
    'calendar': 'Calendar',
}
STATUSES = ['Draft', 'Active', 'Paused', 'Coming later']
PROFILE_FIELDS = {'name', 'role', 'color', 'photo', 'mission', 'personality', 'responsibilities',
                  'workflows', 'skills', 'check_in', 'status'}
LEARNING_FIELDS = {'agent_id', 'workflow', 'lesson', 'evidence'}
PHOTOS = Path(__file__).parent / 'team_photos'
# The first draft of the team used other names. Old rows move to the new ids.
RENAMED = {'bob': 'sam', 'eddie': 'tariq', 'vera': 'laia'}
# Earlier default roles and personalities. Profiles still on these move to the ones in
# team/personalities.md; anything Josh has edited by hand is left alone.
OLD_DEFAULTS = {
    'sam': {'role': ['Content & YouTube Manager'], 'personality': ['Upbeat, direct and organised. Brings a plan with a recommendation, then asks for a yes.']},
    'tariq': {'personality': ['Calm and precise. Reports what changed and flags anything that needs Josh\'s eye.']},
    'laia': {'role': ['Email Writer'], 'personality': ['Warm, sharp with words, and honest about weak subject lines.']},
    'saskia': {'role': ['L&D Content Lead'], 'personality': ['Warm, structured and practical. Thinks in learning outcomes and what the learner does next.']},
}

SEEDS = [
    dict(id='sam', name='Sam', role='YouTube Channel Manager', color='#2383e2',
         mission='Keep the YouTube channel moving. Sam plans and makes the carousels, helps shape '
                 'video ideas, scripts, titles and thumbnails, posts content, and tracks how each '
                 'piece performs against the channel goals.',
         personality='Practical, analytical and confident, with a dry sense of humour. Backs the ideas that will work and challenges the ones that won\'t.',
         responsibilities=['Plan, write and design carousels, then post them once approved',
                           'Help develop video ideas, scripts, titles and thumbnails',
                           'Keep the YouTube board and calendar up to date',
                           'Track video and carousel performance against the goals',
                           'Add what works and what flops to the lessons-learned log'],
         workflows=['youtube', 'carousels', 'calendar'],
         skills=['script-touch-up', 'Carousel playbook (to write)', 'Thumbnail rules (to write)',
                 'Posting through Chrome'],
         check_in='Every weekday morning, with the day\'s carousels, videos due and anything blocked.',
         status='Draft'),
    dict(id='tariq', name='Tariq', role='Video Editor', color='#9065b0',
         mission='Turn recorded footage into finished videos. Tariq runs the editing workflow from '
                 'transcript to clean cut, flubs, keying, animations and the Premiere export.',
         personality='Relaxed, creative and slightly sarcastic. Particular about pacing and quality, and pushes back when a change would make the video worse.',
         responsibilities=['Pick up videos once they are recorded',
                           'Run the transcribe, clean cut, flubs and keying passes',
                           'Prepare the animation deck and shot list',
                           'Hand over a Premiere-ready export and note anything to review'],
         workflows=['editing', 'youtube'],
         skills=['video-editing-prep', 'Josh OS editing engine'],
         check_in='When a recording lands, and when an edit is ready for review.',
         status='Draft'),
    dict(id='laia', name='Laia', role='Email Copywriter', color='#d9730d',
         mission='Write the newsletters and sequences in Josh\'s voice, keep drafts at least two weeks '
                 'ahead, and track how each email performs in Kit.',
         personality='Warm, observant and witty. Makes commercial emails sound personal and calls out anything that reads like marketing.',
         responsibilities=['Draft four newsletters a week for review',
                           'Keep the email buffer at least two weeks ahead',
                           'Schedule approved emails in Kit',
                           'Track open and click rates and report what is working',
                           'Add email lessons to the lessons-learned log'],
         workflows=['email', 'calendar'],
         skills=['Email writing scripts', 'Email reference library', 'Kit'],
         check_in='Monday and Thursday, with the buffer and last week\'s results.',
         status='Draft'),
    dict(id='saskia', name='Saskia', role='Learning & Development / Course Designer', color='#b5487a',
         mission='Turn what Josh teaches into course content people finish. Saskia plans and writes the CareerOS '
                 'lessons for Heard at the Top and Promoted in 6, builds the lesson slides, makes the playbooks, '
                 'cheat sheets and worksheets, and keeps lesson recording on track.',
         personality='Thoughtful, composed and subtly dry. Challenges anything without a clear learning outcome, and turns ideas into lessons people can apply.',
         responsibilities=['Plan each course and lesson around what the learner can do afterwards',
                           'Write and tighten lesson scripts in Josh\'s voice',
                           'Build beat-by-beat lesson slides ready to record',
                           'Make the playbooks, cheat sheets and worksheets that go with each lesson',
                           'Keep the recording plan on track and flag lessons with no date',
                           'Add what helps learners finish to the lessons-learned log'],
         workflows=['courses', 'calendar'],
         skills=['lesson-slides', 'careeros-playbook'],
         check_in='Weekly, with what is recorded, what is next to record and anything missing for it.',
         status='Draft'),
    dict(id='max', name='Max', role='Business Manager', color='#0f7b6c',
         mission='Sell the courses and programmes, and track the business numbers against the '
                 'goal of USD 15,000 a month from courses and upsells.',
         personality='Commercial and numbers-first. Keeps every conversation tied to revenue.',
         responsibilities=['Track revenue, sales and customer numbers',
                           'Spot which content leads to sales',
                           'Suggest offers, launches and follow-ups'],
         workflows=['business', 'clients'],
         skills=['Stripe', 'Calendly', 'Kit'],
         check_in='Weekly, once the business metrics are added.',
         status='Coming later'),
]


def _text(value, limit, label):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{label} must be text, up to {limit:,} characters.')
    return value.strip()


def _list(value, limit, label):
    if not isinstance(value, list) or len(value) > 30:
        raise ValueError(f'{label} must be a list of up to 30 items.')
    return [v for v in (_text(x, limit, label) for x in value) if v]


class Team:
    def __init__(self, store):
        self.store = store
        # Import photos before taking the write lock: import_image uses its own connection.
        photos = {seed['id']: self._photo(seed['id']) for seed in SEEDS}
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS team_agents(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,position INTEGER NOT NULL,body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS team_learnings(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,body TEXT NOT NULL)')
            c.execute('BEGIN IMMEDIATE')
            self._rename(c)
            self._refresh_defaults(c)
            for i, seed in enumerate(SEEDS):
                if not c.execute('SELECT 1 FROM team_agents WHERE id=?', (seed['id'],)).fetchone():
                    # New team members join in their place in the list, ahead of anyone after them.
                    c.execute('UPDATE team_agents SET position=position+1 WHERE position>=?', (i,))
                    agent = dict(seed, revision=1, photo=photos[seed['id']], created_at=now(), updated_at=now())
                    c.execute('INSERT INTO team_agents VALUES(?,?,?,?)', (seed['id'], 1, i, json.dumps(agent)))

    def _refresh_defaults(self, c):
        seeds = {s['id']: s for s in SEEDS}
        for aid, old in OLD_DEFAULTS.items():
            row = c.execute('SELECT revision,body FROM team_agents WHERE id=?', (aid,)).fetchone()
            if not row:
                continue
            agent = json.loads(row['body'])
            changes = {k: seeds[aid][k] for k, values in old.items() if agent.get(k) in values}
            if changes:
                agent.update(changes, revision=row['revision'] + 1, updated_at=now())
                c.execute('UPDATE team_agents SET revision=?,body=? WHERE id=?', (agent['revision'], json.dumps(agent), aid))

    def _photo(self, aid):
        """Import the bundled default photo for a team member, if there is one."""
        file = PHOTOS / (aid + '.jpg')
        if not file.is_file():
            return ''
        try:
            return self.store.import_image(file.name, file.read_bytes())['asset']
        except (OSError, ValueError):
            return ''

    def _rename(self, c):
        """Move first-draft team members to their new ids. Untouched profiles are replaced by
        the new defaults; edited ones keep their edits under the new id."""
        for old, new in RENAMED.items():
            row = c.execute('SELECT revision,body FROM team_agents WHERE id=?', (old,)).fetchone()
            if not row:
                continue
            if row['revision'] > 1 and not c.execute('SELECT 1 FROM team_agents WHERE id=?', (new,)).fetchone():
                agent = dict(json.loads(row['body']), id=new)
                c.execute('UPDATE team_agents SET id=?,body=? WHERE id=?', (new, json.dumps(agent), old))
            else:
                c.execute('DELETE FROM team_agents WHERE id=?', (old,))
            for r in c.execute('SELECT id,body FROM team_learnings WHERE agent_id=?', (old,)).fetchall():
                c.execute('UPDATE team_learnings SET agent_id=?,body=? WHERE id=?', (new, json.dumps(dict(json.loads(r['body']), agent_id=new)), r['id']))
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='carousels'").fetchone():
                for r in c.execute('SELECT id,body FROM carousels').fetchall():
                    body = json.loads(r['body'])
                    if body.get('owner') == old:
                        c.execute('UPDATE carousels SET body=? WHERE id=?', (json.dumps(dict(body, owner=new)), r['id']))

    def all(self):
        with self.store.connect() as c:
            agents = [json.loads(r['body']) for r in c.execute('SELECT body FROM team_agents ORDER BY position')]
            learnings = [json.loads(r['body']) for r in c.execute('SELECT body FROM team_learnings ORDER BY rowid DESC')]
        return dict(agents=agents, learnings=learnings, workflows=WORKFLOWS, statuses=STATUSES)

    def validate(self, a):
        a['name'] = _text(a['name'], 60, 'Name')
        if not a['name']:
            raise ValueError('Give this team member a name.')
        a['role'] = _text(a['role'], 120, 'Role')
        for key, limit in [('mission', 2000), ('personality', 1000), ('check_in', 500)]:
            a[key] = _text(a[key], limit, key.replace('_', ' ').capitalize())
        a['responsibilities'] = _list(a['responsibilities'], 300, 'Responsibilities')
        a['skills'] = _list(a['skills'], 120, 'Skills')
        if not isinstance(a['workflows'], list) or set(a['workflows']) - set(WORKFLOWS):
            raise ValueError('Choose workflows from the list.')
        if a['status'] not in STATUSES:
            raise ValueError('Choose a valid status.')
        color = a['color']
        if not (isinstance(color, str) and len(color) == 7 and color[0] == '#' and all(ch in '0123456789abcdefABCDEF' for ch in color[1:])):
            raise ValueError('Use a colour like #2383e2.')
        if a['photo']:
            try:
                if not isinstance(a['photo'], str) or len(a['photo']) != 64:
                    raise ValueError
                self.store.asset(a['photo'])
            except ValueError:
                raise ValueError('Upload the photo again.') from None

    def save(self, aid, changes, revision):
        if not isinstance(changes, dict) or set(changes) - PROFILE_FIELDS:
            raise ValueError('Unknown profile field.')
        if type(revision) is not int:
            raise ValueError('A saved revision is required.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT revision,body FROM team_agents WHERE id=?', (aid,)).fetchone()
            if not row:
                raise KeyError('Team member not found.')
            if row['revision'] != revision:
                raise Conflict('This profile changed in another window. Reload it before saving again.')
            previous = json.loads(row['body'])
            agent = dict(previous, **changes)
            self.validate(agent)
            if all(agent[k] == previous[k] for k in PROFILE_FIELDS):
                return previous
            agent.update(revision=revision + 1, updated_at=now())
            c.execute('UPDATE team_agents SET revision=?,body=? WHERE id=?', (agent['revision'], json.dumps(agent), aid))
        return agent

    def learn(self, data):
        if not isinstance(data, dict) or set(data) - LEARNING_FIELDS - {'id'}:
            raise ValueError('Unknown lesson field.')
        lid = data.get('id') or str(uuid.uuid4())
        uuid.UUID(lid)
        item = dict(id=lid, agent_id=data.get('agent_id', ''), workflow=data.get('workflow', ''),
                    lesson=_text(data.get('lesson', ''), 2000, 'Lesson'),
                    evidence=_text(data.get('evidence', ''), 2000, 'Evidence'), created_at=now())
        if not item['lesson']:
            raise ValueError('Write the lesson first.')
        if item['workflow'] and item['workflow'] not in WORKFLOWS:
            raise ValueError('Choose a workflow from the list.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if item['agent_id'] and not c.execute('SELECT 1 FROM team_agents WHERE id=?', (item['agent_id'],)).fetchone():
                raise ValueError('Choose a team member from the list.')
            existing = c.execute('SELECT body FROM team_learnings WHERE id=?', (lid,)).fetchone()
            if existing:
                return json.loads(existing['body'])
            c.execute('INSERT INTO team_learnings VALUES(?,?,?)', (lid, item['agent_id'], json.dumps(item)))
        return item

    def forget(self, lid):
        with self.store.connect() as c:
            if not c.execute('DELETE FROM team_learnings WHERE id=?', (lid,)).rowcount:
                raise KeyError('Lesson not found.')
        return dict(ok=True)
