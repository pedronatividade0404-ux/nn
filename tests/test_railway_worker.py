import os,importlib,json,time,sys,subprocess
from types import SimpleNamespace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

@pytest.fixture
def worker(tmp_path,monkeypatch):
 monkeypatch.setenv('WORKER_TOKEN','test-worker-secret-with-at-least-32-characters')
 monkeypatch.setenv('WORKER_DATA_DIR',str(tmp_path))
 import worker.server as server
 server=importlib.reload(server)
 # Keep endpoint tests focused on validation; supervisor tested separately.
 def ensure(id):
  folder=server.tenant(id);(folder/'assets').mkdir(parents=True,exist_ok=True);return folder
 monkeypatch.setattr(server,'ensure',ensure)
 c=TestClient(server.app)
 c.headers['Authorization']='Bearer '+os.environ['WORKER_TOKEN']
 return c,server,tmp_path

def test_auth_and_traversal(worker):
 c,s,root=worker;id='a'*32
 assert c.get('/health').status_code==200
 assert c.post('/v1/users/'+id+'/provision',headers={'Authorization':'wrong'}).status_code==401
 assert c.post('/v1/users/invalid/provision').status_code==422
 assert c.put('/v1/users/'+id+'/file',params={'path':'../config.json'},content=b'bad').status_code==422
 assert c.put('/v1/users/'+id+'/file',params={'path':'status.json'},content=b'bad').status_code==422

def test_chunk_assembly_and_tenant_isolation(worker):
 c,s,root=worker;id='a'*32;asset='c'*32
 assert c.post('/v1/users/'+id+'/upload-begin',json={'id':asset}).status_code==200
 for offset,data in [(0,b'abc'),(3,b'def')]:
  assert c.put('/v1/users/'+id+'/file',params={'path':'assets/.uploads/'+asset+'/'+str(offset)},content=data).status_code==200
 r=c.post('/v1/users/'+id+'/upload-complete',json={'id':asset,'size':6,'ext':'.mp4'});assert r.status_code==200,r.text
 assert c.get('/v1/users/'+id+'/file',params={'path':'assets/'+asset+'.mp4'}).content==b'abcdef'
 assert c.get('/v1/users/'+'b'*32+'/file',params={'path':'assets/'+asset+'.mp4'}).status_code==404
 # Finalization idempotent and no shell injection.
 assert c.post('/v1/users/'+id+'/upload-complete',json={'id':asset,'size':6,'ext':'.mp4'}).status_code==200
 assert c.post('/v1/users/'+id+'/upload-complete',json={'id':asset,'size':6,'ext':'.mp4;echo'}).status_code==422

def test_configuration_and_stop_do_not_affect_other_tenant(worker):
 c,s,root=worker;id='a'*32;other='b'*32;account='c'*32
 config={'expires':time.time()+100,'expiry_token':'private','expiry_url':'https://example.com','accounts':{account:{'desired':'running','rtmp':'rtmp://push.tiktok.com/key','video':'','revision':1}}}
 for owner in (id,other):
  assert c.put('/v1/users/'+owner+'/file',params={'path':'config.pending'},content=json.dumps(config).encode()).status_code==200
  assert c.post('/v1/users/'+owner+'/commit').status_code==200
 assert 'expiry_token' not in json.loads((root/'users'/id/'config.json').read_text())
 assert c.post('/v1/users/'+id+'/stop').status_code==200
 assert json.loads((root/'users'/id/'config.json').read_text())['accounts'][account]['desired']=='stopped'
 assert json.loads((root/'users'/other/'config.json').read_text())['accounts'][account]['desired']=='running'
 config['accounts'][account]['rtmp']='rtmp://127.0.0.1/key'
 c.put('/v1/users/'+id+'/file',params={'path':'config.pending'},content=json.dumps(config).encode())
 assert c.post('/v1/users/'+id+'/commit').status_code==422

def test_adapter_only_permits_fixed_operations(monkeypatch):
 from app import daytona_service as ds
 calls=[]
 def request(id,method,path,**kwargs):calls.append((id,method,path,kwargs));return SimpleNamespace(content=b'{}')
 monkeypatch.setattr(ds,'request',request)
 env=ds.get(SimpleNamespace(id='a'*32,sandbox='old-daytona-id'))
 assert env.id=='railway-'+'a'*32
 env.process.exec('mkdir -p /home/daytona/nexatok/assets/.uploads/'+'b'*32)
 assert calls[-1][2]=='/upload-begin'
 with pytest.raises(ValueError):env.process.exec('rm -rf /')
 with pytest.raises(ValueError):env.fs.upload_file(b'x','/etc/passwd')

def test_global_stream_capacity_and_release(tmp_path):
 folders=[tmp_path/'user1',tmp_path/'user2'];slots=tmp_path/'slots';bin=tmp_path/'bin';bin.mkdir()
 fake=bin/'ffmpeg';fake.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n');fake.chmod(0o755)
 procs=[]
 def wait(folder,state):
  end=time.time()+15
  while time.time()<end:
   try:
    if json.loads((folder/'status.json').read_text())['accounts']['a']['state']==state:return
   except (OSError,KeyError,ValueError):pass
   time.sleep(.1)
  raise AssertionError(state)
 try:
  for folder in folders:
   (folder/'assets').mkdir(parents=True);(folder/'assets'/'video.mp4').write_bytes(b'x')
   (folder/'config.json').write_text(json.dumps({'expires':time.time()+60,'accounts':{'a':{'desired':'running','video':'video.mp4','rtmp':'rtmp://push.tiktok.com/key'}}}))
  env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH'],'NEXATOK_SLOT_DIR':str(slots),'MAX_CONCURRENT_STREAMS':'1'}
  for i,folder in enumerate(folders):
   procs.append(subprocess.Popen([sys.executable,str(Path('worker/agent.py').resolve())],env={**env,'NEXATOK_AGENT_ROOT':str(folder)}))
   wait(folder,'running' if i==0 else 'waiting_capacity')
  procs[0].terminate();procs[0].wait(timeout=12)
  wait(folders[1],'running')
 finally:
  for p in procs:
   if p.poll() is None:p.terminate();p.wait(timeout=12)

