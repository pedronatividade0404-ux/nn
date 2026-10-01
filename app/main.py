import os,time,secrets,re,json,tempfile
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Depends,Request,Response,UploadFile,File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
from sqlalchemy import select,func,delete
from sqlalchemy.exc import IntegrityError
from .db import Session,User,Login,Key,Account,Asset,Job,Attempt,init,uid
from .security import seal,unseal,digest,password_hash,password_ok
from . import daytona_service as ds
from .tiktok import cookies
PLANS={'basic':{'days':3,'accounts':1},'pro':{'days':7,'accounts':3},'ultimate':{'days':12,'accounts':5}}
PUBLIC=os.getenv('PUBLIC_URL','http://localhost:8000').rstrip('/')
FRONT=os.getenv('FRONTEND_URL',PUBLIC).rstrip('/')
PROD=os.getenv('APP_ENV','development')=='production'
@asynccontextmanager
async def lifespan(app):
 init()
 yield
app=FastAPI(title='NexaTok Web',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=list(set([PUBLIC,FRONT])),allow_credentials=True,allow_methods=['GET','POST','PATCH','PUT','DELETE'],allow_headers=['Content-Type','X-CSRF-Token'])
@app.middleware('http')
async def security(request,call_next):
 if request.url.path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
  origin=request.headers.get('origin')
  if origin and origin.rstrip('/') not in (PUBLIC,FRONT):return Response('Origem não autorizada',status_code=403)
  if request.cookies.get('nexa_session'):
   a=request.cookies.get('nexa_csrf','');b=request.headers.get('x-csrf-token','')
   if not a or not secrets.compare_digest(a,b):return Response('Token CSRF inválido',status_code=403)
 r=await call_next(request)
 r.headers['X-Content-Type-Options']='nosniff';r.headers['Referrer-Policy']='same-origin'
 r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https://open.tiktokapis.com; connect-src 'self' "+PUBLIC+"; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
 if request.url.path.startswith('/api/'):r.headers['Cache-Control']='no-store'
 return r
_schema_ready=False
_schema_lock=__import__('threading').Lock()
def db():
 global _schema_ready
 if not _schema_ready:
  with _schema_lock:
   if not _schema_ready:init();_schema_ready=True
 with Session() as s:yield s
def current(request:Request,s=Depends(db)):
 token=request.cookies.get('nexa_session',''); login=s.get(Login,digest(token))
 if not login or login.expires<time.time():raise HTTPException(401,'Faça login para continuar.')
 return s.get(User,login.user_id)
def admin(u=Depends(current)):
 if not u.admin:raise HTTPException(403,'Acesso exclusivo do administrador.')
 return u
def active(u):
 if u.expires<=time.time():raise HTTPException(403,'Ative ou renove seu plano.')
 return PLANS[u.plan]['accounts']
def owned(s,model,id,u):
 obj=s.get(model,id)
 if not obj or obj.user_id!=u.id:raise HTTPException(404,'Registro não encontrado.')
 return obj
def queue(s,u,kind,payload=None):
 pending=s.scalar(select(func.count()).select_from(Job).where(Job.user_id==u.id,Job.state.in_(['queued','running'])))
 if pending>=30:raise HTTPException(429,'Aguarde as operações pendentes antes de solicitar outra.')
 job=Job(user_id=u.id,kind=kind,payload=payload or {});s.add(job);s.commit()
 from .dispatch import process
 result=process(job.id,u.id);s.expire_all();return result
def public_config(c):return {**{k:v for k,v in c.items() if k!='rtmp_secret'},'has_rtmp':bool(c.get('rtmp_secret'))}
def user_out(u):return {'id':u.id,'email':u.email,'name':u.name,'admin':u.admin,'plan':u.plan,'expires':u.expires,'account_limit':PLANS.get(u.plan,{}).get('accounts',0),'environment':{'state':u.env_status,'error':u.env_error,'id':u.sandbox}}
def lockuser(s,u):
 from .db import Control
 control=s.get(Control,u.id)
 if control and control.lease>time.time():raise HTTPException(409,'Aguarde a operação em andamento antes de alterar configurações.')
 return s.scalar(select(User).where(User.id==u.id).with_for_update())
