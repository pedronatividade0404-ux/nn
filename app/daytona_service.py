import os
from pathlib import Path
from .diagnostics import stage
from daytona.common.errors import DaytonaNotFoundError, DaytonaConflictError
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams, ListSandboxesQuery

def client():
 if not os.getenv('DAYTONA_API_KEY'): raise RuntimeError('Configure DAYTONA_API_KEY nas variáveis Production do Vercel.')
 return Daytona(DaytonaConfig(api_key=os.environ['DAYTONA_API_KEY'],api_url=os.getenv('DAYTONA_API_URL','https://app.daytona.io/api')))
def get(user):
 if not user.sandbox: raise RuntimeError('Ambiente ainda não disponível.')
 return client().get(user.sandbox)
def find_named(d,user):
 try:s=d.get('nexatok-'+user.id)
 except DaytonaNotFoundError:return None
 if s.name!='nexatok-'+user.id or (s.labels or {}).get('nexatok_user')!=user.id:
  raise ValueError('O ambiente com este nome não possui a identificação esperada do usuário. Revise as etiquetas no Daytona.')
 return s

def create_or_reuse(d,user,snapshot):
 try:
  return d.create(CreateSandboxFromSnapshotParams(name='nexatok-'+user.id,snapshot=snapshot,labels={'nexatok_user':user.id},public=False,auto_stop_interval=0,auto_pause_interval=0),timeout=120)
 except DaytonaConflictError:
  s=find_named(d,user)
  if s is None:raise
  return s

def provision(user):
 if not os.getenv('DAYTONA_API_KEY','').strip():raise ValueError('DAYTONA_API_KEY não definida. Configure em Production no Vercel e faça redeploy.')
 d=stage("autenticar Daytona",client)
 existing=stage('listar ambientes Daytona',lambda:list(d.list(ListSandboxesQuery(labels={'nexatok_user':user.id}))))
 if len(existing)>1: raise RuntimeError('Mais de um sandbox para este usuário; revise no Daytona.')
 s=existing[0] if existing else stage('buscar ambiente pelo nome',lambda:find_named(d,user))
 if s is None:
  snapshot=os.getenv('DAYTONA_SNAPSHOT_'+user.plan.upper()) or os.getenv('DAYTONA_SNAPSHOT')
  if not snapshot: raise RuntimeError('Configure DAYTONA_SNAPSHOT com FFmpeg e Python instalados.')
  s=stage('criar sandbox Daytona',lambda:create_or_reuse(d,user,snapshot))
 if str(s.state).lower().split('.')[-1]!='started':stage('iniciar ambiente existente',lambda:d.start(s))
 r=stage('verificar Python e FFmpeg',lambda:s.process.exec('mkdir -p /home/daytona/nexatok/assets && command -v ffmpeg && command -v python3',timeout=20))
 if r.exit_code!=0: raise ValueError('O snapshot deve conter FFmpeg, Python 3 e /home/daytona gravável.')
 for name in ('agent.py','finalize_upload.py'):
  stage('enviar '+name,lambda:s.fs.upload_file(str(Path(__file__).resolve().parents[1]/'worker'/name),'/home/daytona/nexatok/'+name,timeout=30))
 r=stage('iniciar supervisor',lambda:s.process.exec('cd /home/daytona/nexatok && python3 agent.py --launch',timeout=15))
 if r.exit_code!=0: raise ValueError('Não foi possível iniciar supervisor de transmissão no snapshot.')
 return s
