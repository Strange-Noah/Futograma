"""One evidence contract for onboarding, backfill, incremental and delayed replay.

Discovery breadth belongs to the caller. This coordinator never fetches sources,
resolves quarantines, or infers management from appearances.
"""
import json,sqlite3
from . import canonical_fixture as canonical,quality_fixture as fixture_quality
from . import quality_lifecycle as lifecycle,quality_membership as membership

MODES={'ONBOARD','BACKFILL','INCREMENTAL','DELAYED_RECONCILIATION'}

def migrate(db):
 canonical.migrate(db);lifecycle.migrate(db)
 db.execute('CREATE TABLE IF NOT EXISTS quality_fixture_acceptance(match_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL,result_json TEXT NOT NULL)')
 db.execute('CREATE TABLE IF NOT EXISTS quality_unresolved_observations(observation_key TEXT PRIMARY KEY,entity_key TEXT NOT NULL,payload_json TEXT NOT NULL)')

def evidence_refs(proof):
 refs=[]
 for p in proof.get('field_evidence',{}).values():
  e=dict(provider=p.get('provider') or proof.get('provider'),source_url=p.get('url') or p.get('source_url'),sha256=p.get('sha256'))
  if e not in refs:refs.append(e)
 return refs

def check_mode(mode):
 if mode not in MODES:raise ValueError('UNSUPPORTED_PIPELINE_MODE')

def reconcile_membership(db,club_id,encountered,on,observations=(),mode='INCREMENTAL'):
 check_mode(mode);migrate(db);result=membership.reconcile(db,club_id,encountered,on,observations)
 for d in result['decisions']:
  pid=str(d['player_id']);ob=result['obligations'][pid] if pid in result['obligations'] else result['obligations'].get(d['player_id'])
  if ob is None:continue
  key=f'membership:{club_id}:{pid}';fp=lifecycle.fingerprint(d);previous=lifecycle.load(db,key)
  state=lifecycle.evaluate({'membership':ob},['membership'],fp,previous=previous)
  lifecycle.persist(db,key,state,on+'T00:00:00+00:00')
 return result

def resolve_fixture_identity(db,fixture,proof):
 """Resolve before using a preferred new ID; never merge two existing IDs."""
 f=fixture_quality.normalize(fixture);binding=db.execute('SELECT match_id FROM canonical_fixture_sources WHERE domain=? AND provider=? AND provider_match_id=?',('CLUB',f.get('provider'),str(f.get('provider_match_id')))).fetchone();mid=binding[0] if binding else None
 if mid is None and f.get('kickoff_utc'):
  candidates=[]
  for row in db.execute("SELECT match_id,edition FROM canonical_fixtures WHERE domain='CLUB' AND home_key=? AND away_key=? AND competition_key=?",(str(f['home']['id']),str(f['away']['id']),str(f['competition_key']))):
   if row['edition'] is not None and row['edition']!=f.get('season'):continue
   if canonical.instant(canonical.value(db,row['match_id'],'kickoff')[0])==canonical.instant(f['kickoff_utc']):candidates.append(row['match_id'])
  if len(candidates)>1:return f,proof,'AMBIGUOUS_EXISTING_CANONICAL_FIXTURE'
  if candidates:mid=candidates[0]
 if mid is not None and mid!=f['match_id']:
  if db.execute('SELECT 1 FROM canonical_fixtures WHERE match_id=?',(f['match_id'],)).fetchone():return f,proof,'REGISTERED_CANONICAL_IDS_MAY_NOT_BE_MERGED'
  f=dict(f,match_id=mid);proof=json.loads(canonical.encoded(proof))
  if 'match_id' in proof.get('field_evidence',{}):proof['field_evidence']['match_id']=dict(proof['field_evidence']['match_id'],value=mid,basis='DERIVED_FROM_UNIQUE_VERIFIED_FIXTURE_BINDING')
 return f,proof,None


