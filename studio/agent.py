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
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from .store import now

SKILLS = {'sam': ['script-touch-up'], 'tariq': ['video-editing-prep'], 'laia': [], 'saskia': ['lesson-slides', 'careeros-playbook', 'written-guides'], 'anouk': [], 'max': []}
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
LINKLR = {'sam': LINKLR_READ + LINKLR_MAKE, 'laia': LINKLR_READ + LINKLR_MAKE, 'max': LINKLR_READ, 'anouk': LINKLR_READ}
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
KIT = {'laia': KIT_READ, 'max': KIT_READ, 'anouk': KIT_READ}
# Reading Gmail, only to collect new emails from the creators in the library. Nothing that sends, labels or deletes.
GMAIL_READ = tuple('mcp__claude_ai_Gmail__' + t for t in ('search_threads', 'get_thread', 'get_message', 'list_labels'))
# Anouk reads Gmail and saves replies as drafts for Josh to send. Nothing that sends, labels or deletes.
GMAIL_DRAFT = tuple('mcp__claude_ai_Gmail__' + t for t in ('create_draft', 'list_drafts', 'get_draft', 'update_draft'))
GMAIL = {'laia': GMAIL_READ, 'anouk': GMAIL_READ + GMAIL_DRAFT}
# Calendly through the connector linked to Claude on this PC: reading calls, and single-use links to book a meeting.
CALENDLY_READ = tuple('mcp__claude_ai_Calendly__' + t for t in (
    'users-get_current_user', 'meetings-list_events', 'meetings-get_event', 'meetings-list_event_invitees',
    'event_types-list_event_types', 'availability-list_user_busy_times', 'scheduling_links-create_single_use_scheduling_link'))
