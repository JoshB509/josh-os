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
