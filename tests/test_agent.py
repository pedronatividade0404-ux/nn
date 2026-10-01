import os,time,json,subprocess,sys
from pathlib import Path

def test_real_supervisor_start_manual_stop_and_expiry(tmp_path):
 root=tmp_path/'studio';(root/'assets').mkdir(parents=True);(root/'assets'/'video.mp4').write_bytes(b'test')
 bin=tmp_path/'bin';bin.mkdir();fake=bin/'ffmpeg'
 fake.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n');fake.chmod(0o755)
 env={**os.environ,'NEXATOK_AGENT_ROOT':str(root),'PATH':str(bin)+os.pathsep+os.environ['PATH']}
 cfg={'expires':time.time()+60,'accounts':{'a':{'desired':'running','revision':1,'rtmp':'rtmp://push.tiktok.com/key','video':'video.mp4','loop':True,'restart_on_crash':True}}}
 def write():
  p=root/'config.pending';p.write_text(json.dumps(cfg));p.replace(root/'config.json')
 def wait_state(state):
  end=time.time()+10
  while time.time()<end:
   try:
    j=json.loads((root/'status.json').read_text())
    if j['accounts']['a']['state']==state:return j
   except (OSError,ValueError,KeyError):pass
   time.sleep(.1)
  raise AssertionError('Supervisor did not reach '+state)
 write();p=subprocess.Popen([sys.executable,str(Path(__file__).parents[1]/'worker'/'agent.py')],env=env)
 try:
  wait_state('running');cfg['accounts']['a'].update(desired='stopped',revision=2);write();wait_state('stopped')
  time.sleep(2.2);wait_state('stopped')
  cfg['accounts']['a'].update(desired='running',revision=3);write();wait_state('running')
  cfg['expires']=time.time()-1;write();wait_state('stopped')
 finally:p.terminate();p.wait(timeout=10)
