"""Private repository orchestrates pinned public code using its own GITHUB_TOKEN."""
import os,json,subprocess,sqlite3,base64,re
from pathlib import Path
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import inspect,pack,snapshot,sha,unpack
from squad_pipeline.cloud.export import export
from squad_pipeline.cloud.storage import StaleWriter

def store(ref='main'):
 if os.environ['GITHUB_REPOSITORY']!='Strange-Noah/Futograma-data':raise RuntimeError('PRIVATE_DATA_REPOSITORY_REQUIRED')
 return GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],ref)
def ensure_ref(s,ref,head):
 if not s.api('GET',s.base+'/git/ref/heads/'+ref,allow404=True):s.api('POST',s.base+'/git/refs',dict(ref='refs/heads/'+ref,sha=head))
def commit_tree(s,ref,files,message):
 head=s.api('GET',s.base+'/git/ref/heads/'+ref)['object']['sha'];entries=[]
 for path,body in files.items():
  blob=s.api('POST',s.base+'/git/blobs',dict(content=base64.b64encode(body).decode(),encoding='base64'));entries.append(dict(path=path,mode='100644',type='blob',sha=blob['sha']))
 tree=s.api('POST',s.base+'/git/trees',dict(tree=entries));commit=s.api('POST',s.base+'/git/commits',dict(message=message,tree=tree['sha'],parents=[head]));s.api('PATCH',s.base+'/git/refs/heads/'+ref,dict(sha=commit['sha'],force=False))
 if s.api('GET',s.base+'/git/ref/heads/'+ref)['object']['sha']!=commit['sha']:raise RuntimeError('REF_READBACK_FAILED')
 return commit['sha']
def publish_site(s,work,code_commit):
 work.mkdir(parents=True,exist_ok=False);head,p=s.restore(work/'source.sqlite');m=export(work/'source.sqlite',work/'site',head,p,ROOT/'web');ensure_ref(s,'site',head)
 files={str(x.relative_to(work/'site')):x.read_bytes() for x in (work/'site').rglob('*') if x.is_file()};files['publication.json']=json.dumps(dict(data_commit=head,version=m['version'],code_commit=code_commit)).encode();commit=commit_tree(s,'site',files,'site: '+m['version']['data_version']);return dict(commit=commit,version=m['version'])
