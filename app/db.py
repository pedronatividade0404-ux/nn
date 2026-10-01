import os, uuid, time
from sqlalchemy import create_engine, String, Text, Float, Integer, Boolean, ForeignKey, JSON
from sqlalchemy.orm import DeclarativeBase, mapped_column, sessionmaker
url=os.getenv('DATABASE_URL','sqlite:///./nexatok.db').replace('postgres://','postgresql+psycopg://',1)
if url.startswith('postgresql://'): url=url.replace('postgresql://','postgresql+psycopg://',1)
if os.getenv('VERCEL') and url.startswith('sqlite'):raise RuntimeError('Configure DATABASE_URL com PostgreSQL externo para Vercel.')
engine=create_engine(url, pool_pre_ping=True,pool_size=2,max_overflow=1, **({'connect_args':{'check_same_thread':False}} if url.startswith('sqlite') else {}))
Session=sessionmaker(engine,expire_on_commit=False)
class Base(DeclarativeBase): pass
def uid(): return uuid.uuid4().hex
class User(Base):
 __tablename__='users'
 id=mapped_column(String,primary_key=True,default=uid)
 email=mapped_column(String(254),unique=True,index=True)
 name=mapped_column(String(80))
 password=mapped_column(Text)
 admin=mapped_column(Boolean,default=False)
 plan=mapped_column(String,default='free')
 expires=mapped_column(Float,default=0)
 sandbox=mapped_column(String,nullable=True)
 env_status=mapped_column(String,default='inactive')
 env_error=mapped_column(Text,default='')
class Login(Base):
 __tablename__='sessions'
 token=mapped_column(String,primary_key=True)
 user_id=mapped_column(ForeignKey('users.id'),index=True)
 expires=mapped_column(Float)
class Key(Base):
 __tablename__='license_keys'
 digest=mapped_column(String,primary_key=True)
 plan=mapped_column(String)
 days=mapped_column(Integer)
 used_by=mapped_column(String,nullable=True)
 used_at=mapped_column(Float,nullable=True)
class Account(Base):
 __tablename__='accounts'
 id=mapped_column(String,primary_key=True,default=uid)
 user_id=mapped_column(ForeignKey('users.id'),index=True)
 name=mapped_column(String(80))
 secret=mapped_column(Text)
 config=mapped_column(JSON,default=dict)
 runtime=mapped_column(JSON,default=dict)
class Asset(Base):
 __tablename__='assets'
 id=mapped_column(String,primary_key=True,default=uid)
 user_id=mapped_column(ForeignKey('users.id'),index=True)
 name=mapped_column(String(255))
 kind=mapped_column(String)
 path=mapped_column(String)
 size=mapped_column(Integer)
class Job(Base):
 __tablename__='jobs'
 id=mapped_column(String,primary_key=True,default=uid)
 user_id=mapped_column(ForeignKey('users.id'),index=True)
 kind=mapped_column(String)
 payload=mapped_column(JSON,default=dict)
 state=mapped_column(String,default='queued',index=True)
 error=mapped_column(Text,default='')
 attempts=mapped_column(Integer,default=0)
 created=mapped_column(Float,default=time.time)
 lease=mapped_column(Float,default=0)
class Attempt(Base):
 __tablename__='auth_attempts'
 id=mapped_column(String,primary_key=True)
 count=mapped_column(Integer,default=0)
 until=mapped_column(Float)
def init():
 if engine.dialect.name=='postgresql':
  from sqlalchemy import text
  with engine.begin() as connection:
   connection.execute(text('SELECT pg_advisory_xact_lock(78191384)'))
   Base.metadata.create_all(connection)
 else:Base.metadata.create_all(engine)
class OAuth(Base):
 __tablename__='tiktok_oauth'
 id=mapped_column(String,primary_key=True,default=uid)
 user_id=mapped_column(ForeignKey('users.id'),index=True)
 secret=mapped_column(Text)
 expires=mapped_column(Float)
 status=mapped_column(String,default='new')
class Control(Base):
 __tablename__='environment_control'
 user_id=mapped_column(ForeignKey('users.id'),primary_key=True)
 callback=mapped_column(Text,default='')
 lease=mapped_column(Float,default=0)
 owner=mapped_column(String,default='')
class Upload(Base):
 __tablename__='multipart_uploads'
 id=mapped_column(String,primary_key=True,default=uid)
 user_id=mapped_column(ForeignKey('users.id'),index=True)
 name=mapped_column(String(255))
 kind=mapped_column(String)
 size=mapped_column(Integer)
 offset=mapped_column(Integer,default=0)
 path=mapped_column(String)
 hashes=mapped_column(JSON,default=dict)
 expires=mapped_column(Float)
