"""Cloud-isolated international acceptance. Source limitations remain visible in coverage."""
import os,json,gzip,tarfile,hashlib,sqlite3,subprocess,sys
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import inspect,sha,snapshot,pack
from squad_pipeline.international_discovery import profile_hints,team_fixture_hints,payload
from squad_pipeline.international_fotmob import parse_public_match
from squad_pipeline.international_store import migrate,evidence,team,put_match,put_appearance,encoded,digest,import_reference_evidence_package,bridge_references,complete_missing_minutes
from squad_pipeline.international_retry import observe
from squad_pipeline.international_sky_fallback import acquire_sky_alternatives
from urllib.parse import urlsplit
from squad_pipeline.source_redirects import fetch_public_redirects
from squad_pipeline.provider_identity_discovery import verify_team_identities,provider_candidates
from squad_pipeline.identity_pipeline import resolve
from squad_pipeline.identity_sources import fotmob as parse_identity_profile
from squad_pipeline.identity_pipeline import DDL,Record,persist_verified_alias,similarity
from squad_pipeline.managed_roster import migrate as management_migrate,import_verified_history,add_event
from squad_pipeline.cloud.all_match_export import export_all_matches
from squad_pipeline.cloud.export_publish import publish_with_export_preflight
from squad_pipeline.pending_matches import migrate as club_retry_migrate
from squad_pipeline.live_sources import Client,HOSTS

def initial_history_start(on):
 from datetime import date
 d=date.fromisoformat(on);year=d.year if d.month>=7 else d.year-1
 return f'{year}-07-01'

def personal_window(db,pid,normal_start,on):
 seen=db.execute('SELECT completed_at FROM pipeline_checkpoints WHERE scope=?',('player-history:'+str(pid),)).fetchone()
 if not seen and db.execute("SELECT 1 FROM sqlite_master WHERE name='player_discovery_coverage'").fetchone():seen=db.execute("SELECT 1 FROM player_discovery_coverage WHERE player_id=? AND history_status NOT IN ('PENDING','FAILED') LIMIT 1",(pid,)).fetchone()
 return (normal_start,False) if seen else (min(normal_start,initial_history_start(on)),True)

