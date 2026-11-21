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
from . import gmail_access

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

# Guidance only: the connector/backend must validate and persist actual attribution.
LINK_ATTRIBUTION_RULES = """
Tracked-link source attribution (all agents):
Use lookup_link_context and preview_tracked_link before creation, using only fields the installed tool schema supports.
For youtube_video and video pinned_comment sources, resolve and verify the intended video's Linklr video_id using
lookup_link_context or register_video. A local project ID or video title is not a Linklr video_id. For channel-wide links,
use youtube_channel; channel-wide reuse can be intentional and does not identify an individual post.
For sequence emails, supply and verify both sequence_name and sequence_position for the intended sequence step.
For broadcasts, use a stable per-email email_key when the installed schema supports it. If it does not, report that
per-broadcast identity cannot be supplied through that schema; never send an unsupported field or substitute a label.
A preview reuse match is not sufficient proof of source identity. A different post label does not make a distinct source.
Reuse only after checking the same intended source, asset/destination, placement, campaign and applicable video/email
identity. Never silently reuse another post's URL or accept an ambiguous match. Keep caption and QR placements separate.
The currently inspected MCP schema lacks creative_id, posted_at, carousel_instance_id and independent saved-link readback.
For community/carousel requests, report this structured-association gap honestly. Do not claim that a label, date,
placement name or locally saved URL establishes creative/post association. Do not invent unsupported fields, change
campaigns/destinations to force uniqueness, or use legacy planning endpoints to repair history. Existing published URLs
and event history must remain unchanged. Creation/import timestamps are not proof of publication.
Use the actual tool result when reporting creation or reuse. Where supported, read back the saved source and destination;
when readback or required association is unavailable, state exactly what remains unverified. These instructions are
workflow guidance, not proof that the connector enforces attribution or that a link has been correctly associated.
"""
# CareerOS is authenticated in this project's private Claude MCP configuration.
# Explicitly authorized learner-support reads are independent of the other integration setup gate.
CAREEROS_READ = tuple('mcp__careeros__' + t for t in (
    'find_learners', 'get_learner', 'get_learner_admin_history', 'get_learner_progress',
    'get_learner_timeline', 'list_questions', 'list_reviews'))
CAREEROS = {'anouk': CAREEROS_READ}
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
EMAIL_WRITE = ('subject', 'preview', 'body', 'planned_date', 'send_time', 'notes', 'description', 'audience', 'type')
MCP_STARTUP_WAIT_MS = '30000'


def show_only(answer):
    """An answer that shows an email keeps only its reply and the email to show. Everything else in it (email saves,
    a new draft rebuilt from the chat, a Kit draft, any other change) is held back. Returns (answer, held field names)."""
    if not str((answer or {}).get('show_email') or '').strip():
        return answer, []
    held = sorted(k for k, v in answer.items() if k not in ('reply', 'show_email', 'replacement') and v not in (None, [], {}, '', False))
    return {k: answer[k] for k in ('reply', 'show_email') if k in answer}, held
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
GMAIL = {'laia': GMAIL_READ}
# Anouk uses the independent read-only Gmail gate below, never the broad activation switch.
# Calendly through the connector linked to Claude on this PC: reading calls, and single-use links to book a meeting.
CALENDLY_READ = tuple('mcp__claude_ai_Calendly__' + t for t in (
    'users-get_current_user', 'meetings-list_events', 'meetings-get_event', 'meetings-list_event_invitees',
    'event_types-list_event_types', 'availability-list_user_busy_times', 'scheduling_links-create_single_use_scheduling_link'))
CALENDLY = {'anouk': CALENDLY_READ}
# Team members still being set up. They get none of their outside tools (Gmail, Calendly, Kit, Linklr, Chrome)
# until Josh adds their id to "team_live" in settings.json. CareerOS-only reads are explicitly authorized separately.
SETUP_MODE = {'anouk'}
from .email_visuals import VISUAL_PROPS

EMAIL_PROPS = {
    'email_visuals': VISUAL_PROPS,
    'emails': {
        'type': 'array',
        'description': 'Emails to add or change in Josh OS. Leave empty when nothing needs saving.',
        'items': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'Id of an existing email to change. Leave out to add a new draft. Only when Josh asked for a change.'},
            **{k: {'type': 'string'} for k in EMAIL_WRITE},
            'tags': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Local planner tags only, not Kit subscriber tags.'},
            'archived': {'type': 'boolean', 'description': 'Archive/restore this local planner record only when Josh requests it. Never deletes from Kit.'},
            'links': {'type': 'array', 'description': 'Explicit links attached to natural words in body: [{label, href}]. '
                      'Use only the exact provided or verified URL, preserving Liquid. The label must appear once in body. '
                      'For a CTA, write a natural sentence and link its descriptive words; do not append a colon and bare URL. '
                      'Preserve existing links unless Josh requests a change; update label and body together.',
                      'items': {'type': 'object', 'required': ['label','href'],
                                'properties': {'label': {'type':'string'}, 'href': {'type':'string'}}}},
            'sent': {'type': 'boolean', 'description': (
                'True only when Kit shows this email went out, with kit_broadcast_id. Josh OS checks that id against Kit and '
                'refuses otherwise. False to undo a Sent that was wrong. Never set it from a planned date or from a chat '
                'message alone. Leave it out otherwise.')},
            'kit_broadcast_id': {'type': 'string', 'description': 'With sent true: the Kit broadcast id that went out.'},
            'source_ids': {'type': 'array', 'items': {'type': 'string'},
                           'description': 'Library emails this one is based on, as JonBrosio:<id> or EdLawrence:<message_id>.'},
            'results': {'type': 'object', 'description': 'How it did in Kit: recipients, opens, open_rate, clicks, click_rate, unsubscribes, new_people, kit_broadcast_id, checked_at.'}}},
    },
    'show_email': {
        'type': ['string', 'null'],
        'description': ('When Josh asks to see an email: its id from the snapshot. Josh OS adds the saved email, word for word, '
                        'to your reply. Read only. Never rebuild an email from the chat to show it, and never save it when '
                        'showing it. Leave null otherwise.')},
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
   "CareerOS Â· Guide" for a companion and "CareerOS Â· Working document" for a working document.
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
            'frameio': {'type': 'string', 'enum': ['send', 'feedback'], 'description': (
                'Optional Frame.io review, only when Josh asks in this message. "send" uploads the newest checked export to his '
                'private Frame.io review folder (never public, never YouTube). "feedback" reads the comments on the exact version '
                'that was sent. Josh OS does it and tells you what happened. Leave it out otherwise.')},
            'finish': {'type': 'boolean', 'description': 'True when Josh asks you to put the video together, render the animations, '
                                                        'assemble the edit or finish it, and says he is happy with the animations, however he puts it. '
                                                        'Josh OS does it all on this PC, with no shell or terminal needed: it opens Premiere and your '
                                                        'project, builds and saves the cut if there is no good cut yet, approves the storyboard on his words '
                                                        'in this message, renders and places the animations, exports and checks the file, and you post back '
                                                        'at each step. Never tell Josh to run anything himself or to open a terminal. Leave it out when he '
                                                        'has not said the animations are good, and ask him instead.'},
        },
    },
}
CAN_CREATE_PROJECT = {'sam', 'tariq'}
CAN_EDIT_COURSE = {'saskia'}

def requests_new_project(message):
    """New-card creation needs a direct request, not a suggestion or historical context."""
    import re
    text = str(message or '').lower()
    if re.search(r"\b(?:don't|do not|never|stop|cancel)\b.{0,40}\b(?:create|make|add|start|save|card|video|project)\b", text):
        return False
    return bool(re.search(r'\b(?:create|make|add|start|save|put|set up)\b.{0,160}\bnew (?:video |idea )?(?:card|video|project)\b', text)
                or re.search(r'\b(?:create|make|add|start|set up)\s+(?:me\s+)?(?:a|an)\s+(?:new\s+)?(?:video |idea )?(?:card|video|project)\b', text)
                or re.search(r'\bcreate\s+(?:card|video|project)\b', text)
                or re.search(r'\bnew (?:card|video|project)\b.{0,100}\bplease\b', text))

PROJECT_PROPS = {
    'new_projects': {
        'type': 'array', 'maxItems': 20,
        'description': 'Create NEW video cards when Josh asks. Never reuse or overwrite another card. New cards start at Idea; dates are planning only, never publishing or scheduling.',
        'items': {'type': 'object', 'additionalProperties': False, 'required': ['name'], 'properties': {
            'name': {'type': 'string', 'minLength': 1, 'maxLength': 240},
            'notes': {'type': 'string', 'maxLength': 50000},
            'script': {'type': 'string', 'maxLength': 200000},
            'publish_date': {'type': 'string', 'description': 'Optional planned YYYY-MM-DD, never a real scheduled send.'},
            'add_titles': {'type': 'array', 'items': {'type': 'string', 'maxLength': 500}, 'maxItems': 200}
        }}
    },
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
            'add_titles': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Title ideas to add to the video.'},
            'remove_title_ids': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Exact saved title IDs to remove, only when Josh asks.'},
            'chosen_title': {'type': 'string', 'description': 'Exact saved title ID to choose, or empty to clear. Existing lock is enforced.'},
            'top_three': {'type': 'array', 'maxItems': 3, 'items': {'type': 'string'}, 'description': 'Ordered existing thumbnail asset IDs; empty clears selection. Existing lock is enforced.'},
            'remove_thumbnail_assets': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Remove these saved thumbnail references from the card only. Original files remain.'},
            'description': {'type': 'string', 'description': 'The whole YouTube description shown in Packaging, when it changes. This is the text Publish uses, so there is never a separate copy. Josh approves it again on the video page.'},
            'pinned_comment': {'type': 'string', 'description': 'The whole pinned comment shown in Packaging, when it changes. Publish uses this exact text. Josh approves it again on the video page.'},
            'packaging': {'type': 'object', 'description': 'Partial local packaging brief: thumbnail_concept, thumbnail_reference, curiosity_question, concrete_payoff. Merged with existing.'},
            'lead_magnet': {'type': 'object', 'description': 'Partial local lead-magnet brief: decision unknown/none/yes, name, contents, result, free_status, destination, evidence. Does not upload or publish.'},
            'archived': {'type': 'boolean', 'description': 'Archive or restore the local video card; does not delete files or change YouTube.'}},
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
            'note': {'type': 'string', 'description': 'What Josh asked for: words, formats, poses, how many, anything to copy.'},
            'provider': {'type': 'string', 'enum': ['gpt-image', 'local'], 'description': 'Default gpt-image. Use local only when Josh explicitly requests the local compositor.'},
            'reference_assets': {'type': 'array', 'maxItems': 6, 'items': {'type': 'string'},
                                 'description': 'Exact image asset IDs from this conversation or video to use/edit. Never paths or URLs.'}},
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
                'size': {'type': 'number', 'description': 'Font size in pixels on the 1920 wide frame; default 200, technical range 8 to 1080.'},
                'fit': {'type': 'string', 'enum': ['size', 'fill', 'none'], 'description': 'size shrinks to fit; fill fills the box; none preserves size/placement and allows intentional cropping.'},
                'margin': {'type': 'number', 'description': 'Automatic fitting margin, default 40. Use 0 for edge placement, or fit none for cropping.'},
                'bar': {'type': 'boolean', 'description': 'A coloured bar behind the words.'},
                'bar_color': {'type': 'string'},
                'bolden': {'type': 'number', 'description': 'Thickens letters; normally 0 to 6, maximum 64.'},
                'outline': {'type': 'number', 'description': 'Outline width; normally 0 to 14, maximum 128.'},
                'outline_color': {'type': 'string'},
                'shadow': {'type': 'boolean', 'description': 'The house glow and drop shadow. On unless you turn it off.'},
                'case': {'type': 'string', 'enum': ['as-is', 'upper']}}},
            'size': {'type': 'number', 'description': 'close only. Font size in 1280-wide units, about 142.'},
            'face_cx': {'type': 'number'}, 'face_cy': {'type': 'number'}, 'face_w': {'type': 'number'},
            'head_top': {'type': 'number'}, 'body_bottom': {'type': 'number'}, 'desk_top': {'type': 'number'},
            'baseline': {'type': 'number'}, 'bar_left': {'type': 'number'}, 'bar_right': {'type': 'number'},
            'text_cx': {'type': 'number', 'description': 'Text centre on the 1920x1080 canvas.'},
            'line_gap': {'type': 'number', 'description': 'Baseline spacing as a multiple of font size; default 0.92.'},
            'text_behind': {'type': 'boolean', 'description': 'Place the catalogue cutout over the text, including hands where they overlap.'},
            'shade': {'type': 'boolean', 'description': 'Use the bottom shade; default true.'},
            'scene': {'type': 'string', 'enum': ['seated', 'standing', 'close']},
            'shade_start': {'type': 'number'}},
            'required': ['label', 'format', 'pose']}},
    },
    'required': ['reply', 'options'],
}
GPT_THUMB_JOB_SCHEMA = {
    'type': 'object', 'properties': {
        'reply': {'type': 'string'},
        'options': {'type': 'array', 'maxItems': 3, 'items': {
            'type': 'object', 'properties': {
                'label': {'type': 'string'}, 'words': {'type': 'string'},
                'prompt': {'type': 'string', 'description': 'Complete desired composition, exact lettering, reference roles and details to preserve. User instructions override design defaults.'},
                'pose': {'type': 'string', 'description': 'Optional catalogue pose name, used as a likeness reference, not fixed geometry.'},
                'scene': {'type': 'string', 'enum': ['seated', 'standing', 'close'], 'description': 'Optional existing background reference.'}},
            'required': ['label', 'prompt']}}}, 'required': ['reply', 'options']}