def main():
 require_hosted();mode=os.environ['FUTOGRAMA_MODE'];code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();os.environ['GITHUB_SHA']=code_commit;work=ROOT/'.cloud'/('hosted-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT']+'-'+mode);work.mkdir(parents=True,exist_ok=False);s=store();original,pointer=s.head();r=dict(mode=mode,code_commit=code_commit,formal_ingestion=False,authority_handover=False)
 if mode=='bootstrap':
  if pointer:raise RuntimeError('REFUSE_REBOOTSTRAP_EXISTING_BASELINE')
  release=s.api('GET',s.base+'/releases/tags/futograma-seed-20261029');assets={a['name']:a for a in release['assets']};s.download(assets['baseline_manifest.json']['id'],work/'seed.json');metadata=json.loads((work/'seed.json').read_text());s.download(assets['baseline.sqlite.gz']['id'],work/'seed.gz')
  if sha(work/'seed.gz')!=metadata['compressed']['sha256']:raise RuntimeError('SEED_HASH_MISMATCH')
  unpack(work/'seed.gz',work/'seed.sqlite');verified=inspect(work/'seed.sqlite',metadata['migrated']);head,p=s.publish(work/'seed.sqlite',work/'seed.gz',original,code_commit,allow_bootstrap=True);head,p=s.publish(work/'seed.sqlite',work/'seed.gz',head,code_commit);r.update(status='BOOTSTRAP_VERIFIED',data_commit=head,sqlite_sha256=verified['sha256'])
 elif mode in ('acceptance','recover_acceptance'):
  run=os.environ.get('ACCEPTANCE_RUN_ID') or os.environ['GITHUB_RUN_ID'];ref='acceptance/'+run
  if not re.fullmatch('acceptance/[0-9]+',ref):raise RuntimeError('ISOLATED_ACCEPTANCE_REF_REQUIRED')
  a=store(ref)
  if mode=='acceptance':
   ensure_ref(s,ref,original);s.restore(work/'baseline.sqlite');snapshot(work/'baseline.sqlite',work/'acceptance.sqlite');db=sqlite3.connect(work/'acceptance.sqlite');db.execute('CREATE TABLE acceptance_marker(run_id TEXT)');db.execute('INSERT INTO acceptance_marker VALUES(?)',(run,));db.commit();db.close();pack(work/'acceptance.sqlite',work/'acceptance.gz');head,_=a.head();head,p=a.publish(work/'acceptance.sqlite',work/'acceptance.gz',head,code_commit);head,p=a.publish(work/'acceptance.sqlite',work/'acceptance.gz',head,code_commit)
   try:a.publish(work/'acceptance.sqlite',work/'acceptance.gz',p['current']['parent_head'],code_commit)
   except StaleWriter:pass
   else:raise RuntimeError('STALE_WRITER_ACCEPTED')
   with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('acceptance_run_id='+run+'\n')
   r.update(status='ISOLATED_PERSISTENCE_PUBLISHED',ref=ref,head=head,stale_writer_rejected=True)
  else:
   head,p=a.restore(work/'previous.sqlite',previous=True);digest=sha(work/'previous.sqlite');pack(work/'previous.sqlite',work/'recovery.gz');head,p=a.publish(work/'previous.sqlite',work/'recovery.gz',head,code_commit);head,p=a.publish(work/'previous.sqlite',work/'recovery.gz',head,code_commit);a.restore(work/'current-check.sqlite');a.restore(work/'previous-check.sqlite',previous=True)
   if sha(work/'current-check.sqlite')!=digest or sha(work/'previous-check.sqlite')!=digest:raise RuntimeError('RECOVERY_MISMATCH')
   plan=a.prune(apply=False)
   if set(plan['keep_release_ids'])!={p['current']['release_id'],p['previous']['release_id']}:raise RuntimeError('RETENTION_PROTECTION_FAILED')
   r.update(status='INDEPENDENT_JOB_RECOVERY_PASSED',ref=ref,head=head,current_and_previous_verified=True,retention=plan)
 elif mode=='preflight':
  from squad_pipeline.cloud import driver
  import sys
  os.environ['FUTBOL_DATA_REPOSITORY']=s.repository;os.environ['FUTBOL_DATA_TOKEN']=s.token;os.environ['FUTBOL_REMOTE_ACCEPTANCE_ENABLED']='true';sys.argv=['driver','preflight','--backend','github','--work',str(work/'network')];n=driver.main();r.update(status=n['status'],network=n)
 elif mode in ('verify','verify_previous'):
  head,p=s.restore(work/'current.sqlite');info=inspect(work/'current.sqlite');s.restore(work/'previous.sqlite',previous=True);db=sqlite3.connect('file:'+str(work/'current.sqlite')+'?mode=ro',uri=True);holds=db.execute('SELECT COUNT(*) FROM cloud_evidence_holds WHERE resolved=0').fetchone()[0];db.close();r.update(status='REMOTE_RESTORE_VERIFIED',head=head,sqlite_sha256=info['sha256'],previous_sha256=sha(work/'previous.sqlite'),tables=info['tables'],unresolved_holds=holds)
 elif mode=='site':r.update(status='VERIFIED_SITE_TRANSPORT_PUBLISHED',site=publish_site(s,work/'publication',code_commit))
 elif mode=='update':
  from squad_pipeline.cloud import driver
  import sys
  os.environ['FUTBOL_DATA_REPOSITORY']=s.repository;os.environ['FUTBOL_DATA_TOKEN']=s.token;sys.argv=['driver','update','--backend','github','--work',str(work/'update')];n=driver.main();r.update(status=n['status'],formal_ingestion=bool(n.get('publish_site')))
  if n.get('publish_site'):r['site']=publish_site(s,work/'publication',code_commit)
 else:raise RuntimeError('UNKNOWN_MODE')
 if mode not in ('bootstrap','update') and s.head()[0]!=original:raise RuntimeError('PRODUCTION_POINTER_CHANGED_DURING_ACCEPTANCE')
 r['production_pointer_unchanged']=mode in ('bootstrap','update') or s.head()[0]==original
 with (work/'result.json').open('x') as f:json.dump(r,f,ensure_ascii=False,indent=2)
 print(json.dumps(r,ensure_ascii=False,indent=2))
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Futograma '+mode+'\n```json\n'+json.dumps(r,ensure_ascii=False,indent=2)+'\n```\n')
 if mode=='preflight' and r['status']!='CLOUD_REAL_NETWORK_PREFLIGHT_PASSED':raise SystemExit(2)
if __name__=='__main__':main()
