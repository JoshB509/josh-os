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

SKILLS = {'sam': ['script-touch-up'], 'tariq': ['video-editing-prep'], 'laia': [], 'saskia': ['lesson-slides', 'careeros-playbook'], 'anouk': [], 'max': []}
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
# Reading Kit through the Kit connector linked to Claude on this PC. Laia reads, she doesn't send.
KIT_READ = tuple('mcp__claude_ai_Kit__' + t for t in (
    'list_broadcasts', 'get_broadcast', 'get_stats_for_a_broadcast', 'get_stats_for_a_list_of_broadcasts',
    'get_link_clicks_for_a_broadcast', 'get_email_stats', 'get_growth_stats', 'list_sequences', 'get_sequence',
    'list_sequence_emails', 'get_sequence_email', 'list_tags', 'list_forms', 'list_segments', 'get_current_account'))
KIT = {'laia': KIT_READ, 'max': KIT_READ}
# Reading Gmail, only to collect new emails from the creators in the library. Nothing that sends, labels or deletes.
GMAIL_READ = tuple('mcp__claude_ai_Gmail__' + t for t in ('search_threads', 'get_thread', 'get_message', 'list_labels'))
GMAIL = {'laia': GMAIL_READ}
EMAIL_PROPS = {
    'emails': {
        'type': 'array',
        'description': 'Emails to add or change in Josh OS. Leave empty when nothing needs saving.',
        'items': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'Id of an existing email to change. Leave out to add a new draft.'},
            **{k: {'type': 'string'} for k in EMAIL_WRITE},
            'sent': {'type': 'boolean', 'description': 'True only when Kit shows this email has gone out.'},
            'source_ids': {'type': 'array', 'items': {'type': 'string'},
                           'description': 'Library emails this one is based on, as JonBrosio:<id> or EdLawrence:<message_id>.'},
            'results': {'type': 'object', 'description': 'How it did in Kit: recipients, opens, open_rate, clicks, click_rate, unsubscribes, new_people, kit_broadcast_id, checked_at.'}}},
    },
    'library': {
        'type': 'array',
        'description': 'New emails from Ed Lawrence or Jon Brosio to save to the email library. Full body text, one entry per email.',
        'items': {'type': 'object', 'properties': {
            'author': {'type': 'string', 'enum': ['Ed Lawrence', 'Jon Brosio']}, 'message_id': {'type': 'string'},
            'subject': {'type': 'string'}, 'date': {'type': 'string'}, 'body': {'type': 'string'},
            'gmail_url': {'type': 'string'}, 'sender': {'type': 'string'}},
            'required': ['author', 'message_id', 'subject', 'date', 'body']},
    },
}
# Team members who look after courses can change a lesson's script, preparation notes and notes from chat.
LESSON_PROPS = {
    'lessons': {
        'type': 'array',
        'description': 'Changes to course lessons in Josh OS. Leave empty when nothing needs saving.',
        'items': {'type': 'object', 'properties': {
            'lesson_id': {'type': 'string', 'description': 'Id of the lesson from the snapshot.'},
            'script': {'type': 'string', 'description': 'The whole script, when you rewrite it. Never a part of it.'},
            'prep': {'type': 'string', 'description': 'Preparation notes for the lesson.'},
            'notes': {'type': 'string', 'description': 'A short note on what changed and why.'}},
            'required': ['lesson_id']},
    },
}
# What Saskia sends back from a lesson job (prepare, draft, slides, guide or thumbnail).
LESSON_JOB_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string', 'description': 'What you did, for Josh, in a few sentences. Mention anything he needs to confirm.'},
        'prep': {'type': 'string', 'description': 'Prepare only. The preparation notes, in Markdown.'},
        'script': {'type': 'string', 'description': 'Draft only. The whole script file, header paragraph and all.'},
        'slides_path': {'type': 'string', 'description': 'Slides only. The full path of the HTML file you saved.'},
        'cued_script': {'type': 'string', 'description': 'Slides only. The script with a bold red CHANGE n cue before the words each beat fires on.'},
        'beats': {'type': 'integer', 'description': 'Slides only. The total number of beats.'},
        'guide_pdf': {'type': 'string', 'description': 'Guide only. The full path of the lesson guide PDF you saved.'},
        'thumbnail': {'type': 'string', 'description': 'Thumbnail only. The full path of the thumbnail you saved.'},
    },
    'required': ['reply'],
}
JOB_TOOLS = {'prepare': 'Read,Grep,Glob', 'draft': 'Read,Grep,Glob', 'slides': 'Read,Grep,Glob,Write,Edit',
             'guide': 'Read,Grep,Glob,Write,Edit,Bash', 'thumbnail': 'Read,Grep,Glob,Write,Edit,Bash'}
