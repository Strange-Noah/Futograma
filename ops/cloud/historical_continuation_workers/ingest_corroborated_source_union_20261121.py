import sys,json,sqlite3
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path.cwd()))
from squad_pipeline.source_interface import SourceService,SourceRequest,RawSourceArtifact,EvidenceStore
from squad_pipeline import canonical_fixture as c
from squad_pipeline.canonical_adapters import observe_fixture
from squad_pipeline.quality_pipeline import process_fixture
from squad_pipeline.identity_registry import lookup
from squad_pipeline.live_parse import canon,CLUBS
from squad_pipeline.competition_identity import COMP
COMP['FA Community Shield']=247
COMP['French Ligue 1']=53 # exact retained Sky header, existing Ligue1 canonical ID
from ops.cloud.coldstart_acceptance import persist_fixture_row,apply_appearance
from squad_pipeline.cloud.quality import sha,pack
from squad_pipeline.cloud.storage import LocalStore
from squad_pipeline.cloud.export import export
if 'source' not in globals():
 root=Path('.cloud/one-pass-corroborated-source-union-ingestion-20261121');source=Path('.cloud/one-pass-shared-fixture-team-labels-20261121/candidate.sqlite')
root.mkdir(exist_ok=False);target=root/'candidate.sqlite';original=sha(source)
with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(target) as b:a.backup(b)
db=sqlite3.connect(target);db.row_factory=sqlite3.Row;db.execute('pragma foreign_keys=on');before={t:db.execute('select count(*) from '+t).fetchone()[0] for t in ['matches','player_match_minutes']};svc=SourceService(EvidenceStore(root/'sources',archives=[Path.cwd()]),'archived-sky-ingestion',set(),max_calls=0);seen=set();results=[];gaps=[]
comps={'FA Community Shield':247,'French Ligue 1':53,'German Bundesliga':54,'Spanish La Liga':87,'German DFB Cup':209,'The FA Cup':132,'FIFA Club World Cup':78,'Carabao Cup':133,'Premier League':47,'Ligue 1':53,'Serie A':55,'UEFA Champions League':42,'Bundesliga':54,'La Liga':87}
comps=COMP|comps
for receipt in globals().get('source_receipts',[Path('.cloud/one-pass-broad-squads-native-final-20261121/results-private.json'),Path('.cloud/one-pass-barca-bayern-native-final-20261121/results-private.json'),Path('.cloud/one-pass-monthly-squads-native-20261121/results-private.json'),Path('.cloud/one-pass-barca-bayern-next-native-20261121/results-private.json'),Path('.cloud/one-pass-retained-sky-label-recovery-20261121/results-private.json'),Path('.cloud/one-pass-arsenal-last-h1-native-20261121/results-private.json')]):
 if receipt.name not in {'results-private.json','lineups-private.json'}:continue
 try:rows=json.load(open(receipt))
 except (ValueError,OSError):continue
 if not isinstance(rows,list):continue
 for row in rows:
  if not isinstance(row,dict) or not (row.get('native') or {}).get('sheets') or 'skysports.com/football/' not in row.get('url',''):continue
  if row.get('parse') in ['INCOMPLETE','MALFORMED']:continue
  a=row.get('artifact');url=row['url']
  if not a or a['content_sha256'] in seen:continue

  if url.endswith('/521664'):continue
  seen.add(a['content_sha256']);db.execute('savepoint fixture')
  try:
   p=Path(a['locator']);p=p if p.is_absolute() else receipt.parent/p
   if not p.exists():
    retained=list(Path('.cloud').glob('*/bodies/'+a['content_sha256']+'.response'));assert retained,'RETAINED_BODY_NOT_FOUND';p=retained[0]
   art=RawSourceArtifact(SourceRequest(**a['request']),a['content_sha256'],str(p.resolve()),a['metadata'],a['authority']);_,parsed=svc.observe(art.request,art);assert parsed and parsed.outcome=='PARSED'
   n=parsed.native;f=dict(n['fixture']);day=f['match_date'];assert '2024-07-01'<=day<'2026-07-01';comp=comps.get(f['competition']);assert comp,'COMPETITION_SCOPE_REQUIRES_EXISTING_MAPPING';own=[s for s in n['sheets'] if s.get('configured_club_id') in [8456,9825,9847,8634,9823]];assert own,'OTHER_CLUB_SEPARATE_ACCEPTED_CANDIDATE'
   ev=dict(f['source_evidence'][0],complete_squad=all(s['complete_squad'] for s in own),source_identity_verified=True);mid=str(n['provider_fixture_id']);season=c.club_period(day)[1];labels=f['source_team_names'];f.update(match_id='source-sky:'+mid,competition_id=comp,competition_edition=season)
   exact=[]
   for prior in db.execute('select match_id from canonical_fixtures where domain=? and competition_key=? and edition=?',('CLUB',str(comp),season)):
    existing=prior[0]
    if c.value(db,existing,'kickoff')[0]!=c.instant(f['kickoff']):continue
    legacy=c.metadata(db,existing) # Accepted catalogue fixtures may correctly precede legacy match-row materialization.
    if all((any(f[side+'_team']==CLUBS[ss['configured_club_id']][1] and str(legacy[side+'_team_id']) in {str(ss['configured_club_id']),'club:'+str(ss['configured_club_id'])} for ss in own) or canon(legacy.get(side+'_team',''))==canon(f['source_team_names'][side]) or c.resolve_entity(db,'TEAM','Sky',f['source_team_names'][side])==str(legacy[side+'_team_id'])) for side in ['home','away']) and all(c.value(db,existing,k)[0]==v for k,v in c.score_fields(f['score']).items()):exact.append(existing)
   assert len(exact)<=1,'MULTIPLE_ACCEPTED_PHYSICAL_FIXTURES'
   if not exact:
    same_day=[]
    for prior in db.execute('select match_id,home_key,away_key from canonical_fixtures where domain=? and competition_key=? and edition=?',('CLUB',str(comp),season)):
     instant=c.value(db,prior[0],'kickoff')[0]
     if not instant or instant[:10]!=day:continue
     if any(('club:'+str(ss['configured_club_id'])) in [prior[1],prior[2]] for ss in own):same_day.append(prior[0])
    assert not same_day,'EXISTING_SAME_CLUB_COMPETITION_DAY_REQUIRES_RETAINED_FIXTURE_ADJUDICATION'
   binding=dict(match_id=exact[0],operation='EXACT_EXISTING_CANONICAL_TUPLE') if exact else observe_fixture(db,f,ev,create=True,registered_id=f['match_id'])
   assert binding['operation']!='SOURCE_CONFLICT';mid=binding['match_id'];meta=c.metadata(db,mid);scores=c.score_fields(f['score'])
   packet=dict(match_id=mid,provider='Sky',provider_match_id=n['provider_fixture_id'],actual_provider_match_id=n['provider_fixture_id'],home=dict(id=meta['home_team_id'],name=labels['home']),away=dict(id=meta['away_team_id'],name=labels['away']),competition_key=str(comp),competition_label=f['competition'],season=season,kickoff_utc=f['kickoff'],status='FINISHED',home_score=scores['home_score'],away_score=scores['away_score'])
   fe=dict(field_evidence={k:dict(provider='Sky',url=url,sha256=a['content_sha256'],value=v,state='VERIFIED',basis='ARCHIVED_SOURCE_SERVICE_NATIVE_FIXTURE') for k,v in packet.items() if k!='actual_provider_match_id'});q=process_fixture(db,packet,fe,'2026-11-16',mode='BACKFILL');assert q['decision']=='ACCEPT',q['decision']
   persist_fixture_row(db,dict(fixture=packet,source=ev,discovery_row=dict(home=packet['home'],away=packet['away'],tournament=dict(name=f['competition']))),mid);counts=Counter()
   for sheet in own:
    cid=sheet['configured_club_id']
    for person in sheet['players']:
     pid=lookup(db,cid,person)
     if pid is None:
      scoped=[d for d in json.load(open(globals().get('identity_file','.cloud/one-pass-scoped-identities-r19-20261121.json'))) if d['club_id']==cid and d['observed_name']==person['name'] and any(v['start']<=day<v['end_exclusive'] for v in d['validity'])]
      if len(scoped)==1:
       d=scoped[0];proof=d['verified_identity']['proof'];assert proof['identity_status']=='VERIFIED' and proof['identity_proof'];provider_id=proof['source_record']['provider_ids']['FotMob'];pid=lookup(db,cid,dict(name=person['name'],provider_key='fotmob:'+provider_id));assert pid==d['canonical_player_id'];person=dict(person,provider_key='fotmob:'+provider_id,provider_player_id=provider_id)
     if pid is None:gaps.append(dict(club_id=cid,name=person['name'],role=person['role'],match_id=mid,source=url,sha256=a['content_sha256']));continue
     if db.execute('select 1 from player_match_minutes where club_id=? and match_id=? and player_id=?',(cid,mid,pid)).fetchone():continue
     player=dict(person,participation_status='STARTER' if person['role']=='starters' else 'BENCH_PARTICIPATION_UNKNOWN',minutes=None,reported_minutes=None,derived_minutes=None,source_path=person.get('json_path'))
     counts[apply_appearance(db,cid,mid,player,ev,'2026-11-16','BACKFILL',canonical_player_id=pid)['state']]+=1
   db.execute('release fixture');results.append(dict(url=url,state='ACCEPT',counts=dict(counts)))
  except (ValueError,AssertionError,KeyError,TypeError,StopIteration) as e:
   db.execute('rollback to fixture');db.execute('release fixture');results.append(dict(url=url,state='HELD',reason=str(e)))
  (root/'results-private.json').write_text(json.dumps(results,indent=2));db.commit()
assert db.execute('pragma integrity_check').fetchone()[0]=='ok';assert not db.execute('pragma foreign_key_check').fetchall();assert sha(source)==original
summary=dict(states=dict(Counter(r['state'] for r in results)),new_matches=db.execute('select count(*) from matches').fetchone()[0]-before['matches'],new_player_match_rows=db.execute('select count(*) from player_match_minutes').fetchone()[0]-before['player_match_minutes'],unresolved_identity_rows=len(gaps),network_calls=svc.client.calls,production_writes=0,sha256=sha(target));db.close()
try:
 z=root/'candidate.sqlite.gz';pack(target,z);head,pointer=LocalStore(root/'store').publish(target,z,None);export(target,root/'site',head,pointer,Path('web'));summary['ordinary_export']='PASS'
except ValueError as e:summary.update(ordinary_export='FAIL',export_error=str(e))
(root/'unresolved-private.json').write_text(json.dumps(gaps,indent=2));(root/'SUMMARY.json').write_text(json.dumps(summary,indent=2));print(summary)
