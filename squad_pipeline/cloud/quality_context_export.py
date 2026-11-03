"""Whitelisted issue export. Source debt is never a player-match record."""
import json
from ..canonical_fixture import exists

def issues(db):
 rows=[]
 if exists(db,'quality_source_issues'):
  for key,scope,mid,reason,payload in db.execute('SELECT * FROM quality_source_issues ORDER BY issue_key'):
   p=json.loads(payload);source=p.get('source') or {};rows.append(dict(issue_key=key,scope=scope,provider_match_id=mid,club_id=p.get('managing_club_id'),failure_classification=reason,state='PENDING_VERIFICATION',source_url=source.get('url') or p.get('source_url'),record_kind='SOURCE_ISSUE'))
 if exists(db,'quality_unresolved_observations'):
  for key,entity,payload in db.execute('SELECT * FROM quality_unresolved_observations ORDER BY observation_key'):
   p=json.loads(payload);q=p.get('quality') or {};f=p.get('fixture') or {};rows.append(dict(issue_key=key,scope='FIXTURE_CONTRACT',provider_match_id=f.get('provider_match_id'),canonical_match_id=q.get('canonical_match_id'),failure_classification=','.join(q.get('missing',[])+q.get('conflicts',[])),state=q.get('state','PENDING_VERIFICATION'),record_kind='SOURCE_ISSUE'))
 return rows
