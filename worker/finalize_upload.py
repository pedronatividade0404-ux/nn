import sys,re,shutil,os
from pathlib import Path
id,size,ext=sys.argv[1:];size=int(size)
assert re.fullmatch('[a-f0-9]{32}',id) and ext in ('.mp4','.mkv','.mov','.webm','.jpg','.jpeg','.png') and size>0
root=Path(os.getenv('NEXATOK_AGENT_ROOT','/home/daytona/nexatok'))/'assets';dest=root/(id+ext);source=root/'.uploads'/id
if dest.exists() and dest.stat().st_size==size:sys.exit(0)
parts=sorted(source.iterdir(),key=lambda p:int(p.name))
assert sum(p.stat().st_size for p in parts)==size
pending=dest.with_suffix('.pending')
with pending.open('wb') as out:
 for part in parts:
  with part.open('rb') as inp:shutil.copyfileobj(inp,out,1024*1024)
assert pending.stat().st_size==size
os.chmod(pending,0o600);pending.replace(dest);shutil.rmtree(source)