def process_fixture(db,fixture,proof,on,mode='INCREMENTAL',stability_review=None,trigger=None):
 check_mode(mode);migrate(db);proof=dict(proof,provider=fixture.get('provider'));f,proof,resolution_error=resolve_fixture_identity(db,fixture,proof);key='fixture-source:'+str(f.get('provider'))+':'+str(f.get('provider_match_id'));fingerprint=lifecycle.fingerprint([f,proof]);previous=lifecycle.load(db,key)
 if previous and not lifecycle.revalidation_plan(previous,fingerprint,trigger) and registry_allows_reuse(db,previous.get('canonical_match_id') or f['match_id']):
  return dict(decision='ACCEPT',quality=previous,skipped_full_revalidation=True,match_id=previous.get('canonical_match_id') or f['match_id'])
 registry_anomaly=bool(previous and previous.get('state')=='STABLE' and not registry_allows_reuse(db,previous.get('canonical_match_id') or f['match_id']))
 if registry_anomaly:trigger=trigger or 'CANONICAL_ANOMALY'
 result=fixture_quality.evaluate(f,proof);refs=evidence_refs(proof);good=result['decision']=='ACCEPT';conflict=result['decision']=='CONFLICTED'
 obligations={k:lifecycle.obligation('SATISFIED' if ok else 'CONFLICTED' if conflict else 'MISSING',refs,reason) for k,ok,reason in [('fixture_identity',result['identity_state']=='VERIFIED','ORDERED_TEAM_PROVIDER_FIXTURE_COMPETITION_SEASON'),('provider_fixture_identity',str(f.get('provider_match_id'))==str(f.get('actual_provider_match_id')) and f.get('actual_provider_match_id') is not None,'STRICT_RETURNED_PROVIDER_ID'),('score_status',good,'COMPLETE_APPLICABLE_STATUS_AND_SCORE_CONTRACT')]}
 if resolution_error:
  good=False;obligations['fixture_identity']=lifecycle.obligation('CONFLICTED',refs,resolution_error)
 if registry_anomaly:
  good=False;obligations['score_status']=lifecycle.obligation('CONFLICTED',refs,'CANONICAL_REGISTRY_ANOMALY_REQUIRES_TARGETED_REVIEW')
 state=lifecycle.evaluate(obligations,list(obligations),fingerprint,previous,stability_review,trigger)
 good=good and state['decision']=='ACCEPT'
 if good:
  obs=fixture_quality.canonical_observation(f,proof)
  for side in ('home','away'):
   canonical.entity(db,'TEAM',f[side]['id'],f[side]['name'])
  canonical.entity(db,'COMPETITION',f['competition_key'],f.get('competition_label') or f['competition_key'])
  # A partial old identity is a blocker, never authorization to create a duplicate.
  binding=db.execute('SELECT match_id FROM canonical_fixture_sources WHERE domain=? AND provider=? AND provider_match_id=?',('CLUB',f['provider'],str(f['provider_match_id']))).fetchone()
  if not binding and f.get('kickoff_utc'):
   for row in db.execute("SELECT * FROM canonical_fixtures WHERE domain='CLUB' AND competition_key=?",(str(f['competition_key']),)):
    if canonical.value(db,row['match_id'],'kickoff')[0]!=canonical.instant(f.get('kickoff_utc')):continue
    if (row['home_key']==str(f['home']['id']) or row['away_key']==str(f['away']['id'])) and (row['home_key'],row['away_key'])!=(str(f['home']['id']),str(f['away']['id'])):
     state=lifecycle.evaluate(dict(obligations,fixture_identity=lifecycle.obligation('CONFLICTED',refs,'POSSIBLE_EXISTING_FIXTURE_REQUIRES_ENTITY_BRIDGE')),list(obligations),fingerprint,previous,trigger='IDENTITY_CONFLICT');good=False;break
  if good:
   try:
    c=canonical.reconcile(db,obs,create=True,registered_id=None if binding else f['match_id']);f=dict(f,match_id=c['match_id']);state['canonical_match_id']=c['match_id']
    if c['operation']=='SOURCE_CONFLICT':
     state=lifecycle.evaluate(dict(obligations,score_status=lifecycle.obligation('CONFLICTED',refs,'CANONICAL_SOURCE_CONFLICT')),list(obligations),fingerprint,previous,trigger='SOURCE_CORRECTION');good=False
   except ValueError as e:
    state=lifecycle.evaluate(dict(obligations,fixture_identity=lifecycle.obligation('CONFLICTED',refs,str(e))),list(obligations),fingerprint,previous,trigger='IDENTITY_CONFLICT');good=False
 if good:
  # Rebind derived canonical ID in proofs; provider ID proof remains unchanged.
  p=dict(proof,field_evidence=dict(proof['field_evidence']));p['field_evidence']['match_id']=dict(p['field_evidence']['match_id'],value=f['match_id']);result=fixture_quality.evaluate(f,p)
  db.execute('INSERT INTO quality_fixture_acceptance VALUES(?,?,?) ON CONFLICT(match_id) DO UPDATE SET payload_json=excluded.payload_json,result_json=excluded.result_json',(f['match_id'],canonical.encoded(f),canonical.encoded(result)))
 else:
  payload=dict(fixture=f,proof=proof,result=result,quality=state);db.execute('INSERT OR IGNORE INTO quality_unresolved_observations VALUES(?,?,?)',(canonical.digest(payload),key,canonical.encoded(payload)))
 lifecycle.persist(db,key,state,on+'T00:00:00+00:00')
 return dict(decision='ACCEPT' if good else 'QUARANTINE',quality=state,fixture=f,match_id=f.get('match_id'),skipped_full_revalidation=False,contract=result)

