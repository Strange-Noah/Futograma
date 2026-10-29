"""Live remote safety tests; only newly created isolated resources may be written."""
import os,json,subprocess,threading,gzip
from concurrent.futures import ThreadPoolExecutor
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.storage import StaleWriter
from squad_pipeline.cloud.quality import sha
from squad_pipeline.core import ROOT

def main():
 require_hosted()
 if os.environ['GITHUB_REPOSITORY']!='Strange-Noah/Futograma-data' or os.environ.get('FUTBOL_CLOUD_ENABLED')!='false':raise RuntimeError('ISOLATED_DISABLED_PRODUCTION_TEST_ONLY')
 s=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN']);before,p=s.head();ref='acceptance/safety-'+os.environ['GITHUB_RUN_ID'];s.api('POST',s.base+'/git/refs',dict(ref='refs/heads/'+ref,sha=before));a=GitHubStore(s.repository,s.token,ref);code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();work=ROOT/'.cloud/remote-safety';work.mkdir(parents=True,exist_ok=False);s.restore(work/'source.sqlite')
 with (work/'corrupt.gz').open('xb') as f:f.write(b'Controlled invalid gzip: isolated upload failure test')
 try:a.publish(work/'source.sqlite',work/'corrupt.gz',before,code)
 except (gzip.BadGzipFile,EOFError):pass
 else:raise RuntimeError('CORRUPT_UPLOAD_WAS_NOT_REJECTED')
 if a.head()[0]!=before:raise RuntimeError('FAILED_UPLOAD_ADVANCED_POINTER')
 parent=a.api('GET',a.base+'/git/commits/'+before);commits=[]
 for writer in [1,2]:
  blob=a.api('POST',a.base+'/git/blobs',dict(content=json.dumps({'synthetic_writer':writer}),encoding='utf-8'));tree=a.api('POST',a.base+'/git/trees',dict(base_tree=parent['tree']['sha'],tree=[dict(path='acceptance-cas-marker.json',mode='100644',type='blob',sha=blob['sha'])]));commit=a.api('POST',a.base+'/git/commits',dict(message='Isolated simultaneous writer '+str(writer),tree=tree['sha'],parents=[before]));commits.append(commit['sha'])
 barrier=threading.Barrier(2)
 def push(commit):
  barrier.wait()
  try:a.api('PATCH',a.base+'/git/refs/heads/'+ref,dict(sha=commit,force=False));return 'ACCEPTED'
  except StaleWriter:return 'REJECTED'
 with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(push,commits))
 if sorted(results)!=['ACCEPTED','REJECTED']:raise RuntimeError('REMOTE_CAS_RACE_FAILED:'+str(results))
 after,current=s.head()
 if after!=before or current!=p:raise RuntimeError('PRODUCTION_POINTER_CHANGED')
 result=dict(status='REMOTE_SAFETY_PASSED',isolated_ref=ref,real_upload_corruption_rejected=True,failed_upload_pointer_unchanged=True,simultaneous_git_ref_writers=results,production_head_before=before,production_head_after=after,production_facts_written=False,releases_deleted=0)
 print(json.dumps(result,indent=2))
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Remote safety\n```json\n'+json.dumps(result,indent=2)+'\n```\n')
if __name__=='__main__':main()
