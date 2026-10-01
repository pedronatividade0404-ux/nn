"""Bounded operations invoked by HTTP requests. No continuous external worker."""
import time,json,os,logging
from pathlib import Path
from sqlalchemy import select,or_
from app.db import Session,User,Account,Asset,Job,Control,init
from app.security import unseal,seal
from app.daytona_service import client,get,provision
from app.tiktok import TikTok
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
ROOT='/home/daytona/nexatok/'
def sync(s,u):
 if not u.sandbox:return
 sandbox=get(u)
 control=s.get(Control,u.id)
 if not control:
  control=Control(user_id=u.id,callback=seal(__import__('secrets').token_urlsafe(40)));s.add(control);s.commit()
 elif not control.callback:control.callback=seal(__import__('secrets').token_urlsafe(40));s.commit()
 cfg={'expires':u.expires,'accounts':{},'expiry_url':os.getenv('PUBLIC_URL','').rstrip('/')+'/api/internal/expired','expiry_token':unseal(control.callback),'user_id':u.id}
 for a in s.scalars(select(Account).where(Account.user_id==u.id)):
  c=dict(a.config);c['rtmp']=unseal(c.pop('rtmp_secret')) if c.get('rtmp_secret') else ''
  if c.get('video'):
   asset=s.get(Asset,c['video']);c['video']=Path(asset.path).name if asset and asset.user_id==u.id else ''
  cfg['accounts'][a.id]=c
 sandbox.fs.upload_file(json.dumps(cfg).encode(),ROOT+'config.pending')
 r=sandbox.process.exec('chmod 600 /home/daytona/nexatok/config.pending && mv /home/daytona/nexatok/config.pending /home/daytona/nexatok/config.json',timeout=10)
 if r.exit_code!=0:raise RuntimeError('Falha ao sincronizar configurações.')
 read_status(s,u,sandbox)
def read_status(s,u,sandbox=None):
 if not u.sandbox:return
 sandbox=sandbox or get(u)
 status=json.loads(sandbox.fs.download_file(ROOT+'status.json'))
 for a in s.scalars(select(Account).where(Account.user_id==u.id)):
  old=a.runtime or {}
  a.runtime={**old,**status.get('accounts',{}).get(a.id,{'state':'stopped'}),'heartbeat':status.get('heartbeat',0)}
 s.commit()
def execute(s,j):
 u=s.get(User,j.user_id)
 if j.kind=='sync':sync(s,u);return
 if j.kind=='provision':
  if u.expires<=time.time():raise ValueError('Plano expirado.')
  u.env_status='creating';s.commit()
  sandbox=provision(u);u.sandbox=sandbox.id;u.env_status='ready';u.env_error='';s.commit();sync(s,u);return
 a=s.get(Account,j.payload.get('account_id'))
 if not a or a.user_id!=u.id:raise ValueError('Conta removida.')
 c=dict(a.config)
 if j.kind not in ('stop','finish') and u.expires<=time.time():raise ValueError('Plano expirado.')
 if j.kind=='generate':
  if c.get('desired')=='running':raise ValueError('Pare a transmissão antes de gerar outra chave.')
  cover=None
  if c.get('cover'):
   asset=s.get(Asset,c['cover'])
   if asset and asset.user_id==u.id:cover=get(u).fs.download_file(asset.path)
  result=TikTok(unseal(a.secret)).generate(c,cover)
  c.update(rtmp_secret=seal(result['rtmp']),room_id=result['room_id'],share_url=result['share_url'])
 elif j.kind=='check':
  TikTok(unseal(a.secret)).check();a.runtime={**a.runtime,'session_valid':True};s.commit();return
 elif j.kind=='finish':
  c.update(desired='stopped',revision=c.get('revision',0)+1);a.config=c;s.commit();sync(s,u)
  if c.get('room_id'):TikTok(unseal(a.secret)).finish(c['room_id'])
  c.update(room_id='',share_url='',rtmp_secret='')
 elif j.kind in ('start','stop'):
  c.update(desired='running' if j.kind=='start' else 'stopped',revision=c.get('revision',0)+1)
 a.config=c;s.commit()
 if u.sandbox:sync(s,u)
