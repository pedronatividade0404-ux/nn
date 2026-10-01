"""Railway transport. Module name retained for compatibility with existing imports."""
import os,re
from pathlib import Path
from types import SimpleNamespace
import httpx
from .diagnostics import stage
PREFIX='/home/daytona/nexatok/'
class WorkerError(Exception):
 def __init__(self,status):self.status_code=status;super().__init__('Worker request failed')
def request(user,method,path,*,content=None,params=None,json=None,timeout=60):
 url=os.getenv('WORKER_URL','').rstrip('/');token=os.getenv('WORKER_TOKEN','')
 if not url.startswith('https://') or len(token)<32:raise ValueError('Configure WORKER_URL HTTPS e WORKER_TOKEN (mínimo 32 caracteres) no Vercel.')
 with httpx.Client(timeout=timeout,follow_redirects=False) as c:
  r=c.request(method,url+'/v1/users/'+user+path,headers={'Authorization':'Bearer '+token},content=content,params=params,json=json)
 if r.status_code>=400:
  # Never expose arbitrary upstream messages or bodies containing secrets.
  if r.status_code==409:raise ValueError('Worker sem capacidade para esta operação. Pare outra live ou confira o limite de usuários.')
  raise WorkerError(r.status_code)
 return r
class FS:
 def __init__(self,id):self.id=id
 def relative(self,path):
  if not path.startswith(PREFIX):raise ValueError('Caminho inválido.')
  return path[len(PREFIX):]
 def upload_file(self,source,path,timeout=60):
  data=source if isinstance(source,bytes) else Path(source).read_bytes()
  request(self.id,'PUT','/file',content=data,params={'path':self.relative(path)},timeout=timeout)
 def download_file(self,path):return request(self.id,'GET','/file',params={'path':self.relative(path)}).content
 def delete_file(self,path,recursive=False):request(self.id,'DELETE','/file',params={'path':self.relative(path)})
class Process:
 def __init__(self,id):self.id=id
 def exec(self,command,timeout=60):
  if command=='chmod 600 /home/daytona/nexatok/config.pending && mv /home/daytona/nexatok/config.pending /home/daytona/nexatok/config.json':
   r=request(self.id,'POST','/commit',timeout=timeout)
  elif re.fullmatch(r'mkdir -p /home/daytona/nexatok/assets/\.uploads/[a-f0-9]{32}',command):
   r=request(self.id,'POST','/upload-begin',json={'id':command.rsplit('/',1)[1]},timeout=timeout)
  elif re.fullmatch(r'python3 /home/daytona/nexatok/finalize_upload.py [a-f0-9]{32} [0-9]+ \.(mp4|mkv|mov|webm|jpg|jpeg|png)',command):
   _,_,id,size,ext=command.split();r=request(self.id,'POST','/upload-complete',json={'id':id,'size':int(size),'ext':ext},timeout=timeout)
  else:raise ValueError('Comando não permitido no worker.')
  return SimpleNamespace(exit_code=0,result='')
class Environment:
 def __init__(self,id):self.user_id=id;self.id='railway-'+id;self.fs=FS(id);self.process=Process(id)
def get(user):return Environment(user.id)
def provision(user):
 stage('preparar ambiente Railway',lambda:request(user.id,'POST','/provision'))
 return get(user)
def client():return SimpleNamespace(stop=lambda sandbox,**kw:request(sandbox.user_id,'POST','/stop'))
