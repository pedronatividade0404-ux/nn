import os
from pathlib import Path
from daytona import Daytona, DaytonaConfig, CreateSandboxFromSnapshotParams, ListSandboxesQuery

def client():
 if not os.getenv('DAYTONA_API_KEY'): raise RuntimeError('Configure DAYTONA_API_KEY no servidor e no worker.')
 return Daytona(DaytonaConfig(api_key=os.environ['DAYTONA_API_KEY'],api_url=os.getenv('DAYTONA_API_URL','https://app.daytona.io/api')))
def get(user):
 if not user.sandbox: raise RuntimeError('Ambiente ainda não disponível.')
 return client().get(user.sandbox)
def provision(user):
 d=client()
 existing=list(d.list(ListSandboxesQuery(labels={'nexatok_user':user.id})))
 if len(existing)>1: raise RuntimeError('Mais de um sandbox para este usuário; revise no Daytona.')
 if existing:
  s=existing[0]
  if str(s.state).lower().split('.')[-1]!='started':d.start(s)
 else:
  snapshot=os.getenv('DAYTONA_SNAPSHOT_'+user.plan.upper()) or os.getenv('DAYTONA_SNAPSHOT')
  if not snapshot: raise RuntimeError('Configure DAYTONA_SNAPSHOT com FFmpeg e Python instalados.')
  s=d.create(CreateSandboxFromSnapshotParams(name='nexatok-'+user.id, snapshot=snapshot, labels={'nexatok_user':user.id}, public=False,auto_stop_interval=0,auto_pause_interval=0),timeout=120)
 r=s.process.exec('mkdir -p /home/daytona/nexatok/assets && command -v ffmpeg && command -v python3',timeout=20)
 if r.exit_code!=0: raise RuntimeError('O snapshot deve conter FFmpeg e Python 3.')
 for name in ('agent.py','finalize_upload.py'):
  s.fs.upload_file(str(Path(__file__).resolve().parents[1]/'worker'/name),'/home/daytona/nexatok/'+name,timeout=30)
 r=s.process.exec('cd /home/daytona/nexatok && python3 agent.py --launch',timeout=15)
 if r.exit_code!=0: raise RuntimeError('Não foi possível iniciar supervisor de transmissão.')
 return s