GPT_THUMB_BRIEF = """Plan a GPT Image thumbnail job for Josh. You only propose prompts; the host calls OpenAI and reports
actual results. Return one option unless Josh requests alternatives, at most three. Never claim an image already exists.
Use the user's exact words and composition instructions ahead of house-style advice. There are no template baseline,
placement, font or scene-geometry rules for this provider. Describe overlaps, hands, lettering, background and likeness
in the prompt. The host supplies selected reference images, in listed order. Identify their intended roles in the prompt.
For Josh's subject/background, optionally select an existing catalogue pose by name and scene seated, standing or close;
these become image references, not a deterministic render. Do not add them when the user requests a different subject or
background. With no selected references, pose or scene, the provider generates from text; otherwise it edits from references.
An edit of an earlier image needs its exact asset ID in the outer thumbnail request. Do not invent file paths, URLs or IDs.
House defaults such as yellow bars or lower text placement are optional. The user's own instructions always win.
No automatic local fallback is allowed. Provider access/errors are reported by the host, and only a successful API image
can be called GPT output. The finished image still needs Josh to review text accuracy and likeness before use."""
THUMB_BRIEF = """Make a round of YouTube thumbnail options for this video. Use 05_thumbnails.md as design defaults;
Josh's explicit directions and reference style override its composition and styling advice. You can read files but you
do not run anything or save anything yourself. Answer with up to three options in "options". Josh OS checks each one and
builds it with Josh's approved scripts (oneline.py for "middle", close_unit.py for "close"), in the house text treatment.

The two formats you can use:
- "middle": Josh seated at the desk, one line of words across the bottom (the current A/B winner). Settings and defaults:
  pose (a Seated cutout, seated-13-palm-present-neutral is the approved one), face_cx 960, head_top 22, body_bottom 1100,
  desk_top 1008, words (two to four words), baseline 975, bar_left 130, bar_right 1790 (normally avoids the badge corner),
  shade_start 400. Leave a number out to use its default.
- "close": Josh close on the left, one or two lines of words on the right. pose (finger up, chin or side point work, like
  seated-06-side-point-neutral), face_cx 483, face_cy 350, face_w 432, lines (one or two, short), size about 142, shade_start 325.
  Default text centre is 1297, first baseline 470, text box 737 to 1857.
These are starting points, not mandatory geometry. Explicit baseline, text_cx, bar_left/bar_right, face position and
head_top/body_bottom override them. Coordinates use a fixed 1920x1080 canvas. A middle baseline below 850 is valid.
With style, middle lines stack upward from baseline; close lines stack downward. line_gap defaults to 0.92 times size.
Set text_behind true to put the whole catalogue cutout over the text (hands overlap where the cutout overlaps).
Set shade false to omit bottom shading; scene may be seated, standing or close. A separate hands-only mask is not available.
Read the approved specs in the tools folder (spec_make-them-explain_desk-bottom_v01.json, spec_close_never-go-blank_v03.json)
and the thumb-notes files the training names. Look at the pose previews in the Pose Catalogue before you pick a pose.
Text style. Leave "style" out for the house look (Josh Thumbnail V2, white words on the yellow bar). When Josh asks for
another font, colour, size or weight, set "style" and give the words in "lines" (one or two). Josh OS shrinks words that
would run off the frame and tells Josh it did. For intentional cropping or exact size/placement, use style fit none;
margin controls the automatic fitting inset (default 40). Positive sizes and bounded finite coordinates still apply.
Three or more big words stay bigger split over two lines. Fonts you can use, as font: weights:
{fonts}
Colours by name: {colours}, or any hex code. Big bold yellow words, for example, are style font anton (or impact, chivo,
gotham black), color bright-yellow, bar false, size 220 to 300, case upper, with the shadow on and outline 0 to 6 in black.
If Josh asks for a font that isn't in this list, say it isn't available and pick the closest one from the list.
Pick words that name the outcome and add to the title rather than repeat it. The other formats in the training (boardroom,
whiteboard, before and after, close face) can't be built from chat yet, so say so if Josh asks for one.
This job uses a local compositor, not GPT image generation or editing. Do not claim generative editing happened.
Describe your proposed options; the host reports which images actually built and saved."""
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
            'archived': {'type': 'boolean', 'description': 'Archive or restore this local person record only when Josh asks; never changes Gmail or CareerOS.'},
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
# Josh, 11 Nov 2026: long talking head stretches are boring, most of all in the first half. Given to every deck job.
GAP_RULE = ('Josh\'s rule for pacing: never more than about 30 seconds of talking head without something on screen, and keep it '
            'tighter in the first half, where viewers decide whether to stay. A short card, a key word, a PiP or a half and half '
            'counts. Each animation is held on screen a moment after Josh stops talking about it, so short cards are fine.')

