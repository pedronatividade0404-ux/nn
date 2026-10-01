"""Supervisor isolado por cliente. Não depende do navegador ou do servidor web."""
import os,json,time,subprocess,signal,sys,tempfile,fcntl,urllib.request,shutil,re,threading
from collections import deque
from urllib.parse import urlsplit,unquote
from pathlib import Path
ROOT=Path(os.getenv('NEXATOK_AGENT_ROOT','/home/daytona/nexatok'))
ROOT.mkdir(parents=True,exist_ok=True)
def atomic(path,data):
 p=path.with_suffix('.tmp');p.write_text(json.dumps(data));os.chmod(p,0o600);p.replace(path)
def load(path,default):
 try:return json.loads(path.read_text())
 except (OSError,ValueError):return default
if '--launch' in sys.argv:
 lock=open(ROOT/'agent.lock','a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:sys.exit(0)
 fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
 subprocess.Popen([sys.executable,__file__],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 for _ in range(30):
  if (ROOT/'status.json').exists():break
  time.sleep(.1)
 sys.exit(0)
lock=open(ROOT/'agent.lock','a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:sys.exit(0)
procs={}; states={}; retries={}; starts={}; revisions={}; errors={}
def capture_errors(pipe,tail,rtmp):
 try:
  parts=urlsplit(rtmp)
  secrets=[rtmp,parts.path,parts.path.rsplit('/',1)[-1],parts.query]
  secrets+=[unquote(x) for x in secrets if x]
  for raw in pipe:
   line=raw.decode('utf-8',errors='replace').strip()
   for secret in sorted(set(secrets),key=len,reverse=True):
    if secret:line=line.replace(secret,'[oculto]')
   line=re.sub(r'(?:rtmps?|https?)://\S+','[URL oculta]',line)
   line=re.sub(r'(?i)(?:bearer\s+\S+|(?:token|stream_key|sessionid|cookie)\s*[:=]\s*[^\s;,]+)','[credencial oculta]',line)
   line=re.sub(r'[A-Za-z0-9_=-]{24,}','[identificador oculto]',line)
   line=re.sub(r'[\x00-\x1f<>]',' ',line)
   if line:tail.append(line[:400])
 finally:pipe.close()

running=True
next_expiry_attempt=0
next_cleanup=0
def shutdown(signum,frame):
 global running
 running=False
signal.signal(signal.SIGTERM,shutdown)
signal.signal(signal.SIGINT,shutdown)
def stop(k):
 p=procs.pop(k,None)
 if p and p.poll() is None:
  p.terminate()
  try:p.wait(timeout=8)
  except subprocess.TimeoutExpired:p.kill();p.wait()
try:
 while running:
  cfg=load(ROOT/'config.json',{'expires':0,'accounts':{}}); now=time.time(); active=cfg.get('expires',0)>now
  if now>=next_cleanup:
   next_cleanup=now+3600
   uploads=ROOT/'assets'/'.uploads'
   if uploads.exists():
    for directory in uploads.iterdir():
     if directory.is_dir():
      recent=max([directory.stat().st_mtime]+[p.stat().st_mtime for p in directory.iterdir()])
      if now-recent>172800:shutil.rmtree(directory,ignore_errors=True)
  accounts=cfg.get('accounts',{})
  for k in list(procs):
   if k not in accounts:stop(k);states.pop(k,None)
  for k,c in accounts.items():
   p=procs.get(k); status=states.setdefault(k,{'state':'stopped','error':''})
   desired=active and c.get('desired')=='running'
   rev=c.get('revision',0)
   if revisions.get(k)!=rev:
    stop(k);p=None;retries[k]=0;revisions[k]=rev;status.update(state='stopped',error='')
   if not desired:
    stop(k);status.update(state='stopped',started_at=None);continue
   if p and p.poll() is not None:
    code=p.returncode;procs.pop(k);p=None
    detail=' | '.join(list(errors.get(k,[]))[-3:])
    status.update(state='error' if code else 'finished',error=('FFmpeg encerrou com código '+str(code)+('. '+detail if detail else '')) if code else '')
    if code and c.get('restart_on_crash'):retries[k]=now+c.get('restart_delay',30);status['state']='restarting'
    else:retries[k]=float('inf')
   if p and c.get('auto_restart') and now-starts[k]>=c.get('restart_minutes',360)*60:
    stop(k);p=None;retries[k]=now+c.get('restart_delay',30);status['state']='restarting'
   if not p and now>=retries.get(k,0):
    path=ROOT/'assets'/c.get('video','')
    if not path.is_file():status.update(state='error',error='Vídeo não encontrado.');retries[k]=float('inf');continue
    args=['ffmpeg','-hide_banner','-loglevel','error','-re']
    if c.get('loop',True):args+=['-stream_loop','-1']
    args+=['-i',str(path),'-c:v','libx264','-preset','veryfast','-b:v','2500k','-maxrate','2500k','-bufsize','5000k','-pix_fmt','yuv420p','-g','60','-c:a','aac','-b:a','128k','-ar','44100','-f','flv',c['rtmp']]
    try:
     tail=deque(maxlen=8);errors[k]=tail
     procs[k]=subprocess.Popen(args,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
     threading.Thread(target=capture_errors,args=(procs[k].stderr,tail,c['rtmp']),daemon=True).start()
     starts[k]=now;status.update(state='starting',started_at=now,error='')
    except OSError:status.update(state='error',error='Não foi possível executar FFmpeg.');retries[k]=now+30
   elif p and now-starts[k]>2:status['state']='running'
  atomic(ROOT/'status.json',{'heartbeat':now,'accounts':states,'version':3})
  # Local deadline stops FFmpeg even if the Vercel callback is unreachable.
  # A scoped callback asks the backend to stop the sandbox, reducing compute billing.
  if not active and cfg.get('expires',0)>0 and cfg.get('expiry_url') and now>=next_expiry_attempt:
   next_expiry_attempt=now+60
   try:
    req=urllib.request.Request(cfg['expiry_url'],data=json.dumps({'user_id':cfg['user_id']}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+cfg['expiry_token']},method='POST')
    with urllib.request.urlopen(req,timeout=40) as response:response.read()
   except Exception:pass
  time.sleep(2)
finally:
 for k in list(procs):stop(k)
