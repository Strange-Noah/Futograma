"""Evidence-backed membership reconciliation shared by onboarding and updates.

Only append existing management events and decision provenance; never mutate
participation/history. Provider squad assertions remain explicit obligations.
"""
import hashlib,json,re
from datetime import date
from .managed_roster import decision as managed_decision,add_event,DDL as MANAGEMENT_DDL
from .roster_policy import roster_decision

POSITIVE={'FIRST_TEAM','FIRST_TEAM_INJURED','LOAN_IN'}
NEGATIVE={'TRANSFERRED_OUT','LOAN_OUT'}
ASSERTIONS=POSITIVE|NEGATIVE|{'YOUTH','EXPLICIT_RETURN','NEVER_BELONGED','IDENTITY_MISMATCH'}
DDL='''CREATE TABLE IF NOT EXISTS quality_membership_decisions(decision_id TEXT PRIMARY KEY,club_id INTEGER NOT NULL,player_id INTEGER NOT NULL,reviewed_on TEXT NOT NULL,payload_json TEXT NOT NULL);'''

def encoded(x):return json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def digest(x):return hashlib.sha256(encoded(x).encode()).hexdigest()
def valid_evidence(items):
 return bool(items) and all(isinstance(e,dict) and re.fullmatch('[a-fA-F0-9]{64}',e.get('sha256','')) and str(e.get('source_url') or e.get('url') or '').startswith('https://') for e in items)
def trusted(o):
 return o.get('identity_status')=='VERIFIED' and o.get('verification_status')=='VERIFIED' and o.get('source_scope') in {'OFFICIAL_ROSTER','VERIFIED_TRANSFER','AUTHORIZED_MANUAL'} and valid_evidence(o.get('evidence',[]))

def evaluate(prior,observations,on):
 """Pure decision: membership is distinct from identity, participation and age."""
 date.fromisoformat(on);evidence=[e for o in observations for e in o.get('evidence',[])];obligations=[];accepted=[]
 for o in observations:
  status=o.get('assertion')
  if status not in ASSERTIONS:raise ValueError('INVALID_MEMBERSHIP_ASSERTION')
  if status=='IDENTITY_MISMATCH':obligations.append('SOURCE_IDENTITY_MISMATCH');continue
  if not trusted(o):obligations.append('INSUFFICIENT_MEMBERSHIP_EVIDENCE');continue
  start=o.get('effective_from')
  if start:
   date.fromisoformat(start)
   if start>on:continue
  elif status in NEGATIVE|{'EXPLICIT_RETURN'}:
   obligations.append('EFFECTIVE_DATE_UNVERIFIED');continue
  accepted.append(o)
 # A same-day contradictory current assertion is never silently last-writer-wins.
 latest_day=max((o.get('effective_from') or o.get('observed_on') or on for o in accepted),default=None)
 active={o['assertion'] for o in accepted if (o.get('effective_from') or o.get('observed_on') or on)==latest_day}
 conflict=bool(active&POSITIVE and active&NEGATIVE) or ('NEVER_BELONGED' in active and bool(active&POSITIVE))
 if conflict or 'SOURCE_IDENTITY_MISMATCH' in obligations or ('NEVER_BELONGED' in active and prior.get('included')):return dict(decision='CONFLICTED',included=prior.get('included',False),cohort=prior.get('cohort','UNKNOWN'),status=prior.get('status','UNKNOWN'),reason='CONTRADICTORY_MEMBERSHIP_ASSERTIONS',obligation=dict(status='CONFLICTED',evidence=evidence,reason='CONTRADICTORY_MEMBERSHIP_ASSERTIONS'),events=[])
 events=[];selected=max(accepted,key=lambda o:(o.get('effective_from') or o.get('observed_on') or on,o.get('observed_on') or on),default=None)
 if selected:
  status=selected['assertion'];start=selected.get('effective_from') or selected.get('observed_on') or on
  if start>on:raise ValueError('OBSERVATION_FROM_FUTURE')
  # A youth card cannot erase a previously verified first-team management event.
  if status=='YOUTH' and prior.get('included'):
   obligations.append('YOUTH_ASSERTION_DOES_NOT_DOWNGRADE_FIRST_TEAM');selected=None
  elif status in POSITIVE|NEGATIVE|{'EXPLICIT_RETURN'}:
   mapped='FIRST_TEAM' if status=='EXPLICIT_RETURN' else status
   events=[dict(status=mapped,effective_from=selected.get('effective_from'),observed_on=selected.get('observed_on') or on,sources=selected['evidence'],reason='Unified membership reconciliation: '+status,end_exclusive=selected.get('end_exclusive'),precision='EXPLICIT_RETURN_DAY' if status=='EXPLICIT_RETURN' else 'DAY' if selected.get('effective_from') else 'OBSERVED_SNAPSHOT')]
 if selected and selected['assertion'] in {'YOUTH','NEVER_BELONGED'}:
  status=selected['assertion'];result=dict(decision='YOUTH' if status=='YOUTH' else 'EXCLUDED',included=False,cohort='YOUTH' if status=='YOUTH' else 'EXCLUDED',status=status,reason='VERIFIED_'+status)
 elif selected:
  status=selected['assertion'];included=status in POSITIVE|{'EXPLICIT_RETURN'};result=dict(decision='INCLUDED' if included else 'EXCLUDED',included=included,cohort='FIRST_TEAM' if included else 'EXCLUDED',status='FIRST_TEAM' if status=='EXPLICIT_RETURN' else status,reason='VERIFIED_CURRENT_MEMBERSHIP')
 else:
  result=dict(decision=prior.get('state','UNKNOWN'),included=prior.get('included',False),cohort=prior.get('cohort','FIRST_TEAM' if prior.get('included') else 'UNKNOWN'),status=prior.get('status','UNKNOWN'),reason='PRIOR_VERIFIED_MEMBERSHIP_RETAINED' if prior.get('included') or prior.get('state')=='EXCLUDED' else 'INSUFFICIENT_MEMBERSHIP_EVIDENCE')
 satisfied=selected is not None or prior.get('state') in {'INCLUDED','EXCLUDED'} or (prior.get('state')=='YOUTH' and bool(prior.get('sources')))
 result.update(obligation=dict(status='SATISFIED' if satisfied else 'MISSING',evidence=evidence or prior.get('sources',[]),reason=result['reason']),pending_reasons=sorted(set(obligations)),events=events)
 return result