JOB_TIMEOUT = {'prepare': 900, 'draft': 1200, 'slides': 2700, 'guide': 2400, 'thumbnail': 1200}
JOB_BRIEF = {
    'prepare': ("Prepare this lesson so the script can be drafted. Read 08_lessons_writing.md, the course's gold standard scripts "
                "it names, the scripts for the lessons before and after this one, and the audience notes for this course. "
                "Then write preparation notes in \"prep\" with these headings. What the learner can do after it. The one key point. "
                "The pain to open on, in the learner's words. The sections, each with what it shows and an example from a function "
                "other than data. Josh's stories or material that fit. What the lesson before sets up and what the next one needs. "
                "Questions for Josh. Don't write the script yet."),
    'draft': ("Draft the script for this lesson. Load the script-touch-up skill and follow 08_lessons_writing.md, whose lesson "
              "rules override the skill where they differ. Work from the preparation notes and Josh's note. Read two of the gold "
              "standard scripts first. Send the whole file in \"script\", in the course's script file format (the title line, a "
              "short paragraph on what this version is, a line with ---, then Hook, Signpost, Section and Close headings). Run "
              "the checklist in 08 before you send it, and check the length against the minutes."),
    'slides': ("Build the space-bar slides for this lesson from the approved script below, following the lesson-slides skill and "
               "09_lessons_slides.md exactly. Start from the most recent slide file in this course's Slides folder, write a beat "
               "map first, then save the HTML into the Slides folder named number_date_vNN_slug_slides.html, matching the files "
               "already there. Never overwrite a version Josh has recorded with. The file must set const TOTAL and the global "
               "go(n) the other files use, because Josh OS steps through every beat to check it. Send the path in \"slides_path\", "
               "the beat count in \"beats\" and the cued script in \"cued_script\", quoting the script exactly with a red bold CHANGE n "
               "before the words each beat fires on, in the format 09 shows."),
    'guide': ("Make the written lesson guide PDF for this lesson, following 10_lessons_guides-thumbnails-upload.md and the BRIEF.md "
              "in the guide kit. Run the kit on this PC with the Python named in settings.json, which has Playwright. Save the HTML "
              "and PDF into the course's Lesson_Guides folder, named like the others, and send the PDF path in \"guide_pdf\"."),
    'thumbnail': ("Make the course app thumbnail for this lesson, following 10_lessons_guides-thumbnails-upload.md. Add or update its "
                  "entry in the kit's lessons.json and run build.py on this PC with the Python named in settings.json. Save the image "
                  "into the course's Thumbnails folder, named like the others, and send its path in \"thumbnail\"."),
}
# Team members who look after YouTube save each video's results, thumbnail tests and retention reading from chat.
VIDEO_PROPS = {
    'videos': {
        'type': 'array',
        'description': 'Results to save on videos in Josh OS. Leave empty when nothing needs saving. Only include the parts you are filling in.',
        'items': {'type': 'object', 'properties': {
            'project_id': {'type': 'string', 'description': 'Id of the video from the snapshot.'},
            'funnel': {'type': 'string', 'enum': ['top', 'middle', 'bottom']},
            'metrics': {'type': 'object', 'description': 'Numbers you read in YouTube Studio or Linklr: views, impressions, ctr (percent), '
                        'avg_view_duration (seconds), avg_percent_viewed, subscribers, emails, customers, revenue, and as_of (YYYY-MM-DD).'},
            'tests': {'type': 'array', 'description': 'The full list of thumbnail test rounds for the video, including rounds already saved.',
                      'items': {'type': 'object', 'properties': {
                          'id': {'type': 'string'}, 'changed': {'type': 'string', 'enum': ['text', 'visual', 'font', 'colour', 'face', 'layout', 'other']},
                          'hypothesis': {'type': 'string'}, 'screenshot': {'type': 'string', 'description': 'Keep the asset id already saved, give the full path of an image file on this PC, or leave it out.'},
                          'variants': {'type': 'array', 'items': {'type': 'object', 'properties': {
                              'label': {'type': 'string'}, 'text': {'type': 'string'}, 'look': {'type': 'string'},
                              'share': {'type': 'number', 'description': 'Watch time share in percent.'}, 'asset': {'type': 'string'}}}},
                          'winner': {'type': 'string', 'description': 'Label of the winning thumbnail, once YouTube shows one.'},
                          'why': {'type': 'string', 'description': 'Why it won, and what that teaches for future thumbnails.'}}}},
            'retention': {'type': 'object', 'properties': {
                'summary': {'type': 'string'}, 'notes': {'type': 'string'},
                'dips': {'type': 'array', 'items': {'type': 'object', 'properties': {
                    'at': {'type': 'string', 'description': 'Time in the video, like 3:40.'}, 'kind': {'type': 'string', 'enum': ['drop', 'hold', 'spike']},
                    'script': {'type': 'string', 'description': 'The script line at that point.'}, 'why': {'type': 'string'}}}}}},
            'review': {'type': 'string', 'description': 'Your short review of the video against its funnel stage.'},
            'transcript': {'type': 'string', 'description': 'The full transcript from YouTube Studio. Keep it out of the retention notes.'},
            'lessons': {'type': 'array', 'items': {'type': 'string'}, 'description': 'What this video taught, in a line each.'}},
            'required': ['project_id']},
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
    # Laia writes the emails. Her voice guide is loaded into every chat, and she reads the swipe files when she writes.
    'laia': {
        'embed': ['01_house-style.md'],
        'embed_abs': [r'E:\CareerOS\09_Email\voice-and-sales-language.md'],
        'read': [],
        'dirs': ['09_Email'],
    },
    # Saskia reads the course content and programme downloadables, and any saskia_*.md training files Josh adds.
    'saskia': {
        'embed': ['01_house-style.md'],
        'read': ['08_lessons_writing.md', '09_lessons_slides.md', '10_lessons_guides-thumbnails-upload.md'],
        'dirs': ['04_Programme', '08_Strategy'],
    },
    # Anouk helps customers get value from the courses, so she reads the course content and audience notes,
    # plus any anouk_*.md training files Josh adds.
    'anouk': {
        'embed': ['01_house-style.md'],
        'read': [],
        'dirs': ['04_Programme', '08_Strategy'],
    },
}


PERSONALITIES = Path('team') / 'personalities.md'
GOALS = Path('team') / 'goals'  # team/goals/<id>.md: what success means for that person, read every time
# Sections every team member reads, found by title so renumbering the file never breaks it.
SHARED_SECTIONS = ('purpose', 'shared', 'team dynamics', 'overall experience', 'the overall experience')


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
        return title in names or any(title.startswith(k) for k in SHARED_SECTIONS)
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
        for f in map(Path, spec.get('embed_abs', [])):
            if f.is_file():
                parts += ['', f'=== Training: {f.name} ===', f.read_text(encoding='utf-8')]
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
        goals = self.root / GOALS / f"{agent['id']}.md"
        if goals.is_file():
            lines += ['', f"=== Your goals (team/goals/{agent['id']}.md) ===", goals.read_text(encoding='utf-8')]
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
            *(['- Each video has a Results tab in Josh OS. When you read a video\'s numbers, thumbnail test or retention graph (from',
               '  YouTube Studio, Linklr or the screenshots Josh adds), save them with the "videos" list in your answer, and open',
               '  any screenshot path from the snapshot with Read before you describe it. Send the full list of test rounds when you',
               '  change tests. Follow your goals file for how to judge a video and what to learn from it.',
               '  Screenshots you take in Chrome stay in your chat and Josh OS has no way to attach them. Save the numbers and your reading anyway,',
               '  and ask Josh to drop the screenshots on the Results tab. Put a transcript in "transcript", never in the notes.',
               '  Only say something is saved when it is in this answer, and say so plainly if a save comes back as failed.']
              if 'youtube' in (agent.get('workflows') or []) and getattr(self, 'results', None) is not None else []),
            *(['- The Email page in Josh OS is the record for every email. Save to it with the "emails" list in your answer. Each',
               '  entry changes an email when you give its id from the snapshot, or adds a new draft without one. You can change the',
               '  subject, preview, body, planned_date (YYYY-MM-DD), send_time (HH:MM, Dubai), notes and description. Josh approves',
               '  emails himself, so never change their status, except setting sent when Kit shows one has gone out. Keep {{ subscriber.first_name }} and link wording as they are.',
               '  When you re-plan dates, spread the emails across open days from tomorrow and tell Josh the new plan.',
               '- You can read Kit through its tools (mcp__claude_ai_Kit__..., find them with ToolSearch). Use them to see what has',
               '  actually gone out and how each email did: opens, clicks, link clicks and unsubscribes. Josh sometimes sends drafts',
               '  himself in Kit, so match sent broadcasts to drafts by subject and wording. When one has gone out, set sent true',
               '  with its real send date and save its results. Never create, change or send anything in Kit.',
               '- Your writing material. Your voice guide is loaded above. When you write, read the files that fit the job in',
               '  E:\\CareerOS\\09_Email: Swipe-Files/subject-lines.md, hooks-and-openers.md, ctas.md, email-angles.md,',
               '  headline-formats.md, power-words.md, workshop-method.md, and parker-checkout-and-bumps.md for anything that sells.',
               '  sales-page-patterns.md is the long form voice, and Sequences/ holds past sequences.',
               '- The email library is every Ed Lawrence and Jon Brosio email we have, in References/EmailExamples in Josh OS',
               '  (EdLawrence/newsletters.json and newsletters.md, JonBrosio/corpus.json, and additions.json in each). Before you',
               '  draft, search it with Grep for how they handled a similar idea, subject or call to action.',
               '- When an email is based on one from the library, link it in source_ids so Josh can read the two side by side.',
               '  The id is the folder and the email id: JonBrosio:<id> from corpus.json, EdLawrence:<message_id> from',
               '  newsletters.json, or the folder and id from additions.json. Copy the id exactly.',
               '- Keep the library up to date. When Josh asks, search Gmail (mcp__claude_ai_Gmail__search_threads, find it with',
               '  ToolSearch) for emails from Ed Lawrence (hi@film-booth.com) and Jon Brosio since the newest one in the library,',
               '  read each with get_message or get_thread, and save each newsletter in the "library" list with its full body text,',
               '  message id, subject, date and Gmail link. Skip receipts, account notices and anything that is not a newsletter.',
               '  Gmail is for reading only. Never send, label, archive, delete or draft anything there.',
               '- Learn from the results. Compare open rates by subject line style and click rates by where the link sits and how',
               '  it is worded, save what works as lessons, and use it in the next drafts.',
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
        goals = self.root / GOALS / f"{agent['id']}.md"
        if goals.is_file():
            lines += ['', f"=== Your goals (team/goals/{agent['id']}.md) ===", goals.read_text(encoding='utf-8')]
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
        results = getattr(self, 'results', None) if 'youtube' in (agent.get('workflows') or []) else None
        if results is None:
            for p in sorted(videos, key=lambda p: p.get('publish_date') or '9999')[:40]:
                lines.append(f"- {p.get('publish_date') or 'no date'} | {p['stage']} | {p.get('chosen_title') or p['name']}")
            return '\n'.join(lines)
        lines[-1] = ('Videos in Josh OS and their results. id | date | stage | title | funnel | numbers | thumbnail tests | retention. '
                     'Screenshot paths are image files you can open with Read:')
        saved = results.all()
        def shot(aid):
            try:
                return str(self.store.asset(aid)[0]) if aid else ''
            except ValueError:
                return ''
        for p in sorted(videos, key=lambda p: p.get('publish_date') or '9999')[:40]:
            r = saved.get(p['id']) or {}
            tests = '; '.join(f"round {t['round']} ({t.get('changed') or 'not set'}): "
                              + ', '.join(f"{v['label']} \"{v.get('text', '')}\" {v.get('share') if v.get('share') is not None else '?'}%" for v in t.get('variants', []))
                              + (f", winner {t['winner']}" if t.get('winner') else ', no winner yet')
                              + (f", screenshot {shot(t.get('screenshot'))}" if t.get('screenshot') else '')
                              for t in r.get('tests', [])) or 'none recorded'
            ret = r.get('retention') or {}
            retention = (f"screenshot {shot(ret.get('screenshot'))}" if ret.get('screenshot') else 'no graph yet') + (', read' if ret.get('dips') else '')
            lines.append(f"- {p['id']} | {p.get('publish_date') or 'no date'} | {p['stage']} | {p.get('chosen_title') or p['name']} | "
                         f"{r.get('funnel') or 'funnel not set'} | {json.dumps(r.get('metrics') or {})} | {tests} | {retention}")
        return '\n'.join(lines)

    def lesson_snapshot(self, agent):
        courses = getattr(self, 'courses', None)
        if courses is None or 'courses' not in (agent.get('workflows') or []):
            return ''
        lines = ['Course lessons in Josh OS. id | course | number | title | stage | what it has:']
        for l in courses.all()['items']:
            has = ', '.join(k for k, v in l['has'].items() if v) or 'nothing yet'
            lines.append(f"- {l['id']} | {l['course']} | {l['number']} | {l['title']} | {l['stage']} | {has}")
        return '\n'.join(lines)

    def apply_lessons(self, courses, updates):
        done = []
        for u in (updates or [])[:10]:
            if not isinstance(u, dict):
                continue
            lid = str(u.get('lesson_id') or '')
            try:
                current = courses.get(lid)
                changes = {k: u[k] for k in ('script', 'prep', 'notes') if isinstance(u.get(k), str) and u[k].strip()}
                if changes:
                    courses.save(lid, changes, current['revision'])
                done.append(dict(id=lid, title=f"{current['number']} {current['title']}", action='updated', kind='lesson'))
            except Exception as e:  # noqa: BLE001 - every failure is shown in the chat
                done.append(dict(id=lid, title='', action='failed', kind='lesson', error=str(e).strip("'\"")[:200]))
        return done

    def course_job(self, lesson, task, note, content_dir):
        """One piece of lesson work for Saskia, with the tools and time it needs. Josh OS checks what comes back."""
        agent = next((a for a in self.team.all()['agents'] if a['id'] == 'saskia'), None)
        if not agent:
            raise AgentError('Saskia is not on the team.')
        learnings = [x for x in self.team.all()['learnings'] if x['agent_id'] in ('', 'saskia')][:80]
        training_text, dirs = self.training('saskia')
        course_dir = Path(content_dir) / {'hatt': 'Heard_at_the_Top', 'career': 'Career_Track', 'practical': 'Practical_Career_Track',
                                          'data': 'Data_Storytelling'}[lesson['course']]
        keep = ('id', 'course', 'number', 'title', 'type', 'minutes', 'part', 'part_name', 'stage', 'source_url', 'notes')
        facts = {k: lesson.get(k) for k in keep if lesson.get(k) not in (None, '')}
        facts['files'] = {k: v.get('path') for k, v in (lesson.get('files') or {}).items()}
        facts['lesson_before'] = (lesson.get('neighbours') or {}).get('previous')
        facts['lesson_after'] = (lesson.get('neighbours') or {}).get('next')
        parts = [JOB_BRIEF[task], '', f'This course\'s folder is {course_dir}. Your training files are in {self.training_dir()}.',
                 '', 'The lesson:', json.dumps(facts, ensure_ascii=False, indent=1)]
        if note.strip():
            parts += ['', 'Josh\'s note: ' + note.strip()]
        if lesson.get('prep') and task != 'prepare':
            parts += ['', 'Preparation notes:', lesson['prep']]
        if lesson.get('script') and task != 'prepare':
            parts += ['', 'The approved script:' if task == 'slides' else 'The current script:', lesson['script']]
        system = self.desk_system_prompt(agent, learnings) + '\n\n' + '\n'.join([
            'You are doing one piece of lesson work that Josh started from the Lessons page in Josh OS. Do the work fully, then',
            'answer once. Josh OS saves what you send back and checks any file you name, so name the real path of every file you',
            'save, and only save files inside the course folder. Never touch recordings, and never move or delete Josh\'s files.'])
        return self.run(system, '\n'.join(parts), LESSON_JOB_SCHEMA, list(dirs) + [str(course_dir), str(Path(content_dir).parent.parent / '08_Strategy')],
                        tools=JOB_TOOLS[task], timeout=JOB_TIMEOUT[task], full=True)

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
    def run(self, system, prompt, schema=SCHEMA, dirs=(), chrome=False, extra_tools=(), full=False, tools=None, timeout=None):
        claude = find_claude(self.root)
        if not claude:
            raise AgentError('I can\'t reach Claude on this PC yet. Install Claude Code and sign in, then try again.')
        with tempfile.TemporaryDirectory() as tmp:
            sys_file = Path(tmp) / 'system.md'
            sys_file.write_text(system, encoding='utf-8')
            wrapper = claude.lower().endswith(('.cmd', '.bat'))
            cmd = [claude, '-p', '--output-format', 'json', '--no-session-persistence',
                   '--append-system-prompt-file', str(sys_file),
                   '--tools', tools or ALLOWED_TOOLS, '--allowedTools', ','.join([tools or ALLOWED_TOOLS] + ([CHROME_TOOLS] if chrome else []) + list(extra_tools))]
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
                                     cwd=str(self.root), timeout=timeout or (TIMEOUT * 2 if chrome else TIMEOUT),
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
        if kind == 'lesson' and getattr(self, 'courses', None) is not None:
            try:
                l = self.courses.get(cid)
            except KeyError:
                return ''
            body = keep(l, ('id', 'course', 'number', 'title', 'type', 'minutes', 'part_name', 'stage', 'next', 'prep', 'notes', 'script'))
            body['files'] = {k: v.get('path') for k, v in (l.get('files') or {}).items()}
            return ('Josh has this lesson open right now, so "this" and "the script" mean this one. To change it, send it in the '
                    '"lessons" list with this id, with the whole script if you rewrite it. Approving, building slides and publishing '
                    'are buttons on the lesson for Josh, so tell him when one is ready rather than doing it.\n'
                    + json.dumps(body, ensure_ascii=False, indent=1))
        if kind == 'project':
            try:
                p = self.store.get(cid)
            except KeyError:
                return ''
            title = next((t.get('text') for t in p.get('titles') or [] if t.get('id') == p.get('chosen_title')), '') or p['name']
            results = getattr(self, 'results', None)
            saved = results.get(cid) if results is not None else None
            return ('Josh has this video open right now, so "this video" means this one. You can read it but not change the video '
                    'card from this chat, so say what to change. '
                    + ('You can save its results, thumbnail tests and retention reading with the "videos" list, using this id. '
                       'The full script is below, so line retention moments up with the exact words.' if saved is not None else '')
                    + '\n' + json.dumps(dict(id=p['id'], name=p['name'], title=title, stage=p['stage'], publish_date=p.get('publish_date'),
                                            notes=(p.get('notes') or '')[:2000], script=(p.get('script') or '')[:20000],
                                            results=saved), ensure_ascii=False, indent=1))
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
            lessons = self.lesson_snapshot(agent)
            if lessons:
                parts += ['', lessons]
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
            results = getattr(self, 'results', None) if 'youtube' in (agent.get('workflows') or []) else None
            if results is not None:
                schema['properties'].update(VIDEO_PROPS)
            courses = getattr(self, 'courses', None) if 'courses' in (agent.get('workflows') or []) else None
            if courses is not None:
                schema['properties'].update(LESSON_PROPS)
                dirs = list(dirs) + [str(self.store.root / 'assets')]
            reply, answer = self.run(self.desk_system_prompt(agent, learnings), '\n'.join(parts),
                                     schema, dirs, chrome=agent_id in CHROME and not proactive,
                                     extra_tools=['mcp__linklr__' + t for t in LINKLR.get(agent_id, ())] + list(KIT.get(agent_id, ())) + list(GMAIL.get(agent_id, ())), full=True)
            answer = answer if isinstance(answer, dict) else {}
            todo = answer.get('carousels')
            saved = tracker.apply(agent_id, todo) if keeps and isinstance(todo, list) else []
            found = answer.get('inspo')
            if keeps and inspo is not None and isinstance(found, list):
                saved += inspo.apply(found)
            if mails is not None and isinstance(answer.get('emails'), list):
                saved += self.apply_emails(mails, answer['emails'])
            if results is not None and isinstance(answer.get('videos'), list):
                saved += results.apply(answer['videos'])
            if courses is not None and isinstance(answer.get('lessons'), list):
                saved += self.apply_lessons(courses, answer['lessons'])
            if mails is not None and isinstance(answer.get('library'), list):
                saved += mails.add_references(answer['library'][:40])
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
            if u.get('sent') is True:
                changes['status'] = 'Sent'
            if isinstance(u.get('results'), dict):
                changes['results'] = {k: v for k, v in u['results'].items() if v is not None}
            if isinstance(u.get('source_ids'), list):
                known = mails.references()
                changes['source_ids'] = [str(x) for x in u['source_ids'] if str(x) in known][:5]
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
        home = {'sam': 'carousels', 'laia': 'email', 'tariq': 'editing', 'saskia': 'courses', 'anouk': 'customers'}.get(agent['id'], '')
        norm = lambda t: ' '.join(str(t).lower().split()).rstrip('.')
        seen = {norm(l['lesson']) for l in self.team.all()['learnings'] if l['agent_id'] in ('', agent['id'])}
        for l in lessons[:5]:
            if not isinstance(l, dict) or not str(l.get('lesson', '')).strip() or norm(l['lesson']) in seen:
                continue
            seen.add(norm(l['lesson']))
            flow = l.get('workflow') if l.get('workflow') in ('youtube', 'carousels', 'editing', 'email', 'courses', 'customers') else home
            try:
                item = self.team.learn(dict(agent_id=agent['id'], workflow=flow, lesson=str(l['lesson'])[:2000],
                                            evidence=str(l.get('evidence', ''))[:2000]))
                kept.append(item['lesson'])
            except (ValueError, KeyError):
                continue
        return kept
