"""Validate permitted Kit content-tree, accessibility and rendered-text observations.

Only the observed linear paragraph/link schema is supported. Kit's HTML email tables
and style wrappers are not mistaken for the editable body. No browser DOM extraction,
cookie/query-string collection, model HTML or hidden application state is used.
"""
import hashlib
import html
import json
import re
from urllib.parse import urlsplit


def tree_body(api,*,allow_pending_caption=False):
    tree=api.get('content_tree')
    if (api.get('content_tree_is_clean') is not True or api.get('content_tree_warnings')!=[]
            or not isinstance(tree,list) or not 1<=len(tree)<=1000):
        raise ValueError('Kit must return a clean structured body with no conversion warnings.')
    links=[];paragraphs=[];budget=[0]
    def inline(node,depth=0):
        budget[0]+=1
        if depth>6 or budget[0]>10000 or not isinstance(node,dict):raise ValueError('Unsupported structured Kit body.')
        if 'text' in node:
            if set(node)-{'text','bold','italic','underline','strikethrough'} or not isinstance(node['text'],str):
                raise ValueError('Unknown Kit text properties require review.')
            text=node['text']
            if len(text)>200000 or ('\u200b' in text and text!='\u200b') or '\n' in text or '\r' in text:
                raise ValueError('Ambiguous inline text or editor filler requires review.')
            if text=='\u200b':text=''
            out=html.escape(text,quote=False)
            for key,tag in (('bold','strong'),('italic','em'),('underline','u')):
                if key in node and type(node[key]) is not bool:raise ValueError('Invalid text mark.')
                if node.get(key):out=f'<{tag}>{out}</{tag}>'
            if node.get('strikethrough'):raise ValueError('Strikethrough requires separate support.')
            return out,text
        if node.get('type')!='link' or set(node)-{'type','href','children'} or not isinstance(node.get('children'),list):
            raise ValueError('Only observed paragraph text and links are supported.')
        href=node.get('href');parsed=urlsplit(href) if isinstance(href,str) else None
        if not parsed or parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Unsupported Kit link destination.')
        pieces=[inline(c,depth+1) for c in node['children']];label=''.join(t for h,t in pieces)
        links.append(dict(href=href,text=label))
        return '<a href="'+html.escape(href,quote=True)+'">'+''.join(h for h,t in pieces)+'</a>',label
    out=[];images=[]
    for paragraph in tree:
        if isinstance(paragraph,dict) and paragraph.get('type')=='image':
            # Exact schema observed through supported get_broadcast, 2026-11-16.
            if (set(paragraph)!={'type','fullWidth','src','showCaption','alt'}
                    or paragraph['fullWidth'] is not False
                    or type(paragraph['showCaption']) is not bool
                    or (paragraph['showCaption'] and not allow_pending_caption)
                    or not isinstance(paragraph['alt'],str) or len(paragraph['alt'])>4000):
                raise ValueError('Only observed unlinked, caption-free Kit images are supported.')
            src=paragraph['src'];parsed=urlsplit(src) if isinstance(src,str) else None
            if (not parsed or len(src)>4096 or parsed.scheme!='https' or parsed.hostname!='embed.filekitcdn.com'
                    or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment
                    or not re.fullmatch(r'/e/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+/email',parsed.path)):
                raise ValueError('Unrecognized observed Kit image resource URL.')
            images.append(dict(src=src,alt=paragraph['alt'],resource_id=parsed.path,showCaption=paragraph['showCaption']))
            out.append('<p><img src="'+html.escape(src,quote=True)+'" alt="'+html.escape(paragraph['alt'],quote=True)+'"></p>')
            continue
        if (not isinstance(paragraph,dict) or paragraph.get('type')!='paragraph' or set(paragraph)-{'type','children'}
                or not isinstance(paragraph.get('children'),list)):
            raise ValueError('Only the observed linear paragraph body is supported; no inferred layout.')
        pieces=[inline(c) for c in paragraph['children']]
        out.append('<p>'+''.join(h for h,t in pieces)+'</p>');paragraphs.append(''.join(t for h,t in pieces))
    return dict(html=''.join(out),paragraphs=paragraphs,links=links,images=images)


def observed_body(api,scoped_tree,page_text,broadcast_id):
    """Require complete rendered wording plus every body href in the observed order."""
    body=tree_body(api);url=f'https://app.kit.com/campaigns/{broadcast_id}/draft'
    if api.get('id')!=broadcast_id:raise ValueError('Wrong content-tree broadcast identity.')
    if any(marker in scoped_tree or marker in page_text for marker in ('[BLOCKED:','[TRUNCATED','truncated due to')):
        raise ValueError('The supported body observation was denied or truncated. Do not bypass it.')
    if not re.match(r'^textbox "Email content editor" \[ref_\d+\]\n',scoped_tree):
        raise ValueError('Read the actual scoped Email content editor accessibility node.')
    if re.search(r'^\s*(?:image|button|checkbox|textbox)\b',scoped_tree.split('\n',1)[1],re.M):
        raise ValueError('The editor contains unsupported non-text blocks or controls.')
    observed_links=re.findall(r'^\s+link(?: "[^\n]*")? \[ref_\d+\] href="([^\n]*)"$',scoped_tree,re.M)
    if observed_links!=[link['href'] for link in body['links']]:
        raise ValueError('Observed body links differ from the structured Kit body.')
    # get_page_text's observed <main> envelope, not arbitrary assistant text.
    header='Title: '+str(api.get('subject',''))+' - Kit\nURL: '+url+'\nSource element: <main>\n---\n'
    if not page_text.startswith(header):raise ValueError('The visible title/destination or plain-text source does not match this draft.')
    # Zero-width editor caret fillers occur around links and empty paragraphs. Tree
    # text containing intentional inline zero-width characters is rejected above.
    rendered=page_text[len(header):].replace('\u200b','')
    prefix=re.match(r'^Subject Line\nSuggest subject lines\nAB test toggle\nPreview Text\n\d+/150\n\nAppears after the subject line in the inbox\.\n+',rendered)
    if not prefix:raise ValueError('The observed editor text boundary is unfamiliar; manual review is required.')
    expected='\n\n'.join(p for p in body['paragraphs'] if p)
    if not expected or not rendered[prefix.end():].startswith(expected):raise ValueError('Rendered wording differs from the structured Kit body.')
    tail=rendered[prefix.end()+len(expected):]
    if not re.fullmatch(r'\n\nUnsubscribe \| Update your profile \| [^\n]+\n\n\d+ words\s*',tail):
        raise ValueError('Unexpected wording or an unfamiliar footer follows the approved body.')
    # The word counter changes when images are added; the existing footer must not.
    footer=re.sub(r'\n\n\d+ words\s*$','',tail)
    return dict(html=body['html'],proof=dict(source='kit-content-tree+scoped-accessibility+page-text',
        body_text_sha256=hashlib.sha256(expected.encode()).hexdigest(),
        links_sha256=hashlib.sha256(json.dumps(body['links'],sort_keys=True).encode()).hexdigest(),
        footer_sha256=hashlib.sha256(footer.encode()).hexdigest()))