def test_transport_roundtrip_with_real_worker_routes(worker,monkeypatch):
 from app import daytona_service as ds
 c,server,root=worker;id='a'*32;asset='d'*32
 monkeypatch.setenv('WORKER_URL','https://worker.example.test')
 class Transport:
  def __init__(self,**kwargs):pass
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def request(self,method,url,**kwargs):return c.request(method,url,**kwargs)
 monkeypatch.setattr(ds.httpx,'Client',Transport)
 env=ds.provision(SimpleNamespace(id=id))
 env.process.exec('mkdir -p /home/daytona/nexatok/assets/.uploads/'+asset)
 env.fs.upload_file(b'test','/home/daytona/nexatok/assets/.uploads/'+asset+'/0')
 env.process.exec('python3 /home/daytona/nexatok/finalize_upload.py '+asset+' 4 .mp4')
 assert env.fs.download_file('/home/daytona/nexatok/assets/'+asset+'.mp4')==b'test'
 cfg={'expires':time.time()+100,'accounts':{}}
 env.fs.upload_file(json.dumps(cfg).encode(),'/home/daytona/nexatok/config.pending')
 env.process.exec('chmod 600 /home/daytona/nexatok/config.pending && mv /home/daytona/nexatok/config.pending /home/daytona/nexatok/config.json')
 ds.client().stop(env)
 assert json.loads((root/'users'/id/'config.json').read_text())['expires']==0

def test_ffmpeg_error_tail_redacts_stream_key(tmp_path):
 folder=tmp_path/'user';(folder/'assets').mkdir(parents=True);(folder/'assets'/'video.mp4').write_bytes(b'x')
 bin=tmp_path/'bin';bin.mkdir();fake=bin/'ffmpeg'
 fake.write_text('#!/usr/bin/env python3\nimport sys\nprint("Connection timed out rtmp://push.tiktok.com/live/PRIVATEKEY",file=sys.stderr)\nsys.exit(1)\n');fake.chmod(0o755)
 (folder/'config.json').write_text(json.dumps({'expires':time.time()+60,'accounts':{'a':{'desired':'running','video':'video.mp4','rtmp':'rtmp://push.tiktok.com/live/PRIVATEKEY'}}}))
 env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ['PATH'],'NEXATOK_AGENT_ROOT':str(folder),'NEXATOK_SLOT_DIR':str(tmp_path/'slots')}
 p=subprocess.Popen([sys.executable,str(Path('worker/agent.py').resolve())],env=env)
 try:
  end=time.time()+10
  while time.time()<end:
   try:status=json.loads((folder/'status.json').read_text())['accounts']['a']
   except (OSError,KeyError,ValueError):time.sleep(.1);continue
   if status['state']=='error':
    assert 'Connection timed out' in status['error'];assert 'PRIVATEKEY' not in status['error'];return
   time.sleep(.1)
  raise AssertionError('Missing error detail')
 finally:p.terminate();p.wait(timeout=12)

def test_real_worker_lifecycle_restores_volume_and_stops_on_expiry(tmp_path,monkeypatch):
 monkeypatch.setenv('WORKER_TOKEN','lifecycle-secret-at-least-32-characters')
 monkeypatch.setenv('WORKER_DATA_DIR',str(tmp_path/'data'))
 bin=tmp_path/'bin';bin.mkdir();fake=bin/'ffmpeg'
 fake.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n');fake.chmod(0o755)
 monkeypatch.setenv('PATH',str(bin)+os.pathsep+os.environ['PATH'])
 import worker.server as server
 server=importlib.reload(server);owner='a'*32;account='b'*32;asset='c'*32
 headers={'Authorization':'Bearer '+os.environ['WORKER_TOKEN']}
 def status(c,state,newer=0):
  end=time.time()+12
  while time.time()<end:
   j=c.get('/v1/users/'+owner+'/file',params={'path':'status.json'},headers=headers).json()
   if j.get('heartbeat',0)>newer and j.get('accounts',{}).get(account,{}).get('state')==state:return j
   time.sleep(.1)
  raise AssertionError(state)
 cfg={'expires':time.time()+60,'accounts':{account:{'desired':'running','video':asset+'.mp4','rtmp':'rtmp://push.tiktok.com/key','revision':1}}}
 def commit(c):
  assert c.put('/v1/users/'+owner+'/file',params={'path':'config.pending'},content=json.dumps(cfg).encode(),headers=headers).status_code==200
  assert c.post('/v1/users/'+owner+'/commit',headers=headers).status_code==200
 with TestClient(server.app) as c:
  assert c.post('/v1/users/'+owner+'/provision',headers=headers).status_code==200
  c.put('/v1/users/'+owner+'/file',params={'path':'assets/'+asset+'.mp4'},content=b'video',headers=headers)
  commit(c);status(c,'running')
 assert all(p.poll() is not None for p in server.children.values())
 restart=time.time();server=importlib.reload(server)
 with TestClient(server.app) as c:
  status(c,'running',restart)
  cfg['expires']=time.time()-1;commit(c);status(c,'stopped')
