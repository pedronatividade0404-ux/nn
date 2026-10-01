"""Port do adaptador do aplicativo. Endpoints internos podem mudar ou recusar LIVE."""
import os,json,re,httpx
BASE='https://webcast16-normal-c-useast2a.tiktokv.com/'
VERSION=os.getenv('TIKTOK_STUDIO_VERSION','0.69.2')
def cookies(raw):
 raw=raw.strip()
 if raw.startswith(('[','{')):
  data=json.loads(raw)
  if isinstance(data,dict):data=data.get('cookies',data)
  if isinstance(data,dict):data=[{'name':k,'value':v} for k,v in data.items()]
  parts=[]
  for c in data:
   domain=c.get('domain','tiktok.com')
   if domain.lstrip('.').endswith('tiktok.com'):parts.append(f"{c['name']}={c['value']}")
  raw='; '.join(parts)
 elif '\t' in raw:
  parts=[]
  for line in raw.splitlines():
   if line.startswith('#HttpOnly_'):line=line[len('#HttpOnly_'):]
   elif line.startswith('#'):continue
   p=line.split('\t')
   if len(p)>=7 and p[0].lstrip('.').endswith('tiktok.com'):parts.append(p[5]+'='+p[6])
  raw='; '.join(parts)
 if '\n' in raw or '\r' in raw or not re.search(r'(?:^|;\s*)(sessionid|sid_tt)=',raw):raise ValueError('Cookies inválidos: forneça sessionid ou sid_tt (texto, JSON ou Netscape).')
 return raw
def refusal_detail(data):
 # Only return fixed descriptions for recognized provider messages, never raw JSON.
 text=' '.join(str(data.get(k,'')) for k in ('prompts','message')).lower()
 if any(x in text for x in ("please login", "doesn't login", 'not logged in', 'login required', 'log in first')):
  return 'O TikTok informa que a solicitação não está autenticada. A sessão enviada não foi reconhecida neste endpoint.'
 if any(x in text for x in ('session expired', 'session has expired')):
  return 'O TikTok informa que a sessão expirou.'
 if any(x in text for x in ('permission denied', 'no permission', 'not enough permissions', 'not eligible', 'not authorized')):
  return 'O TikTok informa falta de permissão ou elegibilidade para esta operação.'
 if any(x in text for x in ('update to the latest', 'version too old', 'outdated version')):
  return 'O TikTok solicita atualizar a versão do cliente.'
 return 'Mensagem do TikTok ainda não classificada; o código sozinho não identifica a causa.'

class TikTok:
 def __init__(self,cookie):
  self.cookie=cookie
  self.headers={'Cookie':cookie,'User-Agent':f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) TikTokLIVEStudio/{VERSION} Chrome/108.0.5359.215 Safari/537.36'}
  self.params={'aid':'8311','app_name':'tiktok_live_studio','channel':'studio','device_platform':'windows','live_mode':'6','version_code':VERSION,'webcast_language':'en','app_language':'en','language':'en'}
 def request(self,path,*,method='GET',data=None,files=None,params=None):
  with httpx.Client(timeout=30,follow_redirects=False) as c:
   r=c.request(method,BASE+path,headers=self.headers,params={**self.params,**(params or {})},data=data,files=files)
  if r.status_code in (401,403):raise ValueError('TikTok recusou a sessão ou a autorização LIVE. Atualize os cookies e confirme acesso ao LIVE Studio.')
  if r.status_code!=200:raise ValueError(f'TikTok respondeu HTTP {r.status_code}.')
  try:j=r.json()
  except ValueError:raise ValueError('TikTok devolveu uma resposta inválida.')
  if j.get('status_code',0)!=0 or j.get('data',{}).get('prompts'):
   code=j.get('status_code')
   code=str(code) if isinstance(code,int) or (isinstance(code,str) and code.isdigit() and len(code)<16) else 'indisponível'
   prompts=bool(j.get('data',{}).get('prompts'))
   raise ValueError(f'TikTok recusou a operação (HTTP {r.status_code}, status_code={code}, prompts={prompts}). {refusal_detail(j.get("data",{}))}')
  return j.get('data',{})
 def generate(self,config,cover=None):
  cover_uri=''
  if cover:cover_uri=self.request('webcast/room/upload/image/',method='POST',files={'file':('cover.jpg',cover,'image/jpeg')}).get('uri','')
  data={'title':config.get('title','Minha live'),'live_studio':'1','gen_replay':str(config.get('replay',False)).lower(),'chat_auth':'1','cover_uri':cover_uri,'close_room_when_close_stream':str(config.get('close_room',True)).lower(),'hashtag_id':config.get('topic',''),'game_tag_id':config.get('game','0'),'screenshot_cover_status':'1','live_sub_only':'0','chat_sub_only_auth':'2','multi_stream_scene':'0','gift_auth':'1','chat_l2':'1','star_comment_switch':'true','multi_stream_source':'1'}
  if config.get('mature'):data['age_restricted']='4'
  d=self.request('webcast/room/create/',method='POST',data=data,params={'priority_region':config.get('region','')})
  url=d.get('stream_url',{}).get('rtmp_push_url','')
  if not url.startswith(('rtmp://','rtmps://')):raise ValueError('TikTok não retornou uma URL RTMP válida.')
  return {'rtmp':url,'room_id':str(d.get('room_id','')),'share_url':d.get('share_url','')}
 def finish(self,room):self.request('webcast/room/finish_abnormal/',method='POST',data={'room_id':room})
 def games(self):return self.request('webcast/room/hashtag/list/')
 def check(self):
  # Use TikTok web account information as a session check, separate from LIVE authorization.
  with httpx.Client(timeout=20) as c:r=c.get('https://www.tiktok.com/passport/web/account/info/',headers=self.headers,params={'aid':'1459'})
  try:j=r.json()
  except ValueError:raise ValueError('Não foi possível validar a sessão TikTok.')
  if r.status_code!=200 or j.get('message')=='error':raise ValueError('Sessão TikTok expirada ou recusada.')
  return {'valid':True}
