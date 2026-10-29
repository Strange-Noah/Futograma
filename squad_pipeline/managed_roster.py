"""Evidence-backed club workload management; match participation never grants membership."""
import json,hashlib
POSITIVE={'FIRST_TEAM','FIRST_TEAM_INJURED','LOAN_IN','CLUB_MANAGED_DEVELOPMENT'}
NEGATIVE={'LOAN_OUT','TRANSFERRED_OUT','MANAGEMENT_ENDED'}
DDL="""CREATE TABLE IF NOT EXISTS club_management_events(event_id TEXT PRIMARY KEY,club_id INTEGER,player_id INTEGER,status TEXT,effective_from TEXT,end_exclusive TEXT,observed_on TEXT,verification_status TEXT,date_precision TEXT,source_json TEXT,reason TEXT);"""
def migrate(db):db.executescript(DDL)
def add_event(db,club_id,player_id,status,effective_from,observed_on,sources,reason,end_exclusive=None,precision='DAY',verification='VERIFIED'):
 if status not in POSITIVE|NEGATIVE|{'UNKNOWN'}:raise ValueError('INVALID_MANAGEMENT_STATUS')
 if not sources:raise ValueError('MANAGEMENT_REQUIRES_EVIDENCE')
 row=[club_id,player_id,status,effective_from,end_exclusive,observed_on,verification,precision,json.dumps(sources,ensure_ascii=False,sort_keys=True),reason];key=hashlib.sha256(json.dumps(row,ensure_ascii=False).encode()).hexdigest();db.execute('INSERT OR IGNORE INTO club_management_events VALUES(?,?,?,?,?,?,?,?,?,?,?)',[key,*row]);return key

def decision(db,club_id,player_id,on):
 rows=[dict(r) for r in db.execute('SELECT * FROM club_management_events WHERE club_id=? AND player_id=?',(club_id,player_id))] if db.execute("SELECT 1 FROM sqlite_master WHERE name='club_management_events'").fetchone() else []
 rows=[r for r in rows if r['verification_status']=='VERIFIED' and (r['effective_from'] or r['observed_on'])<=on and (r['status'] in NEGATIVE or not r['end_exclusive'] or on<r['end_exclusive'])]
 # A verified departure/loan-out persists until an explicit dated return/re-signing event, not an ownership profile.
 negatives=[r for r in rows if r['status'] in NEGATIVE]
 positives=[r for r in rows if r['status'] in POSITIVE]
 last_out=max(negatives,key=lambda r:(r['observed_on'],r['effective_from'] or r['observed_on']),default=None)
 if last_out:
  returned=[r for r in positives if (r['effective_from'] or r['observed_on'])>(last_out['effective_from'] or last_out['observed_on']) and r['date_precision'] in ('EXPLICIT_RETURN_DAY','EXPLICIT_SIGNING_DAY')]
  if not returned and last_out['end_exclusive'] and on>=last_out['end_exclusive']:return dict(state='UNKNOWN',included=False,status='RETURN_UNVERIFIED',sources=json.loads(last_out['source_json']),reason='Scheduled loan end is not proof of return')
  if not returned:return dict(state='EXCLUDED',included=False,status=last_out['status'],effective_from=last_out['effective_from'],end_exclusive=last_out['end_exclusive'],date_precision=last_out['date_precision'],sources=json.loads(last_out['source_json']),reason=last_out['reason'])
  positives=returned
 if not positives:return dict(state='UNKNOWN',included=False,status='UNKNOWN',sources=[],reason='No current verified management assertion; historic appearances and ownership are insufficient')
 r=max(positives,key=lambda r:(r['observed_on'],r['effective_from'] or ''));return dict(state='INCLUDED',included=True,status=r['status'],effective_from=r['effective_from'],end_exclusive=r['end_exclusive'],date_precision=r['date_precision'],sources=json.loads(r['source_json']),reason=r['reason'])

def import_verified_history(db):
 # Legacy verified FIRST_TEAM/LOAN_IN/injury evidence is a dated assertion; never import unknown, match unions or contract-only rows.
 for r in db.execute("SELECT * FROM player_eligibility WHERE verification_status='VERIFIED'").fetchall():
  r=dict(r);status=r['classification']
  if status not in POSITIVE|NEGATIVE:continue
  source=json.loads(r['source_json'] or '[]')
  if not source:continue
  add_event(db,r['club_id'],r['player_id'],status,r['start_date'],r['verified_as_of'],source,'Retained independently verified affiliation assertion: '+(r['reason'] or ''),precision='LEGACY_KNOWN_BY_DAY' if status in NEGATIVE else 'LEGACY_VERIFIED_START_OR_SNAPSHOT')
