import time,json
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import select
from app.db import Session,User,Control,Upload,Asset,Job,Account
from app.security import seal
from app.dispatch import process
from test_app import signup,activate,account
import pytest
class Cloud:
 id='fake-sandbox'
 def __init__(self):
  self.files={};self.uploads=[];self.commands=[]
  self.fs=self;self.process=self
 def upload_file(self,src,dst,timeout=None):
  data=src if isinstance(src,bytes) else Path(src).read_bytes()
  self.files[dst]=data;self.uploads.append((dst,len(data)))
 def download_file(self,path):return json.dumps({'heartbeat':time.time(),'accounts':{}}).encode()
 def exec(self,cmd,**kwargs):self.commands.append(cmd);return SimpleNamespace(exit_code=0)
 def delete_file(self,path,**kwargs):pass
@pytest.fixture
def cloud(monkeypatch):
 fake=Cloud();monkeypatch.setattr('app.operations.provision',lambda u:fake)
 monkeypatch.setattr('app.operations.get',lambda u:fake)
 monkeypatch.setattr('app.daytona_service.get',lambda u:fake)
 return fake
@pytest.fixture
def creator(cloud):
 c=signup('cloud'+str(time.time_ns())+'@example.com');activate(c);return c

def test_activate_runs_without_worker_and_scoped_expiry(creator,cloud):
 u=creator.get('/api/me').json()['user'];assert u['environment']['state']=='ready'
 with Session() as s:
  j=s.scalar(select(Job).where(Job.user_id==u['id']));assert j.state=='done'
 cfg=json.loads(cloud.files['/home/daytona/nexatok/config.pending'])
 assert cfg['user_id']==u['id'];assert cfg['expiry_token'];assert 'DAYTONA_API_KEY' not in json.dumps(cfg)

def test_per_user_lease_blocks_duplicate_operation(creator):
 id=creator.get('/api/me').json()['user']['id']
 with Session() as s:
  ctrl=s.get(Control,id);ctrl.lease=time.time()+60;ctrl.owner='other';s.commit()
 assert creator.post('/api/environment/retry').status_code==409
 with Session() as s:ctrl=s.get(Control,id);ctrl.lease=0;s.commit()

def test_chunk_upload_large_video_retries_and_completion(creator,cloud):
 r=creator.post('/api/uploads',json={'name':'movie.mp4','size':4*1024*1024,'kind':'video'});assert r.status_code==200,r.text
 id=r.json()['id'];size=r.json()['chunk_size'];b=b'x'*size
 r=creator.post('/api/uploads/'+id+'/chunk?offset=0',files={'file':('part.bin',b)});assert r.status_code==200,r.text
 assert r.json()['offset']==size
 assert creator.post('/api/uploads/'+id+'/chunk?offset=0',files={'file':('part.bin',b)}).status_code==200
 assert creator.post('/api/uploads/'+id+'/chunk?offset=0',files={'file':('part.bin',b'y'*size)}).status_code==409
 assert creator.post('/api/uploads/'+id+'/complete').status_code==409
 r=creator.post('/api/uploads/'+id+'/chunk?offset='+str(size),files={'file':('part.bin',b'z'*1024*1024)});assert r.status_code==200
 assert creator.post('/api/uploads/'+id+'/complete').status_code==200
 assert creator.post('/api/uploads/'+id+'/complete').status_code==200
 with Session() as s:assert s.get(Upload,id) is None;assert s.get(Asset,id).size==4*1024*1024
 assert creator.get('/api/dashboard').json()['assets'][0]['id']==id

def test_expiry_callback_auth_and_authoritative_renewal(creator,cloud,monkeypatch):
 id=creator.get('/api/me').json()['user']['id']
 from app.security import unseal
 with Session() as s:token=unseal(s.get(Control,id).callback)
 # Backend callback is a machine request with no session cookies or CSRF cookie.
 from fastapi.testclient import TestClient
 from app.main import app
 c=TestClient(app)
 assert c.post('/api/internal/expired',json={'user_id':id},headers={'Authorization':'Bearer wrong'}).status_code==401
 r=c.post('/api/internal/expired',json={'user_id':id},headers={'Authorization':'Bearer '+token});assert r.status_code==200;assert r.json()['state']=='renewed'
 stopped=[];monkeypatch.setattr('app.daytona_service.client',lambda:SimpleNamespace(stop=lambda sandbox,**k:stopped.append(sandbox.id)))
 with Session() as s:u=s.get(User,id);u.expires=time.time()-10;s.commit()
 r=c.post('/api/internal/expired',json={'user_id':id},headers={'Authorization':'Bearer '+token});assert r.status_code==200;assert r.json()['state']=='suspended';assert stopped==['fake-sandbox']

