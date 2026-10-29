"""Publish only an isolated preview after same-job hosted browser acceptance."""
import os,json,subprocess,hashlib
from pathlib import Path
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.quality import inspect
from ops.cloud.phase2_acceptance import store,ensure_ref
from ops.cloud.platform import commit_tree

def main():
 require_hosted()
 if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')=='CONFIRMED':raise RuntimeError('DISABLED_PRODUCTION_ACCEPTANCE_REQUIRED')
 work=ROOT/'.cloud/phase2-second';report=json.loads((work/'result.json').read_text());ui=json.loads((work/'ui-result.json').read_text());assert ui['status']=='UI_ACCEPTANCE_PASSED' and report['pending_survived_independent_job'] and report['retry_beyond_72h_controlled_clock'] and report['duplicate_attempt_idempotent'];s=store();production=s.head();a=store('acceptance/phase2-data');head,p=a.head();assert head==report['accepted_snapshot']['head'];assert p['current']['code_commit']==subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();a.restore(work/'recovered-previous.sqlite',previous=True);assert inspect(work/'recovered-previous.sqlite')['tables']==inspect(work/'working.sqlite')['tables']
 manifest=json.loads((work/'site/data/manifest.json').read_text());assert manifest['version']['data_commit']==head and manifest['version']['sqlite_sha256']==p['current']['database']['sha256'];assert manifest['browser_sha256']==hashlib.sha256((work/'site/data/browser.json').read_bytes()).hexdigest();ensure_ref(s,'site-mvp',production[0]);files={str(x.relative_to(work/'site')):x.read_bytes() for x in (work/'site').rglob('*') if x.is_file()};files['publication.json']=json.dumps(dict(data_commit=head,version=manifest['version'],code_commit=report['code_commit'],acceptance_dataset=True)).encode();site_commit=commit_tree(s,'site-mvp',files,'site: tested raw browser '+p['current']['version']);assert s.head()==production
 published=s.api('GET',s.base+'/contents/data/manifest.json?ref='+site_commit);import base64;assert json.loads(base64.b64decode(published['content']))==manifest
 receipt=dict(status='ISOLATED_PREVIEW_TRANSPORT_PUBLISHED',site_commit=site_commit,data_head=head,sqlite_sha256=p['current']['database']['sha256'],code_commit=report['code_commit'],previous_snapshot_recovered=True,production_pointer_unchanged=True,ui=ui)
 with (work/'publication-result.json').open('x') as f:json.dump(receipt,f,indent=2)
 release=s.api('POST',s.base+'/releases',dict(tag_name='futograma-phase2-ui-'+os.environ['GITHUB_RUN_ID'],target_commitish=production[0],name='Phase 2 hosted browser and recovery receipts',body='Isolated acceptance only; no production facts or authority handover.',draft=True))
 for name in ['desktop.png','mobile.png','ui-result.json','result.json','publication-result.json']:
  asset=s.upload(release['id'],work/name);target=work/('uploaded-readback-'+name);s.download(asset['id'],target);assert hashlib.sha256(target.read_bytes()).hexdigest()==hashlib.sha256((work/name).read_bytes()).hexdigest()
 s.api('PATCH',s.base+'/releases/'+str(release['id']),dict(draft=False));print(json.dumps(receipt))
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('\n```json\n'+json.dumps(receipt,indent=2)+'\n```\n')
if __name__=='__main__':main()
