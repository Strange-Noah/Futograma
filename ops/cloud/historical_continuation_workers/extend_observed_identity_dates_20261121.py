"""One-time delivery helper: extend accepted alias scope to actual observed days only."""
import sys,json,datetime
from pathlib import Path
source,receipt,target=map(Path,sys.argv[1:]);assert not target.exists()
cases=json.loads(source.read_text());changes=[]
for row in json.loads(receipt.read_text()):
 if row.get('parse')!='PARSED':continue
 n=row['native'];day=n['fixture']['match_date']
 if not '2024-07-01'<=day<'2026-07-01':continue
 for sheet in n['sheets']:
  for person in sheet['players']:
   ds=[d for d in cases if d['club_id']==sheet['configured_club_id'] and d['observed_name']==person['name']]
   if len(ds)!=1:continue
   d=ds[0];proof=d['verified_identity']['proof'];assert proof['identity_status']=='VERIFIED' and proof['identity_proof']
   if any(v['start']<=day<v['end_exclusive'] for v in d['validity']):continue
   ev=dict(date=day,url=row['url'],sha256=row['artifact']['content_sha256'],basis='ALREADY_ACCEPTED_IDENTITY_ALIAS;ACTUAL_SAME_CLUB_MATCHDAY_OBSERVATION;NO_MANAGEMENT_INFERENCE')
   d['validity'].append(dict(start=day,end_exclusive=str(datetime.date.fromisoformat(day)+datetime.timedelta(days=1))))
   d.setdefault('additional_observed_source_dates',[]).append(ev);changes.append(dict(club_id=d['club_id'],name=d['observed_name'],**ev))
target.write_text(json.dumps(cases,indent=2));target.with_suffix('.receipt.json').write_text(json.dumps(changes,indent=2));print(dict(existing_alias_observation_extensions=len(changes),new_identities=0))
