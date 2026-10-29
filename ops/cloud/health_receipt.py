"""At most one small verified-data receipt per 30 days in the CODE repo; never SQLite or raw evidence."""
import json,os,base64,re
from datetime import datetime,timezone,timedelta
from squad_pipeline.cloud.github_store import GitHubStore

def due(receipt,at=None):
 if not receipt:return True
 return (at or datetime.now(timezone.utc))-datetime.fromisoformat(receipt['recorded_at'].replace('Z','+00:00'))>=timedelta(days=30)
def main():
 from squad_pipeline.cloud.hosted_guard import require_mode
 require_mode('update')
 if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('FUTBOL_CLOUD_ENABLED')!='true':raise SystemExit('Only owner-enabled GitHub workflow may publish a health receipt')
 expected=os.environ['EXPECTED_DATA_COMMIT'];digest=os.environ['EXPECTED_SQLITE_SHA']
 if not re.fullmatch('[a-f0-9]{40}',expected) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Verified data revision and SQLite checksum required')
 repo=os.environ['GITHUB_REPOSITORY'];api=GitHubStore(repo,os.environ['FUTBOL_CODE_TOKEN']);info=api.api('GET',api.base);branch=info['default_branch'];refpath=api.base+'/git/ref/heads/'+branch;head=api.api('GET',refpath)['object']['sha'];path='ops/cloud/cloud-health.json';old=api.api('GET',api.base+'/contents/'+path+'?ref='+head,allow404=True);receipt=json.loads(base64.b64decode(old['content'])) if old else None
 if not due(receipt):print('Health receipt is under 30 days old; no code commit');return
 receipt=dict(recorded_at=datetime.now(timezone.utc).isoformat(),data_commit=expected,sqlite_sha256=digest,purpose='Verified data publication health receipt; no football database stored in code Git history');encoded=base64.b64encode((json.dumps(receipt,indent=2)+'\n').encode()).decode();blob=api.api('POST',api.base+'/git/blobs',dict(content=encoded,encoding='base64'));parent=api.api('GET',api.base+'/git/commits/'+head);tree=api.api('POST',api.base+'/git/trees',dict(base_tree=parent['tree']['sha'],tree=[dict(path=path,mode='100644',type='blob',sha=blob['sha'])]));commit=api.api('POST',api.base+'/git/commits',dict(message='chore: monthly verified data health receipt',tree=tree['sha'],parents=[head]));api.api('PATCH',api.base+'/git/refs/heads/'+branch,dict(sha=commit['sha'],force=False));actual=api.api('GET',refpath)['object']['sha']
 if actual!=commit['sha']:raise RuntimeError('Health receipt ref changed during readback; review required')
 print('Monthly small data health receipt committed; no SQLite/raw files or history rewrite')
if __name__=='__main__':main()