def reconcile(db,club_id,encountered_player_ids,on,observations=()):
 """Append audited reconciliation; caller owns transaction, no implicit commit."""
 date.fromisoformat(on);db.execute(MANAGEMENT_DDL);db.execute(DDL)
 grouped={}
 for o in observations:
  if o.get('club_id')!=club_id or not isinstance(o.get('player_id'),int):raise ValueError('EXACT_CLUB_AND_PLAYER_ID_REQUIRED')
  if not db.execute('SELECT 1 FROM players WHERE player_id=?',(o['player_id'],)).fetchone():raise ValueError('REGISTERED_PLAYER_ID_REQUIRED')
  grouped.setdefault(o['player_id'],[]).append(o)
 ids=set(encountered_player_ids)|set(grouped)
 # Opposite direction: inspect currently included identities even if absent from captures.
 ids|={r[0] for r in db.execute('SELECT DISTINCT player_id FROM club_management_events WHERE club_id=?',(club_id,))}
 decisions=[]
 for pid in sorted(ids):
  if not db.execute('SELECT 1 FROM players WHERE player_id=?',(pid,)).fetchone():
   decisions.append(dict(player_id=pid,club_id=club_id,decision='QUARANTINED',included=False,cohort='UNKNOWN',obligation=dict(status='MISSING',evidence=[],reason='REGISTERED_PLAYER_ID_REQUIRED')));continue
  prior=roster_decision(db,club_id,pid,on);result=evaluate(prior,grouped.get(pid,[]),on)
  for event in result.pop('events',[]):add_event(db,club_id,pid,**event)
  # Existing management chronology (e.g. loan expiry) remains authoritative.
  if result['decision'] not in {'YOUTH','CONFLICTED'} and grouped.get(pid) and result['status'] not in {'NEVER_BELONGED'}:
   actual=managed_decision(db,club_id,pid,on)
   if actual['state'] in {'INCLUDED','EXCLUDED'}:
    result.update(decision=actual['state'],included=actual['included'],cohort='FIRST_TEAM' if actual['included'] else 'EXCLUDED',status=actual['status'],obligation=dict(status='SATISFIED',evidence=actual['sources'],reason=actual['reason']))
   elif actual.get('status')=='RETURN_UNVERIFIED':result.update(decision='UNKNOWN',included=False,cohort='UNKNOWN',status='RETURN_UNVERIFIED',obligation=dict(status='MISSING',evidence=actual['sources'],reason=actual['reason']))
  if result['decision']=='YOUTH' and result['obligation']['status']=='SATISFIED':
   db.execute("CREATE TABLE IF NOT EXISTS roster_verification_dossiers(dossier_id TEXT PRIMARY KEY,club_id INTEGER,player_id INTEGER,observed_at TEXT,identity_status TEXT,affiliation_status TEXT,registration_status TEXT,management_status TEXT,payload_json TEXT)")
   dossier=dict(club_id=club_id,player_id=pid,identity_status='VERIFIED',academy_affiliation_status='VERIFIED_CURRENT_ACADEMY_EVIDENCE',management=dict(state='UNKNOWN',included=False,status='UNKNOWN',academy_affiliation=result['obligation']['evidence'],reason='Verified academy affiliation; first-team inclusion not inferred'),provenance=grouped.get(pid,[]))
   db.execute('INSERT OR IGNORE INTO roster_verification_dossiers VALUES(?,?,?,?,?,?,?,?,?)',(digest(dossier),club_id,pid,on,'VERIFIED','VERIFIED_CURRENT_ACADEMY_EVIDENCE','NOT_ASSESSED','YOUTH',encoded(dossier)))
  result.update(player_id=pid,club_id=club_id,reviewed_on=on,provenance=grouped.get(pid,[]),historical_facts_preserved=True)
  key=digest(result);db.execute('INSERT OR IGNORE INTO quality_membership_decisions VALUES(?,?,?,?,?)',(key,club_id,pid,on,encoded(result)));decisions.append(result)
 return dict(decision='REVIEW_REQUIRED' if any(r['obligation']['status']!='SATISFIED' for r in decisions) else 'VERIFIED',decisions=decisions,obligations={str(r['player_id']):r['obligation'] for r in decisions},evidence=[e for o in observations for e in o.get('evidence',[])])
