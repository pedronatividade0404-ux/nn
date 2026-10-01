import os,tempfile,time,json
os.environ['APP_SECRET']='test-secret-at-least-32-characters-only-for-tests'
os.environ['DATABASE_URL']='sqlite:///'+tempfile.mkdtemp()+'/test.db'
os.environ['ADMIN_EMAIL']='owner@example.com'
os.environ['ADMIN_BOOTSTRAP_TOKEN']='installation-secret'
from fastapi.testclient import TestClient
from app.main import app
from app.db import Session,User,Key,Job,Account,Asset,init
from app.security import digest,seal,unseal
from app.tiktok import cookies
from app.operations import execute
from sqlalchemy import select
import pytest
init()
def signup(email):
 c=TestClient(app);r=c.post('/api/auth/register',json={'email':email,'password':'correct-password123','name':'Creator','admin_token':'installation-secret' if email=='owner@example.com' else ''})
 assert r.status_code==200,r.text
 c.headers['X-CSRF-Token']=r.json()['csrf'];return c
@pytest.fixture(autouse=True)
def fake_cloud(monkeypatch):
 def missing(u):raise ValueError('Provider simulated: not provisioned.')
 monkeypatch.setattr('app.operations.provision',missing)
@pytest.fixture
def owner():return signup('owner'+str(time.time_ns())+'@example.com')
def activate(c,plan='pro'):
 with Session() as s:
  key='NEXA-'+str(time.time_ns());s.add(Key(digest=digest(key),plan=plan,days=7));s.commit()
 assert c.post('/api/plans/redeem',json={'key':key}).status_code==200
 return key
def account(c):
 r=c.post('/api/accounts',json={'name':'TikTok','cookies':'sessionid=very-private; sid_tt=private'})
 assert r.status_code==200,r.text
 return r.json()['id']
def test_auth_csrf_and_logout(owner):
 assert owner.get('/api/me').status_code==200
 csrf=owner.headers.pop('X-CSRF-Token')
 assert owner.post('/api/auth/logout').status_code==403
 owner.headers['X-CSRF-Token']=csrf
 assert owner.post('/api/auth/logout').status_code==200
 assert owner.get('/api/me').status_code==401
def test_wrong_origin(owner):
 assert owner.post('/api/plans/redeem',json={'key':'NEXA-INVALID'},headers={'Origin':'https://evil.test'}).status_code==403
def test_free_has_no_access(owner):
 assert owner.post('/api/accounts',json={'name':'x','cookies':'sessionid=x'}).status_code==403
 assert owner.post('/api/environment/retry').status_code==403
def test_keys_single_use_and_renewal(owner):
 key=activate(owner);before=owner.get('/api/me').json()['user']['expires']
 assert owner.post('/api/plans/redeem',json={'key':key}).status_code==400
 activate(owner);after=owner.get('/api/me').json()['user']['expires']
 assert after-before==7*86400
 with Session() as s:assert len(s.scalars(select(Job).where(Job.user_id==owner.get('/api/me').json()['user']['id'])).all())==2
def test_accounts_encrypted_limit_and_isolation(owner):
 activate(owner);aid=account(owner);account(owner);account(owner)
 assert owner.post('/api/accounts',json={'name':'four','cookies':'sessionid=x'}).status_code==403
 second=signup('other'+str(time.time_ns())+'@example.com');activate(second)
 assert second.delete('/api/accounts/'+aid).status_code==404
 assert second.get('/api/accounts/'+aid+'/credentials').status_code==404
 with Session() as s:
  a=s.get(Account,aid);assert 'very-private' not in a.secret;assert 'very-private' in unseal(a.secret)
 assert 'very-private' not in owner.get('/api/dashboard').text
def test_rtmp_validation_and_secret_redaction(owner):
 activate(owner);aid=account(owner)
 bad={'rtmp':'rtmp://127.0.0.1/secret'}
 assert owner.put('/api/accounts/'+aid+'/config',json=bad).status_code==422
 assert owner.put('/api/accounts/'+aid+'/config',json={'rtmp':'rtmps://push.tiktok.com/live/private-key','title':'Hello'}).status_code==200
 d=owner.get('/api/dashboard').json()['accounts'][0]
 assert d['config']['has_rtmp'];assert 'rtmp_secret' not in d['config'];assert 'private-key' not in json.dumps(d)
 assert owner.get('/api/accounts/'+aid+'/credentials').json()['key']=='private-key'
