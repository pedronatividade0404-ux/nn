import os, hashlib, secrets, base64
from cryptography.fernet import Fernet
SECRET=os.environ.get('APP_SECRET','')
if len(SECRET)<32: raise RuntimeError('APP_SECRET deve ter pelo menos 32 caracteres. Gere com python -c "import secrets; print(secrets.token_urlsafe(48))"')
f=Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode()).digest()))
def seal(s): return f.encrypt(s.encode()).decode()
def unseal(s): return f.decrypt(s.encode()).decode()
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def password_hash(s):
 salt=secrets.token_bytes(16); h=hashlib.scrypt(s.encode(),salt=salt,n=16384,r=8,p=1)
 return salt.hex()+':'+h.hex()
def password_ok(s,h):
 salt,want=h.split(':'); got=hashlib.scrypt(s.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1)
 return secrets.compare_digest(got.hex(),want)
