"""3 MiB chunks fit beneath Vercel's 4.5 MB request limit."""
import time,os,hashlib,re
from pathlib import Path
from fastapi import APIRouter,Depends,HTTPException,UploadFile,File
from pydantic import BaseModel,Field
from sqlalchemy import select,func
from .db import Upload,Asset,uid
from . import daytona_service as ds
CHUNK=3*1024*1024
ROOT='/home/daytona/nexatok/assets'
class Begin(BaseModel):
 name:str=Field(min_length=1,max_length=255)
 kind:str
 size:int=Field(gt=0)
def install(app,current,db,active,owned,lockuser):
 @app.post('/api/uploads')
 def begin(c:Begin,u=Depends(current),s=Depends(db)):
  active(u);u=lockuser(s,u)
  if u.env_status!='ready':raise HTTPException(409,'Aguarde seu ambiente ficar pronto.')
  ext=Path(c.name).suffix.lower()
  if c.kind not in ('video','cover') or ext not in (['.mp4','.mkv','.mov','.webm'] if c.kind=='video' else ['.jpg','.jpeg','.png']):raise HTTPException(422,'Tipo de arquivo inválido.')
  limit=int(os.getenv('MAX_VIDEO_MB','500'))*1048576 if c.kind=='video' else 5*1048576
  total=s.scalar(select(func.sum(Asset.size)).where(Asset.user_id==u.id)) or 0
  reserved=s.scalar(select(func.sum(Upload.size)).where(Upload.user_id==u.id,Upload.expires>time.time())) or 0
  if c.size>limit or total+reserved+c.size>int(os.getenv('MAX_STORAGE_MB','2000'))*1048576:raise HTTPException(413,'Limite de arquivo ou armazenamento atingido.')
  id=uid();path=ROOT+'/'+id+ext
  r=ds.get(u).process.exec('mkdir -p '+ROOT+'/.uploads/'+id,timeout=10)
  if r.exit_code!=0:raise HTTPException(502,'Não foi possível preparar o envio.')
  row=Upload(id=id,user_id=u.id,name=Path(c.name).name,kind=c.kind,size=c.size,path=path,offset=0,hashes={},expires=time.time()+86400)
  s.add(row);s.commit();return {'id':id,'chunk_size':CHUNK,'offset':0}
 @app.get('/api/uploads/{id}')
 def progress(id:str,u=Depends(current),s=Depends(db)):
  row=owned(s,Upload,id,u);return {'offset':row.offset,'size':row.size}
 @app.post('/api/uploads/{id}/chunk')
 def chunk(id:str,offset:int,file:UploadFile=File(...),u=Depends(current),s=Depends(db)):
  active(u)
  row=s.scalar(select(Upload).where(Upload.id==id,Upload.user_id==u.id).with_for_update())
  if not row or row.expires<time.time():raise HTTPException(404,'Envio expirado ou não encontrado.')
  content=file.file.read(CHUNK+1);h=hashlib.sha256(content).hexdigest()
  if offset<row.offset and row.hashes.get(str(offset))==h:return {'offset':row.offset}
  if offset!=row.offset:raise HTTPException(409,'Posição de envio incorreta. Consulte o progresso e retome.')
  expected=min(CHUNK,row.size-row.offset)
  if expected<=0 or len(content)!=expected:raise HTTPException(422,'Tamanho da parte inválido.')
  try:ds.get(u).fs.upload_file(content,ROOT+'/.uploads/'+id+'/'+str(offset),timeout=60)
  except Exception:raise HTTPException(502,'Falha ao enviar parte. Tente novamente.')
  row.hashes={**row.hashes,str(offset):h};row.offset+=len(content);s.commit();return {'offset':row.offset}
 @app.post('/api/uploads/{id}/complete')
 def complete(id:str,u=Depends(current),s=Depends(db)):
  active(u)
  row=s.scalar(select(Upload).where(Upload.id==id,Upload.user_id==u.id).with_for_update())
  if not row:
   asset=owned(s,Asset,id,u);return {'id':asset.id,'name':asset.name}
  if row.offset!=row.size:raise HTTPException(409,'Envio incompleto.')
  # All command arguments originate from generated ids, validated extensions and integers.
  ext=Path(row.path).suffix
  r=ds.get(u).process.exec(f'python3 /home/daytona/nexatok/finalize_upload.py {row.id} {row.size} {ext}',timeout=90)
  if r.exit_code!=0:raise HTTPException(502,'Falha ao montar arquivo. Tente finalizar novamente.')
  asset=Asset(id=row.id,user_id=u.id,name=row.name,kind=row.kind,size=row.size,path=row.path)
  s.add(asset);s.delete(row);s.commit();return {'id':asset.id,'name':asset.name}
 @app.delete('/api/uploads/{id}')
 def cancel(id:str,u=Depends(current),s=Depends(db)):
  row=owned(s,Upload,id,u)
  try:ds.get(u).fs.delete_file(ROOT+'/.uploads/'+row.id,recursive=True)
  except Exception:raise HTTPException(502,'Falha ao limpar envio.')
  s.delete(row);s.commit();return {'ok':True}
