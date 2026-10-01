"""Emit metadata only: never exception messages, bodies, headers or credentials."""
import logging,re,traceback
logger=logging.getLogger('nexatok.operations')
class StageError(RuntimeError):
 def __init__(self,stage,cause):
  self.stage=stage;self.cause=cause
  super().__init__('Falha na etapa '+stage)
def stage(name,fn):
 try:return fn()
 except Exception as e:raise StageError(name,e) from e
def report(e,job_id):
 cause=e.cause if isinstance(e,StageError) else e
 step=e.stage if isinstance(e,StageError) else 'executar operação'
 status=getattr(cause,'status_code',None) or getattr(cause,'status',None)
 if not isinstance(status,int):status=getattr(getattr(cause,'response',None),'status_code',None)
 if not isinstance(status,int):status=None
 cls=type(cause).__name__
 hint={401:'Confira WORKER_TOKEN nos dois serviços.',403:'Confira a autenticação do worker.',404:'Confira WORKER_URL e os arquivos do usuário no volume.',429:'Limite de requisições atingido; aguarde.',400:'O worker Railway recusou os parâmetros; confira configuração e limites do worker.',409:'Há um conflito de estado ou recurso no worker Railway.',503:'O serviço está indisponível; tente novamente.'}.get(status,'')
 if 'Timeout' in cls:hint='O provedor demorou para responder. Confira o estado e os logs do worker no Railway.'
 if isinstance(cause,FileNotFoundError):hint='Um arquivo necessário não foi incluído no deploy.'
 frames=[f'{f.filename.rsplit("/",1)[-1]}:{f.lineno}:{f.name}' for f in traceback.extract_tb(cause.__traceback__)]
 logger.error('NEXATOK_JOB_FAILED job=%s stage=%s type=%s status=%s frames=%s',job_id,step,cls,status,','.join(frames))
 return f'Falha em {step}: {cls}'+(f' (HTTP {status})' if status else '')+'. '+hint+f' Referência: {job_id[:8]}.'