def test_resume_interrupted_nonidempotent_job_does_not_repeat(creator,monkeypatch):
 id=creator.get('/api/me').json()['user']['id']
 with Session() as s:
  job=Job(user_id=id,kind='generate',state='running',lease=time.time()-1,payload={});s.add(job);s.commit();jid=job.id
 def fail(*a):raise AssertionError('Must not repeat room creation')
 monkeypatch.setattr('app.operations.execute',fail)
 r=creator.post('/api/operations/resume');assert r.status_code==200;assert r.json()['state']=='error';assert 'incerto' in r.json()['error']

def test_upload_session_isolation(creator,cloud):
 r=creator.post('/api/uploads',json={'name':'a.mp4','size':10,'kind':'video'});id=r.json()['id']
 other=signup('isolated'+str(time.time_ns())+'@example.com');activate(other)
 assert other.get('/api/uploads/'+id).status_code==404
 assert other.post('/api/uploads/'+id+'/chunk?offset=0',files={'file':('part.bin',b'0123456789')}).status_code==404

def test_daily_maintenance_auth_and_stop(creator,monkeypatch):
 import os
 from fastapi.testclient import TestClient
 from app.main import app
 os.environ['CRON_SECRET']='maintenance-secret'
 id=creator.get('/api/me').json()['user']['id']
 with Session() as s:u=s.get(User,id);u.expires=time.time()-1;s.commit()
 stopped=[];monkeypatch.setattr('app.daytona_service.client',lambda:SimpleNamespace(stop=lambda sandbox,**k:stopped.append(sandbox.id)))
 c=TestClient(app);assert c.get('/api/internal/maintenance').status_code==401
 r=c.get('/api/internal/maintenance',headers={'Authorization':'Bearer maintenance-secret'});assert r.status_code==200
 assert stopped;assert any(x['id']==id for x in r.json()['results'])

def test_vercel_entrypoint_and_static_routes():
 import index,tomllib
 from fastapi.testclient import TestClient
 config=tomllib.loads(Path('pyproject.toml').read_text())
 assert config['tool']['vercel']['entrypoint']=='index:app'
 with TestClient(index.app) as c:
  assert c.get('/api/health').json()=={'status':'ok'}
  assert 'NexaTok' in c.get('/').text
  assert c.get('/static/app.js').status_code==200
  assert c.get('/static/style.css').status_code==200

def test_upload_finalizer_assembles_actual_chunks_and_is_idempotent(tmp_path):
 import os,subprocess,sys
 id='a'*32;folder=tmp_path/'assets'/'.uploads'/id;folder.mkdir(parents=True)
 (folder/'0').write_bytes(b'first-part');(folder/'10').write_bytes(b'second-part')
 env={**os.environ,'NEXATOK_AGENT_ROOT':str(tmp_path)}
 script=Path('worker/finalize_upload.py').resolve()
 cmd=[sys.executable,str(script),id,'21','.mp4']
 subprocess.run(cmd,env=env,check=True)
 assert (tmp_path/'assets'/(id+'.mp4')).read_bytes()==b'first-partsecond-part'
 assert not folder.exists()
 subprocess.run(cmd,env=env,check=True)

def test_provider_diagnostics_never_log_exception_secrets(caplog):
 from app.diagnostics import StageError,report
 from daytona.common.errors import DaytonaForbiddenError
 e=DaytonaForbiddenError('Authorization: Bearer TOP_SECRET cookie=sessionid=PRIVATE',status_code=403)
 message=report(StageError('listar ambientes Daytona',e),'reference-id')
 assert 'HTTP 403' in message;assert 'listar ambientes' in message
 assert 'TOP_SECRET' not in caplog.text+message
 assert 'PRIVATE' not in caplog.text+message
 assert 'NEXATOK_JOB_FAILED' in caplog.text