def acquire(db,fetch,client,window_start,window_end,run_id,asset_ref,not_before_utc=None,not_after_utc=None):
 from squad_pipeline.roster_policy import materialize,latest
 from squad_pipeline.minute_reconciliation import apply_existing
 START,END=window_start,window_end
 db.execute('CREATE TABLE IF NOT EXISTS pipeline_checkpoints(scope TEXT PRIMARY KEY,completed_at TEXT)')
 migrate(db);club_retry_migrate(db);db.executescript(DDL);management_migrate(db);import_verified_history(db)
 changes=apply_existing(db);dossiers=latest(db);cohorts=materialize(db,END)
 managed={r['player_id'] for r in cohorts if r['cohort']=='FIRST_TEAM'}
 clubs=defaultdict(list)
 for r in cohorts:clubs[r['player_id']].append(r['club_id'])
 names={r['player_id']:r['display_name'] for r in db.execute('SELECT * FROM players')}
 ids={str(r['provider_player_id']):int(r['player_id']) for r in db.execute("SELECT * FROM verified_identity_links WHERE provider='FotMob'")}
 roster=dict(players=list(dossiers.values()),source_results=[])
 # Current verified certificates supply cross-provider identity evidence; not historic acceptance assets.
 for row in db.execute('SELECT evidence_json FROM verified_name_aliases'):
  result=json.loads(row[0]);roster['source_results'].append(result)
 for d in roster['players']:
  d.setdefault('name',names[int(d['player_id'])]);d.setdefault('remaining_requirements',[])
 db.commit()
 HOSTS.add('www.fotmob.com');fixtures={};claims=defaultdict(list);affiliations={};initial_teams={};coverage={};captures=[];errors=[];parsed=[];conflicts=[];canonical_rows=0;held=[];now=datetime.now(timezone.utc).isoformat()
 def add_fixture(f,outside_window=False):
  from squad_pipeline.international_fotmob import instant
  if not outside_window and ((not_before_utc and instant(f['kickoff'])<instant(not_before_utc)) or (not_after_utc and instant(f['kickoff'])>instant(not_after_utc))):return
  key=f['provider_match_id'];old=fixtures.get(key)
  if old and any(old[k]!=f[k] for k in ('home','away','competition_key','kickoff','level')):
   from squad_pipeline.international_fotmob import instant
   if any(old[k]!=f[k] for k in ('home','away','competition_key','level')) or instant(old['kickoff'])!=instant(f['kickoff']):raise ValueError('DISCOVERED_FIXTURE_CONFLICT')
  fixtures[key]=f
 def pending(entity,provider_id,url,error,pid=None,fixture=None,source=None):
  if source and source.get('sha256'):evidence(db,dict(source,retained_asset_ref=asset_ref),'UNRESOLVED_DISCOVERY_EVIDENCE','UNACCEPTED_SOURCE_CLAIM',1)
  task=dict(entity_type=entity,provider='FotMob',provider_entity_id=provider_id,url=url,player_id=pid,date=(fixture or {}).get('kickoff','')[:10],fixture=fixture,source=source)
  result=dict(status='SOURCE_UNAVAILABLE' if ('SOURCE_UNAVAILABLE:' in str(error) or 'NETWORK_BUDGET_EXHAUSTED' in str(error)) else 'PENDING',error=str(error));observe(db,task,result,run_id);errors.append(dict(player_id=pid,entity=entity,provider_id=provider_id,error=str(error)))
 provider_discovery=[]
 for cid in sorted({c for pid in managed for c in clubs[pid]}):
  url='https://www.fotmob.com/teams/'+str(cid)+'/squad'
  try:
   source,body=fetch(url,'cross_provider_identity_discovery:'+str(cid))
   for record,result in verify_team_identities(body,source,cid,roster['players'],roster['source_results']):
    if result['identity_status']!='VERIFIED':
     provider_discovery.append(dict(club_id=cid,status=result['identity_status'],names=record.names,remaining_requirements=result['remaining_requirements']));continue
    fid=record.provider_ids['FotMob'];pid=int(result['player_id'])
    if fid in ids and ids[fid]!=pid:raise ValueError('IdentityError:PROVIDER_DISCOVERY_CANONICAL_COLLISION')
    old={f for f,p in ids.items() if p==pid}
    if old and fid not in old:raise ValueError('IdentityError:CANONICAL_MULTIPLE_FOTMOB_IDS')
    ids[fid]=pid;persist_verified_alias(db,record,result,END)
    for dossier in roster['players']:
     if int(dossier['player_id'])==pid and dossier['identity_status']!='VERIFIED':
      dossier['identity_status']='VERIFIED';dossier['verified_source_records'].append(record.evidence());dossier['remaining_requirements']=[requirement for requirement in dossier.get('remaining_requirements',[]) if requirement!='Independent stable-ID/biography verification'];data=encoded(dossier);db.execute('INSERT OR IGNORE INTO roster_verification_dossiers VALUES(?,?,?,?,?,?,?,?,?)',(digest(dossier),dossier['club_id'],pid,now,'VERIFIED',dossier['academy_affiliation_status'],'REGISTERED' if dossier['management'].get('registration') else 'UNCONFIRMED',dossier['management']['state'],data))
    provider_discovery.append(dict(club_id=cid,player_id=pid,provider_player_id=fid,status='VERIFIED',proof=result['identity_proof'],source_sha256=source['sha256']))
  except Exception as exc:pending('TEAM_PROVIDER_IDENTITY',str(cid),url,type(exc).__name__+':'+str(exc))
 identity_candidates=provider_candidates(roster['players'],roster['source_results']);prefetched_profiles={}
 for pid in sorted(managed):
  own=[fid for fid,canonical in ids.items() if canonical==pid];coverage[pid]=dict(history_status='PENDING',affiliation_status='PENDING',fixture_status='PENDING',lineup_status='PENDING',evidence=[])
  if not own and pid>0:
   # A numeric canonical value is only a URL candidate. It cannot authorize a provider linkage.
   hint_url='https://www.fotmob.com/players/'+str(pid)
   try:
    hint_source,hint_body=fetch(hint_url,'untrusted_numeric_profile_hint:'+str(pid));record=parse_identity_profile(hint_body,hint_source)[0];verification=resolve(record,identity_candidates)
    if verification['identity_status']=='VERIFIED' and int(verification['player_id'])==pid:
     fid=record.provider_ids['FotMob']
     if fid in ids and ids[fid]!=pid:raise ValueError('IdentityError:PROVIDER_HINT_COLLISION')
     ids[fid]=pid;persist_verified_alias(db,record,verification,END);prefetched_profiles[fid]=(hint_source,hint_body);own=[fid];provider_discovery.append(dict(player_id=pid,provider_player_id=fid,status='VERIFIED_FROM_UNTRUSTED_URL_HINT',proof=verification['identity_proof']))
    else:provider_discovery.append(dict(player_id=pid,status='HINT_REJECTED',identity_result=verification))
   except Exception as exc:provider_discovery.append(dict(player_id=pid,status='HINT_UNAVAILABLE',error=type(exc).__name__+':'+str(exc)))
  if len(own)!=1:
   pending('PLAYER_IDENTITY',str(pid),'https://www.fotmob.com','IdentityError:VERIFIED_FOTMOB_ID_REQUIRED',pid);continue
  fid=own[0];url='https://www.fotmob.com/players/'+fid
  try:
   source,body=prefetched_profiles.pop(fid) if fid in prefetched_profiles else fetch(url,'managed_player_personal_history:'+str(pid));d=payload(body)
   if str(d['id'])!=fid or similarity(names[pid],d.get('name',''))<.6:raise ValueError('IdentityError:PROFILE_ID_OR_NAME_CONFLICT')
   from squad_pipeline.provider_identity_discovery import verify_profile_aliases
   verify_profile_aliases(db,body,source,pid,identity_candidates,END)
   history_start,initial_history=personal_window(db,pid,START,END);h=profile_hints(body,source,history_start,END);coverage[pid].update(history_status='PERSONAL_HISTORY_SCANNED',affiliation_status='EXPLICIT_CAREER_TEAMS' if h['affiliations'] else 'NO_EXPLICIT_TEAM_IN_PUBLIC_CAREER;NOT_NO_APPEARANCES',evidence=[source])
   for a in h['affiliations']:
    if initial_history:initial_teams[a['provider_team_id']]=min(initial_teams.get(a['provider_team_id'],history_start),history_start)
    affiliations.setdefault(a['provider_team_id'],[]).append((pid,a));eid=evidence(db,dict(a['source'],retained_asset_ref=asset_ref,json_path=a['source_path'],national_career_claim={k:a.get(k) for k in ('provider_player_id','provider_team_id','name','level','start_date','end_date','scope','verification_status')}),a['scope'],a['verification_status'],1);tk=team(db,a['name'],a['level'],eid,'FotMob',a['provider_team_id']);db.execute('INSERT OR IGNORE INTO national_affiliation_events VALUES(?,?,?,?,?,?,?,?,?,?)',(digest([pid,a['provider_team_id'],a.get('start_date'),a.get('end_date'),a['verification_status']]),pid,tk,a.get('start_date'),None,END,'EXPLICIT_CAREER_RANGE',a['verification_status'],eid,'PROVIDER_CAREER_RANGE;END_DATE_NOT_ASSUMED_EXCLUSIVE'))
   for f in h['matches']:add_fixture(f,outside_window=initial_history);claims[f['provider_match_id']].append(f)
   db.execute('INSERT OR REPLACE INTO pipeline_checkpoints VALUES(?,?)',('player-history:'+str(pid),now))
  except Exception as exc:pending('PLAYER_HISTORY',fid,url,type(exc).__name__+':'+str(exc),pid)
  print('PLAYER_DISCOVERY',pid,coverage[pid]['history_status'],flush=True)
 for tid,entries in sorted(affiliations.items()):
  url='https://www.fotmob.com/teams/'+tid+'/fixtures'
  try:
   source,body=fetch(url,'verified_national_team_fixture_fallback:'+tid);a=entries[0][1];rows=team_fixture_hints(body,source,a,initial_teams.get(tid,START),END)
   for f in rows:add_fixture(f,outside_window=tid in initial_teams)
   for pid,a in entries:coverage[pid]['fixture_status']='PUBLIC_TEAM_FIXTURES_SCANNED;BOUNDARY_NOT_FULL_HISTORY_PROOF'
  except Exception as exc:
   pending('NATIONAL_TEAM_FIXTURES',tid,url,type(exc).__name__+':'+str(exc));
   for pid,a in entries:coverage[pid]['fixture_status']='FALLBACK_FAILED'
 from squad_pipeline.international_retry import due
 for retry in due(db):
  if retry.get('player_id') is not None and retry['player_id'] not in managed:continue
  fixture=retry.get('fixture')
  if fixture and fixture.get('provider_match_id'):
   add_fixture(fixture,outside_window=True)
   if fixture.get('discovery_claim') and retry.get('player_id') in managed:
    provider_ids=[fid for fid,pid in ids.items() if pid==retry['player_id']]
    if len(provider_ids)==1:claims[fixture['provider_match_id']].append(dict(fixture,player_provider_id=provider_ids[0],discovery_claim=fixture['discovery_claim'],source=retry.get('source') or {}))
 print('FIXTURES_DISCOVERED',len(fixtures),flush=True)
 for mid,f in sorted(fixtures.items()):
  frozen=db.execute("SELECT state FROM international_acquisition_queue WHERE entity_type='MATCH' AND provider='FotMob' AND provider_entity_id=? AND state IN ('QUARANTINED','HUMAN_REVIEW')",(mid,)).fetchone()
  if frozen:
   errors.append(dict(entity='MATCH',provider_id=mid,error='EXISTING_'+frozen['state']+';NO_AUTOMATIC_FACT_WRITE'));continue
  row_count_before=canonical_rows;source=None;db.execute('SAVEPOINT acquired_international_match')
  try:
   source,body=fetch(f['url'],'international_match_lineup:'+mid);sheet=parse_public_match(body,source,f,claims[mid]);eid=evidence(db,dict(source,retained_asset_ref=asset_ref),'EXACT_MATCH_IDS_SIDES_DATE_AND_LINEUP','RAW_RESPONSE_VERIFIED',1);teams={s['side']:team(db,s['name'],sheet['level'],eid,'FotMob',s['provider_team_id']) for s in sheet['sheets']};match=dict(match_id='nt_fotmob_'+mid,home_team_key=teams['home'],away_team_key=teams['away'],kickoff_utc=sheet['kickoff_utc'],madrid_date=sheet['madrid_date'],competition_key=sheet['competition_key'],stage=None,match_state='FINISHED',score=sheet['score'],duration_minutes=sheet['duration_minutes'],extra_time_status=sheet['extra_time_status'],identity_status='PROVIDER_ID_SIDES_DATE_VERIFIED',metadata_json=encoded(dict(competition=sheet['competition_label'],full_squad_identity_status=sheet['full_squad_identity_status'],identity_issues=sheet['identity_issues'])));accepted=put_match(db,match,'FotMob',mid,eid);accepted_players=[]
   for side in sheet['sheets']:
    for p in side['players']:
     pid=ids.get(p['provider_player_id'])
     if pid not in managed:continue
     if similarity(names[pid],p['name'])<.6:raise ValueError('IdentityError:LINEUP_CANONICAL_NAME_CONFLICT')
     a=dict(match_id=accepted,player_id=pid,represented_team_key=teams[side['side']],participation_status=p['participation_status'],minutes=p['minutes'],reported_minutes=p['reported_minutes'],derived_minutes=p['derived_minutes'],minutes_precision=p['minutes_precision'],verification_status=p['verification_status'],evidence_scope=p['evidence_scope'],revision=1,metadata_json=encoded(dict(source_path=p['source_path'],provider_player_id=p['provider_player_id'],shirt=p['shirt'],role=p['role'],minute_observations=p.get('minute_observations',[]))))
     old=db.execute('SELECT * FROM international_appearances WHERE match_id=? AND player_id=?',(accepted,pid)).fetchone()
     if old:
      if old['minutes'] is None and a['minutes'] is not None and not old['verification_status'].startswith('RETAINED_PRIOR'):
       complete_missing_minutes(db,a,eid);old=db.execute('SELECT * FROM international_appearances WHERE match_id=? AND player_id=?',(accepted,pid)).fetchone()
      if a['minutes'] is not None and old['minutes']!=a['minutes']:
       from squad_pipeline.minute_policy import select,audit
       selection=select([dict(status=old['participation_status'],minutes=old['minutes'],provider='RETAINED_ACCEPTED'),dict(status=a['participation_status'],minutes=a['minutes'],provider='FotMob')]);audit(db,'NATIONAL',accepted,pid,selection)
       if selection['accepted']:decision=selection['reason'];agree=1
       else:conflicts.append(dict(player_id=pid,match_id=mid,retained_minutes=old['minutes'],new_minutes=a['minutes'],reason=selection['reason']));decision='RETAINED_ORIGINAL_ADJUDICATION';agree=0
      else:decision='CORROBORATES_EXISTING_REFERENCE';agree=1
     else:canonical_rows+=int(put_appearance(db,a));decision='NEW_EXACT_PROVIDER_ID_FACT';agree=1
     db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(accepted,pid,eid,'lineup_and_minutes',encoded(a),agree,decision))
     if p['history_source']:
      he=evidence(db,dict(p['history_source'],retained_asset_ref=asset_ref),'PERSONAL_HISTORY_TARGET_MINUTES','PROVIDER_ASSERTION_WITH_LINEUP_ID_CHECK',1);db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(accepted,pid,he,'reported_minutes',encoded(dict(minutes=p['minutes'],scope=p['evidence_scope'])),agree,decision))
     if a['minutes'] is None:
      pending('PLAYER_MINUTES',mid+':'+str(pid),f['url'],'SOURCE_MINUTES_INCOMPLETE',pid,dict(f,represented_team_provider_id=p['represented_team_provider_id']),source=source)
     else:
      observe(db,dict(entity_type='PLAYER_MINUTES',provider='FotMob',provider_entity_id=mid+':'+str(pid),url=f['url'],player_id=pid,fixture=f),dict(status='UNCHANGED',match_id=accepted),run_id)
     accepted_players.append(pid)
    # Unknown source IDs are held as structural issues; they never become canonical player 0.
   parsed.append(dict(provider_match_id=mid,canonical_match_id=accepted,players=accepted_players,structural_identity=sheet['full_squad_identity_status'],identity_issues=sheet['identity_issues']));observe(db,dict(entity_type='MATCH',provider='FotMob',provider_entity_id=mid,url=f['url'],fixture=f),dict(status='DISCOVERED',match_id=accepted),run_id)
  except Exception as exc:
   db.execute('ROLLBACK TO acquired_international_match');canonical_rows=row_count_before
   pending('MATCH',mid,f['url'],type(exc).__name__+':'+str(exc),fixture=f,source=source)
   for c in claims[mid]:
    pid=ids.get(c['player_provider_id'])
    if pid in managed:pending('PLAYER_MATCH',mid+':'+str(pid),f['url'],type(exc).__name__+':'+str(exc),pid,dict(f,represented_team_provider_id=c['represented_team_provider_id'],discovery_claim=c['discovery_claim']),source=source)
  db.execute('RELEASE acquired_international_match')
  print('MATCH_DISCOVERY',mid,'parsed' if parsed and parsed[-1]['provider_match_id']==mid else 'held',flush=True)
 alternatives=acquire_sky_alternatives(db,fetch,fixtures,claims,ids,managed,clubs,END,run_id,asset_ref);canonical_rows+=alternatives['new_rows']

 db.commit()
 return dict(window_start=START,window_end=END,managed_players=len(managed),youth_players=sum(r['cohort']=='YOUTH' for r in cohorts),profiles_scanned=len(coverage),fixtures_discovered=len(fixtures),parsed_matches=parsed,independent_alternatives=alternatives,minute_policy_changes=changes,errors=errors,live_requests=client.calls,coverage_policy='ACTUAL_APPEARANCES_ONLY;NO_CALENDAR_GAP_CHECKS',total=dict(db.execute('SELECT COUNT(*) AS appearances,COUNT(DISTINCT player_id) AS players,COUNT(DISTINCT match_id) AS matches FROM international_appearances').fetchone()))
