"""Snapshot store interface. Local adapter tests the same immutable-assets + CAS-pointer contract."""
import json,os,uuid,shutil,fcntl
from pathlib import Path
from datetime import datetime,timezone
from .quality import inspect,sha,pack,unpack
class StaleWriter(RuntimeError):pass
class UploadFailed(RuntimeError):pass
def version():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:12]
def metadata(snapshot,compressed,base,code_commit):return dict(version=version(),database=inspect(snapshot),compressed=dict(sha256=sha(compressed),size=Path(compressed).stat().st_size),parent_head=base,code_commit=code_commit,created_at=datetime.now(timezone.utc).isoformat())
class LocalStore:
 """Explicit non-authoritative local sandbox, never the production data store."""
 def __init__(self,root):self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
 def head(self):
  p=self.root/'current.json'
  if not p.exists():return None,None
  b=p.read_bytes();return sha(p),json.loads(b)
 def restore(self,dest,previous=False):
  head,pointer=self.head()
  if not pointer:raise FileNotFoundError('STORE_NOT_BOOTSTRAPPED')
  ref=pointer['previous'] if previous else pointer['current']
  if not ref:raise FileNotFoundError('NO_PREVIOUS_SNAPSHOT')
  asset=self.root/'versions'/ref['version']/'database.sqlite.gz'
  if sha(asset)!=ref['compressed']['sha256']:raise ValueError('COMPRESSED_HASH_MISMATCH')
  actual=unpack(asset,dest);inspect(dest,ref['database']);return head,pointer
 def publish(self,snapshot,compressed,expected_head,code_commit='LOCAL_TEST',fail_upload=False):
  m=metadata(snapshot,compressed,expected_head,code_commit);folder=self.root/'versions'/m['version'];folder.mkdir(parents=True)
  if fail_upload:raise UploadFailed('CONTROLLED_UPLOAD_FAILURE')
  shutil.copyfile(compressed,folder/'database.sqlite.gz')
  if sha(folder/'database.sqlite.gz')!=m['compressed']['sha256']:raise UploadFailed('READBACK_MISMATCH')
  with (self.root/'pointer.lock').open('a+') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX);head,old=self.head()
   if head!=expected_head:raise StaleWriter('POINTER_CHANGED;REFUSE_STALE_PUBLISH')
   pointer=dict(schema_version=1,generation=(old['generation']+1 if old else 1),current=m,previous=old['current'] if old else None)
   temp=self.root/('pointer-'+uuid.uuid4().hex+'.tmp')
   with temp.open('x') as f:json.dump(pointer,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
   os.replace(temp,self.root/'current.json')
   fd=os.open(self.root,os.O_RDONLY)
   try:os.fsync(fd)
   finally:os.close(fd)
  fresh,p=self.head()
  if p!=pointer:raise UploadFailed('POINTER_READBACK_FAILED')
  return fresh,pointer
 def prune_plan(self):
  head,p=self.head();keep={r['version'] for r in [p['current'],p['previous']] if r} if p else set()
  return dict(mode='DRY_RUN_ONLY_LOCAL',keep=sorted(keep),candidates=[str(x) for x in (self.root/'versions').glob('*') if x.name not in keep])
