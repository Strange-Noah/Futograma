"""Read-only cloud raw replay against a published isolated international snapshot."""
import os,json,sqlite3,hashlib,tarfile,subprocess
from collections import defaultdict
from squad_pipeline.source_redirects import fetch_public_redirects
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import sha
from ops.cloud.phase3_international_e2e import isolated,START,END,REF
from squad_pipeline.international_discovery import profile_hints,team_fixture_hints,payload
from squad_pipeline.international_fotmob import parse_public_match,instant
from squad_pipeline.international_sky_fallback import acquire_sky_alternatives

def table_facts(db):
 tables=('international_matches','international_match_provider_ids','international_appearances','international_reference_bridge','international_fact_revisions')
 existing={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
 return {name:sorted([tuple(r) for r in db.execute('SELECT * FROM '+name)],key=repr) for name in tables if name in existing}

def main():
 isolated();run=os.environ['REPLAY_SOURCE_RUN'];work=ROOT/('.cloud/phase3-raw-replay-'+run);work.mkdir(parents=True,exist_ok=False);store=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],REF)
 def asset(tag,predicate,target):
  release=store.api('GET',store.base+'/releases/tags/'+tag);matches=[a for a in release['assets'] if predicate(a['name'])];assert len(matches)==1
  a=matches[0];store.download(a['id'],target);assert a['digest']=='sha256:'+sha(target);return a
 result_asset=asset('phase3-international-'+run,lambda n:n=='result.json',work/'source-result.json');result=json.loads((work/'source-result.json').read_text());head,pointer=store.restore(work/'snapshot.sqlite');assert pointer['current']==result['pointer']['current'];assert sha(work/'snapshot.sqlite')==result['pointer']['current']['database']['sha256']
 raw_asset=asset('phase3-international-evidence-'+run,lambda n:n.endswith('.tar.gz'),work/'raw.tar.gz')
 captures=[];bodies={}
 with tarfile.open(work/'raw.tar.gz') as archive:
  members={m.name:m for m in archive.getmembers() if m.isfile()};assert sum(m.size for m in members.values())<500000000
  for path,member in members.items():
   if not path.endswith('.metadata.json'):continue
   source=json.load(archive.extractfile(member));body=archive.extractfile(members[source['path']]).read();assert hashlib.sha256(body).hexdigest()==source['sha256'];source=dict(source,provider='Sky' if 'www.skysports.com/' in source['url'] else 'FotMob');captures.append((source,body.decode('utf-8','replace')));bodies[source['url']]=(source,body.decode('utf-8','replace'))
 captures.sort(key=lambda entry:entry[0]['request_timestamp']);bodies={source['url']:(source,body) for source,body in captures}
 db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;original=sqlite3.connect('file:'+str(work/'snapshot.sqlite')+'?mode=ro',uri=True);original.backup(db);original.close();db.execute('PRAGMA foreign_keys=ON')
 from squad_pipeline.minute_reconciliation import apply_existing
 policy_changes=apply_existing(db) if os.environ.get('FUTOGRAMA_REPLAY_NEW_POLICY')=='true' else []
 before=table_facts(db);ids={str(r['provider_player_id']):int(r['player_id']) for r in db.execute("SELECT provider_player_id,player_id FROM verified_identity_links WHERE provider='FotMob'")}
 source_audit=store.api('GET',store.base+'/releases/assets/688943155');store.download(688943155,work/'identity.json.gz');assert source_audit['digest']=='sha256:'+sha(work/'identity.json.gz')
 import gzip
 roster=json.loads(gzip.decompress((work/'identity.json.gz').read_bytes()))['roster'];managed={int(d['player_id']) for d in roster['players'] if d['management']['state']=='INCLUDED'};clubs=defaultdict(list)
 for d in roster['players']:clubs[int(d['player_id'])].append(d['club_id'])
 fixtures={};claims=defaultdict(list);affiliations=defaultdict(list);profile_count=0;source_errors=[]
 def add(f):
  old=fixtures.get(f['provider_match_id'])
  if old:assert all(old[k]==f[k] for k in ('home','away','competition_key','level')) and instant(old['kickoff'])==instant(f['kickoff'])
  fixtures[f['provider_match_id']]=f
 for source,body in captures:
  if not source['purpose'].startswith(('managed_player_personal_history:','untrusted_numeric_profile_hint:')) or source['status']!='SUCCESS':continue
  try:
   pid=int(source['purpose'].split(':',1)[1])
   if pid not in managed or ids.get(str(payload(body)['id']))!=pid:continue
   h=profile_hints(body,source,START,END);profile_count+=1
   for a in h['affiliations']:affiliations[a['provider_team_id']].append(a)
   for f in h['matches']:add(f);claims[f['provider_match_id']].append(f)
  except Exception as exc:source_errors.append(dict(url=source['url'],error=type(exc).__name__+':'+str(exc)))
 for source,body in captures:
  if not source['purpose'].startswith('verified_national_team_fixture_fallback:') or source['status']!='SUCCESS':continue
  tid=source['purpose'].split(':',1)[1]
  try:
   assert affiliations[tid]
   for f in team_fixture_hints(body,source,affiliations[tid][0],START,END):add(f)
  except Exception as exc:source_errors.append(dict(url=source['url'],error=type(exc).__name__+':'+str(exc)))
 assert len(fixtures)==result['fixtures_discovered'],('RAW_DISCOVERY_REPLAY_COUNT',len(fixtures),result['fixtures_discovered'])
 primary=[];retained_differences=[]
 for expected in result['parsed_matches']:
  mid=expected['provider_match_id'];f=fixtures[mid];source,body=bodies[f['url']];sheet=parse_public_match(body,source,f,claims[mid]);actual=[]
  for side in sheet['sheets']:
   for player in side['players']:
    pid=ids.get(player['provider_player_id'])
    if pid not in managed:continue
    actual.append(pid);row=db.execute('SELECT * FROM international_appearances WHERE match_id=? AND player_id=?',(expected['canonical_match_id'],pid)).fetchone();assert row is not None
    if player['minutes'] is not None and row['minutes']!=player['minutes']:retained_differences.append(dict(player_id=pid,match_id=mid,canonical_minutes=row['minutes'],source_minutes=player['minutes']))
  assert sorted(actual)==sorted(expected['players']);primary.append(mid)
 allowed_differences={(int(r['player_id']),str(r['match_id']),r['retained_minutes'],r['new_minutes']) for r in result['retained_reference_disagreements']}
 assert {(r['player_id'],r['match_id'],r['canonical_minutes'],r['source_minutes']) for r in retained_differences}==allowed_differences,'UNEXPLAINED_REPLAY_MINUTE_DIFFERENCE'
 class ReplayClient:
  def fetch(self,url,purpose,conditional=False):
   if url not in bodies:raise ValueError('REPLAY_RAW_RESPONSE_NOT_RETAINED:'+url)
   source,body=bodies[url];return source,body.encode('utf-8')
 def fetch(url,purpose):
  source,body=fetch_public_redirects(ReplayClient(),url,purpose)
  if source['status']!='SUCCESS':raise ValueError('REPLAY_RETAINED_SOURCE_UNAVAILABLE:'+url)
  return source,body.decode('utf-8')
 alternatives=acquire_sky_alternatives(db,fetch,fixtures,claims,ids,managed,clubs,END,'raw-replay-'+run,'phase3-international-evidence-'+run)
 assert alternatives['new_rows']==0,'REPLAY_CREATED_NEW_APPEARANCES';assert table_facts(db)==before,'REPLAY_CHANGED_CANONICAL_FACTS'
 expected_sky={r['provider_match_id'] for r in result.get('independent_alternatives',{}).get('independent_matches',[])};actual_sky={r['provider_match_id'] for r in alternatives['independent_matches']};assert actual_sky==expected_sky,('SKY_REPLAY_MATCH_SET',actual_sky,expected_sky)
 for row in db.execute('SELECT * FROM international_reference_bridge'):
  raw=db.execute('SELECT payload_json FROM validation_national_reference WHERE player_id=? AND match_id=?',(row['player_id'],row['legacy_match_id'])).fetchone()[0];assert hashlib.sha256(raw.encode()).hexdigest()==row['original_payload_sha256']
 assert db.execute('SELECT COUNT(*) FROM international_reference_bridge').fetchone()[0]==21
 quality=dict(db.execute('SELECT COUNT(*) AS appearances,COUNT(DISTINCT player_id) AS players,COUNT(DISTINCT match_id) AS matches,SUM(minutes IS NULL) AS unknown_minutes,SUM(minutes>90) AS appearances_over_90_minutes,MAX(minutes) AS maximum_minutes FROM international_appearances').fetchone());quality['levels']={r[0]:r[1] for r in db.execute('SELECT t.level,COUNT(*) FROM international_appearances a JOIN football_teams t ON a.represented_team_key=t.team_key GROUP BY t.level')};quality['participation']={r[0]:r[1] for r in db.execute('SELECT participation_status,COUNT(*) FROM international_appearances GROUP BY participation_status')};quality['date_range']=dict(db.execute('SELECT MIN(m.madrid_date) AS first_date,MAX(m.madrid_date) AS last_date FROM international_appearances a JOIN international_matches m USING(match_id)').fetchone());quality['retry_states']={r[0]:r[1] for r in db.execute('SELECT state,COUNT(*) FROM international_acquisition_queue GROUP BY state')};quality['identity_mapped_managed_players']=len(managed & set(ids.values()));quality['managed_players']=len(managed);quality['unresolved_tasks']=[dict(r) for r in db.execute("SELECT entity_type,provider,failure_classification,state,COUNT(*) AS tasks FROM international_acquisition_queue WHERE state<>'RESOLVED' GROUP BY entity_type,provider,failure_classification,state")]
 quality['unknown_minute_records']=[dict(r) for r in db.execute('SELECT a.match_id,a.player_id,p.display_name,a.participation_status,a.reported_minutes,a.derived_minutes,a.evidence_scope,m.kickoff_utc FROM international_appearances a JOIN players p USING(player_id) JOIN international_matches m USING(match_id) WHERE a.minutes IS NULL ORDER BY m.kickoff_utc,a.player_id')]
 for difference in retained_differences:difference['player_name']=db.execute('SELECT display_name FROM players WHERE player_id=?',(difference['player_id'],)).fetchone()[0]
 accepted_independent={r['provider_match_id'] for r in alternatives['independent_matches']};uncovered={r['provider_id'] for r in result['unresolved'] if r['entity']=='MATCH'}-accepted_independent;quality['discovery_items_without_independent_acceptance']=[]
 for mid in sorted(uncovered):
  task=db.execute("SELECT payload_json FROM international_acquisition_queue WHERE provider='FotMob' AND entity_type='MATCH' AND provider_entity_id=?",(mid,)).fetchone();fixture=(json.loads(task[0]).get('fixture') or {}) if task else {};quality['discovery_items_without_independent_acceptance'].append(dict(provider_match_id=mid,fixture={key:fixture.get(key) for key in ('home','away','kickoff','competition_key','competition_label','level','url')}))
 latest_dossiers={}
 for row in db.execute('SELECT * FROM roster_verification_dossiers ORDER BY observed_at,dossier_id'):latest_dossiers[(row['club_id'],row['player_id'])]=dict(row)
 quality['unknown_management_dossiers']=[dict(club_id=row['club_id'],player_id=row['player_id'],player_name=db.execute('SELECT display_name FROM players WHERE player_id=?',(row['player_id'],)).fetchone()[0],identity_status=row['identity_status'],management_status=row['management_status'],observed_at=row['observed_at'],remaining_requirements=json.loads(row['payload_json']).get('remaining_requirements',[])) for row in latest_dossiers.values() if row['management_status']=='UNKNOWN'];db.rollback();db.close()
 report=dict(kind='GITHUB_HOSTED_RAW_REPLAY;NO_NETWORK_ACQUISITION;NO_DATABASE_PUBLICATION',source_run=run,source_code=result['code_commit'],replay_code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),data_head=result['data_head'],execution_ref_head=head,sqlite_sha256=sha(work/'snapshot.sqlite'),raw_asset_id=raw_asset['id'],raw_asset_sha256=sha(work/'raw.tar.gz'),verified_response_bodies=len(captures),profile_responses=profile_count,fixtures=len(fixtures),primary_match_replays=len(primary),independent_sky_match_replays=len(actual_sky),canonical_facts_unchanged=True,quality=quality,new_appearances=0,legacy_references_preserved=21,new_policy_resolutions=policy_changes,retained_minute_differences=retained_differences,source_discovery_errors=source_errors,sky_source_gaps=alternatives['errors'],production_authority='DISABLED',national_history_complete=False)
 path=work/'raw-replay-result.json'
 with path.open('x') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
 release=store.api('POST',store.base+'/releases',dict(tag_name='phase3-raw-replay-'+os.environ['GITHUB_RUN_ID'],target_commitish=REF,name='Independent raw replay without database publication',body='Fresh GitHub restore; immutable raw responses only; canonical facts unchanged; production disabled.',draft=True));a=store.upload(release['id'],path);store.download(a['id'],work/'readback.json');assert sha(path)==sha(work/'readback.json');store.api('PATCH',store.base+'/releases/'+str(release['id']),dict(draft=False));assert store.head()[0]==head
 print(json.dumps({k:v for k,v in report.items() if k not in ('sky_source_gaps','source_discovery_errors')},ensure_ascii=False))
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:stream.write('Raw replay PASS; canonical international facts unchanged; no database publication. Coverage remains partial; production DISABLED.\n')
if __name__=='__main__':main()