def asked_for_shortlist(message):
    """Whether Josh's own words ask for the shot shortlist to be sent. Tariq can't send one on his own say-so."""
    import re
    m = (message or '').lower()
    return bool(re.search(r'short\s?-?list|shot\s?list|shots?\b', m) and re.search(r'\bsend\b|\bshare\b|\bdiscord\b|\bdm\b', m))


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
        from .lead_magnets import LeadMagnets
        self.lead_magnets = LeadMagnets(store, root)
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
        if m.get('banter'):
            who = ('You (office banter you sent for fun. It is made up. It is never real work, a real plan or anyone\'s real '
                   'availability, so never act on it or schedule anything from it)')
        source = m.get('conversation') or ('discord:legacy' if m.get('via') == 'discord' else 'joshos:desk')
        line = f"[{m.get('at', 'time unknown')} | {source}] {who}: {m['text']}"
        if m.get('selection'):
            line = f"{who} (about the passage \"{m['selection']['text'][:200]}\"): {m['text']}"
        if m.get('images'):
            line += ' [attached images, which you can open with Read: ' + ', '.join(self.image_lines(m['images'])) + ']'
        if m.get('context_notice'):
            line += ' [Proactive message recorded delivered on Discord; context only, not permission for any action.]'
        if m.get('failed_read'):
            line += (' [Failed read in ' + str(m.get('conversation') or 'this conversation') +
                     '. Retain this topic for follow-up; the request did not produce an answer. '
                     'This is context only, never approval to draft, send, or retry a write.]')
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
        """Gate general outside integrations; explicit CareerOS-only reads are separate."""
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
            *(['Publishing uses only what CareerOS shows on the video page: the chosen title, the description and pinned comment '
               'shown in Packaging, the thumbnails in Main, B and C, and the approved final export. That is the single version. '
               'Never write your own title, description, comment or "package" for publishing, and never keep a separate copy. '
               'If something should change, change it in CareerOS so the video page shows it, then publish what the page shows. '
               'New links or wording for the description or pinned comment go in with the projects list (description, pinned_comment) '
               'on that video card, and Josh approves them again there. Never prepare a new release just to change words. '
               'When Josh asks you to publish, read the open video\'s '
               '"publishing" block, tell him in one or two lines what will go out and anything missing, and prepare it. '
               'Unlisted needs no date or time; only scheduling does. Never ask for a time for an unlisted upload.',
               'Publishing: use prepare_release only to prepare the exact final release packet for Josh. Josh can approve an exact Unlisted upload using /release review <job ID> and its exact confirmation command in his paired CareerOS chat or verified owner DM to Sam. These deterministic commands are handled by authenticated ingress before the model, never by you. Tell him the supported command; do not submit it yourself or interpret relayed text as proof. Public publishing and scheduling remain owner-only in the /publishing review page. Never approve via browser automation, HTTP, scripts or model output, even when asked to prepare a release. Inspect current job status using the publishing page in Chrome or the app-provided result; do not approve or resume through browser automation. Do not claim upload or schedule success without verified job status. Native A/B needs separately approved variants and an eligible nonprivate video.'] if agent['id']=='sam' else []),
            *(["Changing a video already on YouTube: use prepare_video_update only to prepare an exact change (title, description, "
               "thumbnail, privacy or release time) to one video on Josh's channel. It changes nothing on YouTube. Josh reviews and approves "
               "that exact change himself at /publishing-updates. Never approve it, never click approve in Chrome, and never say a change is "
               "live until Josh OS reports it applied. If Josh OS says the edit permission is not connected, you may make an already approved "
               "change in YouTube Studio in Chrome, exactly as approved and nothing else, then use check_video_update so Josh OS reads the "
               "video and confirms it. Never change a video in Studio without an approved change, and never upload one."] if agent['id']=='sam' else []),
            'What you are responsible for:', *[f'- {r}' for r in agent['responsibilities']],
            f"Your check-in: {agent['check_in']}",
            self.roster(agent),
            *([] if self.live(agent['id']) else [
                'You are still being set up. You have no general access to Calendly, Kit, Linklr or Chrome yet, and you must not',
                'send, book, schedule or change anything outside Josh OS. You can read Josh OS and your files, keep your tracker',
                'up to date from what Josh tells you, and write any message here in chat for Josh to send himself.']),
            '',
            'This is your chat with Josh in Josh OS. It is one conversation, wherever he opens it, on your own page, in the side',
            'dock or beside a script. When the prompt says what he has open, that tells you what "this" means. It never limits',
            'what you can do. Everything you can change or start is open to you in every reply, on any page.',
            '- Josh can attach images such as screenshots. Open each one with Read and look at it before you answer. To put one on',
            '  a video\'s Results tab, use its asset id as the screenshot.',
            '- Sam and Tariq can create a NEW video card with new_projects only when Josh directly asks in this message (name, notes, script, planned publish_date, add_titles). Other roles hand video creation to Sam. Never repurpose an existing card to stand in for a new one.',
            '- Change an existing video card with the projects list and its exact project_id. These actions work in every chat, including Discord, without opening a particular page.',
            '- Josh OS is the one record of everything. Josh checks the app, so every change he asks for has to be made on the item',
            '  itself in Josh OS, in its own fields (date, stage, title, description, pinned comment, slides and so on), in this same',
            '  reply. A note, a file on the E drive, a separate packet or a message in chat does not count as making the change.',
            '- When Josh asks you to replace, swap or move something, change both sides in the same reply. The new item goes on the',
            '  date or slot, and the old one comes off it. Give the old one the new date Josh named, or clear its date if he named',
            '  none, so it shows as needing a date. Never leave two things on the same day unless Josh asked for both.',
            '- If Josh OS has no field for a change, say so plainly in your reply and ask what to do. Never put it somewhere the app',
            '  doesn\'t show and call it done.',
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
                  '  Check the intended source before reusing a preview match; follow the shared attribution guidance.']
                 if 'create_tracked_link' in LINKLR.get(agent['id'], ()) else []),
               '- If a Linklr tool says it needs authentication, tell Josh to sign in to Linklr in Claude Code once.']
              if agent['id'] in LINKLR else []),
            *(['- CareerOS learner support is available through mcp__careeros__ read-only tools. Resolve the learner with',
               '  find_learners first; do not guess between ambiguous matches. Read access grants and their provenance rather',
               '  than inferring current entitlement from legacy membership labels. Keep the 47 core lessons separate from',
               '  Data Storytelling and course downloads. Lesson opens are not completions or evidence of watching.',
               '  Preserve tracking coverage and backfill labels: missing history means not tracked, never zero.',
               '  Email handoff or queued reply notifications do not prove delivery. Respect private question visibility',
               '  and avoid unnecessary personal details. Never change learners, access, questions, reviews or messages.',
               '  If authentication fails, say the connection needs reconnecting; never request or expose tokens.']
              if agent['id'] in CAREEROS else []),
            *gmail_access.prompt_rules(agent['id'], self.root),
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
               '  emails himself, so never change their status, except setting sent true with its kit_broadcast_id when Kit shows',
               '  that broadcast completed, or sent false to undo a Sent that was wrong. A planned date passing, or Josh saying',
               '  something was sent, is never enough: if he does, find the broadcast in Kit first and ask which email he means.',
               '  When a planned date has passed with no send, ask Josh whether to replan or retire it. Keep {{ subscriber.first_name }} and link wording as they are.',
               '  When Josh asks to see an email, put its id in show_email and say nothing more about its text. Never rebuild an',
               '  email from the chat, and never save one just because he asked to see it. Only say a change is saved when',
               '  Josh OS reports it under your reply; if a save fails, say so plainly.',
               '- When Josh asks for visual explanations, use email_visuals for this saved email id and revision. Supply short',
               '  text lines or safe static SVG, alt text and after_line (count blank body lines). Up to 16 visuals are supported.',
               '  Templates are optional starting points. Choose width/height or aspect_ratio, layout (text/card/standalone),',
               '  background, optional BEFORE/AFTER badge and one-line note. Footer defaults off. Use standalone SVG for unique',
               '  designs, or slide-card for wide example slides. Keep text readable; shorten notes that do not fit. Never force',
               '  before/after examples into square carousel frames. Keep source content and numbers faithful.',
               '  Save body changes first in a separate turn. These are local PNGs shown in the email preview; never HTML or file paths.',
               '  This replaces the entire visual set. No carousel permissions, upload, Kit proposal or delivery is included.',
               '  Only report success when the save result confirms it. Kit proposals are blocked until approved hosting is supported.',
               '- Kit through its tools may still be connecting when you start. If its tools are missing, say Kit is still',
               '  connecting, not that it is unavailable, and use Kit\'s last complete read in your snapshot.',
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
        from .gpt_images import GPTImages
        status = GPTImages().status()
        return ['- For a requested thumbnail, put the video in "thumbnails", with the full design request in "note".',
                '  Use provider "gpt-image" by default for generation and reference-image editing. Use "local" only if Josh',
                '  explicitly asks for the local compositor. Never substitute local output for a failed GPT request.',
                '  Include exact reference_assets IDs for attached or earlier images he wants used/edited; current attachments',
                '  are included by default. Never invent IDs, paths or URLs. His composition instructions override style defaults.',
                '  The host reports whether the job started and whether an image was saved. Do not claim success before that.',
                '  GPT provider status: ' + status['message'],
                '  Local middle/close formats remain available with flexible font, colour, position and overlap settings.']

    def start_thumbnail_request(self, thumbs, request, message, images, via):
        if not isinstance(request, dict):
            raise ValueError('Invalid thumbnail request.')
        pid = str(request.get('project_id') or '')
        project = self.store.get(pid)
        known = set(images)
        known.update(t['asset'] for t in project.get('thumbnails', []) if t.get('asset'))
        for entry in self.thread(DESK, 'sam')[-20:]:
            known.update(entry.get('images') or [])
        refs = thumbs.reference_ids(request.get('reference_assets', images))
        if not set(refs) <= known:
            raise ValueError('Use reference image IDs already attached to this conversation or video.')
        return thumbs.start(pid, str(request.get('note') or message)[:8000], via,
                            provider=request.get('provider', 'gpt-image'), reference_assets=refs, user_note=message)

    def carousel_rules(self):
        from .carousels import TAG_OPTIONS, TAG_TEXT, TAG_FLAGS, RESULT_NUMBERS, STAGES
        return [
            '- The Carousels page in Josh OS is the record for every carousel, its tags and its results. Save to it with the',
            '  "carousels" list in your answer. Each entry adds a carousel, or changes one when you give its id from the snapshot.',
            '  When you draft carousels, save each one there as a Drafting card with hook, slides (a line of three dashes between',
            '  slides), caption and tags. When you post or schedule one, set the stage, post_date and link. When you read stats,',
            '  save them in results. Never invent a number. Leave out anything you did not check.',
            '  When one carousel replaces another on a day, send both entries. The new one gets the date, and the old one gets the',
            '  new date Josh named, or an empty post_date if he named none. A note saying it moved is not enough, because Today, the',
            '  calendar and the queue all read post_date.',
            f"  Stages: {', '.join(STAGES)}. Folder is the slide images folder under E:\\CareerOS\\03_Community_Posts.",
            '  Tags use these exact values: ' + '; '.join(f"{k} = {' / '.join(v)}" for k, v in TAG_OPTIONS.items()) + '.',
            f"  Free text tags: {', '.join(sorted(TAG_TEXT))} (post_time like 21:00). True or false tags: {', '.join(sorted(TAG_FLAGS))}. slide_count is a whole number.",
            f"  results has d1 and d7, each with any of {', '.join(RESULT_NUMBERS)} and checked_at (a date).",
            '  Tags and results you send are merged into what is already saved. Describe proposed changes, not a successful save.',
            '  The host saves after your answer and supplies the verified receipt. Never say saved, changed or redrawn before that receipt.',
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
            '- Paper is a default, not a restriction. Sam can use carousel_slide_override to replace one existing slide with',
            '  a custom composition or Sam-owned local_asset_id, preserving all other slide images byte-for-byte.',
            '  Josh must ask to replace/redesign a slide and select or name the carousel. Use its current revision and',
            '  1-based slide_number. Supply visual (structured static layers) OR local_asset_id; optional slide_text changes',
            '  only that slide text. Use listed authorized upload/slide image IDs or your own LocalAssets IDs for pictures.',
            '  Dimensions, layout, colours and branding are overridable. Cover/photo and QR images can be composed with text.',
            '  This action accepts no arbitrary paths or SVG. Do not also send carousels/make_design in the same reply.',
            '  Scheduled/posted carousels are blocked; a successful override requires review again. It never sends or publishes.',
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
            from .emails import proven_send
            today = _d.today().isoformat()
            rows = [e for e in mails.all() if not e.get('archived')]
            upcoming = sorted([e for e in rows if e.get('status') != 'Sent'], key=lambda e: e.get('planned_date') or '9999')
            sent = sorted([e for e in rows if e.get('status') == 'Sent'], key=lambda e: e.get('planned_date') or '', reverse=True)[:8]
            lines += ['', 'Emails in Josh OS. id | planned date | time | status | subject. The planned date is only the plan, never',
                      'proof of a send. Draft = being written or waiting for Josh, Ready = Josh approved it, Scheduled or Published =',
                      'in Kit, Sent = Kit shows it went out. To show Josh an email, put its id in show_email; Josh OS adds the saved text.']
            for e in upcoming + sent:
                k = e.get('kit') or {}
                proven = proven_send(self.store, e, getattr(self, 'kit_drafts', None))
                proof = proven['broadcast_id'] if proven else None
                flag = ''
                if e['status'] != 'Sent' and e.get('planned_date') and e['planned_date'] < today:
                    flag = ' (planned date passed and Kit shows no send: ask Josh to replan it or retire it, never mark it sent)'
                elif e['status'] == 'Sent' and not proof:
                    flag = ' (Sent, with no Kit link in Josh OS. Leave it as it is unless Josh asks)'
                lines.append(f"- {e['id']} | {e.get('planned_date') or 'no date'} | {e.get('send_time') or ''} | {e['status']}"
                             + (f" | Kit {proof}" if proof else '') + flag + f" | {e.get('subject', '')[:90]} | revision {e['revision']} | {len(e.get('images', []))} local visuals")
            try:
                with self.store.connect() as c:
                    snaps = [json.loads(r[0]) for r in c.execute("SELECT body FROM business_counters WHERE key LIKE 'kit:%' ORDER BY key DESC LIMIT 5")]
            except Exception:  # noqa: BLE001 - the list above still stands
                snaps = []
            snap = next((s for s in snaps if s.get('complete')), None)
            if snap:
                newest = sorted(((v.get('send_at') or ''), bid, v.get('recipients')) for bid, v in snap['items'].items())[-5:]
                lines += [f"Kit's last complete read of sent broadcasts ({str(snap['taken_at'])[:16]} UTC, {len(snap['items'])} broadcasts). "
                          'Newest sends, broadcast id | sent at UTC | recipients:']
                lines += [f'- {bid} | {at[:16]} | {n}' for at, bid, n in reversed(newest)]
        people = getattr(self, 'people', None)
        if people is not None and 'customers' in (agent.get('workflows') or []):
            from .people import PATHWAYS
            lines += ['', 'Your customer success tracker. id | name <email> | pathway | stage | next step | open promises | last touchpoint | summary.',
                      'Stages by pathway: ' + '; '.join(f"{k} ({v[0]}): {', '.join(v[1])}" for k, v in PATHWAYS.items())]
            lines += people.describe() or ['- nobody yet']
            archived_people = [p for p in people.all()['items'] if p.get('archived')][:40]
            if archived_people:
                lines += ['Archived local people (may restore only if Josh asks): ' + json.dumps([{k:p.get(k) for k in ('id','name','email','revision')} for p in archived_people], ensure_ascii=False)]
            lines += self.admin_people(people)
        if agent_id in CAN_CREATE_PROJECT:
            lines += ['', 'Local video card editing IDs (no approval; locks stay enforced):']
            for item in self.store.projects()[:60]:
                lines.append(json.dumps({k:item.get(k) for k in ('id','name','revision','archived','titles','chosen_title','thumbnails','top_three','packaging','lead_magnet','locks')},ensure_ascii=False))
        lines += ['', 'Videos in Josh OS:']
        videos = [p for p in self.store.projects() if not p.get('archived')]
        results = getattr(self, 'results', None) if 'youtube' in (agent.get('workflows') or []) else None
        if results is None:
            for p in sorted(videos, key=lambda p: p.get('publish_date') or '9999')[:40]:
                lines.append(f"- {p['id']} | {p.get('publish_date') or 'no date'} | {p['stage']} | {p.get('chosen_title') or p['name']}")
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
                changes = {k: u[k] for k in fields if k in u and u[k] is not None and (k == 'minutes' or isinstance(u[k], str))}
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

    def thumbnail_job(self, project, note, folder, provider='local', reference_assets=(), user_note=''):
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
        schema = THUMB_JOB_SCHEMA
        if provider == 'gpt-image':
            brief, schema = GPT_THUMB_BRIEF, GPT_THUMB_JOB_SCHEMA
            facts['reference_assets'] = list(reference_assets)
            facts['user_composition_instructions'] = user_note or note
        parts = [brief, '', f'Your training files are in {self.training_dir()}. The tools folder is {TOOLS}. '
                 f'The Pose Catalogue is {POSES}. This video\'s folder is {folder}, and its thumbnails folder shows earlier rounds.',
                 '', 'The video:', json.dumps(facts, ensure_ascii=False, indent=1)]
        if note.strip():
            parts += ['', "Josh's note: " + note.strip()]
        system = self.desk_system_prompt(agent, learnings) + '\n\nYou are making one round of thumbnails that Josh asked for in chat.'
        return self.run(system, '\n'.join(parts), schema, list(dirs) + [str(folder), str(TOOLS), str(POSES)],
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

    def post(self, agent_id, text, images=(), saved=(), via='', files=(), notice=True, banter=False):
        """A message from a team member that arrives on its own, like the result of a job. With notice, it is also
        offered to studio/notices.py, which decides whether and when it goes to Josh on Discord. Callers that already
        sent it to Discord pass notice=False. Banter is made-up office chat, marked so the team member never mistakes it
        for real work later."""
        m = dict(id=str(uuid.uuid4()), role='agent', text=text, at=now())
        if banter:
            m['banter'] = True
        if images:
            m['images'] = list(images)
        if saved:
            m['saved'] = list(saved)
        if files:
            m['files'] = [str(f) for f in files]
        if via:
            m['via'] = via
        out = self._append(DESK, agent_id, m)
        hook = getattr(self, 'on_post', None)
        if notice and not banter and hook is not None:
            try:
                hook(agent_id, m)
            except Exception:  # noqa: BLE001 - the message is on the desk either way
                pass
        return out

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
    def run(self, system, prompt, schema=SCHEMA, dirs=(), chrome=False, extra_tools=(), full=False, tools=None, timeout=None, cwd=None, allow=None, read_only=False, guard=None):
        system += LINK_ATTRIBUTION_RULES + self.lead_magnets.agent_context()
        kit_receipts = []
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
            from .kit_drafts import KIT_WRITES
            # Kit writes are never available to a chat, whatever other permissions this PC has. Only a guarded draft run
            # (kit_drafts.py) may make its one approved create or update, checked field by field by kit_draft_guard.py.
            allowed_write = (guard or {}).get('write', {}).get('tool')
            # Gmail writes likewise. Only one guarded run for an action Josh approved exactly (studio/replies.py) may make one.
            from .frameio_review import ALL_WRITES as FRAMEIO_WRITES
            # Frame.io writes too: only a guarded Frame.io run (studio/frameio_review.py) can make one approved upload or stack.
            cmd += ['--disallowedTools', ','.join(t for t in KIT_WRITES + gmail_access.ALL_WRITES + FRAMEIO_WRITES if t != allowed_write)]
            if guard and guard.get('kind') == 'gmail':
                policy = Path(tmp) / 'gmail-action.json'
                policy.write_text(json.dumps(guard), encoding='utf-8')
                hook = subprocess.list2cmdline([Path(sys.executable).as_posix(), Path(__file__).with_name('gmail_action_guard.py').resolve().as_posix(), policy.as_posix()])
                cmd[cmd.index('--tools') + 1] = ALLOWED_TOOLS + ',ToolSearch'  # the guard still denies every one but the approved call
                cmd += ['--permission-mode', 'dontAsk', '--settings', json.dumps({**gmail_access.session_restrictions(), 'hooks': {
                    'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook}]}]}})]
            elif guard:
                policy = Path(tmp) / 'kit-draft.json'
                runtime_policy = dict(guard, reads=list(guard.get('reads') or []) + ALLOWED_TOOLS.split(',') + ['ToolSearch', 'StructuredOutput'])
                if guard.get('kind') == 'kit_draft' and guard.get('audit_id'):
                    import uuid
                    from .kit_draft_guard import audit
                    audit_id = str(uuid.UUID(guard['audit_id']))
                    audit_dir = self.root / 'Data' / 'kit-draft-audit'
                    audit_dir.mkdir(parents=True, exist_ok=True)
                    runtime_policy.update(audit_path=str(audit_dir / (audit_id + '.jsonl')), run_id=str(uuid.uuid4()))
                    audit(runtime_policy, dict(event='RunStarted', write=guard['write'], authorization=guard.get('authorization')))
                policy.write_text(json.dumps(runtime_policy), encoding='utf-8')
                hook = subprocess.list2cmdline([Path(sys.executable).as_posix(), Path(__file__).with_name('kit_draft_guard.py').resolve().as_posix(), policy.as_posix()])
                cmd[cmd.index('--tools') + 1] = ALLOWED_TOOLS + ',ToolSearch'
                cmd += ['--permission-mode', 'dontAsk', '--settings', json.dumps({'hooks': {
                    'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook}]}],
                    'PostToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook}]}]}})]
            elif read_only:
                # Exact-name hook prevents inherited broad permissions or newly discovered MCP
                # tools from turning this read-only request into an external write.
                policy = Path(tmp) / 'read-tools.json'
                policy.write_text(json.dumps(list(dict.fromkeys(
                    ALLOWED_TOOLS.split(',') + ['ToolSearch', 'StructuredOutput'] + [name for name in extra_tools
                    if name in CAREEROS_READ or name in gmail_access.READ_TOOLS]))), encoding='utf-8')
                read_guard_path = Path(__file__).with_name('read_tools_guard.py').resolve()
                hook = subprocess.list2cmdline([Path(sys.executable).as_posix(), read_guard_path.as_posix(), policy.as_posix()])
                cmd[cmd.index('--tools') + 1] = ALLOWED_TOOLS + ',ToolSearch'
                cmd += ['--permission-mode', 'dontAsk', '--settings', json.dumps({**gmail_access.session_restrictions(), 'hooks': {
                    'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook}]}]}})]
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
                # A one-shot run doesn't wait for claude.ai connectors (Kit, Calendly and others) before its first turn, so
                # they were often still "pending" and their tools missing. Wait up to 30 seconds for them, as the Kit and
                # CareerOS reads do (studio/counters.py). Changes no settings or permissions.
                env = dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS=MCP_STARTUP_WAIT_MS) if any(
                    str(t).startswith('mcp__') for t in extra_tools) else None
                out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding='utf-8',
                                     cwd=str(cwd or self.root), timeout=timeout or (TIMEOUT * 2 if chrome else TIMEOUT),
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), env=env)
            except subprocess.TimeoutExpired:
                raise AgentError('That took too long, so I stopped. Try a smaller selection.') from None
            if guard and guard.get('kind') == 'kit_draft':
                receipts_file = policy.with_suffix('.receipts.json')
                if receipts_file.exists():
                    kit_receipts = json.loads(receipts_file.read_text(encoding='utf-8'))
                if runtime_policy.get('audit_path'):
                    audit(runtime_policy, dict(event='RunCompleted', reserved=policy.with_suffix('.used').exists(),
                                               returncode=out.returncode))
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
        if guard and guard.get('kind') == 'kit_draft':
            answer['_kit_receipts'] = kit_receipts
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

    def create_projects(self, entries, operation_id=None):
        """Create cards atomically, without renaming existing work or invoking any remote action."""
        operation_id = operation_id or str(uuid.uuid4())
        done = []
        for index, item in enumerate((entries or [])[:20]):
            pid, title = '', ''
            try:
                if not isinstance(item, dict) or set(item) - {'name', 'notes', 'script', 'publish_date', 'add_titles'}:
                    raise ValueError('Invalid new video fields. Existing card IDs cannot be used for creation.')
                title = item.get('name')
                if not isinstance(title, str) or not title.strip():
                    raise ValueError('Give the new video a name.')
                title = title.strip()
                initial = {k: item[k] for k in ('notes', 'script', 'publish_date') if k in item}
                titles = item.get('add_titles', [])
                if not isinstance(titles, list) or any(not isinstance(t, str) or not t.strip() for t in titles):
                    raise ValueError('Title ideas must be nonempty text.')
                initial['titles'] = [dict(id=str(uuid.uuid4()), text=t.strip()) for t in dict.fromkeys(titles)]
                # Stable within one delivered Discord/request ID. A replay returns the original card, never overwrites it.
                pid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'joshos:chat-card:' + str(operation_id) + ':' + str(index)))
                p = self.store.create(title, pid=pid, initial=initial)
                done.append(dict(id=p['id'], title=p['name'], action='added', kind='project'))
            except (ValueError, TypeError, KeyError) as e:
                done.append(dict(id=pid, title=title or '', action='failed', kind='project', error=str(e).strip("'\"")[:200]))
        return done

    def apply_projects(self, updates, expected_revisions=None, by='team'):
        """Video card changes a team member made from chat."""
        done = []
        for u in (updates or [])[:20]:
            if not isinstance(u, dict):
                continue
            pid, title = str(u.get('project_id') or ''), ''
            try:
                p = self.store.get(pid)
                title = p['name']
                if expected_revisions is not None and expected_revisions.get(pid) != p['revision']:
                    raise ValueError('This video changed while I was working, or was not in my snapshot. Nothing was overwritten; ask again using its current card.')
                changes = {k: u[k] for k in ('name', 'stage', 'publish_date', 'notes', 'script', 'chosen_title') if isinstance(u.get(k), str)}
                for key in ('archived', 'top_three'):
                    if key in u:
                        changes[key] = u[key]
                for key in ('packaging', 'lead_magnet'):
                    if key in u:
                        if not isinstance(u[key], dict):
                            raise ValueError(key + ' must be an object.')
                        changes[key] = dict(p.get(key) or {}, **u[key])
                if 'remove_title_ids' in u:
                    removed = u['remove_title_ids']
                    if not isinstance(removed, list) or set(removed) - {t['id'] for t in p['titles']}:
                        raise ValueError('Use exact existing title IDs to remove titles.')
                    changes['titles'] = [t for t in p['titles'] if t['id'] not in removed]
                    if p.get('chosen_title') in removed:
                        changes.setdefault('chosen_title', '')
                if 'remove_thumbnail_assets' in u:
                    removed = u['remove_thumbnail_assets']
                    if not isinstance(removed, list) or set(removed) - {t['asset'] for t in p['thumbnails']}:
                        raise ValueError('Use exact existing thumbnail asset IDs.')
                    changes['thumbnails'] = [t for t in p['thumbnails'] if t['asset'] not in removed]
                    changes.setdefault('top_three', [a for a in p['top_three'] if a not in removed])
                # The description and pinned comment live on the card (release.description and release.cta), which is what
                # the video page shows and what Publish sends. A changed text loses its approval, so Josh approves it again.
                texts = {'description': ('description', 5000), 'pinned_comment': ('cta', 1000)}
                if any(isinstance(u.get(k), str) for k in texts):
                    release = dict(p.get('release') or {})
                    what = []
                    for key, (field, limit) in texts.items():
                        text = u.get(key)
                        if not isinstance(text, str):
                            continue
                        text = text.strip()
                        if len(text) > limit:
                            raise ValueError(f'The {key.replace("_", " ")} is longer than {limit} characters.')
                        old = dict(release.get(field) or {})
                        if text == old.get('text', ''):
                            continue
                        old.pop('approved', None)
                        release[field] = dict(old, text=text, by=str(by or 'team'), edited_at=now())
                        what.append(field)
                    if what:
                        hist = list(release.get('history') or [])[-40:]
                        hist.append(dict(at=now(), what=', '.join(what)))
                        release['history'] = hist
                        changes['release'] = release
                ideas = [t.strip() for t in (u.get('add_titles') or []) if isinstance(t, str) and t.strip()]
                if ideas:
                    current_titles = changes.get('titles', p['titles'])
                    have = {t['text'] for t in current_titles}
                    changes['titles'] = current_titles + [dict(id=str(uuid.uuid4()), text=t) for t in dict.fromkeys(ideas) if t not in have]
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
            body = keep(e, ('id', 'revision', 'subject', 'preview', 'body', 'planned_date', 'send_time', 'status', 'type', 'notes', 'links', 'images'))
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
        from .conversation_context import origin, recent
        chat_origin = origin(data)
        sel = None if proactive else self.selection(data)
        if not proactive and (not (message or images or files) or len(message) > 4000):
            raise ValueError('Write a message, up to 4,000 characters.')
        agent = next((a for a in self.team.all()['agents'] if a['id'] == agent_id), None)
        if not agent:
            raise KeyError('Team member not found.')
        # Josh approving one exact email action with its code ("send K7Q2PX") is handled by Josh OS itself, never the model.
        approvals = getattr(self, 'replies', None) if agent_id == 'anouk' and not proactive else None
        natural_note = None
        if approvals is not None and message:
            said = approvals.act_on_text(message, self.run, via=via or 'joshos')
            import re as _re
            yc = getattr(self, 'youtube_comments', None)
            pc = _re.match(r'\s*post\s+([A-Za-z0-9]{6})\b', message or '')
            if said is None and yc is not None and pc:
                # Josh approving one exact YouTube reply with its code ("post K7Q2PX"). Handled here, never by the model.
                hit = next((c for c in yc.items().values() if c.get('state') == 'needs_josh' and c.get('code') == pc.group(1).upper()), None)
                if hit is None:
                    said = "That code isn't for a YouTube reply that's waiting, so nothing was posted."
                else:
                    try:
                        out = yc.approve(hit['id'], pc.group(1), by='Josh')
                        said = (f"Posted your reply to {hit['author']} on YouTube." if out['state'] == 'replied' else
                                f"I couldn't tell if the reply posted: {out.get('reason', '')} I won't send it again on my own.")
                    except ValueError as e:
                        said = str(e)
            if said is None and approvals.is_natural(message):
                # "Send that" in Josh's own words. It answers only the message he replied to, or the newest Anouk message in
                # this same conversation, never the shared desk thread (studio/approval_lineage.py). Fails closed.
                last = next((m for m in reversed(self.thread(DESK, agent_id)) if m.get('role') == 'agent'), None)
                said = approvals.natural_from_chat(agent_id, message, data.get('discord') if via == 'discord' else None, via,
                                                   self.run, desk_last=(last or {}).get('id', ''))
                if said is not None and '\n'.join(message.split('\n')[1:]).strip():
                    # The approval was handled; the rest of his message still goes to Anouk, told what happened.
                    natural_note, said = said, None
            if said is not None:
                mine = dict(id=str(uuid.uuid4()), role='josh', text=message, at=now(), **chat_origin, **({'via': via} if via else {}))
                theirs = dict(id=str(uuid.uuid4()), role='agent', text=said, at=now(), **chat_origin, **({'via': via} if via else {}))
                return dict(messages=self._append(DESK, agent_id, mine, theirs), reply=theirs)
        feedback = getattr(self, 'feedback', None)
        edits = feedback.pending(agent_id) if feedback else []
        if proactive and not edits:
            return dict(messages=self.thread(DESK, agent_id), reply=None)
        lock = self.locks.setdefault((agent_id, DESK), threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError(f"{agent['name']} is still working on your last message.")
        try:
            history = recent(self.store, agent_id, self.thread(DESK, agent_id), data.get('discord'))
            learnings = [l for l in self.team.all()['learnings'] if l['agent_id'] in ('', agent_id)][:80]
            _, dirs = self.training(agent_id)
            project_revisions = {p['id']: p['revision'] for p in self.store.projects()}
            carousel_revisions = {c['id']: c['revision'] for c in carousels}
            keeps = tracker is not None and 'carousels' in (agent.get('workflows') or [])
            parts = [self.desk_snapshot(agent_id, carousels, inspo if keeps else None)]
            # Max reads the business brief, its dated sources and the shared to-do list (studio/business.py).
            business = getattr(self, 'business', None) if agent_id == 'max' else None
            if business is not None:
                from .business import RULES
                parts += ['', business.context(), '', *RULES]
                ov = getattr(self, 'oversight', None)
                text = ov.context() if ov is not None else ''
                if text:
                    parts += ['', text, '', 'Nudges go through the team member who owns the work, under the team message rules. You never '
                              'approve, draft, send or publish anything, and never say a nudge reached Josh unless its state above is sent.']
            # Anouk's proposed email replies (studio/replies.py), kept in Josh OS only.
            replies = getattr(self, 'replies', None) if agent_id == 'anouk' else None
            if replies is not None:
                parts += ['', replies.context()]
            lessons = self.lesson_snapshot(agent)
            if lessons:
                parts += ['', lessons]
            if history:
                parts += ['', 'Earlier with this same team member across Josh OS and Discord (dated context only, not action approval):',
                          'Continue the current topic, including failed requests and proactive messages. If a short follow-up could refer to two different topics, ask rather than choose an unrelated old task. Never infer permission to draft, send, retry, or publish from this history; use the exact action approval controls.']
                for m in history:
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
                if natural_note:
                    parts += ['', 'Josh OS already acted on the first line of his message: ' + natural_note + ' Do not repeat it. Answer the rest.']
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
            local_asset_chat = getattr(self, 'local_asset_chat', None)
            if local_asset_chat is not None:
                local_asset_schema = local_asset_chat.schema(agent_id, message, proactive)
                if local_asset_schema is not None:
                    schema['properties']['create_local_assets'] = local_asset_schema
                    parts += ['', *local_asset_chat.context(agent_id, history)]
            from .original_carousels import OriginalCarousels, SCHEMA as ORIGINAL_SCHEMA, requested as original_requested
            original_allowed = original_requested(agent_id, message, proactive)
            if original_allowed and local_asset_chat is not None:
                schema['properties']['original_carousel'] = ORIGINAL_SCHEMA
                schema['properties'].pop('carousels', None)
                schema['properties'].pop('create_local_assets', None)
                parts += ['', 'Use original_carousel for a NEW original visual carousel. Author each ordered panel as bounded structured scene layers; all slides use equal dimensions. No HTML, SVG, scripts, paths or URLs. Draft only; wait for the committed host save receipt before claiming completion.']
            from .photo_carousels import PhotoDrafts, SCHEMA as PHOTO_SCHEMA, requested as photo_requested
            photo_allowed = not original_allowed and photo_requested(agent_id, message, proactive)
            if photo_allowed:
                schema['properties']['photo_draft'] = PHOTO_SCHEMA
                schema['properties'].pop('carousels', None)
                parts += ['', 'Use photo_draft for a NEW full Photo BG1/BG2 carousel. Pick cover-1, cover-2 or cover-3 from existing approved Josh photos. Draft only: plain headline/body slides. For a promotional ending supply only exact post_id and offer_id in promotion; host must obtain authenticated canonical offer and placement links. Never supply or invent URLs. Missing adapter must block, not silently omit QR. Do not claim success before host receipt.']
            carousel_override = carousel_override_scope = None
            carousel_override_note = ''
            carousel_override_requested = False
            if keeps and local_asset_chat is not None:
                from .carousel_overrides import CarouselOverrides, SCHEMA as OVERRIDE_SCHEMA, wants_override
                carousel_override_requested = wants_override(agent_id, message, proactive)
                carousel_override = CarouselOverrides(tracker, local_asset_chat)
                try:
                    carousel_override_scope = carousel_override.prepare(owner=agent_id, message=message,
                        context=data.get('context'), items=carousels, images=images, proactive=proactive)
                    if carousel_override_scope is not None:
                        schema['properties']['carousel_slide_override'] = json.loads(json.dumps(OVERRIDE_SCHEMA))
                        parts += ['', carousel_override.context(carousel_override_scope)]
                except (ValueError, KeyError, OSError) as exc:
                    carousel_override_note = 'Carousel slide override unavailable: ' + str(exc)[:240]
                    parts += ['', carousel_override_note]
                if carousel_override_requested:
                    # This turn may replace one slide, not regenerate/edit the full carousel via the legacy action.
                    schema['properties'].pop('carousels', None)
            if agent_id not in CAN_CREATE_PROJECT or proactive:
                schema['properties'].pop('new_projects', None)
            if agent_id not in CAN_CREATE_PROJECT:
                schema['properties'].pop('projects', None)
            publishing=getattr(self,'publishing',None) if agent_id=='sam' else None
            updates=getattr(self,'video_updates',None) if agent_id=='sam' else None
            if updates is not None:
                schema['properties']['prepare_video_update']={'type':['object','null'],'description':"Only to prepare a change to a video already on Josh's channel, for Josh to review: {\"video_id\": the 11 character id, \"changes\": any of title, description, thumbnail (a JPEG or PNG path), privacy (private, unlisted or public), publish_at (ISO time with its offset, private videos only), \"note\": why}. Changes nothing on YouTube. Leave null otherwise."}
                schema['properties']['check_video_update']={'type':['string','null'],'description':'Only after you made an approved change in YouTube Studio: that change id, so Josh OS reads the video and confirms it. Leave null otherwise.'}
                try:
                    us=updates.status()
                    how=('Josh OS can make approved changes itself' if us['can_update'] else
                         'the edit permission is not connected, so an approved change is made by you in YouTube Studio in Chrome and then checked with check_video_update' if us['read_connected'] else
                         'Josh OS is not connected to YouTube, so no video change can be prepared yet')
                    waiting=[f"{u['id']} on {u['video_id']} ({u['state']})" for u in us['updates'] if u['state'] not in ('applied',)][:5]
                    parts+=['',f"Changes to existing YouTube videos on {us['expected_channel']}, right now: {how}."+(' Open changes: '+'; '.join(waiting)+'.' if waiting else '')]
                except Exception:  # noqa: BLE001 - status is a note, never a reason to fail the reply
                    pass
            if publishing is not None:
                # Built from the video as CareerOS has it (studio/project_publishing.py prepare_for_sam), never from values
                # Sam types, so the packet is exactly what the video page and its Publish panel show (19 Nov 2026).
                schema['properties']['prepare_release']={'type':'object','additionalProperties':False,
                    'required':['project_id','mode','made_for_kids','contains_paid_promotion','contains_synthetic_media'],
                    'properties':{'project_id':{'type':'string','description':'The exact video id.'},
                        'mode':{'type':'string','enum':['unlisted','schedule'],'description':'unlisted needs no date or time. schedule uses the date and time saved on the video.'},
                        'made_for_kids':{'type':'boolean'},'contains_paid_promotion':{'type':'boolean'},'contains_synthetic_media':{'type':'boolean'}},
                    'description':'Prepare the release packet for one video from what CareerOS shows now: its chosen title, description, '
                                  'pinned comment, thumbnails in Main, B and C and approved export. Never approves or uploads. '
                                  'Give the three disclosure answers only as Josh stated them; if he has not, leave this out and ask him, '
                                  'or point him to the Publish panel on the video.'}
            if via:
                schema['properties'].update(SEND_FILES_PROPS)
            thumbs = getattr(self, 'video_thumbs', None) if agent_id in CAN_THUMB else None
            if thumbs is not None:
                schema['properties'].update(THUMB_PROPS)
            drafts = getattr(self, 'kit_drafts', None) if agent_id == 'laia' and not proactive else None
            visual_dispatch = getattr(self, 'kit_visual_dispatch', None) if agent_id == 'laia' and not proactive else None
            if visual_dispatch is not None:
                from .kit_visual_dispatch import PROPS as KIT_VISUAL_JOB_PROPS
                schema['properties'].update(KIT_VISUAL_JOB_PROPS)
                parts += ['', 'Existing exact image jobs the owner allowed Laia to run once: '+json.dumps(visual_dispatch.context())]
            if drafts is not None and getattr(self, 'emails', None) is not None and 'email' in (agent.get('workflows') or []):
                schema['properties']['kit_draft'] = {'type': ['object', 'null'], 'description': (
                    'To prepare a Kit draft broadcast for an email in the planner: email_id and audience, where audience is one '
                    'group, all, any or none, of Kit tags or segments, like {"all": [{"type": "segment", "ids": [123]}]}. Josh OS '
                    'builds the draft from the saved email and Josh approves it before anything goes to Kit. You can never set a '
                    'date, schedule, send or publish. This only prepares a local proposal; approval does not write it. '
                    'Never claim it succeeded before the application validates it, or promise automatic creation. Leave null otherwise.'),
                    'properties': {'email_id': {'type': 'string'}, 'audience': {'type': 'object'}}}
            # Tariq sends his shot shortlist to Discord only when Josh asks for it in this message.
            shortlist = getattr(self, 'send_shortlist', None) if agent_id == 'tariq' and not proactive else None
            if shortlist is not None:
                schema['properties']['send_shortlist'] = {'type': ['string', 'null'], 'description': (
                    "Only when Josh's message asks you to send him the shot shortlist (the finished shots with the script) on "
                    "Discord: that video's project id. Josh OS sends it from your own Discord bot and tells you what happened. "
                    'Leave it null otherwise, and never send it on your own.')}
            mails = getattr(self, 'emails', None) if 'email' in (agent.get('workflows') or []) else None
            if mails is not None:
                schema['properties'].update(EMAIL_PROPS)
                if agent_id != 'laia' or proactive:
                    schema['properties'].pop('email_visuals', None)
                if agent_id != 'laia':
                    schema['properties'].pop('emails', None)
                    schema['properties'].pop('library', None)
            if business is not None and not proactive:
                from .business import TODO_PROPS
                schema['properties'].update(TODO_PROPS)
            results = getattr(self, 'results', None) if 'youtube' in (agent.get('workflows') or []) else None
            if results is not None:
                schema['properties'].update(VIDEO_PROPS)
            people = getattr(self, 'people', None) if 'customers' in (agent.get('workflows') or []) else None
            if people is not None:
                schema['properties'].update(PEOPLE_PROPS)
            if replies is not None and not proactive:
                from .replies import REPLY_PROPS, NEW_REPLY_PROPS
                schema['properties'].update(REPLY_PROPS)
                schema['properties'].update(NEW_REPLY_PROPS)
            ideas = getattr(self, 'youtube_ideas', None) if agent_id == 'sam' else None
            if ideas is not None:
                schema['properties'].update(YOUTUBE_SAVE_PROPS)
            # Saskia delivers a standalone guide Josh approved to the CareerOS Downloads folder (studio/guide_delivery.py).
            delivery = getattr(self, 'guide_delivery', None) if agent_id == 'saskia' and not proactive else None
            if delivery is not None:
                from .guide_delivery import DELIVER_PROPS
                schema['properties'].update(DELIVER_PROPS)
            # Read-only connected-source refreshes; no model-generated approvals or external writes.
            learner_reads = getattr(self, 'learners', None) if agent_id == 'anouk' and not proactive else None
            score_reads = getattr(self, 'scorecard', None) if agent_id == 'max' and not proactive else None
            if learner_reads is not None:
                schema['properties']['lookup_learner'] = {'type': 'object', 'required': ['email'], 'additionalProperties': False,
                    'properties': {'email': {'type': 'string'}, 'force': {'type': 'boolean'}},
                    'description': 'Read learner progress by email when Josh asks. force refreshes an old cached result. Never creates accounts or messages learners; running is not complete.'}
                parts += ['', 'Learner lookup status/cache (read only): ' + json.dumps(learner_reads.all(), ensure_ascii=False)[:20000]]
            if score_reads is not None:
                schema['properties']['refresh_scorecard'] = {'type': 'boolean', 'description': 'Only when Josh asks to refresh business numbers. Starts the existing read-only refresh; never claim finished until source status confirms it.'}
            # Sam gets a video ready to film when Josh says he's ready to record it (studio/filming.py, shared with Film).
            filming = getattr(self, 'filming', None) if agent_id == 'sam' and not proactive else None
            if filming is not None:
                from .filming import FILM_PROPS
                schema['properties'].update(FILM_PROPS)
            # Sam says a video is public; Josh OS checks the public feed itself and tells Tariq (studio/archive.py).
            # Anouk switches her YouTube comment replies on or off, or says how they stand (studio/youtube_comments.py).
            sam_comments = getattr(self, 'youtube_comments', None) if agent_id == 'sam' and not proactive else None
            if sam_comments is not None:
                schema['properties']['post_release_comment'] = {'type': 'string', 'description':
                    'When Josh asks to post the approved lead-magnet comment, give the exact publishing job ID. '
                    'Only an approved release confirmed public is eligible. Never invent IDs or comment copy. '
                    'This posts/reconciles the comment only; desktop pinning is not connected.'}
                if publishing is not None:
                    parts += ['', 'Publishing jobs for exact comment targeting: ' + json.dumps([
                        {'job_id': j['id'], 'video_id': j.get('video_id'), 'title': (j.get('packet') or {}).get('title'),
                         'state': j.get('state')} for j in publishing.status().get('jobs', [])], ensure_ascii=False)]
            ycom = getattr(self, 'youtube_comments', None) if agent_id == 'anouk' and not proactive else None
            if ycom is not None:
                schema['properties']['youtube_comments'] = {'type': 'string', 'enum': ['on', 'off', 'status'], 'description': (
                    'Only when Josh asks in this message to switch your YouTube comment replies on or off, or asks how they stand. '
                    'Josh OS does it and tells you the result. On only counts comments posted from that moment.')}
                parts += ['', ycom.status_line()]
            archive = getattr(self, 'archive', None) if agent_id == 'sam' and not proactive else None
            if archive is not None:
                from .archive import PUBLIC_PROPS
                schema['properties'].update(PUBLIC_PROPS)
            # Workflow membership supplies context, not write authority. Anouk reads learner/course context only.
            courses = getattr(self, 'courses', None) if agent_id in CAN_EDIT_COURSE and 'courses' in (agent.get('workflows') or []) else None
            course_contexts = {}
            if courses is not None:
                from .chat_course_actions import COURSE_ACTION_SCHEMA, course_action_context
                schema['properties']['course_action'] = COURSE_ACTION_SCHEMA
                for lesson in courses.all()['items']:
                    try:
                        course_contexts[lesson['id']] = dict(course_action_context(courses, lesson['id']), title=lesson['title'])
                    except (KeyError, ValueError, OSError):
                        continue
                parts += ['', 'Current exact course-action versions (model proposals cannot approve anything): ' + json.dumps(course_contexts, ensure_ascii=False)]
                schema['properties'].update(LESSON_PROPS)
                schema['properties'].update(GUIDE_PROPS)
                if str(self.store.root / 'assets') not in dirs:
                    dirs = list(dirs) + [str(self.store.root / 'assets')]
            ctx = data.get('context') or {}
            local_release = getattr(self, 'chat_release_actions', None) if agent_id == 'sam' and not proactive else None
            local_release_project = None
            if local_release is not None:
                from .chat_release_actions import SCHEMA as RELEASE_ACTION_SCHEMA
                schema['properties']['local_release_action'] = RELEASE_ACTION_SCHEMA
                candidates = [p for p in self.store.projects() if p['id'] in message or (p['name'] and p['name'].casefold() in message.casefold())]
                if len(candidates) == 1:
                    local_release_project = candidates[0]['id']
                elif not candidates and ctx.get('kind') in ('project','edit') and ctx.get('id') in project_revisions:
                    local_release_project = ctx['id']
                if local_release_project:
                    parts += ['', 'Local release drafting context (not approval): ' + json.dumps(local_release.context(local_release_project))]
                    try:
                        from .project_publishing import summary as publish_summary
                        parts += ['', 'publishing: what CareerOS shows for this video right now, and exactly what Publish and '
                                  'prepare_release will use. There is no other version. '
                                  + json.dumps(publish_summary(self.store, local_release_project), ensure_ascii=False)]
                    except Exception:  # noqa: BLE001 - a note for Sam, never a reason to fail the reply
                        pass
                else:
                    parts += ['', 'For local release drafting, ask which exact video title. No unique target is established.']
            local_edit = getattr(self, 'chat_edit_actions', None) if agent_id == 'tariq' and not proactive else None
            local_edit_project = None
            if local_edit is not None:
                from .chat_edit_actions import TOOL
                schema['properties']['chat_edit_action'] = TOOL['function']['parameters']
                candidates = [p for p in self.store.projects() if p['id'] in message or (p['name'] and p['name'].casefold() in message.casefold())]
                if len(candidates) == 1:
                    local_edit_project = candidates[0]['id']
                elif not candidates and ctx.get('kind') in ('project','edit') and ctx.get('id') in project_revisions:
                    local_edit_project = ctx['id']
                if local_edit_project:
                    try:
                        parts += ['', 'Current bounded editing-action context (revision required, not owner approval): ' + json.dumps(local_edit.context(local_edit_project), ensure_ascii=False)]
                    except (ValueError, KeyError, OSError) as e:
                        parts += ['', 'Bounded editing context unavailable: ' + str(e)[:300]]
                        local_edit_project = None
                else:
                    parts += ['', 'For bounded editing actions, ask Josh which exact video title he means. No unique current target was established. He does not need to open a particular page.']
            er = getattr(self, 'edit_review', None) if 'editing' in (agent.get('workflows') or []) else None
            edit_folder, before, run_tools = None, None, None
            if er is not None:
                schema['properties'].update(EDIT_PROPS)
            fio_ctx = getattr(self, 'frameio', None) if er is not None and ctx.get('id') else None
            if fio_ctx is not None:
                try:
                    text = fio_ctx.context(str(ctx['id']))
                    if text:
                        parts += ['', text]
                except Exception:  # noqa: BLE001 - review context is extra
                    pass
            arc_ctx = getattr(self, 'archive', None) if er is not None and ctx.get('id') else None
            if arc_ctx is not None and (arc_ctx.record(str(ctx['id'])).get('proof') or {}).get('first_verified_at'):
                text = arc_ctx.context(str(ctx['id']))
                if text:
                    parts += ['', text]
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
            # Only CareerOS learner-support reads were approved independently of full activation.
            outside += list(CAREEROS.get(agent_id, ()))
            outside += list(gmail_access.tools_for(agent_id, self.root))
            try:
                reply, answer = self.run(self.desk_system_prompt(agent, learnings), '\n'.join(parts),
                                         schema, dirs, chrome=agent_id in CHROME and not proactive and live,
                                         extra_tools=outside, full=True, read_only=agent_id == 'anouk',
                                         tools=run_tools, cwd=edit_folder if run_tools else None,
                                         allow=scoped(run_tools, edit_folder) if run_tools else None)
            except Exception as exc:
                # A failed read still happened in the conversation. Without this, "try again"
                # resumes the last successful (possibly days-old) email topic instead.
                # This is context only: no offer, send permission, or replay of a write.
                if agent_id == 'anouk' and not proactive:
                    from .approval_lineage import conversation
                    origin = conversation(data.get('discord')) if via == 'discord' else 'joshos:desk'
                    meta = dict(failed_read=True, conversation=origin or 'discord:unknown')
                    if via:
                        meta['via'] = via
                    mine = dict(id=str(uuid.uuid4()), role='josh', text=message, at=now(), **meta)
                    if images:
                        mine['images'] = images
                    if files:
                        mine['files'] = [Path(f).name for f in files]
                    if sel:
                        mine['selection'] = sel
                    failed = dict(id=str(uuid.uuid4()), role='agent', at=now(), **meta,
                                  text="I couldn't do that: " + str(exc).strip(chr(39) + chr(34))[:300])
                    self._append(DESK, agent_id, mine, failed)
                raise
            answer = answer if isinstance(answer, dict) else {}
            # Showing an email is read only for the whole answer: nothing else in it is saved, sent, prepared or started,
            # including a new draft rebuilt from the chat. Only the reply and the email to show are kept.
            answer, held_writes = show_only(answer) if mails is not None else (answer, [])
            if visual_dispatch is not None and isinstance(answer.get('kit_visual_job'),dict):
                try:
                    if any(value for key,value in answer.items() if key not in ('reply','kit_visual_job')):
                        raise ValueError('Save and review other changes separately before running an approved image job.')
                    outcome=visual_dispatch.dispatch(answer['kit_visual_job'],role=agent_id,message=message,
                        context=data.get('context'),proactive=proactive,
                        request_id=chat_origin.get('request_message_id') or data.get('request_id') or '')
                    reply=outcome['note']
                except (ValueError,KeyError,OSError,PermissionError) as exc:
                    reply='The Kit image job was not completed: '+str(exc)[:500]
                answer={'reply':reply}
            release_notice=''
            if local_release is not None and isinstance(answer.get('local_release_action'),dict):
                try:
                    local_release_result=local_release.dispatch(agent_id,answer['local_release_action'],project_id=local_release_project,message=message)
                    release_notice=local_release_result['note']
                except (ValueError,KeyError,OSError) as exc:
                    release_notice='Local release action not completed: '+str(exc)[:300]

            if publishing is not None and isinstance(answer.get('prepare_release'),dict):
                try:
                    w=answer['prepare_release']
                    rel=getattr(getattr(self,'chat_release_actions',None),'release',None)
                    if rel is None:raise ValueError('The release check is not available, so nothing was prepared.')
                    if not isinstance(w.get('project_id'),str) or w['project_id']!=local_release_project:
                        raise ValueError('Name the exact video, or open it, so the packet matches what CareerOS shows.')
                    from .project_publishing import prepare_for_sam
                    release=prepare_for_sam(self.store,rel,publishing,w['project_id'],w.get('mode'),w)
                    if release.get('video_id') or release.get('state')!='needs_review':
                        release_notice='Existing release found with state '+str(release.get('state'))+'. Open /publishing#'+release['id']+' in Josh OS to inspect it. Do not create another upload.'
                    else:
                        release_notice='Release prepared for your review. Open /publishing#'+release['id']+' in Josh OS to review and approve upload and scheduling. Nothing has been uploaded yet.'
                except (ValueError,KeyError,OSError) as exc:
                    release_notice='Release preparation needs attention: '+str(exc)[:300]
            if updates is not None and isinstance(answer.get('prepare_video_update'),dict):
                w=answer['prepare_video_update']
                try:
                    job=updates.prepare(w.get('video_id'),w.get('changes'),by='sam',note=w.get('note',''))
                    msg=('Change prepared for Josh to review at /publishing-updates#'+job['id']+'. Nothing has changed on YouTube.') if job['state']=='needs_review' \
                        else ('That change was already prepared, and it is '+job['state']+'. Open /publishing-updates#'+job['id']+' to see it.')
                except (ValueError,KeyError,OSError) as exc:
                    msg='The video change needs attention: '+str(exc)[:300]
                release_notice=(release_notice+'\n\n' if release_notice else '')+msg
            if updates is not None and isinstance(answer.get('check_video_update'),str) and answer['check_video_update']:
                try:
                    job=updates.verify_chrome(answer['check_video_update'],by='sam')
                    msg='Josh OS read the video and the approved change is live.' if job['state']=='applied' \
                        else 'Josh OS read the video and it does not show the approved change yet: '+str(job.get('error') or '')[:300]
                except (ValueError,KeyError,OSError) as exc:
                    msg='The video change could not be checked: '+str(exc)[:300]
                release_notice=(release_notice+'\n\n' if release_notice else '')+msg
            if release_notice:reply=(reply or '').rstrip()+'\n\n'+release_notice
            want = answer.get('kit_draft') if drafts is not None and mails is not None else None
            if isinstance(want, dict):
                try:
                    email = next(e for e in mails.all() if e['id'] == str(want.get('email_id', '')))
                    if answer.get('email_visuals'):
                        raise ValueError('Save and review local email visuals first. Kit proposals with local visuals are blocked.')
                    d = drafts.propose(email, want.get('audience'))
                    note = (f"The Kit draft for \"{email['subject']}\" is ready for your approval on its Email page. "
                            "This is a local proposal only. Approval and Save draft to Kit are separate steps. "
                            + ("After approval, use Save draft to Kit." if drafts.live() else
                               "Saving drafts to Kit is currently switched off; approval will not create it automatically.")
                            + " Nothing has been created, scheduled or sent in Kit.")
                except StopIteration:
                    note = "I couldn't find that email in the planner, so no Kit draft was prepared."
                except ValueError as e:
                    note = f"I couldn't prepare the Kit draft: {str(e)[:200]}"
                # The operation's verified outcome replaces speculative model success claims.
                reply = note
            ask = answer.get('send_shortlist') if shortlist is not None else None
            if isinstance(ask, str) and ask.strip():
                if asked_for_shortlist(message):
                    try:
                        outcome = shortlist(ask.strip())
                    except Exception as e:  # noqa: BLE001 - tell Josh rather than fail the whole reply
                        outcome = f"I couldn't send the shortlist: {str(e)[:200]}"
                else:
                    outcome = "I didn't send the shortlist, since you didn't ask me to send it."
                reply = (reply or '').rstrip() + '\n\n' + outcome
            saved = []
            local_asset_note = ''
            original_attempt = original_allowed or answer.get('original_carousel') is not None
            if not original_attempt and local_asset_chat is not None and answer.get('create_local_assets') is not None:
                local_asset_saved, local_asset_note = local_asset_chat.dispatch(
                    agent_id, answer['create_local_assets'], message=message, proactive=proactive)
                saved += local_asset_saved
            if local_edit is not None and isinstance(answer.get('chat_edit_action'), dict):
                try:
                    if not local_edit_project:
                        raise ValueError('Name the exact video for this editing action; no unique target was established.')
                    result = local_edit.dispatch(answer['chat_edit_action'], role=agent_id, project_id=local_edit_project, current_message=message)
                    saved.append(dict(id=local_edit_project, title='local edit action', action='updated', kind='edit'))
                    reply = (reply or '').rstrip() + '\n\nLocal edit action: ' + json.dumps(result, ensure_ascii=False, default=str)
                except (ValueError, KeyError, RuntimeError, OSError) as e:
                    reply = (reply or '').rstrip() + '\n\nLocal edit action not applied: ' + str(e)[:500]
            from .chat_local_requests import requested_read
            if learner_reads is not None and isinstance(answer.get('lookup_learner'), dict):
                try:
                    if not requested_read(message):
                        raise ValueError('Ask for a learner lookup or refresh in this message.')
                    lookup = learner_reads.lookup(answer['lookup_learner'])
                    reply = (reply or '').rstrip() + '\n\nLearner lookup: ' + json.dumps(lookup, ensure_ascii=False)
                except (ValueError, KeyError) as e:
                    reply = (reply or '').rstrip() + '\n\nLearner lookup was not started: ' + str(e)
            if score_reads is not None and answer.get('refresh_scorecard') is True:
                if requested_read(message):
                    outcome = score_reads.refresh()
                    reply = (reply or '').rstrip() + '\n\nScorecard refresh: ' + str(outcome.get('state', 'unknown')) + '. Existing figures keep their original source dates until refreshed.'
                else:
                    reply = (reply or '').rstrip() + '\n\nNo refresh started. Ask to refresh the scorecard in this message.'
            if original_attempt:
                from .carousels import save_receipt
                try:
                    row = OriginalCarousels(tracker, local_asset_chat).apply(answer.get('original_carousel'),
                        owner=agent_id, message=message, proactive=proactive,
                        request_id=data.get('request_id') or str(uuid.uuid4()))
                except (ValueError, KeyError, OSError, RuntimeError):
                    # Renderer/storage errors may contain local paths; never echo them.
                    row = dict(kind='carousel', action='failed', title='Original carousel',
                               error='The original carousel was not saved. Review the request before retrying.')
                saved.append(row)
                reply = save_receipt([row])
            photo_attempt = not original_attempt and (photo_allowed or answer.get('photo_draft') is not None)
            if photo_attempt:
                from .carousels import save_receipt
                try:
                    from .photo_distinct import DistinctPhotoReceipts
                    row = PhotoDrafts(tracker, DistinctPhotoReceipts(tracker,self.root)).apply(answer.get('photo_draft'),
                        owner=agent_id, message=message, proactive=proactive,
                        request_id=data.get('request_id') or str(uuid.uuid4()))
                except (ValueError, KeyError, OSError, RuntimeError) as exc:
                    row = dict(kind='carousel',action='failed',title='Photo carousel',error=str(exc)[:300])
                saved.append(row)
                # Only a committed host receipt can report a successful Photo draft.
                reply = save_receipt([row])
            todo = [] if photo_attempt or original_attempt else answer.get('carousels')
            if carousel_override_requested or answer.get('carousel_slide_override') is not None:
                todo = []
            if not photo_attempt and not original_attempt and answer.get('carousel_slide_override') is not None:
                try:
                    if carousel_override is None:
                        raise ValueError('Only Sam can replace a carousel slide on a current request.')
                    row = carousel_override.apply(answer['carousel_slide_override'], scope=carousel_override_scope,
                        owner=agent_id, message=message, proactive=proactive)
                    saved.append(row)
                    carousel_override_note = (f"Replaced slide {row['slide_number']} locally; every other slide image is unchanged. "
                        'The carousel is ready for review. Nothing was sent, uploaded or published.')
                except (ValueError, KeyError, OSError) as exc:
                    carousel_override_note = 'Carousel slide override failed: ' + str(exc)[:240]
                    saved.append(dict(kind='carousel', action='failed', title='Carousel slide override', error=str(exc)[:240]))
            if keeps and isinstance(todo, list) and not photo_attempt and not original_attempt:
                carousel_saved = tracker.apply(agent_id, todo, revisions=carousel_revisions,
                    request_id=(data.get('request_id') or str(uuid.uuid4())) if agent_id == 'sam' else None)
                saved += carousel_saved
                if todo:
                    from .carousels import save_receipt, date_clashes
                    # Model prose was written before the save. Replace it with
                    # committed host outcomes, including partial batch failures.
                    reply = save_receipt(carousel_saved) or 'No carousel changes were saved.'
                    try:
                        clashes = date_clashes(tracker.all()['items'], carousel_saved)
                    except Exception:  # noqa: BLE001 - the warning is extra; the save already happened
                        clashes = []
                    if clashes:
                        reply += '\n' + '\n'.join(clashes)
            found = answer.get('inspo')
            if keeps and inspo is not None and isinstance(found, list):
                saved += inspo.apply(found)
            if mails is not None:
                email_answer = dict(answer) if agent_id == 'laia' else {k:v for k,v in answer.items() if k not in ('emails','library','email_visuals')}
                if proactive:email_answer.pop('email_visuals', None)
                done, shown = self.email_answer(mails, email_answer)
                saved += done
                from .kit_sent import save_failure_reply
                reply = save_failure_reply(reply, done)
                if held_writes:
                    saved.append(dict(id=str(answer.get('show_email')), title='', action='failed', kind='email',
                                      error='Nothing saved. Showing an email changes nothing, so ' + ', '.join(held_writes) + ' was not applied. Ask again for a change.'))
                if shown:
                    reply = (reply or '').rstrip() + '\n\n' + shown
            if people is not None and isinstance(answer.get('people'), list):
                saved += people.apply(answer['people'])
            if replies is not None and not proactive and isinstance(answer.get('reply_proposals'), list):
                saved += replies.apply(answer['reply_proposals'])
            nr = answer.get('new_reply_proposal') if replies is not None and not proactive else None
            if isinstance(nr, dict) and nr.get('body') and nr.get('body', '').strip()[:40] in (reply or ''):
                try:
                    b = replies.create_from_chat(nr, source='anouk chat')
                    saved.append(dict(id=b['id'], title=f"reply to {b['from_name'] or b['from_email']}", action='added', kind='reply'))
                except ValueError as e:
                    saved.append(dict(id='', title='a proposed reply', action='failed', kind='reply', error=str(e)[:200]))
            if results is not None and isinstance(answer.get('videos'), list):
                saved += results.apply(answer['videos'])
            if courses is not None and isinstance(answer.get('course_action'), dict) and not proactive:
                from .chat_course_actions import dispatch_course_action, current_request_authorizes
                from .chat_local_requests import bind_target
                action = answer['course_action']
                try:
                    bound = bind_target(message, action.get('lesson_id'), course_contexts, data.get('context'), noun='lesson')
                    result = dispatch_course_action(courses, agent_id, action, authorize=lambda a: current_request_authorizes(a, bound))
                    saved.append(dict(id=action['lesson_id'], title='course action', action='updated', kind='lesson'))
                    reply = (reply or '').rstrip() + '\n\nCourse action: ' + json.dumps(result, ensure_ascii=False)
                except (ValueError, KeyError, RuntimeError) as e:
                    reply = (reply or '').rstrip() + '\n\nCourse action not applied: ' + str(e)[:500]
            if courses is not None and isinstance(answer.get('lessons'), list):
                saved += self.apply_lessons(courses, answer['lessons'])
            if isinstance(answer.get('new_projects'), list) and not proactive and agent_id in CAN_CREATE_PROJECT and requests_new_project(message):
                operation_id = ':'.join((agent_id, chat_origin.get('conversation', ''),
                                         chat_origin.get('request_message_id') or str(data.get('request_id') or uuid.uuid4())))
                created = self.create_projects(answer['new_projects'], operation_id)
                saved += created
                if created:
                    outcomes = [('Created video card: ' + c['title'] + ' (' + c['id'] + ').') if c['action'] == 'added'
                                else ('Could not create video card: ' + c.get('error', 'Unknown error')) for c in created]
                    reply = (reply or '').rstrip() + '\n\n' + '\n'.join(outcomes)
            if answer.get('new_projects') and (agent_id not in CAN_CREATE_PROJECT or proactive or not requests_new_project(message)):
                reply = (reply or '').rstrip() + '\n\nNo new video card was saved. Ask Sam or Tariq directly to create a new video card.'
            if isinstance(answer.get('projects'), list) and agent_id in CAN_CREATE_PROJECT and not proactive:
                if requests_new_project(message):
                    if answer['projects']:
                        reply = (reply or '').rstrip() + '\n\nExisting cards were left unchanged because you asked for a new card. Changes to an existing card need a separate request.'
                else:
                    saved += self.apply_projects(answer['projects'], project_revisions, by=agent_id)
            if business is not None and not proactive and isinstance(answer.get('todos'), list):
                done, changed = business.apply_todos(answer['todos'])
                saved += done
                if changed:
                    reply = (reply or '').rstrip() + '\n\nUpdated in your list:\n' + '\n'.join('- ' + c for c in changed)
            if thumbs is not None and not proactive and agent_id == 'sam' and isinstance(answer.get('thumbnails'), list):
                for t in answer['thumbnails'][:3]:
                    try:
                        saved.append(self.start_thumbnail_request(thumbs, t, message, images, via))
                    except (ValueError, KeyError) as e:
                        saved.append(dict(id=str((t if isinstance(t, dict) else {}).get('project_id') or ''), title='thumbnails', action='failed', kind='thumbnail',
                                          error=str(e).strip("'\"")[:200]))
                        reply = 'The thumbnail job did not start. ' + str(e).strip("'\"")[:600]
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
            want_guide = answer.get('deliver_guide') if delivery is not None else None
            if isinstance(want_guide, dict) and want_guide.get('path'):
                from .guide_delivery import APPROVE_WORDS
                if APPROVE_WORDS.search(message or ''):
                    try:
                        d = delivery.deliver(want_guide['path'], want_guide.get('title') or Path(str(want_guide['path'])).stem,
                                             dict(by='josh', said=message[:500], at=now(), via=via or 'joshos'))
                        outcome = delivery.describe(d)
                    except (ValueError, OSError) as e:
                        outcome = f"I didn't deliver it: {str(e)[:250]}"
                else:
                    outcome = "I didn't put it in Downloads, since you didn't say you approve it."
                reply = (reply or '').rstrip() + '\n\n' + outcome
            want_film = answer.get('prepare_filming') if filming is not None else None
            if isinstance(want_film, dict) and (want_film.get('project_id') or want_film.get('video_name')):
                from .filming import FILM_WORDS, describe
                if FILM_WORDS.search(message or ''):
                    try:
                        rec = filming.prepare(pid=str(want_film.get('project_id') or '') or None,
                                              query=None if want_film.get('project_id') else want_film.get('video_name'), via=via or 'joshos')
                        outcome = describe(rec)
                    except Exception as e:  # noqa: BLE001 - tell Josh rather than fail the whole reply
                        outcome = f"I couldn't get it ready: {str(e)[:200]}"
                else:
                    outcome = "I didn't set anything up for filming, since you didn't say you're ready to record."
                reply = (reply or '').rstrip() + '\n\n' + outcome
            if sam_comments is not None and answer.get('post_release_comment'):
                try:
                    import re as comment_re
                    if not comment_re.search(r'\b(post|add|pin)\b', message or '', comment_re.I) or not comment_re.search(r'\bcomment\b', message or '', comment_re.I):
                        raise ValueError('Ask to post the approved comment for this video.')
                    rec = sam_comments.post_approved_release_comment(answer['post_release_comment'])
                    outcome = ('The exact approved comment is on YouTube. Pinning is not yet connected.'
                               if rec.get('state') == 'posted' else
                               'Comment delivery is uncertain. I did not retry; inspect YouTube first.')
                except Exception as e:
                    outcome = "I couldn't post the approved comment: " + str(e)[:200]
                reply = (reply or '').rstrip() + '\n\n' + outcome
            want_yc = answer.get('youtube_comments') if ycom is not None else None
            if want_yc in ('on', 'off', 'status'):
                try:
                    if want_yc == 'on':
                        ycom.enable(by='Josh')
                    elif want_yc == 'off':
                        ycom.deactivate(by='Josh')
                    outcome = ycom.status_line()
                except ValueError as e:
                    outcome = str(e)
                reply = (reply or '').rstrip() + chr(10) * 2 + outcome
            want_pub = answer.get('youtube_public') if archive is not None else None
            if isinstance(want_pub, dict) and want_pub.get('project_id') and want_pub.get('video_id'):
                from .archive import describe_proof
                try:
                    outcome = describe_proof(archive.record_public(str(want_pub['project_id']), str(want_pub['video_id']), by='sam'))
                except Exception as e:  # noqa: BLE001 - tell Josh rather than fail the whole reply
                    outcome = f"I couldn't confirm it's public: {str(e)[:200]}"
                reply = (reply or '').rstrip() + '\n\n' + outcome
            if courses is not None and isinstance(answer.get('written_guide'), dict):
                saved += self.start_guide(agent_id, answer['written_guide'], via)
            action = answer.get('edit') if isinstance(answer.get('edit'), dict) else {}
            if er is not None and edit_folder is not None:
                saved += self.apply_edit(er, str(ctx['id']), action, before)
                saved += self.start_go_ahead(str(ctx['id']), self.store.get(str(ctx['id'])).get('name', ''), action, via)
                saved += self.start_finish(str(ctx['id']), self.store.get(str(ctx['id'])).get('name', ''), action, via,
                                           dict(text=message, at=now()) if message else None)
                saved += self.keep_anim_notes(str(ctx['id']), action)
                saved += self.start_deck_job(agent_id, str(ctx['id']), self.store.get(str(ctx['id'])).get('name', ''), action, via)
            elif er is not None and action.get('project_id'):
                saved += self.apply_edit_elsewhere(er, agent_id, str(action['project_id']), action, via,
                                                   dict(text=message, at=now()) if message else None)
            if action.get('finish') is True:
                failed_finish = next((x for x in reversed(saved) if x.get('action') == 'failed'
                                      and x.get('kind') == 'edit' and str(x.get('title', '')).startswith('putting ')), None)
                if failed_finish:
                    reply = 'The edit did not start. ' + str(failed_finish.get('error') or 'The finish request was blocked.')
            fio = getattr(self, 'frameio', None) if er is not None else None
            fio_pid = str(ctx.get('id') or '') if ctx.get('kind') in ('edit', 'project') else str(action.get('project_id') or '')
            if fio is not None and action.get('frameio') == 'send' and fio_pid:
                from .frameio_progress import send_from_chat
                receipt = send_from_chat(fio, fio_pid, dict(by='Josh', said=message[:500], via=via or 'joshos'))
                saved.append(receipt)
                # The model answered before staging. Only the host can confirm its outcome.
                reply = receipt['message']
            elif fio is not None and action.get('frameio') == 'feedback' and fio_pid:
                try:
                    if not message:
                        raise ValueError('It needs your own message asking for it.')
                    b = fio.import_feedback(fio.latest(fio_pid)['id'])
                    note = f"Read {len(b['feedback'])} comments from {b['export']['name']} on Frame.io. They're tied to that exact version."
                except (ValueError, KeyError, RuntimeError) as e:
                    note = f"Frame.io: {str(e).strip(chr(39))[:250]}"
                reply = (reply or '').rstrip() + '\n\n' + note
            replacement = answer.get('replacement') if sel and isinstance(answer.get('replacement'), str) else None
            if mails is not None and agent_id == 'laia' and isinstance(answer.get('library'), list):
                saved += mails.add_references(answer['library'][:40])
            if feedback and isinstance(answer.get('edits_noted'), list):
                known = {e['id'] for e in edits}
                feedback.mark([i for i in answer['edits_noted'] if i in known])
            # Rules learned come in "learned". Course lessons use "lessons", so only fall back to it for agents without courses.
            learned = self.keep_lessons(agent, answer.get('learned') if answer.get('learned') else (None if courses is not None else answer.get('lessons')))
        finally:
            lock.release()
        if natural_note:
            reply = natural_note + '\n\n' + (reply or '')
        # Keep explanations and other verified notices. Append after existing
        # email-failure sanitization so its safety notice and this receipt survive.
        if local_asset_note and not (reply or '').rstrip().endswith(local_asset_note):
            reply = (reply or '').rstrip() + ('\n\n' if reply else '') + local_asset_note
        if carousel_override_note:
            reply = (reply or '').rstrip() + ('\n\n' if reply else '') + carousel_override_note
        theirs = dict(id=str(uuid.uuid4()), role='agent', text=reply, at=now(), **chat_origin)
        offers_from = getattr(self, 'replies', None) if agent_id == 'anouk' else None
        if offers_from is not None:
            try:
                offered = offers_from.offers_for(reply)
            except Exception:  # noqa: BLE001 - an offer is extra
                offered = []
            if offered:
                theirs['offers'] = offered
            if via != 'discord':
                # Shown on the Josh OS desk. Replies sent on Discord are recorded by the bridge with their message ids.
                try:
                    offers_from.lineage.record(agent_id, 'joshos:desk', [theirs['id']], offered, kind='chat', state='sent', at=theirs['at'])
                except Exception:  # noqa: BLE001 - without a record, "send it" on the desk fails closed
                    pass
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
        mine = dict(id=str(uuid.uuid4()), role='josh', text=message, at=now(), **chat_origin)
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

    def email_answer(self, mails, answer):
        """The email part of a team member's answer. Returns (saved entries, text to add to the reply). show_email adds the
        saved email by its stable id and saves nothing; a change to that same email in the same answer is refused."""
        shown = str(answer.get('show_email') or '').strip()
        ups = answer.get('emails') if isinstance(answer.get('emails'), list) else []
        done = []
        if shown:
            held = [x for x in ups if isinstance(x, dict) and str(x.get('id') or '') == shown]
            ups = [x for x in ups if x not in held]
            if held:
                done.append(dict(id=shown, title='', action='failed', kind='email',
                                 error='Not saved. Showing an email never changes it. Ask again if you want a change.'))
        visual = answer.get('email_visuals')
        if visual is not None:
            eid = visual.get('email_id', '') if isinstance(visual, dict) else ''
            try:
                if shown:raise ValueError('Showing an email never changes visuals. Ask again for a change.')
                if any(isinstance(u, dict) and u.get('id') == eid for u in ups):
                    ups = [u for u in ups if not isinstance(u, dict) or u.get('id') != eid]
                    raise ValueError('Save body edits and visual changes in separate turns; neither change to this email was saved.')
                item = mails.visuals.generate(visual)
                done.append(dict(id=item['id'], title=item['subject'], action='updated', kind='email',
                                 fields=['images'], revision=item['revision'], note='Local visual preview saved. Nothing uploaded; Kit preparation is blocked.'))
            except Exception as exc:
                done.append(dict(id=eid, title='', action='failed', kind='email', error=str(exc).strip("'\"")[:300]))
        done += self.apply_emails(mails, ups) if ups else []
        return done, (self.email_text(mails, shown) if shown else '')

    @staticmethod
    def email_text(mails, eid):
        """The saved email by its stable id, word for word, for Josh to read in chat. Reads only."""
        try:
            e = mails.get(eid)
        except KeyError:
            return f"There's no email with id {eid} in Josh OS, so nothing was shown."
        when = f"planned {e.get('planned_date') or 'no date'}{(' ' + e['send_time']) if e.get('send_time') else ''}"
        kit = e.get('kit') or {}
        return '\n'.join([f"**{e['subject']}** Â· {e['status']} Â· {when} Â· saved version {e['revision']}"
                          + (f" Â· Kit broadcast {kit.get('id')} ({kit.get('status')})" if kit.get('id') else ''),
                          *([f"Preview: {e['preview']}"] if e.get('preview') else []), '', e.get('body') or '(no body yet)'])

    def apply_emails(self, mails, updates):
        """Email changes a team member asked for from chat. Status is left to Josh, with two exceptions, both checked here:
        sent true only when Kit's newest complete snapshot shows that broadcast completed, and sent false to undo a Sent
        that has no such proof. Every change is read back, and only the fields that really changed are reported."""
        from .emails import kit_send_proof, proven_send, trusted_broadcasts
        drafts = getattr(self, 'kit_drafts', None)
        done = []
        for u in updates[:40]:
            if not isinstance(u, dict):
                continue
            changes = {k: str(v) for k, v in u.items() if k in EMAIL_WRITE and v is not None}
            for key in ('links', 'tags', 'archived'):
                if key in u:
                    changes[key] = u[key]
            eid = str(u.get('id') or '')
            title = changes.get('subject', '')
            try:
                current = mails.get(eid) if eid else None
                title = title or (current or {}).get('subject', '')
                if isinstance(u.get('results'), dict):
                    changes['results'] = dict((current or {}).get('results') or {}, **{k: v for k, v in u['results'].items() if v is not None})
                if isinstance(u.get('source_ids'), list):
                    known = mails.references()
                    changes['source_ids'] = [str(x) for x in u['source_ids'] if str(x) in known][:5]
                if u.get('sent') is True:
                    if current is None:
                        raise ValueError('A new email starts as a draft. Mark it sent once it exists and Kit shows it went out.')
                    # The broadcast must be one Josh OS itself tied to this email. An id from the chat is only a pointer
                    # to one of those, never proof: any completed broadcast would otherwise fit any email.
                    trusted = trusted_broadcasts(current, drafts)
                    asked = str(u.get('kit_broadcast_id') or '').strip()
                    verified = None
                    if not trusted:
                        from .kit_sent import verified_link
                        verified = verified_link(mails, current, asked)
                        changes.update(verified)
                        trusted = [str(verified['kit']['id'])]
                    if asked and asked not in trusted:
                        raise ValueError(f'Not marked sent. Kit broadcast {asked} is not the one linked to this email ({", ".join(trusted)}).')
                    bid = asked or trusted[0]
                    proof = (dict(sent=True, send_at=verified['kit']['send_at'], snapshot_at=verified['kit']['synced_at'])
                             if verified else kit_send_proof(mails.store, bid))
                    if not proof['sent']:
                        raise ValueError('Not marked sent. ' + proof['reason'] + ' Only a send Kit shows as completed counts, never a planned date or a chat message.')
                    changes['status'] = 'Sent'
                    changes['results'] = dict(changes.get('results') or current.get('results') or {}, kit_broadcast_id=bid)
                    source = 'Kit verified read' if verified else 'Kit snapshot'
                    changes['notes'] = (current.get('notes') or '') + f"\n[Sent per linked Kit broadcast {bid}, sent {proof['send_at']}, {source} {str(proof['snapshot_at'])[:16]}]"
                elif u.get('sent') is False:
                    if not current or current['status'] != 'Sent':
                        raise ValueError('That email is not marked Sent, so there was nothing to undo.')
                    proven = proven_send(mails.store, current, drafts)
                    if proven:
                        raise ValueError(f"Not changed. Kit shows its linked broadcast {proven['broadcast_id']} went out "
                                         f"({proven['send_at']}, {proven['source']}), so it stays Sent.")
                    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
                    today = (_dt.now(_tz.utc) + _td(hours=4)).date().isoformat()     # Dubai, where the planner's dates live
                    if not current.get('planned_date') or current['planned_date'] < today:
                        # Older sends were mostly marked before Josh OS kept Kit links, so missing proof isn't proof of no send.
                        raise ValueError('Not changed. Its planned date has passed (or it has none), and older sends often have '
                                         'no Kit link in Josh OS. Ask Josh before changing it.')
                    back = next((v['status'] for v in mails.history(eid) if v.get('status') not in ('Sent', None)), 'Draft')
                    changes['status'] = back
                if current is not None:
                    mails.save(eid, changes, current['revision'], sync_remote=False)
                    item = mails.get(eid)
                    applied = [k for k in changes if item.get(k) != current.get(k)]
                    off = [k for k in changes if item.get(k) != changes[k] and k not in ('results', 'notes')]
                    if off:
                        done.append(dict(id=eid, title=title, action='failed', kind='email', error='Saved, but ' + ', '.join(off) + ' did not come back as asked.'))
                    elif not applied:
                        done.append(dict(id=eid, title=title, action='unchanged', kind='email'))
                    else:
                        done.append(dict(id=item['id'], title=item.get('subject', ''), action='updated', kind='email', fields=applied,
                                         status=item['status'], revision=item['revision']))
                else:
                    if changes.get('status') == 'Sent' or u.get('sent') is False:
                        raise ValueError('A new email starts as a draft. Mark it sent once it exists and Kit shows it went out.')
                    item = mails.create({'id': str(uuid.uuid4()), 'changes': changes})
                    done.append(dict(id=item['id'], title=item.get('subject', ''), action='added', kind='email', revision=item['revision']))
            except Exception as e:  # noqa: BLE001 - report every failure back to Josh in the chat
                done.append(dict(id=eid, title=title, action='failed', kind='email', error=str(e).strip("'\"")[:300]))
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

    def start_finish(self, pid, name, action, via='', approval=None):
        """Josh told Tariq to put the video together: Josh OS builds and saves the cut if needed, approves the storyboard
        on Josh's own words in this message, renders and places the animations and exports. Tariq posts back."""
        flow = getattr(self, 'edit_flow', None)
        if action.get('finish') is not True or flow is None:
            return []
        if not approval or not str(approval.get('text') or '').strip():
            return [dict(id=pid, title='putting ' + str(name) + ' together', action='failed', kind='edit',
                         error="It needs your own message saying the animations are good to go.")]
        try:
            flow.finish(pid, approval, via=via)
            return [dict(id=pid, title='putting ' + str(name) + ' together (cut, animations, export)', action='started', kind='edit')]
        except Exception as e:  # noqa: BLE001 - Conflict, ValueError, KeyError all go back to Josh
            return [dict(id=pid, title='putting ' + str(name) + ' together', action='failed', kind='edit', error=str(e).strip("'\"")[:200])]

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

    def apply_edit_elsewhere(self, er, agent_id, pid, action, via, approval=None):
        """Edit actions asked for in a chat that isn't the animation review: folders and pictures now, deck changes as a job."""
        try:
            p = self.store.get(pid)
            er.project_folder(pid)
        except (KeyError, ValueError) as e:
            return [dict(id=pid, title='the edit', action='failed', kind='edit', error=str(e)[:200])]
        done = self.apply_edit(er, pid, {k: action[k] for k in ('setup', 'rerender') if k in action}, self._deck_state(er, pid))
        done += self.start_go_ahead(pid, p['name'], action, via)
        done += self.start_finish(pid, p['name'], action, via, approval)
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
            from . import deck_source
            authoring_source = deck_source.capture(flow, pid) if flow else None
            if flow:
                threading.Thread(target=self._deck_progress, args=(Path(folder) / DECK_PROGRESS, mark, stop), daemon=True).start()
            learnings = [x for x in self.team.all()['learnings'] if x['agent_id'] in ('', agent_id)][:80]
            _, dirs = self.training(agent_id)
            parts = [self.focus(dict(kind='edit', id=pid), [], None), '', 'Josh asked: ' + change]
            if authoring_source is not None:
                parts += ['', deck_source.brief(authoring_source)]
                parts += ['Write the versioned *_slides_vNN.html and *_shot-list_vNN.md directly in ' + str(folder) +
                          ', where CareerOS discovers them. Do not put the deliverables under video/sources. '
                          'Preserve earlier versions, including files already under an edit folder. '
                          'The shot list must cite the approved clean transcript digest and sequence above as its source.']
            elif self.anim_notes_text(pid):
                parts += ['', self.anim_notes_text(pid)]
            parts += ['', GAP_RULE]
            # Keep the deck as it was before any change, so an edit never loses the version Josh approved (12 Nov 2026: a
            # deck change overwrote the 4 Signs v02 slides with no copy kept).
            import shutil
            from datetime import datetime, timezone
            keep = Path(folder) / 'deck-versions' / f"before-deck-change-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
            for f in [p for p in Path(folder).glob('*_slides_v*.html')] + [p for p in Path(folder).glob('*_shot-list_v*.md')]:
                keep.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, keep / f.name)
            gaps = flow.animation_gaps(pid) if flow and authoring_source is None else []
            if gaps:
                parts += ['In the current cut these stretches have no animation at all. Fill them unless Josh said otherwise:']
                parts += [f"- {g['from']} to {g['to']} ({g['seconds']:.0f}s): \"{g['text']}\"" for g in gaps]
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
            if authoring_source is not None:
                deck_source.record(flow, er, pid, authoring_source)
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
        self.post(agent_id, text, saved=saved, via=via, notice=via != 'discord')
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
        self.post(agent_id, text, files=files, via=via, notice=via != 'discord')
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
