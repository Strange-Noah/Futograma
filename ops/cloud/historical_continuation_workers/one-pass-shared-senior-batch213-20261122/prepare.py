"""One-time retained senior fixture context preparation using existing canonical writers."""
import sys,json,sqlite3,re
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path.cwd()))
from squad_pipeline.source_interface import SourceService,EvidenceStore,SourceRequest,RawSourceArtifact
from squad_pipeline.live_parse import Tree,uk_datetime
from squad_pipeline.international_parse import namekey
from squad_pipeline.canonical_fixture import entity
from squad_pipeline.international_store import evidence,team,put_match,encoded
from squad_pipeline.cloud.quality import sha
if 'source' not in globals():
 source=Path('.cloud/one-pass-declared-country-reference212-20261122/reference.sqlite');parent=Path('.cloud/one-pass-shared-senior-batch213-20261122');root=Path('.cloud/one-pass-shared-senior-reference213-20261122')
root.mkdir(exist_ok=False);target=root/'reference.sqlite';original=sha(source)
with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)as a,sqlite3.connect(target)as b:a.backup(b)
db=sqlite3.connect(target);db.row_factory=sqlite3.Row;db.execute('pragma foreign_keys=on');svc=SourceService(EvidenceStore(root/'sources',archives=[Path.cwd()]),'historical-national-header-context',set(),max_calls=0)
labels={'FIFA World Cup European Qualifying':'OFFICIAL_COMPETITION:FIFA_WCQ_UEFA','FIFA World Cup South American Qualifying':'OFFICIAL_COMPETITION:FIFA_WCQ_CONMEBOL','FIFA World Cup Asian Qualifying':'OFFICIAL_COMPETITION:FIFA_WCQ_AFC','UEFA Nations League':'OFFICIAL_COMPETITION:UEFA_NATIONS_LEAGUE','European Championships':'OFFICIAL_COMPETITION:UEFA_EURO','FIFA World Cup':'OFFICIAL_COMPETITION:FIFA_WORLD_CUP','International Match':'OFFICIAL_COMPETITION:SENIOR_FRIENDLY','International Friendly':'OFFICIAL_COMPETITION:SENIOR_FRIENDLY'}
extra_competitions=globals().get('additional_competitions',{})
labels.update(extra_competitions)
for label,key in extra_competitions.items(): entity(db,'COMPETITION',key,label)
db.commit()
relevant={namekey(x)for x in ['Germany','England','Spain','Portugal','France','Brazil','Netherlands','Croatia','Norway','Denmark','Belgium','Ukraine','Switzerland','Austria','Sweden','Poland','Italy','Argentina','Colombia','Japan']};relevant.update(namekey(x) for x in globals().get('documented_countries',()));out=[];accepted=[]
for row in json.load(open(parent/'results-private.json')):
 a=row.get('artifact')
 if not a:out.append(dict(url=row['url'],state='SOURCE_UNAVAILABLE',reason=row.get('reason')));continue
 db.execute('savepoint header_context')
 try:
  p=Path(a['locator']);p=p if p.is_absolute()else parent/p;assert p.resolve().is_relative_to(Path.cwd().resolve()),'PROJECT_BODY_REQUIRED'
  art=RawSourceArtifact(SourceRequest(**a['request']),a['content_sha256'],str(p.resolve()),a['metadata'],a['authority']);got=svc.acquire(art.request,art);assert got.outcome=='FOUND','RETAINED_SOURCE_UNAVAILABLE';raw=svc.store.read(got.artifact).decode();t=Tree(raw).root;canonical=[x.attrs.get('href')for x in t.all(tag='link')if x.attrs.get('rel')=='canonical'];assert canonical==[row['url']],'EXACT_SOURCE_CANONICAL_RESOURCE_REQUIRED';native=row['url'].rsplit('/',1)[-1];assert native.isdigit()
  header=t.one('sdc-site-match-header');names=[x.txt().strip()for x in header.all('sdc-site-match-header__team-name-block-target')];assert len(names)==2 and len(set(map(namekey,names)))==2,'ORDERED_TWO_COUNTRIES_REQUIRED';assert relevant&set(map(namekey,names)),'NO_RELEVANT_OBSERVED_SENIOR_COUNTRY'
  rule=globals().get('verified_direct_shootouts',{}).get(native)
  aet=globals().get('verified_extra_time',{}).get(native)
  assert header.attrs.get('data-status')=='4' or (header.attrs.get('data-status')=='8' and any(check and names==check['teams'] and a['content_sha256']==check['match_sha256'] for check in (rule,aet))),'VERIFIED_COMPLETED_MATCH_DURATION_REQUIRED'
  assert not any(re.search(r'\b(?:Women|U\d{2}|Legends|XI)\b',n,re.I)for n in names),'OUTSIDE_SENIOR_MENS_SCOPE'
  kickoff=uk_datetime(header.one('sdc-site-match-header__detail-time').txt());day=kickoff[:10];assert '2024-07-01'<=day<'2026-07-01','OUTSIDE_HISTORICAL_PERIOD';scores=[x.txt().strip()for x in header.all('sdc-site-match-header__team-score-block')];assert len(scores)==2 and all(x.isdigit()for x in scores),'EXPLICIT_FINAL_SCORE_REQUIRED';score='-'.join(scores)
  detail=header.one('sdc-site-match-header__detail-fixture').txt();hits=[(label,key)for label,key in labels.items()if re.search(r'\.\s*'+re.escape(label)+r'\b',detail,re.I)];assert hits,'ACCEPTED_MENS_SENIOR_COMPETITION_LABEL_REQUIRED';comp,key=max(hits,key=lambda x:len(x[0]));proof=dict(provider='Sky',url=row['url'],sha256=a['content_sha256'],path=str(p.resolve()),response_timestamp=a['metadata'].get('response_timestamp'),source_locator='Exact canonical resource and native full-time header: ordered countries, explicit competition, kickoff and final score',verified_fixture_context=dict(home=names[0],away=names[1],kickoff=kickoff,score=score,competition=comp,competition_key=key,level='SENIOR'),source_scope='HEADER_CONTEXT_ONLY;FULL_SQUAD_AND_PLAYER_FACTS_REQUIRE_EXISTING_PARSER_AND_IDENTITY_POLICY')
  if aet:
   assert score==aet['score'] and kickoff==aet['kickoff']
   proof['extra_time_evidence']=aet
  if rule:
   assert score==rule['ordered_regulation_score'] and kickoff==rule['kickoff']
   proof['no_extra_time_rule']=rule
  eid=evidence(db,proof,'EXACT_HISTORICAL_NATIONAL_HEADER','RAW_HEADER_VERIFIED_SQUAD_PENDING',0);keys=[]
  for country in names:
   prior=[r['team_key']for r in db.execute("select team_key,display_name from football_teams where team_type='NATIONAL'and level='SENIOR'")if namekey(r['display_name'])==namekey(country)];assert len(prior)<=1,'AMBIGUOUS_ACCEPTED_COUNTRY_KEY';keys.append(team(db,country,'SENIOR',eid,verified_key=prior[0]if prior else None))
  possible=[r for r in db.execute('select*from international_matches where home_team_key=?and away_team_key=?and madrid_date=?',(keys[0],keys[1],day))];same=[r for r in possible if r['kickoff_utc']==kickoff and r['score'].replace(' ','')==score];assert len(same)<=1,'EXISTING_PHYSICAL_MATCH_AMBIGUITY';assert not possible or len(same)==1,'EXISTING_COUNTRY_DAY_NEEDS_FIXTURE_ADJUDICATION'
  if same:key=same[0]['competition_key'];mid=same[0]['match_id']
  else:mid='nt_sky_header:'+native
  mid=put_match(db,dict(match_id=mid,home_team_key=keys[0],away_team_key=keys[1],kickoff_utc=kickoff,madrid_date=day,competition_key=key,match_state='COMPLETED',score=score,duration_minutes=120 if aet else 90,extra_time_status='VERIFIED_120' if aet else 'VERIFIED_DIRECT_SHOOTOUT_AFTER_90' if rule else 'VERIFIED_NATIVE_FT_90',identity_status='EXACT_CANONICAL_RESOURCE_AND_NATIVE_NATIONAL_HEADER;SQUAD_PENDING',metadata_json=encoded(dict(competition=comp,source=proof))),'Sky',native,eid)
  db.execute('release header_context');db.commit();accepted.append(native);out.append(dict(url=row['url'],state='HEADER_CONTEXT_VERIFIED',reference_match_id=mid,countries=names,date=day,competition_key=key))
 except(ValueError,AssertionError,KeyError,TypeError,OSError)as e:
  db.execute('rollback to header_context');db.execute('release header_context');db.commit();out.append(dict(url=row['url'],state='ACTIONABLE_SOURCE_EXCEPTION',reason=str(e)))
 (root/'results-private.json').write_text(json.dumps(out,indent=2));(root/'NEW_PROVIDER_IDS_PRIVATE.json').write_text(json.dumps(accepted,indent=2))
assert not db.execute('pragma foreign_key_check').fetchall();db.close();assert sha(source)==original
summary=dict(states=dict(Counter(r['state']for r in out)),new_fixture_contexts=len(accepted),new_player_facts=0,network_calls=svc.client.calls,production_writes=0,sha256=sha(target));(root/'SUMMARY.json').write_text(json.dumps(summary,indent=2));print(summary)