def throttle(s,request,email):
 # DB-backed limits shared by all instances; cleaned lazily.
 key=digest((request.client.host if request.client else 'unknown')+'|'+email)
 a=s.scalar(select(Attempt).where(Attempt.id==key).with_for_update());now=time.time()
 if not a:a=Attempt(id=key,count=0,until=now+900);s.add(a)
 if a.until<now:a.count=0;a.until=now+900
 a.count+=1;s.commit()
 if a.count>15:raise HTTPException(429,'Muitas tentativas. Aguarde 15 minutos.')
class Credentials(BaseModel):
 email:str=Field(max_length=254)
 password:str=Field(min_length=10,max_length=128)
 name:str=Field(default='',max_length=80)
 admin_token:str=Field(default='',max_length=128)
 def normalized(self):
  v=self.email.strip().lower()
  if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',v):raise HTTPException(422,'E-mail inválido.')
  return v
def session_response(s,u,response):
 token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(32)
 s.add(Login(token=digest(token),user_id=u.id,expires=time.time()+604800));s.commit()
 # Split deployments must be same-site/custom domains or allow third-party cookies.
 same='none' if FRONT!=PUBLIC and PROD else 'lax'
 response.set_cookie('nexa_session',token,httponly=True,secure=PROD,samesite=same,max_age=604800,path='/')
 response.set_cookie('nexa_csrf',csrf,httponly=True,secure=PROD,samesite=same,max_age=604800,path='/')
 return {'user':user_out(u),'csrf':csrf}
@app.get('/api/health')
def health():return {'status':'ok'}
@app.post('/api/auth/register')
def register(c:Credentials,request:Request,response:Response,s=Depends(db)):
 email=c.normalized();throttle(s,request,email)
 u=User(email=email,name=c.name.strip() or email.split('@')[0],password=password_hash(c.password),admin=(email==os.getenv('ADMIN_EMAIL','').lower() and bool(os.getenv('ADMIN_BOOTSTRAP_TOKEN')) and secrets.compare_digest(c.admin_token,os.getenv('ADMIN_BOOTSTRAP_TOKEN',''))))
 s.add(u)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'Não foi possível criar a conta com este e-mail.')
 return session_response(s,u,response)
@app.post('/api/auth/login')
def login(c:Credentials,request:Request,response:Response,s=Depends(db)):
 email=c.normalized();throttle(s,request,email);u=s.scalar(select(User).where(User.email==email))
 if not u or not password_ok(c.password,u.password):raise HTTPException(401,'E-mail ou senha incorretos.')
 return session_response(s,u,response)
@app.get('/api/me')
def me(request:Request,u=Depends(current)):return {'user':user_out(u),'csrf':request.cookies.get('nexa_csrf')}
@app.post('/api/auth/logout')
def logout(request:Request,response:Response,u=Depends(current),s=Depends(db)):
 s.execute(delete(Login).where(Login.token==digest(request.cookies.get('nexa_session',''))));s.commit()
 response.delete_cookie('nexa_session');response.delete_cookie('nexa_csrf');return {'ok':True}
class PasswordChange(BaseModel):
 old:str=Field(max_length=128)
 new:str=Field(min_length=10,max_length=128)
@app.post('/api/auth/password')
def change(c:PasswordChange,u=Depends(current),s=Depends(db)):
 if not password_ok(c.old,u.password):raise HTTPException(400,'Senha atual incorreta.')
 u.password=password_hash(c.new);s.execute(delete(Login).where(Login.user_id==u.id));s.commit();return {'ok':True}
@app.get('/api/dashboard')
def dashboard(u=Depends(current),s=Depends(db)):
 if u.sandbox and u.env_status=='ready':
  try:
   from .operations import read_status
   read_status(s,u)
  except Exception:s.rollback()
 accounts=s.scalars(select(Account).where(Account.user_id==u.id)).all()
 jobs=s.scalars(select(Job).where(Job.user_id==u.id).order_by(Job.created.desc()).limit(20)).all()
 return {'user':user_out(u),'accounts':[{'id':a.id,'name':a.name,'config':public_config(a.config),'runtime':a.runtime} for a in accounts],'assets':[{'id':a.id,'name':a.name,'kind':a.kind,'size':a.size} for a in s.scalars(select(Asset).where(Asset.user_id==u.id))], 'jobs':[{'id':j.id,'kind':j.kind,'state':j.state,'error':j.error,'created':j.created} for j in jobs],'plans':PLANS}
