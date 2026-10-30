"""Read-only cloud raw replay against a published isolated international snapshot."""
import os,json,sqlite3,hashlib,tarfile,subprocess
from collections import defaultdict
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
 db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;original=sqlite3.connect('file:'+str(work/'snapshot.sqlite')+'?mode=ro',uri=True);original.backup(db);original.close();db.execute('PRAGMA foreign_keys=ON')
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
 def fetch(url,purpose):
  if url not in bodies:raise ValueError('REPLAY_RAW_RESPONSE_NOT_RETAINED:'+url)
  source,body=bodies[url]
  if source['status']!='SUCCESS':raise ValueError('REPLAY_RETAINED_SOURCE_UNAVAILABLE:'+url)
  return source,body
 alternatives=acquire_sky_alternatives(db,fetch,fixtures,claims,ids,managed,clubs,END,'raw-replay-'+run,'phase3-international-evidence-'+run)
 assert alternatives['new_rows']==0,'REPLAY_CREATED_NEW_APPEARANCES';assert table_facts(db)==before,'REPLAY_CHANGED_CANONICAL_FACTS'
 expected_sky={r['provider_match_id'] for r in result.get('independent_alternatives',{}).get('independent_matches',[])};actual_sky={r['provider_match_id'] for r in alternatives['independent_matches']};assert actual_sky==expected_sky,('SKY_REPLAY_MATCH_SET',actual_sky,expected_sky)
 for row in db.execute('SELECT * FROM international_reference_bridge'):
  raw=db.execute('SELECT payload_json FROM validation_national_reference WHERE player_id=? AND match_id=?',(row['player_id'],row['legacy_match_id'])).fetchone()[0];assert hashlib.sha256(raw.encode()).hexdigest()==row['original_payload_sha256']
 assert db.execute('SELECT COUNT(*) FROM international_reference_bridge').fetchone()[0]==21;db.rollback();db.close()
 report=dict(kind='GITHUB_HOSTED_RAW_REPLAY;NO_NETWORK_ACQUISITION;NO_DATABASE_PUBLICATION',source_run=run,source_code=result['code_commit'],replay_code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),data_head=result['data_head'],execution_ref_head=head,sqlite_sha256=sha(work/'snapshot.sqlite'),raw_asset_id=raw_asset['id'],raw_asset_sha256=sha(work/'raw.tar.gz'),verified_response_bodies=len(captures),profile_responses=profile_count,fixtures=len(fixtures),primary_match_replays=len(primary),independent_sky_match_replays=len(actual_sky),canonical_facts_unchanged=True,new_appearances=0,legacy_references_preserved=21,retained_minute_differences=retained_differences,source_discovery_errors=source_errors,sky_source_gaps=alternatives['errors'],production_authority='DISABLED',national_history_complete=False)
 path=work/'raw-replay-result.json'
 with path.open('x') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
 release=store.api('POST',store.base+'/releases',dict(tag_name='phase3-raw-replay-'+os.environ['GITHUB_RUN_ID'],target_commitish=REF,name='Independent raw replay without database publication',body='Fresh GitHub restore; immutable raw responses only; canonical facts unchanged; production disabled.',draft=True));a=store.upload(release['id'],path);store.download(a['id'],work/'readback.json');assert sha(path)==sha(work/'readback.json');store.api('PATCH',store.base+'/releases/'+str(release['id']),dict(draft=False));assert store.head()[0]==head
 print(json.dumps({k:v for k,v in report.items() if k not in ('sky_source_gaps','source_discovery_errors')},ensure_ascii=False))
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:stream.write('Raw replay PASS; canonical international facts unchanged; no database publication. Coverage remains partial; production DISABLED.\n')
if __name__=='__main__':main()
