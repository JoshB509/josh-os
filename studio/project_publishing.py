"""Project-bound publishing preparation. Never uploads or approves."""
from datetime import datetime
from zoneinfo import ZoneInfo
from .youtube_release import digest, file_identity, ReleaseError

FLAGS = ('made_for_kids','contains_paid_promotion','contains_synthetic_media')

def preview(store, release, publishing, pid):
    p=store.get(pid); r=p.get('release') or {}; blocked=[]
    title=next((t['text'] for t in p.get('titles',[]) if t['id']==p.get('chosen_title')), '')
    description=(r.get('description') or {}).get('text','')
    comment=(r.get('cta') or {}).get('text','')
    link=next((l.get('url') for l in r.get('links',[]) if l.get('source_type')=='pinned_comment'),None)
    if link and link not in comment: comment+='\n\n'+link
    aid=next(iter(p.get('top_three') or []),''); thumbnails=[]
    for a in p.get('top_three') or []:
        try: thumbnails.append(str(store.asset(a)[0]))
        except ValueError: blocked.append('A selected thumbnail is unavailable.')
    export=(r.get('export_approval') or {}); ready=False
    try:
        identity=file_identity(export.get('path',''))
        ready=identity['sha256']==export.get('sha256') and identity['size']==export.get('size') and str(identity['path']) in [str(f.resolve()) for f in release.exports(pid)]
    except (OSError,ValueError): pass
    if not ready: blocked.insert(0,"There isn't a completed, approved edit yet.")
    if not title: blocked.append('Choose a title.')
    if not thumbnails: blocked.append('Choose a thumbnail.')
    if not description: blocked.append('Add a description.')
    if not comment: blocked.append('Add a pinned comment.')
    connected=False; channel=None
    if publishing.auth.connected:
        try: channel=publishing.client.channel(); connected=bool(channel)
        except Exception: blocked.append('YouTube publishing connection could not be verified.')
    else: blocked.append('YouTube publishing is not connected.')
    pt=r.get('publish_time') or {}; dt=p.get('publish_date',''); tm=pt.get('time',''); tz=pt.get('tz','')
    sched=list(blocked)
    try:
        from .youtube_release import schedule
        wall=datetime.fromisoformat(dt+'T'+tm); zone=ZoneInfo(tz)
        offset=wall.replace(tzinfo=zone).strftime('%z'); offset=offset[:3]+':'+offset[3:]
        schedule(dt+'T'+tm,tz,offset)
    except Exception: sched.append('Choose a future publish date and time.'); offset=''
    data=dict(project_id=pid,revision=p['revision'],title=title,description=description,pinned_comment=comment,
        thumbnail_url='/assets/'+aid if aid else '',publish_date=dt,publish_time=tm,timezone=tz,
        channel_id=channel,connected=connected,completed_edit=ready,blockers=dict(unlisted=blocked,schedule=sched),
        disclosures={k:None for k in FLAGS},comment_delivery='manual')
    data['preview_digest']=digest(dict(view=data, assets=[file_identity(f) for f in thumbnails], export=identity if ready else None))
    return data,dict(channel_id=channel,video=export.get('path'),title=title,description=description,
        category_id='27',language='en',primary_thumbnail=thumbnails[0] if thumbnails else '',thumbnail_variants=thumbnails,
        local_time=dt+'T'+tm,timezone=tz,utc_offset=offset)

def prepare(store, release, publishing, pid, data):
    view, source=preview(store,release,publishing,pid)
    if type(data.get('revision')) is not int or data['revision']!=view['revision']:
        raise ReleaseError('The video changed. Close and reopen Publish to review it again.')
    if data.get('preview_digest')!=view['preview_digest']:
        raise ReleaseError('The preview changed. Close and reopen Publish to review it again.')
    mode=data.get('mode')
    if mode not in ('unlisted','schedule'): raise ReleaseError('Choose unlisted or schedule.')
    if view['blockers'][mode]: raise ReleaseError(view['blockers'][mode][0])
    source.update({k:data.get(k) for k in FLAGS});source['mode']=mode
    # Preserve the exact reviewed comment in the digest without pretending the upload API pins it.
    source['project_id']=pid;source['project_revision']=view['revision'];source['pinned_comment']=view['pinned_comment']
    return publishing.prepare(source)

SLOTS=('Main','B','C')

def summary(store, pid):
    """What Publish would use for this video right now, read from the same project fields as preview(). It makes no
    YouTube call and does not hash the video, so it is cheap enough to give Sam on every message. Publishing itself
    still checks the export's bytes and the YouTube connection."""
    p=store.get(pid); r=p.get('release') or {}
    from .release_ready import text_sha
    title=next((t['text'] for t in p.get('titles',[]) if t['id']==p.get('chosen_title')), '')
    d=r.get('description') or {}; c=r.get('cta') or {}
    description=d.get('text',''); comment=c.get('text','')
    link=next((l.get('url') for l in r.get('links',[]) if l.get('source_type')=='pinned_comment'),None)
    if link and link not in comment: comment+='\n\n'+link
    names={t['asset']:t.get('name','') for t in p.get('thumbnails') or []}
    thumbs=[dict(slot=SLOTS[i],file=names.get(a,a)) for i,a in enumerate((p.get('top_three') or [])[:3])]
    export=r.get('export_approval') or {}; pt=r.get('publish_time') or {}
    unlisted=[]
    if not export.get('path'): unlisted.append('There isn\'t a completed, approved edit yet.')
    if not title: unlisted.append('Choose a title.')
    if not thumbs: unlisted.append('Choose a thumbnail.')
    if not description: unlisted.append('Add a description.')
    if not comment: unlisted.append('Add a pinned comment.')
    schedule=[] if p.get('publish_date') and pt.get('time') and pt.get('tz') else ['Choose a publish date and time to schedule.']
    return dict(project_id=pid,revision=p['revision'],title=title,description=description,
                description_approved=bool(description) and (d.get('approved') or {}).get('sha')==text_sha(description),
                pinned_comment=comment,pinned_comment_approved=bool(c.get('text')) and (c.get('approved') or {}).get('sha')==text_sha(c.get('text')),
                thumbnails=thumbs,approved_export=dict(file=export['path'].replace('\\','/').rsplit('/',1)[-1],approved_at=export.get('at',''))
                if export.get('path') else None,publish_date=p.get('publish_date',''),publish_time=pt.get('time',''),timezone=pt.get('tz',''),
                missing_to_upload_unlisted=unlisted,also_missing_to_schedule=schedule)

def prepare_for_sam(store, release, publishing, pid, mode, answers):
    """Sam's release packet, built only from the video as CareerOS has it now: the same preview the Publish button shows,
    bound to its current revision and digest. Sam gives no title, description, file or time of his own. The three
    disclosure answers must come from Josh. Josh still approves the upload himself; this only prepares it."""
    view,_=preview(store,release,publishing,pid)
    data=dict(revision=view['revision'],preview_digest=view['preview_digest'],mode=mode)
    for k in FLAGS: data[k]=answers.get(k)
    if any(type(data[k]) is not bool for k in FLAGS):
        raise ReleaseError('Josh has to answer made for kids, paid promotion and altered or synthetic content first. He can do it in the Publish panel on the video.')
    return prepare(store,release,publishing,pid,data)
