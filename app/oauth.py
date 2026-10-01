import os,time,secrets,json,base64,io
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode,parse_qs
import httpx,qrcode
from fastapi import HTTPException
from .db import OAuth
from .security import seal,unseal
BASE='https://open.tiktokapis.com/v2/oauth/'
def config():
 key=os.getenv('TIKTOK_CLIENT_KEY');secret=os.getenv('TIKTOK_CLIENT_SECRET')
 if not key or not secret:raise HTTPException(503,'Configure TIKTOK_CLIENT_KEY e TIKTOK_CLIENT_SECRET para usar Login Kit por QR. Para RTMP, importe cookies pelo Chrome.')
 return key,secret
def call(path,data):
 try:
  with httpx.Client(timeout=20) as c:r=c.post(BASE+path,data=data)
  j=r.json()
 except Exception:raise HTTPException(502,'Falha de comunicação com Login Kit TikTok.')
 if r.status_code!=200 or j.get('error'):raise HTTPException(502,'Login Kit recusou a operação. Confirme credenciais e aprovação do aplicativo TikTok.')
 return j
def start(s,u):
 key,_=config();state=secrets.token_urlsafe(24);ticket=secrets.token_urlsafe(32)
 j=call('get_qrcode/',{'client_key':key,'scope':'user.info.basic','state':state})
 if not j.get('scan_qrcode_url') or not j.get('token'):raise HTTPException(502,'QR não retornado pelo TikTok.')
 p=urlsplit(j['scan_qrcode_url']);params=dict(parse_qsl(p.query));params['client_ticket']=ticket
 if p.scheme not in ('aweme','https'):raise HTTPException(502,'URL de autorização inválida.')
 url=urlunsplit((p.scheme,p.netloc,p.path,urlencode(params),p.fragment))
 row=OAuth(user_id=u.id,secret=seal(json.dumps({'token':j['token'],'ticket':ticket,'state':state})),expires=time.time()+300)
 s.add(row);s.commit();buf=io.BytesIO();qrcode.make(url).save(buf,format='PNG')
 return {'id':row.id,'url':url,'image':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()}
def check(s,u,id):
 row=s.get(OAuth,id)
 if not row or row.user_id!=u.id:raise HTTPException(404,'QR não encontrado.')
 if row.status=='confirmed':return {'done':True,'message':'Perfil TikTok autorizado. Para RTMP, importe os cookies do Chrome.'}
 if row.expires<time.time():return {'done':True,'message':'QR expirado. Gere outro para continuar.'}
 key,secret=config();data=json.loads(unseal(row.secret));j=call('check_qrcode/',{'client_key':key,'client_secret':secret,'token':data['token']})
 status=j.get('status','new')
 if status in ('scanned','confirmed','utilised') and not secrets.compare_digest(j.get('client_ticket',''),data['ticket']):raise HTTPException(502,'Integridade do QR inválida.')
 if status=='confirmed':
  redirect=j.get('redirect_uri',j.get('code',''));params=parse_qs(urlsplit(redirect).query)
  state=j.get('state') or params.get('state',[''])[0]
  if not secrets.compare_digest(state,data['state']):raise HTTPException(502,'Estado de autorização inválido.')
  code=params.get('code',[''])[0] or (j.get('code','') if not redirect.startswith('https://') else '')
  if not code:raise HTTPException(502,'Código de autorização não retornado.')
  tokens=call('token/',{'client_key':key,'client_secret':secret,'grant_type':'authorization_code','code':code})
  row.secret=seal(json.dumps({'tokens':tokens}));row.status='confirmed';s.commit()
  return {'done':True,'message':'Perfil TikTok autorizado. Para RTMP, importe os cookies do Chrome.'}
 return {'done':status in ('expired','utilised'),'message':{'new':'Aguardando leitura do QR…','scanned':'Confirme no aplicativo TikTok.','expired':'QR expirado. Gere outro.'}.get(status,'Aguardando autorização…')}
