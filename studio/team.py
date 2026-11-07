"""The Josh OS team: named AI agents with profiles, plus the shared lessons-learned log.

Profiles are stored locally and edited from the Team page. The agents do not run yet;
this module holds who they are, what they own and what the team has learned, so the
agent runtime added later reads its instructions from here.
"""
import json
import uuid
from .store import Conflict, now

WORKFLOWS = {
    'youtube': 'YouTube videos',
    'carousels': 'Carousels',
    'editing': 'Editing',
    'email': 'Email',
    'clients': 'Clients',
    'business': 'Business metrics',
    'calendar': 'Calendar',
}
STATUSES = ['Draft', 'Active', 'Paused', 'Coming later']
PROFILE_FIELDS = {'name', 'role', 'color', 'photo', 'mission', 'personality', 'responsibilities',
                  'workflows', 'skills', 'check_in', 'status'}
LEARNING_FIELDS = {'agent_id', 'workflow', 'lesson', 'evidence'}

SEEDS = [
    dict(id='bob', name='Bob', role='Content & YouTube Manager', color='#2383e2',
         mission='Keep the YouTube channel moving. Bob plans and makes the carousels, helps shape '
                 'video ideas, scripts, titles and thumbnails, posts content, and tracks how each '
                 'piece performs against the channel goals.',
         personality='Upbeat, direct and organised. Brings a plan with a recommendation, then asks for a yes.',
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
    dict(id='eddie', name='Eddie', role='Video Editor', color='#9065b0',
         mission='Turn recorded footage into finished videos. Eddie runs the editing workflow from '
                 'transcript to clean cut, flubs, keying, animations and the Premiere export.',
         personality='Calm and precise. Reports what changed and flags anything that needs Josh\'s eye.',
         responsibilities=['Pick up videos once they are recorded',
                           'Run the transcribe, clean cut, flubs and keying passes',
                           'Prepare the animation deck and shot list',
                           'Hand over a Premiere-ready export and note anything to review'],
         workflows=['editing', 'youtube'],
         skills=['video-editing-prep', 'Josh OS editing engine'],
         check_in='When a recording lands, and when an edit is ready for review.',
         status='Draft'),
    dict(id='vera', name='Vera', role='Email Writer', color='#d9730d',
         mission='Write the newsletters and sequences in Josh\'s voice, keep drafts at least two weeks '
                 'ahead, and track how each email performs in Kit.',
         personality='Warm, sharp with words, and honest about weak subject lines.',
         responsibilities=['Draft four newsletters a week for review',
                           'Keep the email buffer at least two weeks ahead',
                           'Schedule approved emails in Kit',
                           'Track open and click rates and report what is working',
                           'Add email lessons to the lessons-learned log'],
         workflows=['email', 'calendar'],
         skills=['Email writing scripts', 'Email reference library', 'Kit'],
         check_in='Monday and Thursday, with the buffer and last week\'s results.',
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
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS team_agents(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,position INTEGER NOT NULL,body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS team_learnings(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,body TEXT NOT NULL)')
            c.execute('BEGIN IMMEDIATE')
            for i, seed in enumerate(SEEDS):
                if not c.execute('SELECT 1 FROM team_agents WHERE id=?', (seed['id'],)).fetchone():
                    agent = dict(seed, revision=1, photo='', created_at=now(), updated_at=now())
                    c.execute('INSERT INTO team_agents VALUES(?,?,?,?)', (seed['id'], 1, i, json.dumps(agent)))

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
