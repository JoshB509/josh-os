"""Host receipt contract. Never populate from model JSON or scraped page instructions.
The resolver must authenticate directly to Linklr, bind the exact post and both
placements, and resolve offer copy from the canonical destination registry.
This module checks consistency, NOT authenticity. No live adapter is installed.
"""
import base64,io
from urllib.parse import urlsplit

def validate_receipt(data,r):
    if not isinstance(r,dict):raise ValueError('Missing authenticated receipt.')
    required={'post_id','offer_id','destination','label','description','button','template','caption','qr','source_response_id','verified_at'}
    if set(r)-{'attribution_mode','source_attribution'}!=required:raise ValueError('Incomplete canonical offer/link receipt.')
    p=data['promotion']
    if r['post_id']!=p['post_id'] or r['offer_id']!=p['offer_id']:raise ValueError('Receipt belongs to another post or offer.')
    if r['template'] not in ('SCR','Handbook','EMF','Playbook','Course','Neutral'):raise ValueError('Unknown ending.')
    for k in ('label','description','button','source_response_id','verified_at'):
        if not isinstance(r[k],str) or not r[k].strip() or any(c in r[k] for c in '<>'):raise ValueError('Missing canonical '+k)
    if len(r['label'])>150 or len(r['description'])>400 or len(r['button'])>45:raise ValueError('Canonical offer copy too long.')
    if urlsplit(r['destination']).scheme!='https':raise ValueError('Canonical destination required.')
    for placement in ('caption','qr'):
        link=r[placement]
        if not isinstance(link,dict) or set(link)!={'url','post_id','placement','destination','offer_id'}:raise ValueError('Unknown link attribution.')
        if any(link[k]!=r[k] for k in ('post_id','offer_id','destination')) or link['placement']!=placement:raise ValueError('Wrong placement or destination.')
        u=urlsplit(link['url'])
        if u.scheme!='https' or u.hostname!='linklr.app' or u.username or u.password or u.port or not u.path.strip('/') or u.query or u.fragment:raise ValueError('Unverified Linklr URL.')
    mode=r.get('attribution_mode','separate')
    if mode not in ('shared','separate'):raise ValueError('Unknown attribution mode.')
    same=r['caption']['url']==r['qr']['url']
    if mode=='separate' and same:raise ValueError('Separate attribution requires distinct links.')
    if mode=='shared' and (not same or not (r.get('source_attribution') or {}).get('shared_authorization')):raise ValueError('Explicit shared attribution evidence required.')
    return r

def qr_data_uri(url):
    import qrcode
    q=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=8,border=4)
    q.add_data(url);q.make(fit=True)
    out=io.BytesIO();q.make_image(fill_color='black',back_color='white').save(out,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()

def verify_final(path,url):
    import cv2
    im=cv2.imread(str(path))
    if im is None:raise ValueError('Final slide unreadable.')
    decoded,points,_=cv2.QRCodeDetector().detectAndDecode(im)
    if points is None or decoded!=url:raise ValueError('Final-slide QR missing or mismatched; no draft saved.')