def participation_contract(player,source):
 """A listed bench player without explicit participation remains unknown."""
 evidence=[dict(provider=source.get('provider'),source_url=source.get('url'),sha256=source.get('sha256'))];status=player.get('participation_status');minutes=player.get('minutes');identity=bool(player.get('provider_player_id'))
 played=status in {'STARTER','SUBSTITUTE_USED'};nonplayed=status in {'UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD'};known=played or nonplayed
 numeric=isinstance(minutes,(int,float)) and not isinstance(minutes,bool) and 0<=minutes<=150 and int(minutes)==minutes
 conflict=(nonplayed and minutes not in (0,None)) or (minutes is not None and not numeric)
 return {'player_identity':lifecycle.obligation('SATISFIED' if identity else 'MISSING',evidence,'EXACT_LINEUP_AND_PLAYER_STAT_IDS'), 'participation':lifecycle.obligation('CONFLICTED' if conflict else 'SATISFIED' if known else 'MISSING',evidence,'EXPLICIT_PARTICIPATION_REQUIRED'), 'minutes':lifecycle.obligation('CONFLICTED' if conflict else 'SATISFIED' if numeric and known else 'MISSING',evidence,'REPORTED_MINUTES_OR_EXPLICIT_NONPARTICIPATION')}

def verify_participation(db,match_id,player,source,on,mode='INCREMENTAL'):
 check_mode(mode);migrate(db);key='appearance:'+str(match_id)+':'+str(player.get('provider_player_id'));obligations=participation_contract(player,source);fp=lifecycle.fingerprint([player,source]);previous=lifecycle.load(db,key);q=lifecycle.evaluate(obligations,list(obligations),fp,previous);lifecycle.persist(db,key,q,on+'T00:00:00+00:00');return q

def registry_allows_reuse(db,match_id):
 meta=canonical.metadata(db,match_id)
 if not meta or any(v.get('state')=='SOURCE_CONFLICT' for v in meta['fields'].values()):return False
 if not canonical.exists(db,'quality_fixture_acceptance'):return False
 row=db.execute('SELECT payload_json FROM quality_fixture_acceptance WHERE match_id=?',(match_id,)).fetchone()
 if not row:return False
 fixture=json.loads(row[0]);fields=meta['fields'];registered=db.execute('SELECT competition_key,edition FROM canonical_fixtures WHERE match_id=?',(match_id,)).fetchone()
 if str(fixture['home']['id'])!=str(meta['home_team_id']) or str(fixture['away']['id'])!=str(meta['away_team_id']) or str(fixture['competition_key'])!=str(registered[0]) or fixture['season']!=registered[1]:return False
 def value(field):return (fields.get(field) or {}).get('value')
 for field,key in [('home_score','home_score'),('away_score','away_score'),('match_status','status'),('kickoff_uncertainty','kickoff_uncertainty'),('score_unavailable_reason','score_unavailable_reason')]:
  observed=value(field)
  if field=='match_status':observed=fixture_quality.ALIASES.get(str(observed).upper(),str(observed).upper())
  if fixture.get(key)!=observed:return False
 return canonical.instant(fixture.get('kickoff_utc'))==canonical.instant(value('kickoff'))


