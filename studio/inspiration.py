"""Local inspiration catalog and additive, attributed project links."""
import json, uuid
from pathlib import Path


class Inspiration:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS inspiration_links(project_id TEXT NOT NULL, reference_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(project_id,reference_id))')

    def links(self, pid):
        with self.store.connect() as c:
            return [json.loads(r['body']) for r in c.execute('SELECT body FROM inspiration_links WHERE project_id=?', (pid,))]

    def hook_image(self,r):
        # Local copy of the video thumbnail, downloaded once into References/CreatorHooks/thumbs.
        vid=(r.get('video') or {}).get('youtube_id') or ''
        if vid and (self.root/'References/CreatorHooks/thumbs'/(vid+'.jpg')).is_file():return '/inspiration/creatorhooks-thumbs/'+vid+'.jpg'
        return ''

    def catalog(self):
        cards=[]
        hooks=self.root/'References/CreatorHooks/creatorhooks.library.json'
        if hooks.exists():
            data=json.loads(hooks.read_text(encoding='utf-8-sig'))
            for r in data['references']:
                adaptations=r.get('adaptations',[])
                structured=bool(adaptations) and all('template_slots' in a and 'template_check' in a for a in adaptations)
                revision=r.get('adaptation_revision',data.get('adaptation_revision','strict-template-v2' if structured else ''))
                cards.append(dict(id='hook:'+r['id'],kind='hooks',title=r['example_title'],credit='Jake Thomas / Creator Hooks',source=r.get('source_message_url',''),video=r.get('video',{}).get('canonical_url',''),image=self.hook_image(r),tags=r.get('tags',[]),formats=[f for f in r.get('formats',[]) if f.get('origin')=='explicit_newsletter_template'],examples=r.get('author_examples',[]),adaptations=adaptations,revision=revision,description=r.get('curiosity',{}).get('summary',''),review_status=r.get('review_status','unreviewed')))
        thumbs=self.root/'References/ThumbnailFormats/data/library.json'
        if thumbs.exists():
            for r in json.loads(thumbs.read_text(encoding='utf-8-sig'))['examples']:
                cards.append(dict(id='format:'+r['id'],kind='formats',title=r['title'],credit=r.get('verified_channel') or 'Original creator — see source screenshot',source=r.get('source_url',''),image='/inspiration/thumbnail-formats/'+r['crop_path'],asset_path='References/ThumbnailFormats/'+r['crop_path'],screenshot='/inspiration/thumbnail-formats/'+r['source_screenshot'],tags=r.get('format_tags',[]),description=r.get('observed_scene',''),question=r.get('unanswered_question','')))
        ideas=getattr(self,'youtube_ideas',None)
        for v in (ideas.all() if ideas else []):
            facts=[x for x in (v.get('published') and 'Published '+v['published'], v.get('views_at_save') is not None and f"{v['views_at_save']:,} views when saved", 'Saved '+v['observed_at'][:10]) if x]
            cards.append(dict(id='video:'+v['id'],kind='videos',title=v.get('title') or 'Title unavailable',credit=v.get('channel') or 'Channel unavailable',source=v['url'],video=v['url'],image=('/inspiration/youtube-ideas/'+v['id']+'.jpg') if v.get('thumbnail') else '',tags=v.get('tags') or ['Saved video'],description=' · '.join(facts)+('. '+v['note'] if v.get('note') else ''),unavailable=v.get('unavailable') or []))
        saved={}
        for p in self.store.projects():
            lineage={r.get('asset'):r for r in self.links(p['id']) if r.get('asset')}
            for t in p['thumbnails']:
                if t['asset'] not in saved:
                    origin=lineage.get(t['asset'],{})
                    saved[t['asset']]=dict(id='saved:'+t['asset'],kind='saved',title=t['name'],credit=origin.get('credit','Saved in '+p['name']),source=origin.get('source',''),image='/assets/'+t['asset'],asset=t['asset'],tags=['Saved thumbnail'],description=t.get('candidate_status','Original image kept on this PC.'),provenance=origin or t)
        return cards+list(saved.values())

    def link(self, data):
        ids=data.get('references',[])
        if not isinstance(ids,list) or not ids or len(ids)>100:raise ValueError('Choose 1–100 references')
        catalog={r['id']:r for r in self.catalog()}
        if any(i not in catalog for i in ids):raise ValueError('Reference no longer available; reload the library')
        p=self.store.get(data['project_id'])
        if p['archived'] or p['stage']=='Published':raise ValueError('Choose an unfinished project')
        thumbs=list(p['thumbnails']); titles=list(p['titles']); selected=data.get('titles',[])
        valid={a['title']:(i,a) for i in ids for a in catalog[i].get('adaptations',[]) if catalog[i].get('revision')=='strict-template-v2'}
        if any(t not in valid for t in selected):raise ValueError('Only revised source-template adaptations can be added')
        for text in selected:
            if not any(t['text']==text for t in titles):
                ref,adaptation=valid[text]
                titles.append(dict(id=str(uuid.uuid4()),text=text,source_reference=ref,inspiration=adaptation))
        links=[]
        for i in dict.fromkeys(ids):
            r=dict(catalog[i]); asset=r.get('asset')
            if r.get('asset_path'):
                path=(self.root/r['asset_path']).resolve(); base=(self.root/'References/ThumbnailFormats/crops').resolve()
                if not path.is_relative_to(base):raise ValueError('Invalid reference asset')
                t=self.store.import_image(path.name,path.read_bytes());asset=t['asset']
            if asset and not any(t['asset']==asset for t in thumbs):thumbs.append(dict(asset=asset,name=r['title'][:240],title_id=''))
            r.pop('asset_path',None);r['asset']=asset;links.append(r)
        # Revision guard protects concurrent edits; linking does not overwrite any script/media.
        p=self.store.save(p['id'],dict(thumbnails=thumbs,titles=titles),data['revision'])
        with self.store.connect() as c:
            for r in links:c.execute('INSERT OR REPLACE INTO inspiration_links VALUES(?,?,?)',(p['id'],r['id'],json.dumps(r)))
        return p
