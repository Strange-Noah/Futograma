"""One-time bulk national delivery using existing verified contexts, parsers and writers."""
import sys,json,sqlite3,re
from pathlib import Path
from collections import Counter,defaultdict
from functools import lru_cache
sys.path.insert(0,str(Path.cwd()))
from squad_pipeline.source_interface import SourceService,EvidenceStore,SourceRequest,RawSourceArtifact
from squad_pipeline.identity_sources import fotmob
from squad_pipeline.identity_registry import lookup
from squad_pipeline.international_parse import parse_sky,namekey
from squad_pipeline.international_store import evidence,team,put_match,put_appearance,encoded
from squad_pipeline.minute_policy import select,audit
from squad_pipeline.live_parse import Tree
from squad_pipeline.cloud.quality import sha,pack
from squad_pipeline.cloud.storage import LocalStore
from squad_pipeline.cloud.export import export
if 'source' not in globals():
 source=Path('.cloud/one-pass-declared-country-ingestion212-20261122/candidate.sqlite');reference=Path('.cloud/one-pass-shared-senior-reference213-20261122/reference.sqlite');root=Path('.cloud/one-pass-shared-senior-ingestion213-20261122')
root.mkdir(exist_ok=False);target=root/'candidate.sqlite';original=sha(source);reference_hash=sha(reference)
with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)as a,sqlite3.connect(target)as b:a.backup(b)
db=sqlite3.connect(target);db.row_factory=sqlite3.Row;db.execute('pragma foreign_keys=on');ref=sqlite3.connect(reference.resolve().as_uri()+'?mode=ro',uri=True);ref.row_factory=sqlite3.Row
svc=SourceService(EvidenceStore(root/'sources',archives=[Path.cwd()]),'retained-national-whole-sheets',set(),max_calls=0);artifacts={};profile_items=[]
for receipt in [*Path('.cloud').glob('*/results-private.json'), *globals().get('source_receipts', [])]:
 try:rows=json.load(open(receipt))
 except(ValueError,OSError):continue
 if not isinstance(rows,list):continue
 for row in rows:
  if not isinstance(row,dict)or not row.get('artifact'):continue
  a=row['artifact'];artifacts.setdefault(a['content_sha256'],(receipt.parent,a))
  if a['request']['provider']=='FotMob' and re.search(r'/players/\d+',a['request']['url']):profile_items.append((receipt.parent,a))
def read(parent,a):
 p=Path(a['locator']);p=p if p.is_absolute()else parent/p
 if not p.exists()or not p.resolve().is_relative_to(Path.cwd().resolve()):
  found=list(Path('.cloud').glob('*/bodies/'+a['content_sha256']+'.response'));assert found,'RETAINED_PROJECT_BODY_NOT_FOUND';p=found[0]
 art=RawSourceArtifact(SourceRequest(**a['request']),a['content_sha256'],str(p.resolve()),a['metadata'],a['authority']);got=svc.acquire(art.request,art);assert got.outcome=='FOUND','SOURCE_REPLAY_UNAVAILABLE'
 return svc.store.read(got.artifact).decode(),dict(a['metadata'],url=art.request.url,sha256=art.content_sha256,path=str(p.resolve()),provider=art.request.provider)
links={r['provider_player_id']:r['player_id']for r in db.execute("select * from verified_identity_links where provider='FotMob'")if json.loads(r['evidence_json']).get('identity_status')=='VERIFIED'};profiles={};seen=set();profile_errors=[]
for parent,a in profile_items:
 if a['content_sha256']in seen:continue
 seen.add(a['content_sha256']);ident=re.search(r'/players/(\d+)',a['request']['url'])[1]
 if ident not in links:continue
 try:
  raw,cap=read(parent,a);r,=fotmob(raw,dict(cap,status='SUCCESS'));pid=links[ident]
  stamp=a['metadata'].get('response_timestamp','')
  if pid not in profiles or stamp>profiles[pid][0]:profiles[pid]=(stamp,r)
 except(ValueError,AssertionError,KeyError,TypeError,OSError)as e:profile_errors.append(dict(provider_player_id=ident,reason=str(e)))
