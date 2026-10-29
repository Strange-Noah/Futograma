"""GitHub Release assets hold SQLite; only a small CAS manifest enters the data repository's Git history."""
import os,re,json,base64,urllib.request,urllib.error,urllib.parse
from pathlib import Path
from datetime import datetime,timezone,timedelta
from .quality import inspect,sha,unpack
from .storage import metadata,StaleWriter,UploadFailed
from ..acquisition import NoRedirect
MARKER='Managed by Futbol snapshot pipeline; only current.json on the configured data ref is authoritative.'
class GitHubStore:
 def __init__(self,repository,token,ref='main'):
  if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+',repository):raise ValueError('EXPLICIT_OWNER_AND_DATA_REPOSITORY_REQUIRED')
  if not token:raise ValueError('DATA_REPOSITORY_TOKEN_REQUIRED')
  if not re.fullmatch(r'[A-Za-z0-9_./-]+',ref) or '..' in ref:raise ValueError('INVALID_DATA_REF')
  self.repository=repository;self.token=token;self.ref=ref;self.base='/repos/'+repository
 def api(self,method,path,data=None,allow404=False):
  headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','User-Agent':'FutbolSnapshot/1.0'}
  if data is not None:headers['Content-Type']='application/json'
  req=urllib.request.Request('https://api.github.com'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
  try:
   with urllib.request.urlopen(req,timeout=40) as r:body=r.read(16000000);return json.loads(body) if body else None
  except urllib.error.HTTPError as e:
   if allow404 and e.code==404:return None
   if e.code in [409,422] and '/git/refs/' in path:raise StaleWriter('DATA_REF_COMPARE_AND_SWAP_REJECTED') from None
   raise RuntimeError('GITHUB_HTTP_'+str(e.code)+' '+method+' '+path.split('?')[0]) from None
 def head(self):
  ref=self.api('GET',self.base+'/git/ref/heads/'+urllib.parse.quote(self.ref,safe='/'));head=ref['object']['sha'];item=self.api('GET',self.base+'/contents/current.json?ref='+head,allow404=True)
  return head,json.loads(base64.b64decode(item['content'])) if item else None
 def download(self,asset_id,dest):
  p=Path(dest)
  if p.exists():raise ValueError('DOWNLOAD_TARGET_EXISTS')
  url='https://api.github.com'+self.base+'/releases/assets/'+str(int(asset_id));headers={'Authorization':'Bearer '+self.token,'Accept':'application/octet-stream','User-Agent':'FutbolSnapshot/1.0'};opener=urllib.request.build_opener(NoRedirect())
  for _ in range(4):
   try:
    with opener.open(urllib.request.Request(url,headers=headers),timeout=60) as r,p.open('xb') as out:
     size=0
     for chunk in iter(lambda:r.read(1048576),b''):
      size+=len(chunk)
      if size>=1900000000:raise ValueError('ASSET_SIZE_LIMIT')
      out.write(chunk)
    return p
   except urllib.error.HTTPError as e:
    if e.code not in [301,302,303,307,308]:raise UploadFailed('ASSET_DOWNLOAD_HTTP_'+str(e.code)) from None
    target=urllib.parse.urlsplit(e.headers.get('Location',''))
    if target.scheme!='https' or target.hostname not in ['release-assets.githubusercontent.com','objects.githubusercontent.com','github.com']:raise UploadFailed('UNEXPECTED_ASSET_REDIRECT')
    # Never forward a repository token to a signed CDN location or emit that location into logs.
    url=target.geturl();headers={'Accept':'application/octet-stream','User-Agent':'FutbolSnapshot/1.0'}
  raise UploadFailed('ASSET_REDIRECT_LIMIT')
 def restore(self,dest,previous=False):
  head,pointer=self.head()
  if not pointer:raise FileNotFoundError('DATA_REPOSITORY_NOT_BOOTSTRAPPED;NO_DATABASE_IN_CODE_REPOSITORY')
  if pointer.get('schema_version')!=1:raise ValueError('POINTER_SCHEMA_UNSUPPORTED')
  m=pointer['previous'] if previous else pointer['current']
  if not m:raise FileNotFoundError('NO_PREVIOUS_SNAPSHOT')
  zipped=Path(str(dest)+'.download.gz');self.download(m['asset_id'],zipped)
  if sha(zipped)!=m['compressed']['sha256'] or zipped.stat().st_size!=m['compressed']['size']:raise UploadFailed('RESTORED_ASSET_HASH_SIZE_MISMATCH')
  unpack(zipped,dest);inspect(dest,m['database']);return head,pointer
 def upload(self,release_id,path):
  path=Path(path);size=path.stat().st_size
  if size>=1900000000:raise ValueError('RELEASE_ASSET_TOO_LARGE')
  url='https://uploads.github.com'+self.base+'/releases/'+str(release_id)+'/assets?name='+urllib.parse.quote(path.name)
  headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','Content-Type':'application/gzip','Content-Length':str(size),'User-Agent':'FutbolSnapshot/1.0'}
  try:
   with path.open('rb') as body:
    req=urllib.request.Request(url,data=body,headers=headers,method='POST')
    with urllib.request.urlopen(req,timeout=180) as r:asset=json.loads(r.read(1000000))
  except urllib.error.HTTPError as e:raise UploadFailed('ASSET_UPLOAD_HTTP_'+str(e.code)) from None
  if asset.get('state')!='uploaded' or asset.get('size')!=size:raise UploadFailed('ASSET_UPLOAD_NOT_COMPLETE')
  remote_digest=asset.get('digest')
  if remote_digest and remote_digest!='sha256:'+sha(path):raise UploadFailed('ASSET_REPORTED_DIGEST_MISMATCH')
  return asset
 def publish(self,snapshot,compressed,expected_head,code_commit,allow_bootstrap=False):
  if not re.fullmatch(r'[a-f0-9]{40}',code_commit):raise ValueError('REAL_CODE_COMMIT_SHA_REQUIRED')
  head,old=self.head()
  if head!=expected_head:raise StaleWriter('BASE_HEAD_ALREADY_STALE')
  if not old and not allow_bootstrap:raise ValueError('EXPLICIT_BOOTSTRAP_REQUIRED')
  if old and allow_bootstrap:raise ValueError('REFUSE_REBOOTSTRAP_EXISTING_AUTHORITY')
  m=metadata(snapshot,compressed,expected_head,code_commit);tag='futbol-data-'+m['version'];release=self.api('POST',self.base+'/releases',dict(tag_name=tag,target_commitish=expected_head,name='Futbol data '+m['version'],body=MARKER,draft=True,prerelease=False));asset=self.upload(release['id'],compressed);m.update(release_id=release['id'],asset_id=asset['id'],tag=tag)
  # Download the actual uploaded bytes before publishing a pointer; a mere API success is insufficient.
  verify=Path(str(compressed)+'.verify-download.'+m['version']);self.download(asset['id'],verify)
  if sha(verify)!=m['compressed']['sha256']:raise UploadFailed('UPLOADED_ASSET_READBACK_FAILED')
  checkdb=Path(str(snapshot)+'.verify-restore.'+m['version']+'.sqlite');unpack(verify,checkdb);inspect(checkdb,m['database']);self.api('PATCH',self.base+'/releases/'+str(release['id']),dict(draft=False))
  pointer=dict(schema_version=1,generation=(old['generation']+1 if old else 1),current=m,previous=old['current'] if old else None)
  # Commit has exactly the expected parent. A concurrent descendant makes force=false reject it.
  commit=self.api('GET',self.base+'/git/commits/'+expected_head);blob=self.api('POST',self.base+'/git/blobs',dict(content=base64.b64encode((json.dumps(pointer,ensure_ascii=False,indent=2)+'\n').encode()).decode(),encoding='base64'));tree=self.api('POST',self.base+'/git/trees',dict(base_tree=commit['tree']['sha'],tree=[dict(path='current.json',mode='100644',type='blob',sha=blob['sha'])]));new=self.api('POST',self.base+'/git/commits',dict(message='data: '+m['version'],tree=tree['sha'],parents=[expected_head]));self.api('PATCH',self.base+'/git/refs/heads/'+urllib.parse.quote(self.ref,safe='/'),dict(sha=new['sha'],force=False));actual,p=self.head()
  if actual!=new['sha'] or p!=pointer:raise UploadFailed('PUBLISHED_POINTER_READBACK_MISMATCH')
  return actual,pointer
 def prune(self,apply=False):
  expected,pointer=self.head()
  if not pointer:raise ValueError('NO_VERIFIED_POINTER;REFUSE_PRUNING')
  keep={m['release_id'] for m in [pointer['current'],pointer['previous']] if m};cut=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat();candidates=[]
  for page in range(1,21):
   rows=self.api('GET',self.base+'/releases?per_page=100&page='+str(page))
   for r in rows:
    if r['id'] in keep or r.get('body')!=MARKER or not r['tag_name'].startswith('futbol-data-'):continue
    if r.get('draft') and r['created_at']>=cut:continue # interrupted fresh upload may still be in progress
    candidates.append(dict(id=r['id'],tag=r['tag_name']))
   if len(rows)<100:break
  for r in candidates if apply else []:
   if self.head()[0]!=expected:raise StaleWriter('DATA_HEAD_CHANGED_DURING_RETENTION;STOP')
   self.api('DELETE',self.base+'/releases/'+str(r['id']))
  return dict(mode='APPLY_MANAGED_RELEASE_RETENTION' if apply else 'DRY_RUN',keep_release_ids=sorted(keep),candidates=candidates,git_history_rewritten=False,git_tags_deleted=False)
