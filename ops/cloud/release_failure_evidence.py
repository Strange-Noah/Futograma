"""Persist failed isolated-run evidence; never read local Mac or promote a snapshot."""
import os,tarfile
from pathlib import Path
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
require_hosted()
if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or not os.environ.get('FUTBOL_DATA_REF','').startswith('acceptance/'):raise RuntimeError('ISOLATION_REQUIRED')
p=ROOT/'.cloud/failed-readiness-evidence.tar.gz'
with tarfile.open(p,'w:gz') as t:
 for sub in ['squad_pipeline/raw/live','squad_pipeline/audit/live_deployment','logs/live']:
  path=ROOT/sub
  if path.exists():t.add(path,arcname=sub)
s=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],os.environ['FUTBOL_DATA_REF']);r=s.api('POST',s.base+'/releases',dict(tag_name='readiness-failure-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1'),target_commitish=s.ref,name='Failed readiness run evidence; not production',body='Unresolved evidence, do not delete.',draft=True));a=s.upload(r['id'],p);s.download(a['id'],ROOT/'.cloud/failed-evidence-readback.tar.gz')
from squad_pipeline.cloud.quality import sha
assert sha(p)==sha(ROOT/'.cloud/failed-evidence-readback.tar.gz');s.api('PATCH',s.base+'/releases/'+str(r['id']),dict(draft=False));print('FAILURE_EVIDENCE_PRESERVED',a['id'])
