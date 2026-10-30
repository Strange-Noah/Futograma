"""Bounded first-team refresh; youth-only players are retained without repeated membership audits."""
import json
from dataclasses import fields
from .core import ROOT
from .identity_pipeline import Record,DDL,persist_verified_alias
from .roster_verification import seed_candidates,verify_records
from .identity_sources import official_roster
from .managed_roster import decision,add_event
from .international_store import digest,encoded,evidence

def refresh(db,fetch,on,asset_ref):
    db.executescript(DDL);candidates=seed_candidates([dict(r) for r in db.execute('SELECT * FROM players')],[dict(r) for r in db.execute('SELECT * FROM validation_player_aliases')]);byid={c.player_id:c for c in candidates};allowed={f.name for f in fields(Record)}
    for row in db.execute('SELECT player_id,evidence_json FROM verified_name_aliases'):
        cert=json.loads(row['evidence_json']);raw=cert.get('source_record',{});r=Record(**{k:v for k,v in raw.items() if k in allowed});r.raw_fields['_verified_identity']=True
        if row['player_id'] in byid:byid[row['player_id']].records.append(r)
    records=[];failures=[]
    for cid,url in json.loads((ROOT/'ops/cloud/roster-sources.json').read_text()).items():
        try:
            source,raw=fetch(url,'daily_official_first_team:'+cid);evidence(db,dict(source,retained_asset_ref=asset_ref),'CURRENT_OFFICIAL_FIRST_TEAM_SNAPSHOT','RAW_SOURCE',1);records.extend(official_roster(raw,source,int(cid)))
        except Exception as exc:failures.append(dict(club_id=int(cid),url=url,error=type(exc).__name__+':'+str(exc),prior_membership_retained=True))
    # Positive cards can promote a youth player in future. Absence is never automatic relegation/departure.
    scope={r['club_id']:[p[0] for p in db.execute('SELECT DISTINCT player_id FROM validation_roster_membership WHERE club_id=?',(r['club_id'],))] for r in db.execute('SELECT * FROM clubs')};prior={(cid,pid):decision(db,cid,pid,on) for cid,players in scope.items() for pid in players}
    result=verify_records(candidates,records,on,scope,prior)
    for r in result['source_results']:
        if r['identity_status']=='VERIFIED':persist_verified_alias(db,Record(**{k:v for k,v in r['source_record'].items() if k in allowed}),r,on)
    updates=0
    for d in result['players']:
        state=d['management']
        if d['identity_status']=='VERIFIED' and state['state']=='INCLUDED' and state.get('evidence'):
            add_event(db,d['club_id'],int(d['player_id']),state['status'],state.get('effective_from'),on,state['evidence'],'Daily unified raw official roster verification',end_exclusive=state.get('end_exclusive'),precision=state.get('date_precision','OBSERVED_SNAPSHOT'));updates+=1
    db.commit();return dict(source_failures=failures,verified_positive_updates=updates,unresolved_new_source_records=result['unresolved_source_records'],youth_reaudits=False)