CALENDLY = {'anouk': CALENDLY_READ}
# Team members still being set up. They get none of their outside tools (Gmail, Calendly, Kit, Linklr, Chrome)
# until Josh adds their id to "team_live" in settings.json. Until then they plan and draft in chat only.
SETUP_MODE = {'anouk'}
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
        'description': 'Changes to course lessons in Josh OS, and work to start on them. Leave empty when nothing needs saving.',
        'items': {'type': 'object', 'properties': {
            'lesson_id': {'type': 'string', 'description': 'Id of the lesson from the snapshot. Leave it out to add a new lesson.'},
            'course': {'type': 'string', 'enum': ['hatt', 'career', 'practical', 'data'], 'description': 'New lessons only. The course.'},
            'number': {'type': 'string', 'description': 'New lessons only. The lesson number, like 3.5.'},
            'part_name': {'type': 'string', 'description': 'New lessons only, and only for a new part. The part name.'},
            'type': {'type': 'string', 'description': 'New lessons only. Lesson, Exercise or Download. Lesson if unsure.'},
            'title': {'type': 'string', 'description': 'The lesson title, to rename it or for a new lesson.'},
            'minutes': {'type': 'number', 'description': 'Planned length in minutes.'},
            'script': {'type': 'string', 'description': 'The whole script, when you write or rewrite it. Never a part of it.'},
            'prep': {'type': 'string', 'description': 'Preparation notes for the lesson.'},
            'notes': {'type': 'string', 'description': 'A short note on what changed and why.'},
            'stage': {'type': 'string', 'description': 'Move the lesson to this stage. Josh OS refuses a move its gates block. Published means it is already live in the course app.'},
            'start': {'type': 'string', 'enum': ['prepare', 'draft', 'slides', 'guide', 'thumbnail'],
                      'description': 'Start this piece of work on the lesson now. It runs on this PC after your reply.'},
            'start_note': {'type': 'string', 'description': 'What Josh asked for, passed to the job.'},
            'publish': {'type': 'boolean', 'description': 'Publish the lesson to the CareerOS Google Drive folder now. It needs the video, guide and thumbnail.'}}},
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
# Saskia writes standalone written guides from any chat: a document of its own on one topic, in the lesson guides' house
# style, built to HTML and PDF in the Guides folder. A job Josh OS runs after the reply, like the lesson work.
GUIDE_PROPS = {
    'written_guide': {
        'type': 'object',
        'description': 'Start a standalone written guide when Josh asks for one, from any chat. It is its own explanation document on '
                       'one topic for the reader to keep and use, not a breakdown of a lesson. It runs on this PC after your reply '
                       'and you post the PDF back when it is done. Leave it out unless he asked for a guide or document like this.',
        'properties': {
            'kind': {'type': 'string', 'enum': ['working', 'companion'],
                     'description': 'working: a tactical working document for someone who has studied the lessons and is now writing '
                                    'the real thing (a goals form, a review), organised by the writing task. companion: a guide that '
                                    'follows a set of lessons. When unsure, working. See the written-guides skill.'},
            'topic': {'type': 'string', 'description': 'What the guide is about, in a few words.'},
            'brief': {'type': 'string', 'description': 'Everything Josh asked for: who it is for, what it should cover, examples or '
                                                       'material he named, length, and anything to avoid. Quote him where you can.'},
        },
        'required': ['topic', 'brief'],
    },
}
GUIDE_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string', 'description': 'What you made, for Josh, in a few sentences, and anything he needs to confirm.'},
        'pdf': {'type': 'string', 'description': 'The full path of the guide PDF the builder saved.'},
        'html': {'type': 'string', 'description': 'The full path of the guide HTML the builder saved.'},
    },
    'required': ['reply'],
}
GUIDE_BRIEF = """Make a standalone written guide of the kind given below. The written-guides skill below says what each kind
is and how it's structured, and it is the brief. Follow its section for this kind exactly, and never confuse the two. A working
document is organised around the writing task the reader is doing now, and a companion follows the lessons.

1. Read the house brief {kit}\\BRIEF.md, the exemplar {kit}\\lessons\\3.1.html, base.css, extra.css and the macro list at
   the top of {kit}\\make.py for the components and voice. For a working document, also read the opening and one letter's
   section of the BENS Dictionary, {bens}, the practical style reference. Read 08_Strategy\\audience.md and the voice notes
   your training files point to. Read the real lessons the topic comes from, and say in your reply which files you used.
2. Save the body to {work}\\guide.html. Its first line is <!-- title: The title | slug: the-slug -->. The cover eyebrow is
   "CareerOS · Guide" for a companion and "CareerOS · Working document" for a working document.
3. Build it with
       "{python}" -m studio.written_guide "{work}\\guide.html" "{out}"
   run from {root}. It prints the HTML and PDF paths, the page count and a picture of the guide in print layout. Read the
   picture and check every page for layout and for use, as the skill says, then fix and build again (each build is a new
   version, so earlier ones stay). Grep guide.html for the banned words and dashes before the last build.
4. Answer once, with the final pdf and html paths, the source files you used and the examples you wrote yourself."""
# Sam saves YouTube videos Josh drops into chat (Josh OS or Discord) to the inspiration library, once per video ID.
YOUTUBE_SAVE_PROPS = {
    'save_youtube': {
        'type': 'array',
        'description': 'YouTube videos Josh asks you to save as ideas or inspiration, one item per link. Josh OS saves each to the '
                       'inspiration library with its title, channel, date, views and thumbnail, and adds a line to your reply saying '
                       'whether it was saved or was already there, with the details. So keep your own reply to one short sentence, like "On it." or a line on why it\'s a good one, and don\'t say whether it\'s new or already saved, or mention the line. Leave empty unless he asked you to save a video.',
        'items': {'type': 'object', 'properties': {
            'url': {'type': 'string', 'description': 'The YouTube link exactly as Josh gave it.'},
            'note': {'type': 'string', 'description': 'Why he wants it, if he said, in his words.'}},
            'required': ['url']},
    },
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
# Every team member can change a video card from chat, whatever page Josh is on.
# The video editor works on a video's animation deck from the edit review.
EDIT_PROPS = {
    'edit': {
        'type': 'object',
        'description': 'Actions on the open video\'s edit. Leave out when nothing needs doing.',
        'properties': {
            'project_id': {'type': 'string', 'description': 'The video, when Josh is not on its animation review. Id from the snapshot.'},
            'deck_change': {'type': 'string', 'description': 'When Josh asks from chat (not the review page) for the slides or shot list to be '
                                                            'made or changed: exactly what to do. Josh OS runs it as a job on that video and posts back.'},
            'setup': {'type': 'boolean', 'description': 'True to make the video\'s edit folders the way Josh lays them out.'},
            'rerender': {'type': 'array', 'items': {'type': 'integer'},
                         'description': 'Animation numbers whose review pictures need taking again. Josh OS also does this by itself when the slides file changes.'},
            'animation_notes': {'type': 'string', 'description': 'Ideas Josh gives you in chat for this video\'s animations (what a screen '
                                                                  'shows, a running device like cards or a tracker, a picture in picture moment), written as a '
                                                                  'note to keep. Josh OS adds it to the notes box on the Animations tab, which you read when you '
                                                                  'make the slides.'},
            'go_ahead': {'type': 'boolean', 'description': 'True when Josh wants you to start editing this video, however he puts it (for '
                                                          'example "go ahead", "start the edit", "can you get this one cut", "crack on with it"). '
                                                          'Judge what he means, not the exact words. It runs the stages in order: gather the files, set '
                                                          'up Premiere, transcribe and propose the cut. Josh OS runs it on this PC and you post back when '
                                                          'it is ready for his review. Leave it out when he is only asking about the edit or which video '
                                                          'he means is unclear (ask him instead).'},
        },
    },
}
PROJECT_PROPS = {
    'projects': {
        'type': 'array',
        'description': 'Changes to video cards in Josh OS. Leave empty when nothing needs saving. Only include the fields you change.',
        'items': {'type': 'object', 'properties': {
            'project_id': {'type': 'string', 'description': 'Id of the video from the snapshot or the one Josh has open.'},
            'name': {'type': 'string'},
            'stage': {'type': 'string', 'enum': ['Idea', 'Scripting', 'Ready to record', 'Recording', 'Recorded', 'Editing', 'Review',
                                                 'Scheduled', 'Published']},
            'publish_date': {'type': 'string', 'description': 'YYYY-MM-DD.'},
            'notes': {'type': 'string', 'description': 'The whole angle and notes field, when you change it.'},
            'script': {'type': 'string', 'description': 'The whole script, when Josh asks you to change it directly. Never a part of it.'},
            'add_titles': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Title ideas to add to the video.'}},
            'required': ['project_id']},
    },
    'replacement': {'type': ['string', 'null'],
                    'description': 'When Josh has selected a passage of the open script, the rewritten passage ready to drop in. '
                                   'Null otherwise.'},
}
def edit_rule(folder):
    """Claude Code permission rule that allows Write and Edit only inside one folder (tested on this PC: a bare
    "Write" or "Edit" allows writing anywhere, while Edit(//e/<path>/**) allows that folder and refuses the rest)."""
    f = Path(folder).resolve()
    rest = f.as_posix().split(':', 1)[1].strip('/')
    return f"Edit(//{f.drive[0].lower()}/{rest}/**)"


def scoped(tools, folder):
    """The allow list for a run that may write: the read tools as they are, file edits only inside folder."""
    names = [t for t in tools.split(',') if t]
    keep = [t for t in names if t not in ('Write', 'Edit')]
    return ','.join(keep + ([edit_rule(folder)] if any(t in ('Write', 'Edit') for t in names) else []))


# Sam makes YouTube thumbnails as a job Josh OS runs after the reply (see video_thumbnails.py).
CAN_THUMB = {'sam'}
THUMB_PROPS = {
    'thumbnails': {
        'type': 'array',
        'description': 'Start a thumbnail job for a video. It runs on this PC after your reply and posts the images back here.',
        'items': {'type': 'object', 'properties': {
            'project_id': {'type': 'string', 'description': 'Id of the video from the snapshot, or the one Josh has open.'},
            'note': {'type': 'string', 'description': 'What Josh asked for: words, formats, poses, how many, anything to copy.'}},
            'required': ['project_id']},
    },
}
# What a thumbnail job sends back: up to three options, each a format and its settings. Josh OS checks and builds them.
THUMB_JOB_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string', 'description': 'For Josh, in a few sentences: the options and why, in your own voice.'},
        'options': {'type': 'array', 'maxItems': 3, 'items': {'type': 'object', 'properties': {
            'label': {'type': 'string', 'description': 'A short name, like A Make Them Explain.'},
            'format': {'type': 'string', 'enum': ['middle', 'close']},
            'pose': {'type': 'string', 'description': 'The cutout file name without .png, like seated-13-palm-present-neutral.'},
            'words': {'type': 'string', 'description': 'middle. The one line of words.'},
            'lines': {'type': 'array', 'items': {'type': 'string'}, 'description': 'close, or either format with a style. One or two lines of words.'},
            'style': {'type': 'object', 'description': 'Leave out for the house look. Set it for another font, colour, size or bar.', 'properties': {
                'font': {'type': 'string', 'description': 'A font name from the list in the brief.'},
                'weight': {'type': 'string', 'description': 'One of the weights listed for that font. Leave out for its heaviest.'},
                'color': {'type': 'string', 'description': 'Words colour, a name from the list or a hex code like #FFE500.'},
                'size': {'type': 'number', 'description': 'Font size in pixels on the 1920 wide frame, 60 to 360.'},
                'fit': {'type': 'string', 'enum': ['size', 'fill'], 'description': 'size uses the size (shrunk if it would not fit). fill makes the words fill the bar width.'},
                'bar': {'type': 'boolean', 'description': 'A coloured bar behind the words.'},
                'bar_color': {'type': 'string'},
                'bolden': {'type': 'number', 'description': '0 to 6. Thickens the letters.'},
                'outline': {'type': 'number', 'description': '0 to 14. An outline round the letters.'},
                'outline_color': {'type': 'string'},
                'shadow': {'type': 'boolean', 'description': 'The house glow and drop shadow. On unless you turn it off.'},
                'case': {'type': 'string', 'enum': ['as-is', 'upper']}}},
            'size': {'type': 'number', 'description': 'close only. Font size in 1280-wide units, about 142.'},
            'face_cx': {'type': 'number'}, 'face_cy': {'type': 'number'}, 'face_w': {'type': 'number'},
            'head_top': {'type': 'number'}, 'body_bottom': {'type': 'number'}, 'desk_top': {'type': 'number'},
            'baseline': {'type': 'number'}, 'bar_left': {'type': 'number'}, 'bar_right': {'type': 'number'},
            'shade_start': {'type': 'number'}},
            'required': ['label', 'format', 'pose']}},
    },
    'required': ['reply', 'options'],
}
THUMB_BRIEF = """Make a round of YouTube thumbnail options for this video, following 05_thumbnails.md. You can read files but you
do not run anything or save anything yourself. Answer with up to three options in "options". Josh OS checks each one and
builds it with Josh's approved scripts (oneline.py for "middle", close_unit.py for "close"), in the house text treatment.

The two formats you can use:
- "middle": Josh seated at the desk, one line of words across the bottom (the current A/B winner). Settings and defaults:
  pose (a Seated cutout, seated-13-palm-present-neutral is the approved one), face_cx 960, head_top 22, body_bottom 1100,
  desk_top 1008, words (two to four words), baseline 975, bar_left 130, bar_right 1790 (never past 1790, the badge corner),
  shade_start 400. Leave a number out to use its default.
- "close": Josh close on the left, one or two lines of words on the right. pose (finger up, chin or side point work, like
  seated-06-side-point-neutral), face_cx 483, face_cy 350, face_w 432, lines (one or two, short), size about 142, shade_start 325.
  The line positions are fixed by the format.
Read the approved specs in the tools folder (spec_make-them-explain_desk-bottom_v01.json, spec_close_never-go-blank_v03.json)
and the thumb-notes files the training names. Look at the pose previews in the Pose Catalogue before you pick a pose.
Text style. Leave "style" out for the house look (Josh Thumbnail V2, white words on the yellow bar). When Josh asks for
another font, colour, size or weight, set "style" and give the words in "lines" (one or two). Josh OS shrinks words that
would run off the frame and tells Josh it did. Three or more big words stay bigger split over two lines. Fonts you can use, as font: weights:
{fonts}
Colours by name: {colours}, or any hex code. Big bold yellow words, for example, are style font anton (or impact, chivo,
gotham black), color bright-yellow, bar false, size 220 to 300, case upper, with the shadow on and outline 0 to 6 in black.
If Josh asks for a font that isn't in this list, say it isn't available and pick the closest one from the list.
Pick words that name the outcome and add to the title rather than repeat it. The other formats in the training (boardroom,
whiteboard, before and after, close face) can't be built from chat yet, so say so if Josh asks for one.
Your reply is posted together with the finished images, so describe the options as made, not as on the way."""
# Tariq's editorial pass on a recording (see edit_flow.py). Josh OS validates every id before using it.
EDIT_JOB_SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string', 'description': 'For Josh, a few sentences on what you removed and what you kept on purpose.'},
        'removals': {'type': 'array', 'maxItems': 400, 'items': {'type': 'object', 'properties': {
            'start_word': {'type': 'string', 'description': 'Id of the first word to remove, like w00012.'},
            'end_word': {'type': 'string', 'description': 'Id of the last word to remove.'},
            'kind': {'type': 'string', 'enum': ['retake', 'abandoned_start', 'false_start', 'other']},
            'reason': {'type': 'string'},
            'unsure': {'type': 'boolean', 'description': 'True when Josh should listen before this is cut.'}},
            'required': ['start_word', 'end_word', 'kind', 'reason']}},
        'checks': {'type': 'array', 'maxItems': 200, 'items': {'type': 'object', 'properties': {
            'start_word': {'type': 'string'}, 'end_word': {'type': 'string'},
            'reason': {'type': 'string', 'description': 'What Josh should listen for, like "this may be a reworded retake of w00410 to w00431".'}},
            'required': ['start_word', 'end_word', 'reason']},
            'description': 'Moments you would keep but want Josh to hear before he approves.'},
        'pauses': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'event_id': {'type': 'string'}, 'action': {'type': 'string', 'enum': ['keep', 'shorten']},
            'target_ms': {'type': 'number', 'description': 'For shorten: how long the pause should be, in ms.'},
            'reason': {'type': 'string'}}, 'required': ['event_id', 'action', 'reason']}},
    },
    'required': ['reply', 'removals', 'pauses'],
}
EDIT_BRIEF = """Propose the clean cut for this recording. Josh will see the whole transcript with your removals struck through,
and he decides. You can read files but you do not run or change anything.

Retakes. Josh restarts sentences when he stumbles. Remove the earlier, abandoned attempts and keep the last complete take,
but check the earlier one adds no fact, qualifier or emphasis the kept one lacks. A retake isn't always the same words.
Often he starts a sentence, stops, and says it again slightly reworded, so compare meaning, not just wording, across the
next few sentences. Repeated words can be deliberate (lists,
emphasis, a callback, a new sentence that starts the same way), so do not remove a repeat just because it repeats. Give each
removal as the first and last word id. Mark unsure when Josh should listen first.

Checks. Put anything you would keep but are not sure about in checks: a possible reworded retake, a line that trails off,
a word that reads like a stumble or a mistranscription. Josh hears each one with a few seconds either side.

Pauses. Only the long pauses are marked. Keep a pause that does work (a thought boundary, a rhetorical question, set-up
before a payoff, emphasis). Shorten one that is dead air, and say how long it should be. When unsure, keep it. Never shorten
a pause to remove time for its own sake.

Do not cut sounds, coughs or breaths. Josh OS measures those separately and Josh reviews them."""
# Anouk keeps the customer success tracker from chat.
PEOPLE_PROPS = {
    'people': {
        'type': 'array',
        'description': 'People to add or update in the customer success tracker. Leave empty when nothing needs saving.',
        'items': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'Id from the snapshot to update someone. Leave out to add, or give their email.'},
            'name': {'type': 'string'}, 'email': {'type': 'string'},
            'pathway': {'type': 'string', 'enum': ['reply', 'lead', 'client', 'buyer']},
            'stage': {'type': 'string', 'description': 'A stage from that pathway, exactly as written in the snapshot.'},
            'source': {'type': 'string', 'description': 'Where they came from, like the newsletter they replied to or the call date.'},
            'summary': {'type': 'string', 'description': 'Who they are, their situation and what they want, in a few lines.'},
            'next_step': {'type': 'string'}, 'next_due': {'type': 'string', 'description': 'YYYY-MM-DD'},
            'promises': {'type': 'array', 'description': 'Things Josh promised them. Send only the ones you are adding or ticking off.',
                         'items': {'type': 'object', 'properties': {'text': {'type': 'string'}, 'due': {'type': 'string'}, 'done': {'type': 'boolean'}},
                                   'required': ['text']}},
            'tags': {'type': 'array', 'items': {'type': 'string'}},
            'add_history': {'type': 'array', 'description': 'New touchpoints to log, oldest first.',
                            'items': {'type': 'object', 'properties': {
                                'at': {'type': 'string', 'description': 'YYYY-MM-DD'},
                                'direction': {'type': 'string', 'enum': ['in', 'out', 'note', 'draft'],
                                              'description': 'in = they wrote, out = Josh sent, draft = you saved a Gmail draft, note = anything else'},
                                'channel': {'type': 'string', 'description': 'email, call, course, kit and so on'},
                                'summary': {'type': 'string'}, 'link': {'type': 'string', 'description': 'Gmail thread or other link'}},
                                'required': ['summary']}}}},
    },
}
# On Discord, an answer can send files back to Josh: existing files only, inside CareerOS or Josh OS.
SEND_FILES_PROPS = {
    'send_files': {'type': 'array', 'items': {'type': 'string'},
                   'description': 'Full paths of files to send Josh with this reply on Discord, such as a slide image, a PDF or a '
                                  'script file you made or found. Only files that already exist. Up to 10.'},
}
MAX_SEND_BYTES = 25 * 1024 * 1024
# Every chat answer can also note which of Josh's edits it covered, and save lessons.
LEARN_PROPS = {
    'edits_noted': {'type': 'array', 'items': {'type': 'string'},
                    'description': "Ids of Josh's edits you asked about or explained in this reply."},
    'learned': {'type': 'array', 'description': 'Rules you now know for next time, from his edits or his answers.',
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
MAX_CHAT_IMAGES = 6
DESK = 'desk'  # thread key for the general chat on a team member's page

# Training files each team member works from. The folder can be moved with "training_dir" in settings.json.
TRAINING_DIR = r'E:\CareerOS\_Agent-Training'
TRAINING = {
    # Tariq edits the videos. His handoff (tariq_video-editing-handoff.md) is listed for him to read, and he can read the YouTube folders.
    'tariq': {'embed': [], 'read': [], 'dirs': ['12_YouTube']},
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
PLAYBOOKS = Path('team') / 'playbooks'  # team/playbooks/<id>/*.md: step-by-step flows Josh writes, followed exactly
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


DECK_PROGRESS = '.tariq-progress.json'  # Tariq writes his stage here while he makes a deck

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

    def chat_files(self, data):
        """Files Josh attached on Discord (not images), saved by the bridge in Data/discord/inbox."""
        inbox = (self.store.root / 'discord' / 'inbox').resolve()
        out = []
        for f in (data.get('files') or [])[:10]:
            path = Path(str(f)).resolve()
            if not path.is_file() or not path.is_relative_to(inbox):
                raise ValueError('One of those files is no longer saved. Send it again.')
            out.append(str(path))
        return out

    def files_to_send(self, paths):
        """Files an agent wants to send back on Discord: real files inside CareerOS or Josh OS, within Discord's size limit."""
        roots = [self.store.root.resolve(), self.root.resolve(), self.training_dir().parent.resolve()]
        out = []
        for f in (paths or [])[:10] if isinstance(paths, list) else []:
            try:
                path = Path(str(f)).resolve()
            except (OSError, ValueError):
                continue
            if path.is_file() and path.stat().st_size <= MAX_SEND_BYTES and any(path.is_relative_to(r) for r in roots) and str(path) not in out:
                out.append(str(path))
        return out

    def chat_images(self, data):
        """The images Josh attached to a chat message, as saved asset ids. Unknown ids are an error."""
        ids = data.get('images') or []
        if not isinstance(ids, list) or len(ids) > MAX_CHAT_IMAGES:
            raise ValueError(f'Attach up to {MAX_CHAT_IMAGES} images to one message.')
        out = []
        for aid in ids:
            try:
                self.store.asset(str(aid))
            except (ValueError, TypeError):
                raise ValueError('One of those images is no longer saved. Attach it again.') from None
            if str(aid) not in out:
                out.append(str(aid))
        return out

    def image_lines(self, ids):
        paths = []
        for aid in ids:
            try:
                paths.append(f'{self.store.asset(aid)[0]} (asset id {aid})')
            except ValueError:
                continue
        return paths

    def history_line(self, m):
        who = 'Josh' if m['role'] == 'josh' else 'You'
        line = f"{who}: {m['text']}"
        if m.get('selection'):
            line = f"{who} (about the passage \"{m['selection']['text'][:200]}\"): {m['text']}"
        if m.get('images'):
            line += ' [attached images, which you can open with Read: ' + ', '.join(self.image_lines(m['images'])) + ']'
        return line

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

    def admin_people(self, people):
        """Clients, recent course buyers and call leads from Admin, marked when they're already in the tracker."""
        admin = getattr(self, 'admin', None)
        if admin is None:
            return ['', 'Admin (clients, course buyers, Calendly calls) is not connected here.']
        known = {p['email'] for p in people.all()['items'] if p.get('email')}
        mark = lambda e: ' [in tracker]' if e and e in known else ''
        out, buyers_seen, client_emails = [], set(), set()
        try:
            clients = admin.roster()[:40]
            out += ['', 'Clients in Admin. name | emails | programme | status | last call | next call:']
            for c in clients:
                client_emails.update(c.get('emails') or [])
                nxt = (c.get('next_call') or {}).get('start', '')[:10]
                out.append(f"- {c['name']} | {', '.join(c.get('emails') or []) or 'no email'} | {c.get('programme') or ''} | {c.get('status') or ''}"
                           f" | {c.get('last_call') or 'no call yet'} | {nxt or 'none booked'}{mark((c.get('emails') or [''])[0])}")
        except Exception as e:  # noqa: BLE001 - Admin may not be set up on this PC
            out += ['', f'Clients in Admin could not be read ({str(e)[:80]}).']
        try:
            buyers = admin.customers_page().get('customers', [])
            buyers = [b for b in buyers if not b.get('refunded')][:40]
            out += ['', 'Recent course buyers in Admin (newest first). name | email | first bought | what:']
            for b in buyers:
                buyers_seen.add(b['email'])
                out.append(f"- {b.get('name') or '?'} | {b['email']} | {(b.get('first') or '')[:10]} | "
                           f"{', '.join(sorted({i['product'] for i in b.get('items', [])}))}{mark(b['email'])}")
        except Exception as e:  # noqa: BLE001
            out += ['', f'Course buyers in Admin could not be read ({str(e)[:80]}).']
        try:
            leads = [e for e in admin.events() if e.get('email') and e['email'] not in client_emails and e['email'] not in buyers_seen][:30]
            out += ['', 'Calendly calls with people who are not clients or buyers (possible leads). date | name | email | event:']
            out += [f"- {e['start'][:10]} | {e.get('name') or '?'} | {e['email']} | {e.get('event') or ''}{mark(e['email'])}" for e in leads] or ['- none']
        except Exception as e:  # noqa: BLE001
            out += ['', f'Calendly calls could not be read ({str(e)[:80]}).']
        return out

    def live(self, agent_id):
        """False while a team member is still being set up and may not act outside Josh OS."""
        if agent_id not in SETUP_MODE:
            return True
        try:
            on = json.loads((self.root / 'settings.json').read_text(encoding='utf-8-sig')).get('team_live') or []
        except (OSError, ValueError):
            on = []
        return agent_id in on

    def roster(self, agent):
        """Who else is on the team, so people refer work to each other by name."""
        others = [a for a in self.team.all()['agents'] if a['id'] != agent['id']]
        return 'The rest of the team: ' + '; '.join(f"{a['name']} ({a['role']}{', starts later' if a['status'] == 'Coming later' else ''})" for a in others) + '.'

    def desk_system_prompt(self, agent, learnings):
        training, _ = self.training(agent['id'])
        lines = [
            f"You are {agent['name']}, {agent['role']} on Josh's team at CareerOS. You work inside Josh OS, his studio app.",
            f"Your mission: {agent['mission']}",
            f"Your personality: {agent['personality']}",
            'What you are responsible for:', *[f'- {r}' for r in agent['responsibilities']],
            f"Your check-in: {agent['check_in']}",
            self.roster(agent),
            *([] if self.live(agent['id']) else [
                'You are still being set up. You have no access to Gmail, Calendly, Kit, Linklr or Chrome yet, and you must not',
                'send, book, schedule or change anything outside Josh OS. You can read Josh OS and your files, keep your tracker',
                'up to date from what Josh tells you, and write any message here in chat for Josh to send himself.']),
            '',
            'This is your chat with Josh in Josh OS. It is one conversation, wherever he opens it, on your own page, in the side',
            'dock or beside a script. When the prompt says what he has open, that tells you what "this" means. It never limits',
            'what you can do. Everything you can change or start is open to you in every reply, on any page.',
            '- Josh can attach images such as screenshots. Open each one with Read and look at it before you answer. To put one on',
            '  a video\'s Results tab, use its asset id as the screenshot.',
            '- Change a video card with the "projects" list: its name, stage, publish date, notes, script or new title ideas.',
            *([f'- Your skills are in .claude/skills ({", ".join(n + "/SKILL.md" for n in SKILLS.get(agent["id"], []))}). Read the one that',
               '  fits before you write, unless it is already in the prompt.'] if SKILLS.get(agent['id']) else []),
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
            *(self.thumbnail_rules() if agent['id'] in CAN_THUMB and getattr(self, 'video_thumbs', None) is not None else []),
            *(["- You can work on any video's animation deck from any chat, including Discord. When Josh asks for the slides or",
               "  shot list to be made or changed and he is not on that video's animation review, put the video's id in \"edit\"",
               "  as project_id with exactly what to do in deck_change. Josh OS runs it as a job in that video's folder and posts",
               '  back here when it is done, so tell him it is under way. Use setup or rerender with project_id for those alone.']
              if 'editing' in (agent.get('workflows') or []) and getattr(self, 'edit_review', None) is not None else []),
            *(['- Each video has a Results tab in Josh OS. When you read a video\'s numbers, thumbnail test or retention graph (from',
               '  YouTube Studio, Linklr or the screenshots Josh adds), save them with the "videos" list in your answer, and open',
               '  any screenshot path from the snapshot with Read before you describe it. Send the full list of test rounds when you',
               '  change tests. Follow your goals file for how to judge a video and what to learn from it.',
               '  Screenshots you take in Chrome stay in your chat and Josh OS has no way to attach them. Save the numbers and your reading anyway,',
               '  and ask Josh to paste the screenshots into this chat, then save them on the Results tab by asset id. Put a transcript in "transcript", never in the notes.',
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
            *(['- The Lessons page in Josh OS is the record for every course lesson, and you change it from this chat with the',
               '  "lessons" list in your answer. Each entry names a lesson_id from the snapshot. You can rename it (title), set minutes,',
               '  write or rewrite the whole script, save preparation notes and notes, and move its stage. Leave lesson_id out and give',
               '  course, number and title to add a new lesson (part_name only when it starts a new part).',
               '- Stage Published marks a lesson as already live in the course app, put there outside Josh OS. It needs no files,',
               '  so use it only when Josh says the lesson is live. Other moves still pass the same checks as the Lessons page.',
               '- "publish": true copies a lesson that is Ready to publish into the CareerOS Google Drive folder the course app reads.',
               '  The video and the lesson guide PDF go into its track and part folder named "<number> <title>", and the thumbnail',
               '  goes into the track\'s Thumbnails folder. An older file for the same lesson moves to an archive folder there.',
               '  Josh OS refuses it when the video, guide or thumbnail is missing, so say what is missing.',
               '- Start the real work with "start" on a lesson. prepare gathers what the lesson needs, draft writes the script, slides',
               '  builds the deck, guide makes the lesson guide PDF, and thumbnail makes the thumbnail. Each one runs on this PC right',
               '  after your reply, two at a time, and the rest wait their turn. Josh sees progress on each lesson. Put what he asked',
               '  for in start_note. Slides need the script Josh approved, and draft needs preparation notes or a start_note saying',
               '  what to cover. Josh approves scripts himself, so never claim a script is approved.',
               '- When Josh asks you to make, change, rename or add lessons, do it in this answer. Never promise it for a later run.',
               '  Only say something is done or started when it is in this answer, and say so plainly if Josh OS refuses it.']
              if 'courses' in (agent.get('workflows') or []) and getattr(self, 'courses', None) is not None else []),
            *(['- You cannot edit or run files from this chat, but you can change your cards and redo designs as described above.',
               '  Do the work now in this chat rather than promising it for a later run.']
              if agent['id'] in CHROME or 'email' in (agent.get('workflows') or []) else
              ['- Your lesson work happens through the Lessons page as described above. Anything else you can only read from here.']
              if 'courses' in (agent.get('workflows') or []) and getattr(self, 'courses', None) is not None else
              ['- Do what Josh asks in this answer with the lists you have. When something can only be done by one of your scheduled',
               '  tasks on this PC, say so plainly and say what will happen on the next run.']),
            "- Josh's edits to your work are how he teaches you. When your prompt lists changes he made, study what he changed",
            '  and match it from now on. If the reason is clear, say in a line what you will do next time. If it is not, ask about it',
            '  in plain words, like "Why did you change this line? Was it to sound less salesy?". Never ask about typo fixes.',
            '  Put the ids of edits you covered in edits_noted.',
            '- Most of Josh\'s feedback comes in chat. When he asks you to change your work ("make the hook punchier",',
            '  "cut that line", "less salesy"), make the change, then work out the preference behind it. If it would apply to',
            '  future work, save it in learned as a rule. If you can\'t tell whether it was a one-off, ask in one short line, like',
            '  "Want me to do that every time, or just here?" or "Was that to keep it shorter?". Save the rule once he answers.',
            '- When a rule is clear, from an edit or from what Josh tells you, save it in learned as one short instruction you can',
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
        book = self.root / PLAYBOOKS / agent['id']
        for f in (sorted(book.glob('*.md')) if book.is_dir() else []):
            if f.name.lower() != 'readme.md':
                lines += ['', f"=== Playbook: {f.name} (follow it exactly) ===", f.read_text(encoding='utf-8')]
        if learnings:
            lines += ['', 'What the team has learned so far (apply it):', *[f"- {l['lesson']}" + (f" ({l['evidence']})" if l.get('evidence') else '') for l in learnings]]
        if training:
            lines += ['', 'Your training follows. It is Josh\'s own process and it overrides general habits.', training]
        return '\n'.join(lines)

    def thumbnail_rules(self):
        return ['- You make the YouTube thumbnails. When Josh asks for thumbnails (new ones, changes, other words or poses), put',
                '  the video in "thumbnails" with what he asked in "note". Josh OS starts the job after your reply and posts the',
                '  images back here, usually within a few minutes, so tell him they are on the way. Do not describe options you',
                '  have not made. The middle shot and close portrait formats can be built from chat, in the house look or with',
                '  another font, colour, size or bar (fourteen local fonts, including Anton, Impact, Gotham and Franie). Put the',
                '  style Josh wants in the note. Boardroom, whiteboard, before',
                '  and after and close face can not yet, so say so if he asks for one.']

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
            '  same entry). Josh OS draws them with the house template. When you change the words of a post that already has a',
            '  Paper design, Josh OS makes the design again by itself, so Josh always sees the new slides.',
            '- Slides can show more than words. Use a drawing or a picture whenever it explains the point better than text, like',
            '  a chart for a post about charts, a before and after, a simple diagram, a mock slide or email, or a photo.',
            '  Under a slide\'s words, add either one drawing or one picture.',
            '  A drawing is an inline <svg viewBox="0 0 840 520" ...>...</svg> you write yourself, in the house look. Colours are',
            '  ink #15171C, paper #F6F5F1, card #FFFFFF, line #D9D4C9, grey text #4D4A44, gold #F5C21B and deep gold #7A5A00.',
            '  Use font-family Outfit for labels and Manrope for small text, at 22px or more so it reads on a phone. Keep it',
            '  simple with one idea, flat shapes, rounded corners and one gold highlight. No scripts, and no lines of three dashes.',
            '  A picture is a line like [image: <asset id>] for an image Josh attached in chat, or [image: <full path>] for an',
            '  image file on this PC, such as E:\\CareerOS\\03_Community_Posts\\_shared-assets. Every slide still needs its headline.',
            '  The drawing or picture shrinks to fit, so keep the words short on those slides. The images are made after your',
            '  reply, so to check them later open the slide files in the post\'s folder with Read and fix anything that reads badly.',
            '  If Josh wants the look itself changed (colours, sizes, layout), say what should change and that it needs a change',
            '  to the template in Josh OS, which Josh can ask Claude Code to make.',
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
        people = getattr(self, 'people', None)
        if people is not None and 'customers' in (agent.get('workflows') or []):
            from .people import PATHWAYS
            lines += ['', 'Your customer success tracker. id | name <email> | pathway | stage | next step | open promises | last touchpoint | summary.',
                      'Stages by pathway: ' + '; '.join(f"{k} ({v[0]}): {', '.join(v[1])}" for k, v in PATHWAYS.items())]
            lines += people.describe() or ['- nobody yet']
            lines += self.admin_people(people)
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
        """Lesson changes Saskia made from chat: new lessons, edits, stage moves and work started. Approval stays with Josh."""
        done = []
        for u in (updates or [])[:40]:
            if not isinstance(u, dict):
                continue
            lid, title = str(u.get('lesson_id') or ''), str(u.get('title') or '')
            try:
                fields = ('script', 'prep', 'notes', 'title', 'minutes')
                if not lid:
                    made = courses.create(dict(course=u.get('course'), number=u.get('number'), title=u.get('title'),
                                               part_name=u.get('part_name'), type=u.get('type'), minutes=u.get('minutes')))
                    lid = made['id']
                    done.append(dict(id=lid, title=f"{made['number']} {made['title']}", action='added', kind='lesson'))
                    fields = ('script', 'prep', 'notes')
                current = courses.get(lid)
                changes = {k: u[k] for k in fields if k in u and u[k] not in (None, '') and (k == 'minutes' or (isinstance(u[k], str) and u[k].strip()))}
                if changes:
                    current = courses.save(lid, changes, current['revision'])
                title = f"{current['number']} {current['title']}"
                if changes and u.get('lesson_id'):
                    done.append(dict(id=lid, title=title, action='updated', kind='lesson'))
                if u.get('stage') == 'Published' and current['stage'] != 'Published':
                    current = courses.mark_live(lid, current['revision'], by='Saskia')
                    done.append(dict(id=lid, title=title, action='moved', kind='lesson', stage=current['stage']))
                elif u.get('stage') and u['stage'] != current['stage']:
                    current = courses.move(lid, str(u['stage']), current['revision'])
                    done.append(dict(id=lid, title=title, action='moved', kind='lesson', stage=current['stage']))
                if u.get('publish') is True:
                    courses.publish(lid)
                    done.append(dict(id=lid, title=title, action='started', kind='lesson', task='publish'))
                if u.get('start'):
                    courses.start(lid, str(u['start']), str(u.get('start_note') or ''))
                    done.append(dict(id=lid, title=title, action='started', kind='lesson', task=str(u['start'])))
            except Exception as e:  # noqa: BLE001 - every failure is shown in the chat
                done.append(dict(id=lid, title=title, action='failed', kind='lesson', error=str(e).strip("'\"")[:200]))
        return done

    def thumbnail_job(self, project, note, folder):
        """Sam picks up to three thumbnail options for a video. Read-only tools: Josh OS builds what he sends back."""
        from .video_thumbnails import POSES, TOOLS
        agent = next((a for a in self.team.all()['agents'] if a['id'] == 'sam'), None)
        if not agent:
            raise AgentError('Sam is not on the team.')
        learnings = [x for x in self.team.all()['learnings'] if x['agent_id'] in ('', 'sam')][:80]
        _, dirs = self.training('sam')
        title = next((t.get('text') for t in project.get('titles') or [] if t.get('id') == project.get('chosen_title')), '') or project['name']
        facts = dict(id=project['id'], name=project['name'], title=title, stage=project['stage'],
                     title_ideas=[t.get('text') for t in project.get('titles') or []][:12], notes=(project.get('notes') or '')[:3000],
                     script=(project.get('script') or '')[:8000], folder=str(folder))
        from .thumbnail_build import COLOURS, FONTS
        fonts = '\n'.join(f"- {k}: {', '.join(v['weights'])} ({v['label']})" + ('' if all(f.is_file() for f in v['weights'].values())
                                                                                    else ', some files missing on this PC') for k, v in FONTS.items())
        brief = THUMB_BRIEF.replace('{fonts}', fonts).replace('{colours}', ', '.join(COLOURS))
        parts = [brief, '', f'Your training files are in {self.training_dir()}. The tools folder is {TOOLS}. '
                 f'The Pose Catalogue is {POSES}. This video\'s folder is {folder}, and its thumbnails folder shows earlier rounds.',
                 '', 'The video:', json.dumps(facts, ensure_ascii=False, indent=1)]
        if note.strip():
            parts += ['', "Josh's note: " + note.strip()]
        system = self.desk_system_prompt(agent, learnings) + '\n\nYou are making one round of thumbnails that Josh asked for in chat.'
        return self.run(system, '\n'.join(parts), THUMB_JOB_SCHEMA, list(dirs) + [str(folder), str(TOOLS), str(POSES)],
                        tools=ALLOWED_TOOLS, timeout=1200, full=True)

    def edit_proposal_job(self, project, transcript, candidates):
        """Tariq reads a recording's transcript and marks retakes and long pauses. Read-only tools and a fixed answer
        shape: Josh OS checks every word id and pause id, then folds the decisions into a reviewable proposal."""
        agent = next((a for a in self.team.all()['agents'] if a['id'] == 'tariq'), None)
        if not agent:
            raise AgentError('Tariq is not on the team.')
        learnings = [x for x in self.team.all()['learnings'] if x['agent_id'] in ('', 'tariq')][:80]
        parts = [EDIT_BRIEF, '', f"The video: {project['name']}", '',
                 'Places where wording restarts (candidates only, repeats can be deliberate):',
                 json.dumps(candidates[:200], ensure_ascii=False), '', 'The transcript. Each word has its id in brackets, long pauses '
                 'are marked with their id and length:', transcript[:400000]]
        system = self.desk_system_prompt(agent, learnings) + '\n\nYou are proposing one clean cut that Josh will review word by word.'
        return self.run(system, '\n'.join(parts), EDIT_JOB_SCHEMA, [], tools=ALLOWED_TOOLS, timeout=1800, full=True)

    def post(self, agent_id, text, images=(), saved=(), via='', files=()):
        """A message from a team member that arrives on its own, like the result of a job."""
        m = dict(id=str(uuid.uuid4()), role='agent', text=text, at=now())
        if images:
            m['images'] = list(images)
        if saved:
            m['saved'] = list(saved)
        if files:
            m['files'] = [str(f) for f in files]
        if via:
            m['via'] = via
        return self._append(DESK, agent_id, m)

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
        # File edits are held to the course folder. Bash (guide and thumbnail jobs) is not path-limited; see the README.
        return self.run(system, '\n'.join(parts), LESSON_JOB_SCHEMA, list(dirs) + [str(course_dir), str(Path(content_dir).parent.parent / '08_Strategy')],
                        allow=scoped(JOB_TOOLS[task], course_dir),
                        tools=JOB_TOOLS[task], timeout=JOB_TIMEOUT[task], full=True)

    # Running ----------------------------------------------------------------
    def run(self, system, prompt, schema=SCHEMA, dirs=(), chrome=False, extra_tools=(), full=False, tools=None, timeout=None, cwd=None, allow=None):
        claude = find_claude(self.root)
        if not claude:
            raise AgentError('I can\'t reach Claude on this PC yet. Install Claude Code and sign in, then try again.')
        with tempfile.TemporaryDirectory() as tmp:
            sys_file = Path(tmp) / 'system.md'
            sys_file.write_text(system, encoding='utf-8')
            wrapper = claude.lower().endswith(('.cmd', '.bat'))
            cmd = [claude, '-p', '--output-format', 'json', '--no-session-persistence',
                   '--append-system-prompt-file', str(sys_file),
                   '--tools', tools or ALLOWED_TOOLS, '--allowedTools', ','.join([allow or tools or ALLOWED_TOOLS] + ([CHROME_TOOLS] if chrome else []) + list(extra_tools))]
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
                                     cwd=str(cwd or self.root), timeout=timeout or (TIMEOUT * 2 if chrome else TIMEOUT),
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

    def ask(self, data, carousels=(), tracker=None, inspo=None):
        """The chat beside a video's script. It is the same conversation, with the same powers, as every other chat
        with that team member. The open video and any passage Josh selected are its context."""
        if not isinstance(data, dict):
            raise ValueError('Invalid request.')
        self.store.get(data.get('project_id', ''))
        return self.chat(dict(data, context={'kind': 'project', 'id': data.get('project_id', '')}), carousels, tracker, inspo)

    def selection(self, data):
        """The passage of the open script Josh selected, checked against the saved script."""
        sel, ctx = data.get('selection') or {}, data.get('context') or {}
        if not sel:
            return None
        if not isinstance(sel, dict) or ctx.get('kind') != 'project':
            raise ValueError('Open the video to work on part of its script.')
        script = self.store.get(str(ctx.get('id') or ''))['script']
        start, end, text = sel.get('start'), sel.get('end'), sel.get('text', '')
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(script)):
            raise ValueError('The selection no longer matches the script. Select the passage again.')
        if script[start:end] != text:
            raise ValueError('The script changed since you selected that passage. Select it again.')
        return dict(start=start, end=end, text=text)

    def apply_projects(self, updates):
        """Video card changes a team member made from chat."""
        done = []
        for u in (updates or [])[:20]:
            if not isinstance(u, dict):
                continue
            pid, title = str(u.get('project_id') or ''), ''
            try:
                p = self.store.get(pid)
                title = p['name']
                changes = {k: u[k] for k in ('name', 'stage', 'publish_date', 'notes', 'script') if isinstance(u.get(k), str) and u[k].strip()}
                ideas = [t.strip() for t in (u.get('add_titles') or []) if isinstance(t, str) and t.strip()]
                if ideas:
                    have = {t['text'] for t in p['titles']}
                    changes['titles'] = p['titles'] + [dict(id=str(uuid.uuid4()), text=t) for t in dict.fromkeys(ideas) if t not in have]
                if changes:
                    p = self.store.save(pid, changes, p['revision'])
                done.append(dict(id=pid, title=p['name'], action='updated', kind='project'))
            except Exception as e:  # noqa: BLE001 - every failure is shown in the chat
                done.append(dict(id=pid, title=title, action='failed', kind='project', error=str(e).strip("'\"")[:200]))
        return done

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
                    '"lessons" list with this id, with the whole script if you rewrite it. Josh approves scripts himself.\n'
                    + json.dumps(body, ensure_ascii=False, indent=1))
        if kind == 'project':
            try:
                p = self.store.get(cid)
            except KeyError:
                return ''
            title = next((t.get('text') for t in p.get('titles') or [] if t.get('id') == p.get('chosen_title')), '') or p['name']
            results = getattr(self, 'results', None)
            saved = results.get(cid) if results is not None else None
            return ('Josh has this video open right now, so "this video" and "the script" mean this one. Change its card with the '
                    '"projects" list using this id. For a rewrite of part of the script, put the new passage in "replacement" so '
                    'Josh can drop it in. Change the whole script in "projects" only when he asks you to change it directly. '
                    + ('You can save its results, thumbnail tests and retention reading with the "videos" list, using this id. '
                       'The full script is below, so line retention moments up with the exact words.' if saved is not None else '')
                    + '\n' + json.dumps(dict(id=p['id'], name=p['name'], title=title, stage=p['stage'], publish_date=p.get('publish_date'),
                                            notes=(p.get('notes') or '')[:2000], script=(p.get('script') or '')[:20000],
                                            results=saved), ensure_ascii=False, indent=1)
                    + ('\n' + self.anim_notes_text(cid) if self.anim_notes_text(cid) else ''))
        if kind == 'edit' and getattr(self, 'edit_review', None) is not None:
            er = self.edit_review
            try:
                folder = er.project_folder(cid)
                p = self.store.get(cid)
            except KeyError:
                return ''
            slides, shots = er.deck_files(cid)
            review = er.review(cid)
            part = None
            if isinstance(context.get('part'), int) and 0 <= context['part'] < len(review['parts']):
                part = review['parts'][context['part']]
            lines = [f"Josh is reviewing the animation deck for the video \"{p['name']}\" in the edit review. Its folder is {folder}.",
                     f"Slides: {slides or 'none yet'}. Shot list: {shots or 'none yet'}. Edit folder: {er.edit_folder(cid) or 'not set up yet'}.",
                     'You can read and edit files in this folder only. When Josh asks for a change to an animation, change the slides',
                     'HTML (and the shot list if the presses or words move) with Edit, keeping the deck\'s structure: each animation is a',
                     '.scene with data-from and data-to press numbers, grey number cards are .scene.mark, and elements reveal on data-b.',
                     'If you add or remove a press, renumber every later data-from, data-to, data-b, data-off and data-add, and TOTAL.',
                     'Josh OS takes the review pictures again by itself when the slides change. Follow the video-editing-prep skill for',
                     'anything new, and the house style of the existing deck. Never touch the recordings, Premiere projects or masters.']
            if self.anim_notes_text(cid):
                lines += ['', self.anim_notes_text(cid)]
            if part:
                lines += ['', 'The part he has open:', json.dumps({k: part.get(k) for k in ('kind', 'n', 'type', 'time', 'desc', 'script', 'presses')},
                                                                  ensure_ascii=False, indent=1)]
                if part.get('images'):
                    out = er.out_folder(cid)
                    lines += ['Its review pictures, one per press, which you can look at with Read: ' + ', '.join(str(out / n) for n in part['images'])]
            return '\n'.join(lines)
        if kind == 'person' and getattr(self, 'people', None) is not None:
            try:
                person = self.people.get(cid)
            except KeyError:
                return ''
            return ('Josh has this person open on the Customer success page, so "them" and "this person" mean this one. To change '
                    'their record, send it in the "people" list with this id.\n' + json.dumps(person, ensure_ascii=False, indent=1))
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
        images = [] if proactive else self.chat_images(data)
        files = [] if proactive else self.chat_files(data)
        via = 'discord' if data.get('via') == 'discord' else ''
        sel = None if proactive else self.selection(data)
        if not proactive and (not (message or images or files) or len(message) > 4000):
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
                    parts.append(self.history_line(m))
            focus = self.focus(data.get('context'), carousels, inspo)
            if focus:
                parts += ['', focus]
            if sel:
                parts += ['', '--- Josh has selected this passage of the open script ---', sel['text'], '--- End of selection ---',
                          'When he asks for a change to it, put the full rewritten passage in "replacement". Keep anything he did '
                          'not ask you to change and match his formatting, including any # headings. Leave it null when you are '
                          'only answering.']
            if (data.get('context') or {}).get('kind') == 'project':
                for name in SKILLS.get(agent_id, []):
                    text = self.skill_text(name)
                    if text:
                        parts += ['', f'=== Skill: {name} (follow it exactly for any writing on this script) ===', text]
            if edits:
                parts += ['', 'Changes Josh made to your work that you have not raised with him yet (before and after):',
                          feedback.describe(edits)]
            if proactive:
                parts += ['', 'Josh has just finished editing some of your work, listed above, and has not said anything about it. '
                          'Write to him first. Keep it short and natural. For the changes where the reason is clear, say in a line '
                          'what you will do from now on. For the ones you cannot work out, ask about one or two, like "Why did '
                          'you change X to Y? Was it to ...?". Do not ask about typo fixes. Put every edit id you covered in '
                          'edits_noted, and save any rule that is already clear in learned.']
            else:
                parts += ['', f"Josh: {message or '(no text, just the images below)'}"]
                if images:
                    parts += ['', 'Josh attached these images to this message. Open every one with Read and look at it before you answer:']
                    parts += self.image_lines(images)
                if files:
                    parts += ['', 'Josh attached these files to this message. Open every one with Read before you answer:', *files]
                if via:
                    parts += ['', 'Josh sent this from Discord on his phone or computer. Reply as you would in Josh OS. To send him a file '
                              'back, put its full path in send_files.']
            if images or any(m.get('images') for m in history[-12:]):
                dirs = list(dirs) + [str(self.store.root / 'assets')]
            if files:
                dirs = list(dirs) + sorted({str(Path(f).parent) for f in files})
            schema = json.loads(json.dumps(CAROUSEL_SCHEMA if keeps else DESK_SCHEMA))
            schema['properties'].update(LEARN_PROPS)
            schema['properties'].update(PROJECT_PROPS)
            if via:
                schema['properties'].update(SEND_FILES_PROPS)
            thumbs = getattr(self, 'video_thumbs', None) if agent_id in CAN_THUMB else None
            if thumbs is not None:
                schema['properties'].update(THUMB_PROPS)
            mails = getattr(self, 'emails', None) if 'email' in (agent.get('workflows') or []) else None
            if mails is not None:
                schema['properties'].update(EMAIL_PROPS)
            results = getattr(self, 'results', None) if 'youtube' in (agent.get('workflows') or []) else None
            if results is not None:
                schema['properties'].update(VIDEO_PROPS)
            people = getattr(self, 'people', None) if 'customers' in (agent.get('workflows') or []) else None
            if people is not None:
                schema['properties'].update(PEOPLE_PROPS)
            ideas = getattr(self, 'youtube_ideas', None) if agent_id == 'sam' else None
            if ideas is not None:
                schema['properties'].update(YOUTUBE_SAVE_PROPS)
            courses = getattr(self, 'courses', None) if 'courses' in (agent.get('workflows') or []) else None
            if courses is not None:
                schema['properties'].update(LESSON_PROPS)
                schema['properties'].update(GUIDE_PROPS)
                if str(self.store.root / 'assets') not in dirs:
                    dirs = list(dirs) + [str(self.store.root / 'assets')]
            ctx = data.get('context') or {}
            er = getattr(self, 'edit_review', None) if 'editing' in (agent.get('workflows') or []) else None
            edit_folder, before, run_tools = None, None, None
            if er is not None:
                schema['properties'].update(EDIT_PROPS)
            if er is not None and ctx.get('kind') in ('edit', 'project') and ctx.get('id'):
                try:
                    edit_folder = er.project_folder(str(ctx['id']))
                except KeyError:
                    edit_folder = None
                if edit_folder is not None and ctx.get('kind') == 'edit':
                    # The editor may change the deck and shot list, and only inside this video's folder.
                    run_tools = 'Read,Grep,Glob,Edit,Write'
                    before = er.deck_files(str(ctx['id']))[0]
                    before = (before, before.read_text(encoding='utf-8')) if before else None
                    for name in SKILLS.get(agent_id, []):
                        text = self.skill_text(name)
                        if text:
                            parts += ['', f'=== Skill: {name} (follow it for anything you make or change in the deck) ===', text]
            live = self.live(agent_id)
            outside = (['mcp__linklr__' + t for t in LINKLR.get(agent_id, ())] + list(KIT.get(agent_id, ())) + list(GMAIL.get(agent_id, ()))
                       + list(CALENDLY.get(agent_id, ()))) if live else []
            reply, answer = self.run(self.desk_system_prompt(agent, learnings), '\n'.join(parts),
                                     schema, dirs, chrome=agent_id in CHROME and not proactive and live,
                                     extra_tools=outside, full=True,
                                     tools=run_tools, cwd=edit_folder if run_tools else None,
                                     allow=scoped(run_tools, edit_folder) if run_tools else None)
            answer = answer if isinstance(answer, dict) else {}
            todo = answer.get('carousels')
            saved = tracker.apply(agent_id, todo) if keeps and isinstance(todo, list) else []
            found = answer.get('inspo')
            if keeps and inspo is not None and isinstance(found, list):
                saved += inspo.apply(found)
            if mails is not None and isinstance(answer.get('emails'), list):
                saved += self.apply_emails(mails, answer['emails'])
            if people is not None and isinstance(answer.get('people'), list):
                saved += people.apply(answer['people'])
            if results is not None and isinstance(answer.get('videos'), list):
                saved += results.apply(answer['videos'])
            if courses is not None and isinstance(answer.get('lessons'), list):
                saved += self.apply_lessons(courses, answer['lessons'])
            if isinstance(answer.get('projects'), list):
                saved += self.apply_projects(answer['projects'])
            if thumbs is not None and isinstance(answer.get('thumbnails'), list):
                for t in answer['thumbnails'][:3]:
                    try:
                        saved.append(thumbs.start(str((t or {}).get('project_id') or ''), str((t or {}).get('note') or message)[:2000], via))
                    except (ValueError, KeyError) as e:
                        saved.append(dict(id=str((t or {}).get('project_id') or ''), title='thumbnails', action='failed', kind='thumbnail',
                                          error=str(e).strip("'\"")[:200]))
            if ideas is not None and isinstance(answer.get('save_youtube'), list):
                confirm = []
                for item in answer['save_youtube'][:10]:
                    url = str((item or {}).get('url') or '').strip()
                    try:
                        entry, status = ideas.save(url, (item or {}).get('note') or '', by=agent_id, via=via)
                        saved.append(dict(id=entry['id'], title=entry.get('title') or entry['id'], action=status, kind='idea'))
                        confirm.append(ideas.describe(entry, status))
                    except (ValueError, OSError) as e:
                        saved.append(dict(id='', title=url[:80], action='failed', kind='idea', error=str(e)[:200]))
                        confirm.append(f"I couldn't save {url}: {str(e)[:200]}")
                if confirm:
                    reply = (reply or '').rstrip() + '\n\n' + '\n'.join(confirm)
            if courses is not None and isinstance(answer.get('written_guide'), dict):
                saved += self.start_guide(agent_id, answer['written_guide'], via)
            action = answer.get('edit') if isinstance(answer.get('edit'), dict) else {}
            if er is not None and edit_folder is not None:
                saved += self.apply_edit(er, str(ctx['id']), action, before)
                saved += self.start_go_ahead(str(ctx['id']), self.store.get(str(ctx['id'])).get('name', ''), action, via)
                saved += self.keep_anim_notes(str(ctx['id']), action)
                saved += self.start_deck_job(agent_id, str(ctx['id']), self.store.get(str(ctx['id'])).get('name', ''), action, via)
            elif er is not None and action.get('project_id'):
                saved += self.apply_edit_elsewhere(er, agent_id, str(action['project_id']), action, via)
            replacement = answer.get('replacement') if sel and isinstance(answer.get('replacement'), str) else None
            if mails is not None and isinstance(answer.get('library'), list):
                saved += mails.add_references(answer['library'][:40])
            if feedback and isinstance(answer.get('edits_noted'), list):
                known = {e['id'] for e in edits}
                feedback.mark([i for i in answer['edits_noted'] if i in known])
            # Rules learned come in "learned". Course lessons use "lessons", so only fall back to it for agents without courses.
            learned = self.keep_lessons(agent, answer.get('learned') if answer.get('learned') else (None if courses is not None else answer.get('lessons')))
        finally:
            lock.release()
        theirs = dict(id=str(uuid.uuid4()), role='agent', text=reply, at=now())
        sending = self.files_to_send(answer.get('send_files')) if via and isinstance(answer, dict) else []
        if sending:
            theirs['files'] = sending
        if via:
            theirs['via'] = via
        if not proactive and sel:
            theirs.update(selection=sel, replacement=replacement)
        if saved:
            theirs['saved'] = saved
        if learned:
            theirs['learned'] = learned
        if proactive:
            theirs['asked'] = True
            return dict(messages=self._append(DESK, agent_id, theirs), reply=theirs)
        mine = dict(id=str(uuid.uuid4()), role='josh', text=message, at=now())
        if images:
            mine['images'] = images
        if files:
            mine['files'] = [Path(f).name for f in files]
        if via:
            mine['via'] = via
        if sel:
            mine['selection'] = sel
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

    def start_go_ahead(self, pid, name, action, via=''):
        """Josh told Tariq to go ahead with the edit: the staged workflow runs as a job and Tariq posts back."""
        flow = getattr(self, 'edit_flow', None)
        if action.get('go_ahead') is not True or flow is None:
            return []
        try:
            flow.go_ahead(pid, via=via)
            return [dict(id=pid, title='the edit of ' + str(name), action='started', kind='edit')]
        except (ValueError, KeyError) as e:
            return [dict(id=pid, title='the edit of ' + str(name), action='failed', kind='edit', error=str(e)[:200])]

    def apply_edit(self, er, pid, action, before):
        """Set up the edit folders, and take the review pictures again when the deck changed or Tariq asked."""
        done = []
        try:
            if action.get('setup') is True:
                made = er.setup(pid)
                done.append(dict(id=pid, title='Edit folders in ' + made['edit'], action='updated', kind='edit'))
            slides = er.deck_files(pid)[0]
            changed = bool(slides) and (before is None or slides != before[0] or slides.read_text(encoding='utf-8') != before[1])
            asked = [int(n) for n in action.get('rerender') or [] if isinstance(n, int)]
            if changed or asked:
                er.render(pid, None if changed else asked)
                done.append(dict(id=pid, title='the review pictures' + ('' if changed else ' for animation ' + ', '.join(map(str, asked))),
                                 action='updated', kind='edit'))
        except (ValueError, KeyError, OSError) as e:
            done.append(dict(id=pid, title='the edit review', action='failed', kind='edit', error=str(e)[:200]))
        return done

    def apply_edit_elsewhere(self, er, agent_id, pid, action, via):
        """Edit actions asked for in a chat that isn't the animation review: folders and pictures now, deck changes as a job."""
        try:
            p = self.store.get(pid)
            er.project_folder(pid)
        except (KeyError, ValueError) as e:
            return [dict(id=pid, title='the edit', action='failed', kind='edit', error=str(e)[:200])]
        done = self.apply_edit(er, pid, {k: action[k] for k in ('setup', 'rerender') if k in action}, self._deck_state(er, pid))
        done += self.start_go_ahead(pid, p['name'], action, via)
        done += self.keep_anim_notes(pid, action)
        return done + self.start_deck_job(agent_id, pid, p['name'], action, via)

    def anim_notes_text(self, pid):
        flow = getattr(self, 'edit_flow', None)
        text = ((flow.anim_notes(pid).get('text') if flow else '') or '').strip()
        return ("Josh's notes for this video's animations (follow them):\n" + text[:8000]) if text else ''

    def keep_anim_notes(self, pid, action):
        note = str(action.get('animation_notes') or '').strip()
        flow = getattr(self, 'edit_flow', None)
        if note and flow:
            flow.set_anim_notes(pid, note, add=True)
            return [dict(id=pid, title='your animation notes', action='updated', kind='edit')]
        return []

    def start_deck_job(self, agent_id, pid, name, action, via=''):
        """Josh asked for the slides or shot list to be made or changed: it runs as a job and Tariq posts back."""
        change = str(action.get('deck_change') or '').strip()
        if not change:
            return []
        key = ('deck', pid)
        t = self.jobs.get(key) if hasattr(self, 'jobs') else None
        if t and t.is_alive():
            return [dict(id=pid, title=name, action='failed', kind='edit', error='A deck change for this video is already running.')]
        self.jobs = getattr(self, 'jobs', {})
        t = threading.Thread(target=self._deck_job, args=(agent_id, pid, change[:3000], via), daemon=True)
        self.jobs[key] = t
        t.start()
        return [dict(id=pid, title='the deck for ' + name, action='started', kind='edit')]

    def _deck_state(self, er, pid):
        slides = er.deck_files(pid)[0]
        return (slides, slides.read_text(encoding='utf-8')) if slides else None

    def _deck_job(self, agent_id, pid, change, via):
        """The editor makes or changes a video's slides and shot list. File edits only inside that video's folder."""
        er = self.edit_review
        agent = next((a for a in self.team.all()['agents'] if a['id'] == agent_id), None)
        flow = getattr(self, 'edit_flow', None)
        mark = (lambda *x, **k: flow._setup_step(pid, *x, phase='animate', **k)) if flow else (lambda *x, **k: None)
        if flow:
            flow._setup_new(pid, 'animate')
            mark('read', 'running', 'Working on: ' + change[:200])
        stop = threading.Event()
        try:
            folder = er.project_folder(pid)
            if flow:
                threading.Thread(target=self._deck_progress, args=(Path(folder) / DECK_PROGRESS, mark, stop), daemon=True).start()
            learnings = [x for x in self.team.all()['learnings'] if x['agent_id'] in ('', agent_id)][:80]
            _, dirs = self.training(agent_id)
            parts = [self.focus(dict(kind='edit', id=pid), [], None), '', 'Josh asked: ' + change]
            if self.anim_notes_text(pid):
                parts += ['', self.anim_notes_text(pid)]
            for name in SKILLS.get(agent_id, []):
                text = self.skill_text(name)
                if text:
                    parts += ['', f'=== Skill: {name} (follow it for anything you make or change in the deck) ===', text]
            before = self._deck_state(er, pid)
            system = self.desk_system_prompt(agent, learnings) + '\n\nYou are doing one deck change Josh asked for in chat. Do it fully, then answer once.'
            system += ('\n\nJosh watches your progress on the Edit page. As you move between stages, overwrite ' + DECK_PROGRESS + ' in the '
                       'video folder with one line of JSON, for example {"stage": "plan"} or {"stage": "build", "n": 3, "of": 9, "what": "the chessboard"}. '
                       'Stages in order: read (reading the script and notes), plan (deciding the shots), build (once per animation as you start it, '
                       'with its number, the total and a few words on what it shows), shots (writing the shot list), check (checking the deck). '
                       'Keep "what" short and plain, like "the four tips tracker" or "the conversation with the VP".')
            reply, _ = self.run(system, '\n'.join(parts), DESK_SCHEMA, list(dirs), tools='Read,Grep,Glob,Edit,Write',
                                allow=scoped('Read,Grep,Glob,Edit,Write', folder), cwd=folder, timeout=1800, full=True)
            stop.set()
            saved = self.apply_edit(er, pid, {}, before)
            text = reply or 'Done.'
            if flow:
                mark('check', 'done', 'The slides and shot list are ready to review')
                flow._setup_finish(pid, 'done', 'The slides and shot list are ready to review.', 'animate')
        except Exception as e:  # noqa: BLE001 - the failure goes back to Josh in the chat
            stop.set()
            saved, text = [], f"I couldn't change the deck: {str(e)[:300]}"
            if flow:
                flow._phase_fail(pid, 'animate', 'read', str(e)[:300])
        try:
            (Path(er.project_folder(pid)) / DECK_PROGRESS).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        self.post(agent_id, text, saved=saved, via=via)
        if via == 'discord' and getattr(self, 'notify', None):
            try:
                self.notify(agent_id, text, [])
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _deck_progress(path, mark, stop):
        """Turn the progress line Tariq writes as he works into steps on the Edit page."""
        last = None
        labels = dict(read='Reading the script and your notes', plan='Deciding which animation goes where', shots='Writing the shot list',
                      check='Checking the deck against the house style')
        while not stop.wait(2):
            try:
                d = json.loads(path.read_text(encoding='utf-8').strip() or '{}')
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict) or d == last or d.get('stage') not in ('read', 'plan', 'build', 'shots', 'check'):
                continue
            last = d
            if d['stage'] == 'build':
                n, of = d.get('n'), d.get('of')
                what = str(d.get('what') or '').strip()[:80]
                ok = isinstance(n, int) and isinstance(of, int) and 0 < n <= of
                msg = (f'Animation {n} of {of}' if ok else 'Building an animation') + (f', {what}' if what else '')
                mark('build', 'running', msg, fraction=(n - 1) / of if ok else None)
            else:
                mark(d['stage'], 'running', labels[d['stage']])

    def guides_dir(self):
        courses = getattr(self, 'courses', None)
        content = courses.content_dir() if courses is not None else Path(r'E:\CareerOS\04_Programme\Course_Content')
        return Path(content).parent / 'Guides', Path(content) / 'Heard_at_the_Top' / 'Lesson_Guides' / '_source'

    def start_guide(self, agent_id, spec, via=''):
        """Josh asked for a standalone written guide: Saskia writes and builds it as a job and posts the PDF back."""
        topic = str(spec.get('topic') or '').strip()[:120]
        kind = 'companion' if spec.get('kind') == 'companion' else 'working'
        brief = str(spec.get('brief') or '').strip()[:6000]
        if not topic:
            return [dict(id='', title='the guide', action='failed', kind='guide', error='The guide needs a topic.')]
        self.jobs = getattr(self, 'jobs', {})
        key = ('guide', topic.lower())
        t = self.jobs.get(key)
        if t and t.is_alive():
            return [dict(id='', title=topic, action='failed', kind='guide', error='That guide is already being written.')]
        t = threading.Thread(target=self._guide_job, args=(agent_id, topic, brief, via, kind), daemon=True)
        self.jobs[key] = t
        t.start()
        return [dict(id='', title=('the working document on ' if kind == 'working' else 'the guide on ') + topic, action='started', kind='guide')]

    def _guide_job(self, agent_id, topic, brief, via, kind='working'):
        import re as _re
        agent = next((a for a in self.team.all()['agents'] if a['id'] == agent_id), None)
        out, kit = self.guides_dir()
        slug = _re.sub(r'[^a-z0-9]+', '-', topic.lower())[:50].strip('-') or 'guide'
        work = out / '_source' / f"{now()[:10]}_{kind}_{slug}"
        files, text = [], ''
        try:
            work.mkdir(parents=True, exist_ok=True)
            try:
                python = json.loads((self.root / 'settings.json').read_text(encoding='utf-8')).get('python') or sys.executable
            except (OSError, ValueError):
                python = sys.executable
            learnings = [x for x in self.team.all()['learnings'] if x['agent_id'] in ('', agent_id)][:80]
            _, dirs = self.training(agent_id)
            bens = Path(kit).parent.parent / 'Bonuses' / 'BENS-Dictionary' / '2026-10_bens-dictionary_swipe-file_v02.html'
            parts = [GUIDE_BRIEF.format(kit=kit, work=work, out=out, python=python, root=self.root, bens=bens), '',
                     'The kind: ' + ('working document' if kind == 'working' else 'lesson companion'), 'The topic: ' + topic]
            if brief:
                parts += ['', "What Josh asked for: " + brief]
            for name, note in (('written-guides', 'the brief for this guide, follow it exactly'),
                               ('careeros-playbook', 'house checks for CareerOS documents; the written-guides skill wins where they differ')):
                skill = self.skill_text(name)
                if skill:
                    parts += ['', f'=== Skill: {name} ({note}) ===', skill]
            system = self.desk_system_prompt(agent, learnings) + '\n\nYou are writing one standalone guide Josh asked for. Do it fully, then answer once.'
            careeros = Path(out).parent.parent
            reply, answer = self.run(system, '\n'.join(parts), GUIDE_SCHEMA, list(dirs) + [str(kit), str(careeros)],
                                     tools='Read,Grep,Glob,Write,Edit,Bash', allow=scoped('Read,Grep,Glob,Write,Edit,Bash', out),
                                     cwd=str(self.root), timeout=3600, full=True)
            pdf = Path(str((answer or {}).get('pdf') or ''))
            if not pdf.is_file() or out not in pdf.parents:
                found = sorted(out.glob(f'*_{slug}*_guide.pdf'), key=lambda f: f.stat().st_mtime)
                pdf = found[-1] if found else None
            text = reply or 'The guide is done.'
            if pdf:
                files = [pdf]
            else:
                text += ' I could not find the PDF in the Guides folder, so check the build.'
        except Exception as e:  # noqa: BLE001 - the failure goes back to Josh in the chat
            text = f"I couldn't finish the guide on {topic}: {str(e)[:300]}"
        self.post(agent_id, text, files=files, via=via)
        if via == 'discord' and getattr(self, 'notify', None):
            try:
                self.notify(agent_id, text, [str(f) for f in files])
            except Exception:  # noqa: BLE001
                pass

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