def reusable_fixture(db,provider,provider_match_id,source_sha256,directory_sha256,trigger=None):
 """Skip full source parsing only after explicit reviewed stable obligations.

 Both retained source versions must match; status/correction/audit triggers
 force normal parsing. Returned ledger remains tied to the canonical registry
 by the ordinary export contract.
 """
 if trigger:return None
 previous=lifecycle.load(db,'fixture-source:'+provider+':'+str(provider_match_id))
 if not previous or previous.get('state')!='STABLE':return None
 review=previous.get('stability_review') or {};hashes={r.get('sha256') for r in review.get('evidence',[])}
 if not {source_sha256,directory_sha256}<=hashes:return None
 mid=previous.get('canonical_match_id');row=db.execute('SELECT payload_json,result_json FROM quality_fixture_acceptance WHERE match_id=?',(mid,)).fetchone()
 if not row or not registry_allows_reuse(db,mid):return None
 return dict(fixture=json.loads(row[0]),evidence=json.loads(row[1])['evidence'],quality=previous,match_id=mid)

def can_reuse_participation(db,match_id,source_fingerprint):
 migrate(db)
 db.execute('CREATE TABLE IF NOT EXISTS quality_participation_versions(match_id TEXT PRIMARY KEY,source_fingerprint TEXT NOT NULL)')
 row=db.execute('SELECT source_fingerprint FROM quality_participation_versions WHERE match_id=?',(match_id,)).fetchone()
 if not row or row[0]!=source_fingerprint:return False
 if canonical.exists(db,'quality_source_issues') and db.execute("SELECT 1 FROM quality_source_issues WHERE scope='CLUB_PARTICIPATION' AND (provider_match_id=? OR provider_match_id LIKE ?) LIMIT 1",(str(match_id),str(match_id)+':%')).fetchone():return False
 rows=db.execute('SELECT result_json FROM quality_records WHERE entity_key LIKE ?',('appearance:'+str(match_id)+':%',)).fetchall()
 if not rows:return False
 for result, in rows:
  if json.loads(result)['state'] not in {'VERIFIED','STABLE'}:return False
 return True

def mark_participation_version(db,match_id,source_fingerprint):
 can_reuse_participation(db,match_id,source_fingerprint)
 db.execute('INSERT INTO quality_participation_versions VALUES(?,?) ON CONFLICT(match_id) DO UPDATE SET source_fingerprint=excluded.source_fingerprint',(match_id,source_fingerprint))

def record_source_issue(db,scope,provider_match_id,reason,payload,on):
 """Source-specific debt never changes the acceptance of another source's fact."""
 migrate(db);db.execute('CREATE TABLE IF NOT EXISTS quality_source_issues(issue_key TEXT PRIMARY KEY,scope TEXT NOT NULL,provider_match_id TEXT,reason TEXT NOT NULL,payload_json TEXT NOT NULL)')
 key=canonical.digest([scope,str(provider_match_id),reason,payload]);db.execute('INSERT OR IGNORE INTO quality_source_issues VALUES(?,?,?,?,?)',(key,scope,str(provider_match_id),reason,canonical.encoded(payload)))
 source=payload.get('source') or {};refs=[dict(provider=source.get('provider'),source_url=source.get('url'),sha256=source.get('sha256'))] if source.get('sha256') else []
 status='CONFLICTED' if any(s in reason for s in ('MISMATCH','CONFLICT','DIFFERENCE')) else 'MISSING'
 obligation={'provider_fixture_identity':lifecycle.obligation(status,refs,reason)};entity='source-debt:'+scope+':'+str(provider_match_id)+':'+key
 lifecycle.persist(db,entity,lifecycle.evaluate(obligation,list(obligation),key),on+'T00:00:00+00:00');return key
