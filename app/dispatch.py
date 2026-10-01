"""Persistent jobs + per-user lease, with bounded request execution."""
import time,secrets
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from .db import Session,Control,Job,User
from . import operations
from .diagnostics import report
SAFE_RETRY={'provision','start','stop','check'}
def ensure_control(s,uid):
 if s.get(Control,uid):return
 s.add(Control(user_id=uid))
 try:s.commit()
 except IntegrityError:s.rollback()
def acquire(s,uid):
 ensure_control(s,uid)
 c=s.scalar(select(Control).where(Control.user_id==uid).with_for_update())
 if c.lease>time.time():s.rollback();raise HTTPException(409,'Outra operação está em andamento. Aguarde e tente novamente.')
 token=secrets.token_hex(16);c.owner=token;c.lease=time.time()+330;s.commit();return token
def release(s,uid,token):
 s.rollback();c=s.scalar(select(Control).where(Control.user_id==uid).with_for_update())
 if c and c.owner==token:c.owner='';c.lease=0
 s.commit()
def process(job_id,uid):
 with Session() as s:
  token=acquire(s,uid)
  try:
   j=s.get(Job,job_id)
   if not j or j.user_id!=uid:raise HTTPException(404,'Operação não encontrada.')
   if j.state=='done':return {'job_id':j.id,'state':'done'}
   if j.state=='running' and j.lease>time.time():raise HTTPException(409,'Operação ainda em andamento.')
   if j.state=='running' and j.kind not in SAFE_RETRY:
    j.state='error';j.error='Resultado incerto após interrupção. Confira no TikTok antes de repetir.';s.commit()
    return {'job_id':j.id,'state':j.state,'error':j.error}
   j.state='running';j.lease=time.time()+330;j.attempts+=1;s.commit()
   try:
    operations.execute(s,j);j.state='done';j.error=''
   except Exception as e:
    s.rollback();j=s.get(Job,job_id);j.state='error'
    diagnostic=report(e,job_id)
    j.error=str(e) if isinstance(e,ValueError) else diagnostic
    if j.kind=='provision':
     u=s.get(User,uid);u.env_status='error';u.env_error=j.error
   j.lease=0;s.commit();return {'job_id':j.id,'state':j.state,'error':j.error}
  finally:release(s,uid,token)
def resume(uid):
 with Session() as s:
  j=s.scalar(select(Job).where(Job.user_id==uid,((Job.state=='queued')|((Job.state=='running')&(Job.lease<time.time())))).order_by(Job.created).limit(1))
  if not j:return {'state':'idle'}
  id=j.id
 return process(id,uid)