class Redeem(BaseModel):key:str=Field(min_length=8,max_length=128)
@app.post('/api/plans/redeem')
def redeem(c:Redeem,u=Depends(current),s=Depends(db)):
 u=lockuser(s,u);k=s.scalar(select(Key).where(Key.digest==digest(c.key.strip())).with_for_update())
 if not k or k.used_by:raise HTTPException(400,'Chave inválida ou já utilizada.')
 # Renewal does not reduce account capacity while an existing plan is active.
 if u.expires>time.time() and PLANS[k.plan]['accounts']<PLANS[u.plan]['accounts']:raise HTTPException(400,'Renove com um plano de capacidade igual ou maior.')
 k.used_by=u.id;k.used_at=time.time();u.plan=k.plan;u.expires=max(u.expires,time.time())+k.days*86400
 u.env_status='queued';u.env_error='';return queue(s,u,'provision')
@app.post('/api/environment/retry')
def retry(u=Depends(current),s=Depends(db)):
 active(u);u=lockuser(s,u)
 pending=s.scalar(select(Job).where(Job.user_id==u.id,Job.kind=='provision',Job.state.in_(['queued','running'])))
 if pending:
  id=pending.id;s.commit()
  from .dispatch import process
  return process(id,u.id)
 u.env_status='queued';u.env_error='';return queue(s,u,'provision')
class NewKey(BaseModel):
 plan:str
 count:int=Field(default=1,ge=1,le=100)
@app.post('/api/admin/keys')
def keys(c:NewKey,u=Depends(admin),s=Depends(db)):
 if c.plan not in PLANS:raise HTTPException(422,'Plano inválido.')
 out=[]
 for _ in range(c.count):
  key='NEXA-'+secrets.token_urlsafe(24);out.append(key);s.add(Key(digest=digest(key),plan=c.plan,days=PLANS[c.plan]['days']))
 s.commit();return {'keys':out}
@app.get('/api/admin/users')
def users(u=Depends(admin),s=Depends(db)):return [user_out(x) for x in s.scalars(select(User).order_by(User.email))]
class AccountInput(BaseModel):
 name:str=Field(min_length=1,max_length=80)
 cookies:str=Field(min_length=1,max_length=100000)
@app.post('/api/accounts')
def add(c:AccountInput,u=Depends(current),s=Depends(db)):
 limit=active(u);u=lockuser(s,u)
 if s.scalar(select(func.count()).select_from(Account).where(Account.user_id==u.id))>=limit:raise HTTPException(403,'Limite de contas do plano atingido.')
 try:raw=cookies(c.cookies)
 except (ValueError,KeyError,TypeError):raise HTTPException(422,'Cookies inválidos. Use texto, JSON ou Netscape com sessionid/sid_tt.')
 a=Account(user_id=u.id,name=c.name,secret=seal(raw),config={'title':'Minha live','loop':True,'restart_minutes':360,'restart_delay':30,'desired':'stopped'})
 s.add(a);s.commit();return {'id':a.id}
@app.patch('/api/accounts/{id}')
def edit(id:str,c:AccountInput,u=Depends(current),s=Depends(db)):
 active(u);u=lockuser(s,u);a=owned(s,Account,id,u)
 try:raw=cookies(c.cookies)
 except (ValueError,KeyError,TypeError):raise HTTPException(422,'Cookies inválidos.')
 a.name=c.name;a.secret=seal(raw);s.commit();return {'ok':True}
@app.delete('/api/accounts/{id}')
def remove(id:str,u=Depends(current),s=Depends(db)):
 u=lockuser(s,u);a=owned(s,Account,id,u)
 if a.config.get('desired')=='running':raise HTTPException(409,'Pare a transmissão antes de remover a conta.')
 s.delete(a);s.commit()
 if u.sandbox:return queue(s,u,'sync')
 return {'ok':True}
