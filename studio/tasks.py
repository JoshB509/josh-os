"""Small local tasks/goals list, sharing the workspace database and project IDs."""
import json
import uuid
from datetime import date
from .store import Conflict, now

STATUSES = ['Next', 'In progress', 'Waiting', 'Later', 'Done']
FIELDS = {'title', 'kind', 'status', 'next_step', 'link', 'due_date'}
SECTIONS = {'lessons': ('CareerOS lessons', '/lessons'), 'email': ('Email drafts', '/#email'), 'youtube': ('YouTube projects', '/#board')}
# Explicitly requested plans. No dates, progress figures or completion inferred.
SEEDS = [
    ('core-lessons', 'task', 'Finish recording and uploading core CareerOS lessons', 'In progress', 'Continue the remaining lessons, then upload the reviewed recordings.', 'lessons', ''),
    ('november-launch', 'task', 'Prepare the November 1 launch', 'Next', 'Review what remains to be ready for launch.', 'lessons', '2026-11-01'),
    ('email-buffer', 'goal', 'Keep email drafts at least two weeks ahead', 'Next', 'Four newsletters each week from November. Review drafts before approval.', 'email', ''),
    ('autocut-trial', 'task', 'Evaluate the AutoCut clean-cut workflow', 'Waiting', 'Trial installed. The clean-cut test is blocked and has not been completed.', '', ''),
    ('autocut-integration', 'task', 'Explore AutoCut local UI automation', 'Later', 'Future enhancement, after the clean-cut workflow has been evaluated.', '', ''),
    ('course-revenue', 'goal', 'Reach USD 15,000/month from courses + upsells', 'Next', 'Excludes client work, Udemy and other revenue.', 'lessons', ''),
]


class Tasks:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS task_items(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS task_schema(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)')
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM task_schema WHERE version=1').fetchone():
                for key,kind,title,status,next_step,link,due in SEEDS:
                    tid=str(uuid.uuid5(uuid.NAMESPACE_URL,'dajoshi/tasks/'+key))
                    item=dict(id=tid,revision=1,kind=kind,title=title,status=status,next_step=next_step,link=link,due_date=due,created_at=now(),updated_at=now(),completed_at=None)
                    c.execute('INSERT OR IGNORE INTO task_items VALUES(?,?,?)',(tid,1,json.dumps(item)))
                c.execute('INSERT INTO task_schema VALUES(1,?)',(now(),))

    def targets(self):
        items=[dict(value=k,label=v[0],url=v[1]) for k,v in SECTIONS.items()]
        for p in self.store.projects():
            items.append(dict(value='project:'+p['id'],label=p['name'],url='/?project='+p['id'],stage=p['stage'],archived=p['archived']))
        return items

    def all(self):
        with self.store.connect() as c:
            items=[json.loads(r['body']) for r in c.execute('SELECT body FROM task_items ORDER BY rowid')]
        return dict(items=items,statuses=STATUSES,targets=self.targets())

    def validate(self,item):
        for key,limit in [('title',240),('next_step',1000),('link',100),('due_date',10)]:
            if not isinstance(item[key],str) or len(item[key])>limit:raise ValueError('Invalid '+key.replace('_',' '))
        item['title']=item['title'].strip();item['next_step']=item['next_step'].strip()
        if not item['title']:raise ValueError('Give this item a title.')
        if item['kind'] not in ('task','goal'):raise ValueError('Choose a task or a goal.')
        if item['status'] not in STATUSES:raise ValueError('Choose a valid status.')
        if item['due_date']:
            date.fromisoformat(item['due_date'])
            if len(item['due_date'])!=10:raise ValueError('Use a full date.')
        if item['link'] and item['link'] not in SECTIONS:
            if not item['link'].startswith('project:'):raise ValueError('Choose an existing workspace link.')
            self.store.get(item['link'][8:])

    def create(self,data):
        if set(data)-FIELDS-{'id'}:raise ValueError('Unknown task field.')
        tid=data.get('id') or str(uuid.uuid4());uuid.UUID(tid)
        item=dict(id=tid,revision=1,title=data.get('title',''),kind=data.get('kind','task'),status=data.get('status','Next'),next_step=data.get('next_step',''),link=data.get('link',''),due_date=data.get('due_date',''),created_at=now(),updated_at=now(),completed_at=None)
        self.validate(item)
        if item['status']=='Done':item['completed_at']=now()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT body FROM task_items WHERE id=?',(tid,)).fetchone()
            if row:
                previous=json.loads(row['body'])
                if any(previous[k]!=item[k] for k in FIELDS):raise Conflict('This item was already created with different details. Reload before retrying.')
                return previous
            c.execute('INSERT INTO task_items VALUES(?,?,?)',(tid,1,json.dumps(item)))
        return item

    def save(self,tid,changes,revision):
        if not isinstance(changes,dict) or set(changes)-FIELDS:raise ValueError('Unknown task field.')
        if type(revision) is not int:raise ValueError('A saved revision is required.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT revision,body FROM task_items WHERE id=?',(tid,)).fetchone()
            if not row:raise KeyError('Task or goal not found.')
            if row['revision']!=revision:raise Conflict('This item changed in another window. Your draft is still here; reload the latest item before saving again.')
            previous=json.loads(row['body']);item=dict(previous,**changes);self.validate(item)
            if all(item[k]==previous[k] for k in FIELDS):return previous
            item.update(revision=revision+1,updated_at=now())
            item['completed_at']=(previous.get('completed_at') or now()) if item['status']=='Done' else None
            c.execute('UPDATE task_items SET revision=?,body=? WHERE id=?',(item['revision'],json.dumps(item),tid))
        return item