@lru_cache(maxsize=12000)
def known_person(cid,name,provider_key):return lookup(db,cid,dict(name=name,provider_key=provider_key))
(root/'PROFILE_INDEX_PRIVATE.json').write_text(json.dumps({str(pid):dict(url=r.source_url,provider_ids=r.provider_ids)for pid,(_,r)in profiles.items()},indent=2))
members={}
for start,end in [('2024-07-01','2025-01-01'),('2025-01-01','2025-07-01'),('2025-07-01','2026-01-01'),('2026-01-01','2026-07-01')]:
 members[start]={cid:{r[0]for r in db.execute('select distinct p.player_id from player_match_minutes p join matches m using(match_id)where p.club_id=? and m.local_date>=?and m.local_date<?',(cid,start,end))}for cid in[8456,9825,9847,8634,9823]}
frames=ref.execute("select m.*,p.provider_match_id,e.body_sha256,e.payload_json from international_matches m join international_match_provider_ids p using(match_id)join persistent_source_evidence e using(evidence_id)join football_teams t on t.team_key=m.home_team_key where p.provider='Sky'and m.madrid_date>='2024-07-01'and m.madrid_date<'2026-07-01'and t.level='SENIOR' order by m.madrid_date").fetchall();results=[];issues=[];identity_issues=[];receipts=[];baseline_rows=db.execute('select count(*)from international_appearances').fetchone()[0];baseline_matches=db.execute('select count(*)from international_matches').fetchone()[0]
new_ids=set(json.load(open(reference.parent/'NEW_PROVIDER_IDS_PRIVATE.json')))
selection_claims=globals().get('selection_claims',{})
selection_aliases=globals().get('selection_aliases',{})
for frame in frames:
 if frame['provider_match_id']not in new_ids:continue
 db.execute('savepoint national_fixture');pending=[]
 try:
  assert frame['body_sha256']in artifacts,'ACCEPTED_NATIONAL_SOURCE_NOT_IN_ACQUISITION_INDEX';raw,src=read(*artifacts[frame['body_sha256']]);proof=json.loads(frame['payload_json']);names={side:ref.execute('select display_name from football_teams where team_key=?',(frame[side+'_team_key'],)).fetchone()[0]for side in['home','away']};labels={};cols=Tree(raw).root.all('sdc-site-team-lineup__col');assert len(cols)==2,'FULL_TWO_SIDED_SQUAD_REQUIRED'
  for side,col in zip(['home','away'],cols):
   badge=col.one('sdc-site-team-lineup__team-badge-image');assert namekey(badge.attrs['alt'])==namekey(names[side]),'NATIONAL_BADGE_COUNTRY_MISMATCH'
   labels[side]=dict(provider='Sky',side=side,provider_match_id=frame['provider_match_id'],provider_team_id=badge.attrs['src'].rsplit('/',1)[-1].split('.')[0],directory_source_url=src['url'],directory_source_sha256=src['sha256'],source_locator='Same retained full national sheet ordered country badge',full_name=names[side],short_name=col.one('sdc-site-team-lineup__team-name').txt())
  meta=json.loads(frame['metadata_json']);comp=meta.get('competition')or(proof.get('verified_fixture_context')or{}).get('competition');assert comp,'ACCEPTED_COMPETITION_LABEL_REQUIRED'
  if frame['competition_key']=='OFFICIAL_COMPETITION:UEFA_EURO':comp='European Championships'
  if frame['competition_key']=='OFFICIAL_COMPETITION:SENIOR_FRIENDLY':comp='International Match'
  context=dict(names,home_team_key=frame['home_team_key'],away_team_key=frame['away_team_key'],kickoff=frame['kickoff_utc'],competition_label=comp,competition_key=frame['competition_key'],level='SENIOR',evidence=proof,team_label_certificates=labels)
  if frame['duration_minutes']in[90,120]:context.update(verified_duration_minutes=frame['duration_minutes'],duration_evidence=dict(scope='EXACT_MATCH_DURATION',sha256=src['sha256'],accepted_match_id=frame['match_id']))
  if context.get('duration_evidence') and proof.get('no_extra_time_rule'):
   context['duration_evidence']['no_extra_time_rule']=proof['no_extra_time_rule']
  sheet=parse_sky(raw,src,context);day=frame['madrid_date'];period=max(start for start in members if start<=day);selected={}
  for side,squad in zip(['home','away'],sheet['sheets']):
   country=names[side];provider_ids={str(x[0])for x in ref.execute("select provider_team_id from football_team_provider_ids where team_key=?and provider='FotMob'",(frame[side+'_team_key'],))}
   for person in squad['players']:
    possible=set();club_scope=[]
    alias=selection_aliases.get((frame['match_id'],namekey(country),namekey(person['name'])))
    for cid,population in members[period].items():
     pid=known_person(cid,person['name'],person['provider_key'])
     if pid is not None and pid in population:possible.add(pid);club_scope.append(cid)
     if alias and alias['player_id'] in population:
      possible.add(alias['player_id']);club_scope.append(cid)
    if not possible:continue
    supported=[]
    for pid in possible:
     record=profiles[pid][1] if pid in profiles else None
     affiliation=[]
     if record:
      ident=record.provider_ids['FotMob'];entries=record.raw_fields.get('career',{}).get('national team',{}).get('teamEntries',[])
      affiliation=[t for t in entries if str(t.get('participantId'))==ident and (str(t.get('teamId'))in provider_ids or ({'korearepublic':'southkorea'}.get(namekey(t.get('team','')),namekey(t.get('team','')))=={'korearepublic':'southkorea'}.get(namekey(country),namekey(country))))and (not t.get('startDate')or t['startDate'][:10]<=day)and(not t.get('endDate')or day<=t['endDate'][:10])]
     selection=selection_claims.get((frame['match_id'],pid,namekey(country)))
     if selection:affiliation.append(dict(selection,identity_alias=alias if alias and alias['player_id']==pid else None))
     if affiliation:supported.append((pid,record,affiliation))
    if len(supported)!=1:
     identity_issues.append(dict(name=person['name'],date=day,country=country,source=src['url'],candidate_player_ids=sorted(possible),reason='NO_UNIQUE_IDENTITY_PLUS_DOCUMENTED_NATIONAL_CAREER_AT_MATCH_DATE',club_scope=club_scope));continue
    pid,record,affiliation=supported[0];assert pid not in selected,'PLAYER_ON_BOTH_COUNTRY_SIDES';decision=select([dict(status=person['participation_status'],minutes=person['derived_minutes'],provider='Sky',source_url=src['url'])]);assert decision['accepted']or person['derived_minutes']is None,'MINUTE_POLICY_UNRESOLVED';selected[pid]=dict(side=side,person=person,decision=decision,record=record,affiliation=affiliation,club_scope=club_scope)
  for (mid,pid,expected_country),selection in selection_claims.items():
   if mid!=frame['match_id'] or not selection.get('exact_matchday_exclusion') or pid in selected:continue
   for side,squad in zip(('home','away'),sheet['sheets']):
    if namekey(names[side])!=expected_country or not squad['complete_squad']:continue
    if any(namekey(p['name']) in selection['possible_display_names'] for p in squad['players']):continue
    club_scope=[cid for cid,population in members[period].items() if pid in population]
    if not club_scope:continue
    person=dict(participation_status='NOT_IN_MATCHDAY_SQUAD',derived_minutes=0)
    decision=select([dict(status=person['participation_status'],minutes=0,provider='Sky',source_url=src['url'])])
    assert decision['accepted']
    selected[pid]=dict(side=side,person=person,decision=decision,record=None,affiliation=[selection],club_scope=club_scope)
  if selected:
   ev=dict(src,accepted_reference_match=frame['match_id'],reference_database_sha256=reference_hash,source_locator='Existing international_parse.parse_sky: full XI, bench, typed events',identity_scope='VERIFIED_CANONICAL_SOURCE_ID;OBSERVED_CLUB_PERIOD;EXPLICIT_NATIONAL_CAREER_NOT_CITIZENSHIP');eid=evidence(db,ev,'EXACT_HISTORICAL_NATIONAL_FIXTURE_AND_SQUADS','COMPLETE_MATCH_SHEET',0);keys={}
   for side in['home','away']:
    prior=db.execute("select team_key from football_teams where team_type='NATIONAL'and level='SENIOR'and display_name=?",(names[side],)).fetchall();assert len(prior)<=1,'AMBIGUOUS_ACCEPTED_COUNTRY_IDENTITY';keys[side]=team(db,names[side],'SENIOR',eid,verified_key=prior[0][0]if prior else None)
   match=dict(frame);match.update(home_team_key=keys['home'],away_team_key=keys['away'],metadata_json=encoded(dict(competition=comp,source=ev)));mid=put_match(db,match,'Sky',frame['provider_match_id'],eid)
   for pid,d in selected.items():
    person=d['person'];decision=d['decision'];claim=dict(match_id=mid,player_id=pid,represented_team_key=keys[d['side']],participation_status=person['participation_status'],minutes=decision['minutes'],reported_minutes=None,derived_minutes=person['derived_minutes'],minutes_precision=decision['reason'],verification_status='VERIFIED_IDENTITY_AND_PARTICIPATION',evidence_scope='COMPLETE_RETAINED_SENIOR_SQUAD_AND_DOCUMENTED_NATIONAL_SELECTION',revision=1,metadata_json=encoded(dict(source_provider='Sky',source=ev,profile=d['record'].evidence() if d['record'] else None,affiliation=d['affiliation'],club_period_scope=d['club_scope'],minute_disposition=decision['reason'])))
    old=db.execute('select*from international_appearances where match_id=?and player_id=?',(mid,pid)).fetchone()
    if old:
     if old['minutes']!=claim['minutes']or old['represented_team_key']!=claim['represented_team_key']or old['participation_status']!=claim['participation_status']:issues.append(dict(match_id=mid,player_id=pid,reason='RETAINED_ACCEPTED_FACT_PRESERVED;ADDITIONAL_SOURCE_DIFFERENCE'))
     continue
    audit(db,'NATIONAL',mid,pid,decision);assert put_appearance(db,claim);db.execute('insert or ignore into international_fact_evidence values(?,?,?,?,?,?,?)',(mid,pid,eid,'shared_retained_historical_senior_sheet',encoded(claim),1,decision['reason']));pending.append(dict(match_id=mid,player_id=pid,minutes=claim['minutes'],club_scope=d['club_scope'],date=day,source_sha256=src['sha256']))
  db.execute('release national_fixture');db.commit();receipts+=pending;results.append(dict(reference_match_id=frame['match_id'],state='INVESTIGATED',new_rows=len(pending)))
 except(ValueError,AssertionError,KeyError,TypeError,OSError)as e:
  db.execute('rollback to national_fixture');db.execute('release national_fixture');db.commit();results.append(dict(reference_match_id=frame['match_id'],state='ACTIONABLE_EXCEPTION',reason=str(e)))
 (root/'results-private.json').write_text(json.dumps(results,indent=2));(root/'appearances-private.json').write_text(json.dumps(receipts,indent=2))
assert not db.execute('pragma foreign_key_check').fetchall();summary=dict(source_frames=len(frames),results=dict(Counter(x['state']for x in results)),new_national_matches=db.execute('select count(*)from international_matches').fetchone()[0]-baseline_matches,new_national_player_rows=db.execute('select count(*)from international_appearances').fetchone()[0]-baseline_rows,documented_profiles=len(profiles),identity_exceptions=len(identity_issues),retained_fact_differences=len(issues),network_calls=svc.client.calls,production_writes=0);db.close();ref.close();assert sha(source)==original and sha(reference)==reference_hash
try:
 z=root/'candidate.sqlite.gz';pack(target,z);h,p=LocalStore(root/'store').publish(target,z,None);export(target,root/'site',h,p,Path('web'));summary['ordinary_export']='PASS'
except ValueError as e:summary.update(ordinary_export='FAIL',reason=str(e))
summary['sha256']=sha(target)
for name,value in [('SUMMARY',summary),('identity-exceptions-private',identity_issues),('retained-differences-private',issues),('profile-exceptions-private',profile_errors)]:(root/(name+'.json')).write_text(json.dumps(value,indent=2))
print(summary)