class Config(BaseModel):
 title:str=Field(default='Minha live',min_length=1,max_length=100)
 topic:str=Field(default='',max_length=80)
 game:str=Field(default='0',max_length=80)
 region:str=Field(default='',max_length=10)
 replay:bool=False
 close_room:bool=True
 mature:bool=False
 cover:str=''
 video:str=''
 rtmp:str=Field(default='',max_length=2000)
 loop:bool=True
 auto_restart:bool=False
 restart_on_crash:bool=False
 restart_minutes:int=Field(default=360,ge=1,le=10080)
 restart_delay:int=Field(default=30,ge=1,le=3600)
def validate_rtmp(url):
 try:p=urlsplit(url);port=p.port or (443 if p.scheme=='rtmps' else 1935)
 except ValueError:raise HTTPException(422,'URL RTMP inválida.')
 host=p.hostname or ''
 # Avoid SSRF to sandbox-local/private hosts: only TikTok ingestion domains are accepted.
 if p.scheme not in ('rtmp','rtmps') or not any(host.endswith('.'+d) for d in ['tiktok.com','tiktokv.com','tiktokcdn.com','tiktokcdn-us.com','byteoversea.com']) or p.username or port not in (1935,443,80):raise HTTPException(422,'Use uma URL RTMP oficial do TikTok (domínio e porta autorizados).')
@app.put('/api/accounts/{id}/config')
def config(id:str,c:Config,u=Depends(current),s=Depends(db)):
 active(u);u=lockuser(s,u);a=owned(s,Account,id,u)
 for asset_id,kind in [(c.video,'video'),(c.cover,'cover')]:
  if asset_id and owned(s,Asset,asset_id,u).kind!=kind:raise HTTPException(422,'Tipo de arquivo inválido.')
 data=c.model_dump();rtmp=data.pop('rtmp')
 if rtmp:validate_rtmp(rtmp);data['rtmp_secret']=seal(rtmp)
 else:data['rtmp_secret']=a.config.get('rtmp_secret','')
 a.config={**a.config,**data};s.commit();return {'ok':True}
@app.get('/api/accounts/{id}/credentials')
def credentials(id:str,u=Depends(current),s=Depends(db)):
 active(u);u=lockuser(s,u);a=owned(s,Account,id,u);url=unseal(a.config['rtmp_secret']) if a.config.get('rtmp_secret') else ''
 base,key=url.rsplit('/',1) if '/' in url else ('','');return {'rtmp':url,'server':base+'/' if base else '', 'key':key,'share_url':a.config.get('share_url','')}
@app.post('/api/accounts/{id}/{action}')
def action(id:str,action:str,u=Depends(current),s=Depends(db)):
 a=owned(s,Account,id,u)
 if action not in ('start','stop','generate','finish','check'):raise HTTPException(404,'Ação desconhecida.')
 if action not in ('stop','finish'):active(u)
 if action=='start':
  if u.env_status!='ready':raise HTTPException(409,'Aguarde a criação do ambiente.')
  if not a.config.get('video') or not a.config.get('rtmp_secret'):raise HTTPException(422,'Escolha um vídeo e configure o RTMP.')
  owned(s,Asset,a.config['video'],u)
 return queue(s,u,action,{'account_id':id})
@app.get('/api/games/{id}')
def games(id:str,u=Depends(current),s=Depends(db)):
 from .tiktok import TikTok
 active(u);u=lockuser(s,u);a=owned(s,Account,id,u)
 try:return TikTok(unseal(a.secret)).games()
 except Exception:raise HTTPException(502,'TikTok não retornou a lista de categorias/jogos.')
