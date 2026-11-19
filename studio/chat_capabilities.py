"""Inspectable local chat capability contract; connector connectivity is separate."""
VERSION = '2026-11-15-parity-5'
LOCAL = {
    'sam': ['video.create','video.update','video.clear_text','video.archive_restore','video.title_select_remove','video.thumbnail_select_reorder_remove','video.packaging','video.lead_magnet','carousel.create_update_design','thumbnail.generate','youtube.idea_save','filming.prepare','release.propose','release.draft_packaging','release.chapters_draft','release.local_publish_time'],
    'tariq': ['video.create','video.update','video.clear_text','video.archive_restore','edit.setup','edit.broll_catalog_select_manifest_only','edit.deck_job','edit.animation_notes','edit.render_review','edit.register_sources','edit.cancel_analysis','edit.remove_restore_undo_words','edit.cleanup_proposals','edit.clean_transcript','edit.approved_transcript_handoff','archive.prepare_copy_verify'],
    'laia': ['email.draft_create_update','email.show_exact','email.audience_type_tags','email.archive_restore','email.library','kit.draft_propose'],
    'saskia': ['lesson.create_update','lesson.clear_text','lesson.prepare_draft_slides_guide_thumbnail','lesson.cues_save','lesson.recording_date','lesson.use_latest','lesson.audio_recheck'],
    'anouk': ['people.create_update','people.archive_restore','learner.lookup_refresh','reply.propose','reply.exact_existing_approval'],
    'max': ['task.create_update_complete_reopen_snooze','task.link','scorecard.refresh'],
}
GUARDED = {
    'sam': ['YouTube publication/updates still require existing exact owner approvals; local release drafts never upload or schedule.'],
    'tariq': ['No model-created edit/transcript approvals. Native Premiere rebuild/final transcript remain guarded pending project-identity checks.'],
    'laia': ['No scheduling/sending. Kit exact draft save/approval remains its protected UI flow.'],
    'saskia': ['Slide recheck, recording done/session/import/handover remain UI-only pending source-bound confirmation interfaces. No model-generated script approval.'],
    'anouk': ['No course authoring or newsletter planner edits. No account provisioning or new sending authority.'],
    'max': ['Read-only business metrics; no external publishing or payments.'],
}

def matrix():
    return {'version':VERSION,'shared_surfaces':['Team','dock','script chat','Discord'],
            'agents':{k:{'local_actions':list(v)+['visual.create_local'],'guarded':GUARDED[k]} for k,v in LOCAL.items()},
            'note':'This describes implemented routing, not connector availability or proof that a model completed a particular request.'}
