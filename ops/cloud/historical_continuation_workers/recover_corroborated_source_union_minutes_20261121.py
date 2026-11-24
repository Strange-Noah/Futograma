import sys,json,sqlite3
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path.cwd()))
from squad_pipeline.source_interface import SourceService,SourceRequest,RawSourceArtifact,EvidenceStore
from squad_pipeline.identity_registry import lookup
from squad_pipeline.minute_policy import select
from squad_pipeline.competition_identity import COMP
COMP['French Ligue 1']=53
from ops.cloud.coldstart_acceptance import apply_appearance
from squad_pipeline.cloud.quality import sha,pack
from squad_pipeline.cloud.storage import LocalStore
from squad_pipeline.cloud.export import export
if 'source' not in globals():
 root=Path('.cloud/one-pass-corroborated-source-union-minutes-20261121');source=Path('.cloud/one-pass-corroborated-source-union-ingestion-20261121/candidate.sqlite')
root.mkdir(exist_ok=False);target=root/'candidate.sqlite';original=sha(source)
with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(target) as b:a.backup(b)
s=SourceService(EvidenceStore(root/'sources',archives=[Path.cwd()]),'archived-sky-minutes',set(),max_calls=0);db=sqlite3.connect(target);db.row_factory=sqlite3.Row;db.execute('pragma foreign_keys=on');ds=json.load(open(globals().get('identity_file','.cloud/one-pass-scoped-identities-r19-20261121.json')));seen=set();counts=Counter();issues=[];receipts=[]
for receipt in globals().get('source_receipts',[Path('.cloud/one-pass-broad-squads-native-final-20261121/results-private.json'),Path('.cloud/one-pass-barca-bayern-native-final-20261121/results-private.json'),Path('.cloud/one-pass-monthly-squads-native-20261121/results-private.json'),Path('.cloud/one-pass-barca-bayern-next-native-20261121/results-private.json'),Path('.cloud/one-pass-retained-sky-label-recovery-20261121/results-private.json'),Path('.cloud/one-pass-arsenal-last-h1-native-20261121/results-private.json')]):
 if receipt.name not in ['results-private.json','lineups-private.json']:continue
 try:rows=json.load(open(receipt))
 except (ValueError,OSError):continue
 if not isinstance(rows,list):continue
 for row in rows:
  if not isinstance(row,dict) or not (row.get('native') or {}).get('sheets') or 'skysports.com/football/' not in row.get('url',''):continue
  if row.get('parse') in ['INCOMPLETE','MALFORMED']:continue
  a=row.get('artifact')
  if not a or a['content_sha256'] in seen:continue
  seen.add(a['content_sha256'])
  try:
   p=Path(a['locator']);p=p if p.is_absolute() else receipt.parent/p
   if not p.exists():
    retained=list(Path('.cloud').glob('*/bodies/'+a['content_sha256']+'.response'));assert retained,'RETAINED_BODY_NOT_FOUND';p=retained[0]
   art=RawSourceArtifact(SourceRequest(**a['request']),a['content_sha256'],str(p.resolve()),a['metadata'],a['authority']);_,parsed=s.observe(art.request,art);assert parsed and parsed.outcome=='PARSED';n=parsed.native;f=n['fixture'];day=f['match_date'];m=db.execute("select match_id from canonical_fixture_sources where provider='Sky' and provider_match_id=?",(str(n['provider_fixture_id']),)).fetchone()
   if not m:continue
   mid=m[0]
   for sheet in n['sheets']:
    cid=sheet['configured_club_id']
    if cid not in [8456,9825,9847,8634,9823] or not sheet.get('complete_events') or not sheet.get('complete_squad'):continue
    proof=dict(f['source_evidence'][0],complete_squad=True,complete_events=True,source_identity_verified=True,provider_match_id=n['provider_fixture_id'],minute_convention=sheet['minute_convention'])
    for person in sheet['players']:
     player=dict(person);pid=lookup(db,cid,player)
     if pid is None:
      cases=[d for d in ds if d['club_id']==cid and d['observed_name']==player['name'] and any(v['start']<=day<v['end_exclusive'] for v in d['validity'])]
      if len(cases)==1:
       d=cases[0];provider_id=d['verified_identity']['proof']['source_record']['provider_ids']['FotMob'];player.update(provider_key='fotmob:'+provider_id,provider_player_id=provider_id);pid=lookup(db,cid,player);assert pid==d['canonical_player_id']
     if pid is None:counts['held_identity']+=1;continue
     old=db.execute('select participation_status,minutes from player_match_minutes where club_id=? and match_id=? and player_id=?',(cid,mid,pid)).fetchone()
     if not old or old['minutes'] is not None:counts['retained_existing_or_not_ingested']+=1;continue
     decision=select([dict(status=player['status'],minutes=player['derived_minutes'],provider='DERIVED_CLOCK')]);assert decision['accepted'];player.update(participation_status=player['status'],minutes=decision['minutes'],minutes_precision='DERIVED_NOMINAL_90',source_path=player.get('json_path'))
     link=db.execute("select provider_player_id from verified_identity_links where provider='FotMob' and player_id=?",(pid,)).fetchone()
     player['provider_player_id']=link[0] if link else 'canonical:'+str(pid)
     player['provider_id_namespace']='VERIFIED_CANONICAL_REFERENCE;NOT_SKY_NATIVE_ID'
     if link:player['provider_key']='fotmob:'+link[0]
     outcome=apply_appearance(db,cid,mid,player,proof,'2026-11-17','BACKFILL',canonical_player_id=pid,supplement_unknown=True);counts[outcome['state']]+=1;receipts.append(dict(match_id=mid,player_id=pid,minutes=player['minutes'],state=outcome['state'],source_sha256=a['content_sha256']))
   db.commit()
  except (ValueError,AssertionError,KeyError,TypeError) as e:issues.append(dict(url=row['url'],reason=str(e)));db.commit()
assert sha(source)==original;assert db.execute('pragma integrity_check').fetchone()[0]=='ok';assert not db.execute('pragma foreign_key_check').fetchall();db.close()
summary=dict(counts=dict(counts),source_exceptions=len(issues),network_calls=s.client.calls,production_writes=0,sha256=sha(target))
try:
 z=root/'candidate.sqlite.gz';pack(target,z);h,p=LocalStore(root/'store').publish(target,z,None);export(target,root/'site',h,p,Path('web'));summary['ordinary_export']='PASS'
except ValueError as e:summary.update(ordinary_export='FAIL',error=str(e))
for name,value in [('SUMMARY',summary),('minute-receipts-private',receipts),('exceptions-private',issues)]:(root/(name+'.json')).write_text(json.dumps(value,indent=2))
print(summary)