def test_start_requires_video_and_environment(owner):
 activate(owner);aid=account(owner)
 assert owner.post('/api/accounts/'+aid+'/start').status_code==409
 with Session() as s:
  u=s.get(User,owner.get('/api/me').json()['user']['id']);u.env_status='ready';s.commit()
 assert owner.post('/api/accounts/'+aid+'/start').status_code==422
def test_admin_permissions(owner):
 assert owner.post('/api/admin/keys',json={'plan':'pro'}).status_code==403
 c=signup('owner@example.com');r=c.post('/api/admin/keys',json={'plan':'basic','count':2});assert r.status_code==200
 assert len(r.json()['keys'])==2
 assert c.get('/api/admin/users').status_code==200
def test_password_revokes_sessions(owner):
 r=owner.post('/api/auth/password',json={'old':'correct-password123','new':'new-correct-password123'})
 assert r.status_code==200;assert owner.get('/api/me').status_code==401
def test_cookies_formats():
 assert cookies('[{"name":"sessionid","value":"x","domain":".tiktok.com"}]')=='sessionid=x'
 assert cookies('#HttpOnly_.tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tx')=='sessionid=x'
 with pytest.raises(ValueError):cookies('sessionid=x\nheader:evil')
def test_worker_provision_and_control(owner,monkeypatch):
 activate(owner);aid=account(owner)
 class Fake:
  id='sandbox-123'
 monkeypatch.setattr('app.operations.provision',lambda u:Fake())
 calls=[];monkeypatch.setattr('app.operations.sync',lambda s,u:calls.append(u.id))
 with Session() as s:
  u=s.get(User,owner.get('/api/me').json()['user']['id']);j=s.scalar(select(Job).where(Job.user_id==u.id));execute(s,j)
  assert u.sandbox=='sandbox-123';assert u.env_status=='ready'
  a=s.get(Account,aid);a.config={**a.config,'video':'test-video','rtmp_secret':seal('rtmps://push.tiktok.com/live/key')};s.commit()
  execute(s,Job(user_id=u.id,kind='start',payload={'account_id':aid}));assert a.config['desired']=='running'
  execute(s,Job(user_id=u.id,kind='stop',payload={'account_id':aid}));assert a.config['desired']=='stopped'
  assert len(calls)==3
def test_queue_stop_after_expiry(owner):
 activate(owner);aid=account(owner)
 with Session() as s:
  u=s.get(User,owner.get('/api/me').json()['user']['id']);u.expires=time.time()-1;s.commit()
 assert owner.post('/api/accounts/'+aid+'/stop').status_code==200
 assert owner.post('/api/accounts/'+aid+'/start').status_code==403
def test_admin_email_without_bootstrap_cannot_gain_admin():
 old=os.environ['ADMIN_EMAIL'];os.environ['ADMIN_EMAIL']='bootstrap'+str(time.time_ns())+'@example.com'
 try:
  c=TestClient(app);r=c.post('/api/auth/register',json={'email':os.environ['ADMIN_EMAIL'],'password':'correct-password123','name':'Owner'})
  assert r.status_code==200;assert r.json()['user']['admin'] is False
 finally:os.environ['ADMIN_EMAIL']=old

def test_qr_ticket_and_state_validation(owner,monkeypatch):
 activate(owner)
 os.environ['TIKTOK_CLIENT_KEY']='test-key';os.environ['TIKTOK_CLIENT_SECRET']='test-secret'
 from app.db import OAuth
 from app import oauth
 monkeypatch.setattr(oauth,'call',lambda path,data:{'scan_qrcode_url':'aweme://authorize?client_ticket=tobefilled','token':'test-token'})
 r=owner.post('/api/tiktok/qr');assert r.status_code==200;id=r.json()['id'];assert r.json()['image'].startswith('data:image/png;base64,')
 monkeypatch.setattr(oauth,'call',lambda path,data:{'status':'confirmed','client_ticket':'wrong','state':'wrong','code':'bad'})
 assert owner.get('/api/tiktok/qr/'+id).status_code==502
 with Session() as s:
  row=s.get(OAuth,id);info=json.loads(unseal(row.secret));assert row.status=='new'
 def mock(path,data):
  if path=='check_qrcode/':return {'status':'confirmed','client_ticket':info['ticket'],'state':info['state'],'redirect_uri':'https://callback.test?code=ok'}
  return {'access_token':'secret-access-token','refresh_token':'secret-refresh-token'}
 monkeypatch.setattr(oauth,'call',mock)
 r=owner.get('/api/tiktok/qr/'+id);assert r.status_code==200;assert r.json()['done']
 with Session() as s:
  row=s.get(OAuth,id);assert row.status=='confirmed';assert 'secret-access-token' not in row.secret
