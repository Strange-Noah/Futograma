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
from squad_pipeline.provider_identity_discovery import verify_team_identities
from squad_pipeline.identity_pipeline import DDL,Record,persist_verified_alias,similarity
from squad_pipeline.managed_roster import migrate as management_migrate,import_verified_history,add_event
from squad_pipeline.cloud.all_match_export import export_all_matches
from squad_pipeline.live_sources import Client,HOSTS
REF='acceptance/phase3-unified';START='2026-07-01';END='2026-10-29'
def write(path,value):
 with Path(path).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def isolated():
 require_hosted()
 if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='UNCONFIRMED':raise RuntimeError('ISOLATION_REQUIRED')
def main():
 isolated();work=ROOT/'.cloud/phase3-international';work.mkdir(parents=True,exist_ok=False);token=os.environ['GITHUB_TOKEN'];prod=GitHubStore('Strange-Noah/Futograma-data',token,'main');store=GitHubStore('Strange-Noah/Futograma-data',token,REF);prod_before=prod.head();head,_=store.head();store.restore(work/'working.sqlite');before=inspect(work/'working.sqlite')['tables'];db=sqlite3.connect(work/'working.sqlite');db.row_factory=sqlite3.Row
 store.download(688943155,work/'identity.json.gz');asset=store.api('GET',store.base+'/releases/assets/688943155');assert asset['digest']=='sha256:'+sha(work/'identity.json.gz');audit=json.loads(gzip.decompress((work/'identity.json.gz').read_bytes()));roster=audit['roster'];management_migrate(db);import_verified_history(db);db.executescript(DDL)
 from dataclasses import fields
 for r in roster['source_results']:
  if r['identity_status']=='VERIFIED':
   record=Record(**{k:v for k,v in r['source_record'].items() if k in {f.name for f in fields(Record)}});persist_verified_alias(db,record,r,END)
 ids={};names={};managed=set();clubs=defaultdict(list)
 for d in roster['players']:
  pid=int(d['player_id']);names[pid]=d['name'];decision=d['management'];clubs[pid].append(d['club_id'])
  if decision['state']=='INCLUDED':managed.add(pid)
  if decision['state'] in ('INCLUDED','EXCLUDED') and decision.get('evidence'):add_event(db,d['club_id'],pid,decision['status'],decision.get('effective_from'),END,decision['evidence'],'Unified upstream raw-source verification',end_exclusive=decision.get('end_exclusive'),precision=decision.get('date_precision','OBSERVED_SNAPSHOT'))
  raw=encoded(d);db.execute('INSERT OR IGNORE INTO roster_verification_dossiers VALUES(?,?,?,?,?,?,?,?,?)',(digest(d),d['club_id'],pid,END,d['identity_status'],d['academy_affiliation_status'],'REGISTERED' if decision.get('registration') else 'UNCONFIRMED',decision['state'],raw))
  if d['identity_status']=='VERIFIED':
   for record in d['verified_source_records']:
    fid=record.get('provider_ids',{}).get('FotMob')
    if fid:
     if str(fid) in ids and ids[str(fid)]!=pid:raise ValueError('VERIFIED_PROVIDER_ID_COLLISION')
     ids[str(fid)]=pid
 db.commit();migrate(db);store.download(688695104,work/'legacy.tar.gz');assert sha(work/'legacy.tar.gz')=='93a0d9f0dda4d86c844b2d5830669b1e43440986d0305419ad96349e4af8ad86'
 with tarfile.open(work/'legacy.tar.gz') as t:
  package=json.load(t.extractfile('manifest.json'))
  for row in package['source_records']:assert hashlib.sha256(t.extractfile(row['retained_body_path']).read()).hexdigest()==row['retained_body_sha256']
 import_reference_evidence_package(db,package,'github://Strange-Noah/Futograma-data/releases/assets/688695104');bridge=bridge_references(db);assert all(x['state']=='INTEGRATED_PRIOR_TARGET_VERIFICATION' for x in bridge);assert db.execute('SELECT COUNT(*) FROM international_reference_bridge').fetchone()[0]==21;db.commit()
 prior_international_rows=db.execute('SELECT COUNT(*) FROM international_appearances').fetchone()[0]
 HOSTS.add('www.fotmob.com');client=Client('phase3-international-'+os.environ['GITHUB_RUN_ID'],budget=500);fixtures={};claims=defaultdict(list);affiliations={};coverage={};captures=[];errors=[];parsed=[];conflicts=[];canonical_rows=0;held=[];now=datetime.now(timezone.utc).isoformat()
 def fetch(url,purpose):
  r,b=client.fetch(url,purpose,conditional=False);captures.append(r)
  if r['status']!='SUCCESS':raise ValueError('SOURCE_UNAVAILABLE:'+str(r.get('error') or 'HTTP_'+str(r.get('http_status'))))
  return r,b.decode('utf-8','replace')
 def add_fixture(f):
  key=f['provider_match_id'];old=fixtures.get(key)
  if old and any(old[k]!=f[k] for k in ('home','away','competition_key','kickoff','level')):
   from squad_pipeline.international_fotmob import instant
   if any(old[k]!=f[k] for k in ('home','away','competition_key','level')) or instant(old['kickoff'])!=instant(f['kickoff']):raise ValueError('DISCOVERED_FIXTURE_CONFLICT')
  fixtures[key]=f
 def pending(entity,provider_id,url,error,pid=None,fixture=None,source=None):
  if source and source.get('sha256'):evidence(db,dict(source,retained_asset_ref='phase3-international-evidence-'+os.environ['GITHUB_RUN_ID']),'UNRESOLVED_DISCOVERY_EVIDENCE','UNACCEPTED_SOURCE_CLAIM',1)
  task=dict(entity_type=entity,provider='FotMob',provider_entity_id=provider_id,url=url,player_id=pid,date=(fixture or {}).get('kickoff','')[:10],fixture=fixture,source=source)
  result=dict(status='SOURCE_UNAVAILABLE' if ('SOURCE_UNAVAILABLE:' in str(error) or 'NETWORK_BUDGET_EXHAUSTED' in str(error)) else 'PENDING',error=str(error));observe(db,task,result,os.environ['GITHUB_RUN_ID']);errors.append(dict(player_id=pid,entity=entity,provider_id=provider_id,error=str(error)))
 provider_discovery=[]
 for cid in sorted({c for pid in managed for c in clubs[pid]}):
  url='https://www.fotmob.com/teams/'+str(cid)+'/squad'
  try:
   source,body=fetch(url,'cross_provider_identity_discovery:'+str(cid))
   for record,result in verify_team_identities(body,source,cid,roster['players']):
    if result['identity_status']!='VERIFIED':
     provider_discovery.append(dict(club_id=cid,status=result['identity_status'],names=record.names,remaining_requirements=result['remaining_requirements']));continue
    fid=record.provider_ids['FotMob'];pid=int(result['player_id'])
    if fid in ids and ids[fid]!=pid:raise ValueError('IdentityError:PROVIDER_DISCOVERY_CANONICAL_COLLISION')
    old={f for f,p in ids.items() if p==pid}
    if old and fid not in old:raise ValueError('IdentityError:CANONICAL_MULTIPLE_FOTMOB_IDS')
    ids[fid]=pid;persist_verified_alias(db,record,result,END);provider_discovery.append(dict(club_id=cid,player_id=pid,provider_player_id=fid,status='VERIFIED',proof=result['identity_proof'],source_sha256=source['sha256']))
  except Exception as exc:pending('TEAM_PROVIDER_IDENTITY',str(cid),url,type(exc).__name__+':'+str(exc))
 for pid in sorted(managed):
  own=[fid for fid,canonical in ids.items() if canonical==pid];coverage[pid]=dict(history_status='PENDING',affiliation_status='PENDING',fixture_status='PENDING',lineup_status='PENDING',evidence=[])
  if len(own)!=1:
   pending('PLAYER_IDENTITY',str(pid),'https://www.fotmob.com','IdentityError:VERIFIED_FOTMOB_ID_REQUIRED',pid);continue
  fid=own[0];url='https://www.fotmob.com/players/'+fid
  try:
   source,body=fetch(url,'managed_player_personal_history:'+str(pid));d=payload(body)
   if str(d['id'])!=fid or similarity(names[pid],d.get('name',''))<.6:raise ValueError('IdentityError:PROFILE_ID_OR_NAME_CONFLICT')
   h=profile_hints(body,source,START,END);coverage[pid].update(history_status=h['history_coverage'],affiliation_status='EXPLICIT_CAREER_TEAMS' if h['affiliations'] else 'NO_EXPLICIT_TEAM_IN_PUBLIC_CAREER;NOT_NO_APPEARANCES',evidence=[source])
   for a in h['affiliations']:
    affiliations.setdefault(a['provider_team_id'],[]).append((pid,a));eid=evidence(db,dict(a['source'],retained_asset_ref='phase3-international-evidence-'+os.environ['GITHUB_RUN_ID']),a['scope'],a['verification_status'],1);tk=team(db,a['name'],a['level'],eid,'FotMob',a['provider_team_id']);db.execute('INSERT OR IGNORE INTO national_affiliation_events VALUES(?,?,?,?,?,?,?,?,?,?)',(digest([pid,a]),pid,tk,a.get('start_date'),None,END,'EXPLICIT_CAREER_RANGE',a['verification_status'],eid,'PROVIDER_CAREER_RANGE;END_DATE_NOT_ASSUMED_EXCLUSIVE'))
   for f in h['matches']:add_fixture(f);claims[f['provider_match_id']].append(f)
  except Exception as exc:pending('PLAYER_HISTORY',fid,url,type(exc).__name__+':'+str(exc),pid)
  print('PLAYER_DISCOVERY',pid,coverage[pid]['history_status'],flush=True)
 for tid,entries in sorted(affiliations.items()):
  url='https://www.fotmob.com/teams/'+tid+'/fixtures'
  try:
   source,body=fetch(url,'verified_national_team_fixture_fallback:'+tid);a=entries[0][1];rows=team_fixture_hints(body,source,a,START,END)
   for f in rows:add_fixture(f)
   for pid,a in entries:coverage[pid]['fixture_status']='PUBLIC_TEAM_FIXTURES_SCANNED;BOUNDARY_NOT_FULL_HISTORY_PROOF'
  except Exception as exc:
   pending('NATIONAL_TEAM_FIXTURES',tid,url,type(exc).__name__+':'+str(exc));
   for pid,a in entries:coverage[pid]['fixture_status']='FALLBACK_FAILED'
 print('FIXTURES_DISCOVERED',len(fixtures),flush=True)
 for mid,f in sorted(fixtures.items()):
  frozen=db.execute("SELECT state FROM international_acquisition_queue WHERE entity_type='MATCH' AND provider='FotMob' AND provider_entity_id=? AND state IN ('QUARANTINED','HUMAN_REVIEW')",(mid,)).fetchone()
  if frozen:
   errors.append(dict(entity='MATCH',provider_id=mid,error='EXISTING_'+frozen['state']+';NO_AUTOMATIC_FACT_WRITE'));continue
  row_count_before=canonical_rows;source=None;db.execute('SAVEPOINT acquired_international_match')
  try:
   source,body=fetch(f['url'],'international_match_lineup:'+mid);sheet=parse_public_match(body,source,f,claims[mid]);eid=evidence(db,dict(source,retained_asset_ref='phase3-international-evidence-'+os.environ['GITHUB_RUN_ID']),'EXACT_MATCH_IDS_SIDES_DATE_AND_LINEUP','RAW_RESPONSE_VERIFIED',1);teams={s['side']:team(db,s['name'],sheet['level'],eid,'FotMob',s['provider_team_id']) for s in sheet['sheets']};match=dict(match_id='nt_fotmob_'+mid,home_team_key=teams['home'],away_team_key=teams['away'],kickoff_utc=sheet['kickoff_utc'],madrid_date=sheet['madrid_date'],competition_key=sheet['competition_key'],stage=None,match_state='FINISHED',score=sheet['score'],duration_minutes=sheet['duration_minutes'],extra_time_status=sheet['extra_time_status'],identity_status='PROVIDER_ID_SIDES_DATE_VERIFIED',metadata_json=encoded(dict(competition=sheet['competition_label'],full_squad_identity_status=sheet['full_squad_identity_status'],identity_issues=sheet['identity_issues'])));accepted=put_match(db,match,'FotMob',mid,eid);accepted_players=[]
   for side in sheet['sheets']:
    for p in side['players']:
     pid=ids.get(p['provider_player_id'])
     if pid not in managed:continue
     if similarity(names[pid],p['name'])<.6:raise ValueError('IdentityError:LINEUP_CANONICAL_NAME_CONFLICT')
     a=dict(match_id=accepted,player_id=pid,represented_team_key=teams[side['side']],participation_status=p['participation_status'],minutes=p['minutes'],reported_minutes=p['reported_minutes'],derived_minutes=p['derived_minutes'],minutes_precision=p['minutes_precision'],verification_status=p['verification_status'],evidence_scope=p['evidence_scope'],revision=1,metadata_json=encoded(dict(source_path=p['source_path'],provider_player_id=p['provider_player_id'],shirt=p['shirt'],role=p['role'])))
     old=db.execute('SELECT * FROM international_appearances WHERE match_id=? AND player_id=?',(accepted,pid)).fetchone()
     if old:
      if old['minutes'] is None and a['minutes'] is not None and not old['verification_status'].startswith('RETAINED_PRIOR'):
       complete_missing_minutes(db,a,eid);old=db.execute('SELECT * FROM international_appearances WHERE match_id=? AND player_id=?',(accepted,pid)).fetchone()
      if a['minutes'] is not None and old['minutes']!=a['minutes']:
       conflicts.append(dict(player_id=pid,match_id=mid,retained_minutes=old['minutes'],new_minutes=a['minutes'],reason='RETAIN_ORIGINAL_REFERENCE;EXPLICIT_REVISION_REQUIRED'));decision='RETAINED_ORIGINAL_ADJUDICATION';agree=0
      else:decision='CORROBORATES_EXISTING_REFERENCE';agree=1
     else:canonical_rows+=int(put_appearance(db,a));decision='NEW_EXACT_PROVIDER_ID_FACT';agree=1
     db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(accepted,pid,eid,'lineup_and_minutes',encoded(a),agree,decision))
     if p['history_source']:
      he=evidence(db,dict(p['history_source'],retained_asset_ref='phase3-international-evidence-'+os.environ['GITHUB_RUN_ID']),'PERSONAL_HISTORY_TARGET_MINUTES','PROVIDER_ASSERTION_WITH_LINEUP_ID_CHECK',1);db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(accepted,pid,he,'reported_minutes',encoded(dict(minutes=p['minutes'],scope=p['evidence_scope'])),agree,decision))
     if a['minutes'] is None:
      pending('PLAYER_MINUTES',mid+':'+str(pid),f['url'],'SOURCE_MINUTES_INCOMPLETE',pid,dict(f,represented_team_provider_id=p['represented_team_provider_id']),source=source)
     else:
      observe(db,dict(entity_type='PLAYER_MINUTES',provider='FotMob',provider_entity_id=mid+':'+str(pid),url=f['url'],player_id=pid,fixture=f),dict(status='UNCHANGED',match_id=accepted),os.environ['GITHUB_RUN_ID'])
     accepted_players.append(pid)
    # Unknown source IDs are held as structural issues; they never become canonical player 0.
   parsed.append(dict(provider_match_id=mid,canonical_match_id=accepted,players=accepted_players,structural_identity=sheet['full_squad_identity_status'],identity_issues=sheet['identity_issues']));observe(db,dict(entity_type='MATCH',provider='FotMob',provider_entity_id=mid,url=f['url'],fixture=f),dict(status='DISCOVERED',match_id=accepted),os.environ['GITHUB_RUN_ID'])
  except Exception as exc:
   db.execute('ROLLBACK TO acquired_international_match');canonical_rows=row_count_before
   pending('MATCH',mid,f['url'],type(exc).__name__+':'+str(exc),fixture=f,source=source)
   for c in claims[mid]:
    pid=ids.get(c['player_provider_id'])
    if pid in managed:pending('PLAYER_MATCH',mid+':'+str(pid),f['url'],type(exc).__name__+':'+str(exc),pid,dict(f,represented_team_provider_id=c['represented_team_provider_id'],discovery_claim=c['discovery_claim']),source=source)
  db.execute('RELEASE acquired_international_match')
  print('MATCH_DISCOVERY',mid,'parsed' if parsed and parsed[-1]['provider_match_id']==mid else 'held',flush=True)
 for pid,c in coverage.items():
  count=db.execute('SELECT COUNT(*) FROM international_appearances WHERE player_id=?',(pid,)).fetchone()[0];unresolved=db.execute("SELECT COUNT(*) FROM international_acquisition_queue WHERE player_id=? AND state<>'RESOLVED'",(pid,)).fetchone()[0];c['lineup_status']='VERIFIED_RECORDS:'+str(count)+';PARTIAL_DISCOVERY_NO_COMPLETENESS_CLAIM';db.execute('INSERT OR REPLACE INTO player_discovery_coverage VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(pid,START,END,'ALL_PLAYER_INTERNATIONAL_MATCHES',c['history_status'],c['affiliation_status'],c['fixture_status'],c['lineup_status'],now,encoded(dict(strategy='PERSONAL_HISTORY_THEN_EXPLICIT_NATIONAL_FIXTURES',global_unresolved_fixture_count=sum(e['entity']=='MATCH' for e in errors))),unresolved,encoded(c['evidence'])))
 assert bridge_references(db)==[];assert db.execute('SELECT COUNT(*) FROM international_reference_bridge').fetchone()[0]==21;assert not db.execute('PRAGMA foreign_key_check').fetchone();db.commit();counts=dict(db.execute('SELECT COUNT(*) AS appearances,COUNT(DISTINCT player_id) AS players,COUNT(DISTINCT match_id) AS matches FROM international_appearances').fetchone());assert counts['appearances']>=prior_international_rows,'INTERNATIONAL_FACTS_DROPPED';queue=dict(Counter(r[0] for r in db.execute('SELECT state FROM international_acquisition_queue')));db.close();assert inspect(work/'working.sqlite')['tables']==before,'LEGACY_FACTS_CHANGED';snapshot(work/'working.sqlite',work/'snapshot.sqlite');pack(work/'snapshot.sqlite',work/'snapshot.sqlite.gz');code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();data_head,pointer=store.publish(work/'snapshot.sqlite',work/'snapshot.sqlite.gz',head,code);pointer['dataset_ref']=REF;manifest=export_all_matches(work/'snapshot.sqlite',work/'site',data_head,pointer,END,True);browser=json.loads((work/'site/data/browser.json').read_text());assert browser['version']['sqlite_sha256']==pointer['current']['database']['sha256'];assert sum(r.get('match_type')=='NATIONAL' and not r.get('pending') for rows in browser['records'].values() for r in rows)==counts['appearances']
 report=dict(kind='ISOLATED_INTERNATIONAL_DEVELOPMENT_ACCEPTANCE;NOT_PRODUCTION',code_commit=code,data_head=data_head,data_ref=REF,pointer=pointer,old_fact_tables_unchanged=True,production_pointer_unchanged=prod.head()==prod_before,prior_international_rows=prior_international_rows,managed_players=len(managed),provider_identity_discovery=provider_discovery,coverage_rows=len(coverage),live_requests=client.calls,fixtures_discovered=len(fixtures),parsed_matches=parsed,new_international_rows=canonical_rows,total=counts,legacy_reference_records=21,unresolved=errors,retained_reference_disagreements=conflicts,retry_states=queue,export_manifest=manifest,national_history_complete=False,production_authority='DISABLED',website_publication='NOT_PERFORMED')
 assert report['production_pointer_unchanged'];write(work/'result.json',report)
 release=store.api('POST',store.base+'/releases',dict(tag_name='phase3-international-'+os.environ['GITHUB_RUN_ID'],target_commitish=REF,name='Phase 3 isolated international database and export audit',body='Development snapshot only. Coverage and unresolved sources explicit; production disabled.',draft=True))
 for file in [work/'result.json',work/'site/data/browser.json',work/'site/data/manifest.json']:
  a=store.upload(release['id'],file);store.download(a['id'],work/('readback-'+file.name));assert sha(file)==sha(work/('readback-'+file.name))
 store.api('PATCH',store.base+'/releases/'+str(release['id']),dict(draft=False))
 with open(os.environ['GITHUB_OUTPUT'],'a') as out:out.write('data_head='+data_head+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('parsed_matches','unresolved','pointer','export_manifest','provider_identity_discovery')},ensure_ascii=False))
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as out:out.write('## Isolated international result; production DISABLED\n'+json.dumps({k:v for k,v in report.items() if k not in ('parsed_matches','unresolved','pointer','export_manifest','provider_identity_discovery')},ensure_ascii=False)+'\n')
def recovery():
 isolated();work=ROOT/'.cloud/phase3-independent-recovery';work.mkdir(parents=True,exist_ok=False);s=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],REF);head,p=s.restore(work/'current.sqlite');assert head==os.environ['EXPECTED_DATA_HEAD'];previous_head,_=s.restore(work/'previous.sqlite',previous=True);assert previous_head==head;db=sqlite3.connect(work/'current.sqlite');assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok';assert db.execute('SELECT COUNT(*) FROM international_reference_bridge').fetchone()[0]==21;db.close();p['dataset_ref']=REF;m=export_all_matches(work/'current.sqlite',work/'site',head,p,END,True);assert m['version']['sqlite_sha256']==p['current']['database']['sha256'];print('INDEPENDENT_CLOUD_RECOVERY_PASS',head,m['version']['sqlite_sha256']);
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as out:out.write('Independent fresh-checkout cloud restore of current + previous snapshots and exact-version export: PASS. Website publication and complete international coverage are NOT implied.\n')
if __name__=='__main__':recovery() if '--recover' in sys.argv else main()