@app.post('/api/assets')
def upload(kind:str='video',file:UploadFile=File(...),u=Depends(current),s=Depends(db)):
 active(u)
 if u.env_status!='ready':raise HTTPException(409,'Aguarde a criação do ambiente.')
 if kind not in ('video','cover'):raise HTTPException(422,'Tipo inválido.')
 ext=Path(file.filename or '').suffix.lower()
 if ext not in (['.mp4','.mov','.mkv','.webm'] if kind=='video' else ['.jpg','.jpeg','.png']):raise HTTPException(422,'Formato de arquivo não suportado.')
 limit=3*1024*1024 if os.getenv('VERCEL') else (int(os.getenv('MAX_VIDEO_MB','100'))*1024*1024 if kind=='video' else 5*1024*1024)
 quota=int(os.getenv('MAX_STORAGE_MB','200'))*1024*1024
 u=lockuser(s,u)
 total=s.scalar(select(func.sum(Asset.size)).where(Asset.user_id==u.id)) or 0
 a=Asset(id=uid(),user_id=u.id,name=Path(file.filename or 'arquivo').name,kind=kind,size=0)
 a.path='/home/daytona/nexatok/assets/'+a.id+ext
 with tempfile.NamedTemporaryFile() as tmp:
  size=0
  while chunk:=file.file.read(1024*1024):
   size+=len(chunk)
   if size>limit or size+total>quota:raise HTTPException(413,'Arquivo ou armazenamento excede o limite.')
   tmp.write(chunk)
  if not size:raise HTTPException(422,'Arquivo vazio.')
  tmp.flush()
  try:ds.get(u).fs.upload_file(tmp.name,a.path)
  except Exception:raise HTTPException(502,'Falha no envio para o Railway. Tente novamente.')
 a.size=size;s.add(a);s.commit();return {'id':a.id,'name':a.name}
@app.delete('/api/assets/{id}')
def remove_asset(id:str,u=Depends(current),s=Depends(db)):
 a=owned(s,Asset,id,u)
 for account in s.scalars(select(Account).where(Account.user_id==u.id)):
  if id in (account.config.get('video'),account.config.get('cover')):raise HTTPException(409,'Desvincule o arquivo nas configurações das contas.')
 try:ds.get(u).fs.delete_file(a.path)
 except Exception:raise HTTPException(502,'Falha ao excluir no Railway.')
 s.delete(a);s.commit();return {'ok':True}
@app.post('/api/tiktok/qr')
def qr_start(u=Depends(current),s=Depends(db)):
 from .oauth import start
 active(u);return start(s,u)
@app.get('/api/tiktok/qr/{id}')
def qr_check(id:str,u=Depends(current),s=Depends(db)):
 from .oauth import check
 active(u);return check(s,u,id)
@app.post('/api/operations/resume')
def resume_operations(u=Depends(current)):
 from .dispatch import resume
 return resume(u.id)
@app.post('/api/internal/expired')
def expired(request:Request,payload:dict,s=Depends(db)):
 from .db import Control
 from .dispatch import acquire,release
 from .operations import sync
 id=payload.get('user_id','');u=s.get(User,id);control=s.get(Control,id)
 bearer=request.headers.get('authorization','').removeprefix('Bearer ')
 if not u or not control or not control.callback or not secrets.compare_digest(bearer,unseal(control.callback)):
  raise HTTPException(401,'Não autorizado.')
 token=acquire(s,id)
 try:
  s.refresh(u)
  if u.expires>time.time():
   sync(s,u);return {'state':'renewed'}
  if u.sandbox:
   ds.client().stop(ds.get(u),timeout=30)
  u.env_status='suspended';s.commit();return {'state':'suspended'}
 finally:release(s,id,token)
@app.get('/api/internal/maintenance')
def maintenance(request:Request,s=Depends(db)):
 expected=os.getenv('CRON_SECRET','');got=request.headers.get('authorization','')
 if not expected or not secrets.compare_digest(got,'Bearer '+expected):raise HTTPException(401,'Não autorizado.')
 from .dispatch import acquire,release
 expired=s.scalars(select(User).where(User.expires>0,User.expires<=time.time(),User.sandbox.is_not(None),User.env_status!='suspended').limit(5)).all()
 results=[]
 for u in expired:
  token=None
  try:
   token=acquire(s,u.id);s.refresh(u)
   if u.expires>time.time():continue
   ds.client().stop(ds.get(u),timeout=30);u.env_status='suspended';s.commit();results.append({'id':u.id,'state':'suspended'})
  except Exception:s.rollback();results.append({'id':u.id,'state':'retry_later'})
  finally:
   if token:release(s,u.id,token)
 return {'results':results}
from .uploads import install as install_uploads
install_uploads(app,current,db,active,owned,lockuser)
STATIC=Path(__file__).parent/'static'
app.mount('/static',StaticFiles(directory=STATIC),name='static')
@app.get('/')
def home():return FileResponse(STATIC/'index.html')
