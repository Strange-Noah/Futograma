"""One-time, evidence-bound source-team correspondence for actual blocked fixtures."""
import json,sqlite3,sys
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path.cwd()))
from squad_pipeline import canonical_fixture as c
from squad_pipeline.live_parse import CLUBS,Tree,certified_badge_sides
from squad_pipeline.competition_identity import COMP
from squad_pipeline.cloud.quality import sha
from squad_pipeline.source_interface import SourceService,EvidenceStore,SourceRequest,RawSourceArtifact
COMP['French Ligue 1']=53;COMP['FA Community Shield']=247
source=globals().get('source',Path('.cloud/one-pass-bulk-corrected-chain-20261121/batch199-minutes/candidate.sqlite'));root=globals().get('root',Path('.cloud/one-pass-shared-fixture-team-labels-20261121'));root.mkdir(exist_ok=False);target=root/'candidate.sqlite';original=sha(source)
with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)as a,sqlite3.connect(target)as b:a.backup(b)
db=sqlite3.connect(target);db.row_factory=sqlite3.Row;svc=SourceService(EvidenceStore(root/'sources',archives=[Path.cwd()]),'source-team-fixture-correspondence',set(),max_calls=0);before={t:Counter(tuple(r)for r in db.execute('select * from '+t))for t in ['canonical_fixtures','canonical_field_values','matches','players','player_match_minutes']};seen=set();decisions=[];held=[]
for folder in globals().get('folders',['one-pass-broad-squads-native-final-20261121','one-pass-barca-bayern-native-final-20261121','one-pass-monthly-squads-native-20261121','one-pass-barca-bayern-next-native-20261121','one-pass-retained-sky-label-recovery-20261121']):
 for row in json.load(open(Path('.cloud')/folder/'results-private.json')):
  if row.get('parse')!='PARSED' or row['url'].endswith('/521664'):continue
  a=row['artifact'];h=a['content_sha256']
  if h in seen:continue
  seen.add(h);db.execute('savepoint source_label')
  try:
   p=Path(a['locator']);p=p if p.is_absolute()else Path('.cloud')/folder/p
   if not p.exists():p=next(Path('.cloud').glob('*/bodies/'+h+'.response'))
   art=RawSourceArtifact(SourceRequest(**a['request']),h,str(p.resolve()),a['metadata'],a['authority']);got,parsed=svc.observe(art.request,art);assert got.outcome=='FOUND' and parsed.outcome=='PARSED','SOURCE_REPLAY_UNAVAILABLE'
   n=parsed.native;f=n['fixture'];day=f['match_date'];comp=COMP.get(f['competition']);assert comp and '2024-07-01'<=day<'2026-07-01','OUTSIDE_SCOPE'
   own=[s['configured_club_id']for s in n['sheets']if s['configured_club_id']in[8456,9825,9847,8634,9823]]
   sides={side:cid for side in ['home','away']for cid in own if f[side+'_team']==CLUBS[cid][1]};assert sides,'CONFIGURED_OWN_SIDE_REQUIRED'
   choices=[]
   for r in db.execute('select * from canonical_fixtures where domain=? and competition_key=? and edition=?',('CLUB',str(comp),c.club_period(day)[1])):
    if not all(r[side+'_key']=='club:'+str(cid)for side,cid in sides.items()):continue
    if c.value(db,r['match_id'],'kickoff')[0]!=c.instant(f['kickoff']):continue
    if not all(c.value(db,r['match_id'],k)[0]==v for k,v in c.score_fields(f['score']).items()):continue
    choices.append(dict(r))
   assert len(choices)==1,'NO_UNIQUE_EXISTING_ORDERED_OWN_CLUB_COMPETITION_EDITION_KICKOFF_SCORE_TUPLE'
   r=choices[0];anchors=[dict(x)for x in db.execute('select * from canonical_fixture_sources where match_id=? and provider<>?',(r['match_id'],'Sky'))];anchors=[x for x in anchors if (json.loads(x['evidence_json']).get('sha256')or json.loads(x['evidence_json']).get('report_sha256'))];assert anchors,'RETAINED_NON_SKY_ANCHOR_REQUIRED'
   raw=svc.store.read(got.artifact).decode();cols=Tree(raw).root.all('sdc-site-team-lineup__col');capture=dict(a['metadata'],url=row['url'],sha256=h);assert certified_badge_sides(cols,f,capture),'EXACT_ORDERED_FULL_BADGE_CAPTIONS_REQUIRED'
   badges=[col.all('sdc-site-team-lineup__team-badge-image')[0].attrs for col in cols]
   ev=dict(provider='Sky',url=row['url'],sha256=h,confidence='HIGH_SUPPORTED_INFERENCE',method='UNIQUE_EXISTING_OFFICIAL_FIXTURE_TUPLE_AND_CONFIGURED_OWN_CLUB_PLUS_EXACT_ORDERED_SOURCE_BADGE_CAPTIONS',canonical_match_id=r['match_id'],ordered_own_clubs=sides,competition=str(comp),edition=r['edition'],kickoff=f['kickoff'],score=f['score'],source_team_labels=f['source_team_names'],source_badges=badges,retained_anchor_sources=anchors,limitations=['No fuzzy-name comparison','Separately retained publishers; upstream data independence unknown','Correspondence concerns team identity only; no player identity, roster, management, minute or kickoff fact is inferred'])
   for side in ['home','away']:c.assert_alias(db,'TEAM','Sky',f['source_team_names'][side],r[side+'_key'],ev)
   decisions.append(dict(match_id=r['match_id'],url=row['url'],sha256=h,labels=f['source_team_names'],proof=ev));db.execute('release source_label')
  except (ValueError,AssertionError,KeyError,TypeError,StopIteration)as e:
   db.execute('rollback to source_label');db.execute('release source_label');held.append(dict(url=row['url'],reason=str(e)))
  db.commit()
assert all(Counter(tuple(r)for r in db.execute('select * from '+t))==v for t,v in before.items());assert not db.execute('pragma foreign_key_check').fetchall();db.close();assert sha(source)==original
(root/'DECISIONS_PRIVATE.json').write_text(json.dumps(decisions,indent=2));(root/'HELD_PRIVATE.json').write_text(json.dumps(held,indent=2));print(dict(corroborated_fixture_correspondences=len(decisions),held=len(held),accepted_football_fact_changes=0,network_calls=svc.client.calls,sha256=sha(target)))
