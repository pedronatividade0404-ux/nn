"""Authenticated file/config API. No shell commands or customer code are accepted."""
import os,re,json,time,sys,signal,subprocess,threading,shutil,secrets
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI,Depends,Header,HTTPException,Request
from fastapi.responses import Response
from pydantic import BaseModel,Field
ROOT=Path(os.getenv('WORKER_DATA_DIR','/data'));ROOT.mkdir(parents=True,exist_ok=True)
TOKEN=os.getenv('WORKER_TOKEN','');MAX_USERS=int(os.getenv('MAX_WORKER_USERS','10'))
if len(TOKEN)<32:raise RuntimeError('Set WORKER_TOKEN to at least 32 characters')
children={};mutex=threading.RLock();halt=threading.Event()
def auth(authorization:str=Header(default='')):
 if not secrets.compare_digest(authorization,'Bearer '+TOKEN):raise HTTPException(401,'Unauthorized')
def tenant(id):
 if not re.fullmatch('[a-f0-9]{32}',id):raise HTTPException(422,'Invalid user')
 return ROOT/'users'/id
def atomic(path,data):
 path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
 tmp.write_bytes(data);tmp.chmod(0o600);tmp.replace(path)
def ensure(id):
 folder=tenant(id)
 with mutex:
  if not folder.exists():
   count=len(list((ROOT/'users').glob('*'))) if (ROOT/'users').exists() else 0
   if count>=MAX_USERS:raise HTTPException(409,'User capacity reached')
   (folder/'assets').mkdir(parents=True)
  if id not in children or children[id].poll() is not None:
   env={**os.environ,'NEXATOK_AGENT_ROOT':str(folder),'NEXATOK_SLOT_DIR':str(ROOT/'slots')}
   children[id]=subprocess.Popen([sys.executable,str(Path(__file__).with_name('agent.py'))],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 return folder
def monitor():
 while not halt.wait(5):
  for folder in (ROOT/'users').glob('*'):
   if folder.is_dir() and re.fullmatch('[a-f0-9]{32}',folder.name):ensure(folder.name)
@asynccontextmanager
async def lifespan(app):
 for p in (ROOT/'users').glob('*'):
  if p.is_dir() and re.fullmatch('[a-f0-9]{32}',p.name):ensure(p.name)
 thread=threading.Thread(target=monitor,daemon=True);thread.start()
 yield
 halt.set();thread.join(timeout=6)
 for p in list(children.values()):
  if p.poll() is None:p.terminate()
 for p in list(children.values()):
  try:p.wait(timeout=12)
  except subprocess.TimeoutExpired:p.kill();p.wait()
app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
@app.get('/health')
def health():return {'ok':True}
@app.post('/v1/users/{id}/provision',dependencies=[Depends(auth)])
def provision(id:str):ensure(id);return {'ok':True}
def safe_path(id,path,write=False):
 folder=tenant(id)
 allowed=bool(re.fullmatch(r'assets/(?:[a-f0-9]{32}\.(?:mp4|mkv|mov|webm|jpg|jpeg|png)|\.uploads/[a-f0-9]{32}(?:/[0-9]{1,12})?)',path))
 if path in ('config.pending','status.json'):allowed=True
 if not allowed or (write and path=='status.json'):raise HTTPException(422,'Invalid path')
 dest=folder/path
 if not dest.resolve().is_relative_to(folder.resolve()):raise HTTPException(422,'Invalid path')
 return dest
@app.put('/v1/users/{id}/file',dependencies=[Depends(auth)])
async def upload(id:str,path:str,request:Request):
 ensure(id);dest=safe_path(id,path,True);size=0;pieces=[]
 async for chunk in request.stream():
  size+=len(chunk)
  if size>6*1024*1024:raise HTTPException(413,'Chunk too large')
  pieces.append(chunk)
 with mutex:
  used=sum(p.stat().st_size for p in (ROOT/'users').rglob('*') if p.is_file())
  old=dest.stat().st_size if dest.is_file() else 0
  if used-old+size>int(os.getenv('MAX_DATA_MB','300'))*1024*1024 or shutil.disk_usage(ROOT).free<size+110*1024*1024:raise HTTPException(413,'Worker storage capacity reached')
  atomic(dest,b''.join(pieces))
 return {'ok':True}
@app.get('/v1/users/{id}/file',dependencies=[Depends(auth)])
def download(id:str,path:str):
 folder=ensure(id);dest=safe_path(id,path)
 if path=='status.json' and not dest.exists():return Response(json.dumps({'version':4,'heartbeat':0,'accounts':{}}),media_type='application/json')
 if not dest.is_file():raise HTTPException(404,'Not found')
 if dest.stat().st_size>6*1024*1024:raise HTTPException(413,'Only small downloads are supported')
 return Response(dest.read_bytes(),media_type='application/octet-stream')
@app.delete('/v1/users/{id}/file',dependencies=[Depends(auth)])
def delete(id:str,path:str):
 if not path.startswith('assets/'):raise HTTPException(422,'Invalid path')
 dest=safe_path(id,path)
 if dest.is_dir():shutil.rmtree(dest)
 else:dest.unlink(missing_ok=True)
 return {'ok':True}
def validate_config(cfg):
 if not isinstance(cfg,dict) or not isinstance(cfg.get('expires'),(int,float)) or not isinstance(cfg.get('accounts'),dict):raise HTTPException(422,'Invalid config')
 if len(cfg['accounts'])>5:raise HTTPException(422,'Account capacity')
 for key,c in cfg['accounts'].items():
  if not re.fullmatch('[a-f0-9]{32}',key) or not isinstance(c,dict):raise HTTPException(422,'Invalid account')
  if c.get('video') and not re.fullmatch(r'[a-f0-9]{32}\.(mp4|mkv|mov|webm)',c['video']):raise HTTPException(422,'Invalid video')
  if c.get('desired') not in ('running','stopped',None):raise HTTPException(422,'Invalid state')
  from urllib.parse import urlsplit
  url=c.get('rtmp','')
  if url:
   try:p=urlsplit(url);port=p.port or (443 if p.scheme=='rtmps' else 1935)
   except ValueError:raise HTTPException(422,'Invalid RTMP')
   if p.scheme not in ('rtmp','rtmps') or p.username or port not in (1935,443,80) or not any((p.hostname or '').endswith('.'+d) for d in ('tiktok.com','tiktokv.com','tiktokcdn.com','tiktokcdn-us.com','byteoversea.com')):raise HTTPException(422,'Invalid RTMP')
 # Expiry is enforced locally; no outbound callback needed for a shared worker.
 cfg.pop('expiry_url',None);cfg.pop('expiry_token',None)
 return cfg
@app.post('/v1/users/{id}/commit',dependencies=[Depends(auth)])
def commit(id:str):
 folder=ensure(id)
 with mutex:
  try:cfg=validate_config(json.loads((folder/'config.pending').read_text()))
  except (OSError,ValueError):raise HTTPException(422,'Invalid config')
  atomic(folder/'config.json',json.dumps(cfg).encode());(folder/'config.pending').unlink(missing_ok=True)
 return {'ok':True}
@app.post('/v1/users/{id}/stop',dependencies=[Depends(auth)])
def stop(id:str):
 folder=tenant(id)
 with mutex:
  try:cfg=json.loads((folder/'config.json').read_text())
  except (OSError,ValueError):return {'ok':True}
  for c in cfg.get('accounts',{}).values():c.update(desired='stopped',revision=c.get('revision',0)+1)
  cfg['expires']=0;atomic(folder/'config.json',json.dumps(cfg).encode())
 return {'ok':True}
class Upload(BaseModel):
 id:str=Field(pattern='^[a-f0-9]{32}$')
@app.post('/v1/users/{id}/upload-begin',dependencies=[Depends(auth)])
def begin(id:str,data:Upload):
 (ensure(id)/'assets'/'.uploads'/data.id).mkdir(parents=True,exist_ok=True);return {'ok':True}
class Complete(Upload):
 size:int=Field(gt=0,le=100*1024*1024)
 ext:str=Field(pattern=r'^\.(mp4|mkv|mov|webm|jpg|jpeg|png)$')
@app.post('/v1/users/{id}/upload-complete',dependencies=[Depends(auth)])
def complete(id:str,data:Complete):
 folder=ensure(id)
 with mutex:
  result=subprocess.run([sys.executable,str(Path(__file__).with_name('finalize_upload.py')),data.id,str(data.size),data.ext],env={**os.environ,'NEXATOK_AGENT_ROOT':str(folder)},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
 if result.returncode:raise HTTPException(422,'Incomplete upload')
 return {'ok':True}
