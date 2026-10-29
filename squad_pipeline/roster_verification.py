"""Single raw-record verification workflow shared by live acquisition and replay."""
from dataclasses import asdict
from .identity_pipeline import Candidate,Record,ranked_candidates,resolve,management_state,variants
import json,hashlib

def seed_candidates(players,aliases):
    candidates={int(p['player_id']):Candidate(int(p['player_id']),[p['display_name']]) for p in players}
    for alias in aliases:
        pid=int(alias['player_id']);c=candidates[pid];data=json.loads(alias['identity_json']);c.names=list(dict.fromkeys(c.names+(data.get('names') or [])));key=alias['provider_key'];ids={}
        if key.startswith('fotmob:') and key.split(':',1)[1].isdigit():ids={'FotMob':key.split(':',1)[1]}
        elif key.startswith('https://torlytics.de/spieler/'):ids={'Torlytics':key}
        if data.get('fotmob_id') is not None:ids['FotMob']=str(data['fotmob_id'])
        if ids:c.records.append(Record('registry://retained-canonical-provider-link',hashlib.sha256(alias['identity_json'].encode()).hexdigest(),'validation_player_aliases/provider_key','CanonicalRegistry',c.names,provider_ids=ids,retrieved=False,raw_fields={'retained_identity_anchor':True}))
    return list(candidates.values())

def verify_records(candidates,records,on,club_players,prior=None):
    candidates=list(candidates);prior=prior or {};results={};assigned={};byid={str(c.player_id):c for c in candidates}
    def accept(record,result):
        pid=result['player_id'];c=byid[pid];record.raw_fields['_candidate_only']=False;record.raw_fields['_verified_identity']=True
        if all((r.source_url,r.locator)!=(record.source_url,record.locator) for r in c.records):c.records.append(record)
        c.names=list(dict.fromkeys(c.names+record.names));assigned.setdefault(pid,[]).append(record)
    # Existing stable source keys are canonical identity anchors only. No old membership decisions seed this stage.
    for i,r in enumerate(records):
        if not r.provider_ids or r.provider not in ('FotMob','Torlytics'):continue
        result=resolve(r,candidates);results[i]=result
        if result['identity_status']=='VERIFIED':accept(r,result)
    # Provisional assignment is never persisted or treated as a stable ID. Two independent DOB/official records can verify it.
    for i,r in enumerate(records):
        if i in results and results[i]['identity_status']=='VERIFIED':continue
        if not r.birth_date:continue
        ranked=ranked_candidates(r,candidates)
        if not ranked or ranked[0]['score']<.82 or len(ranked)>1 and ranked[0]['score']-ranked[1]['score']<.03:continue
        c=byid[ranked[0]['player_id']]
        if c.dobs() and r.birth_date not in c.dobs():continue
        r.raw_fields['_candidate_only']=True
        c.records.append(r)
    for iteration in range(5):
        changed=0
        for i,r in enumerate(records):
            if i in results and results[i]['identity_status']=='VERIFIED':continue
            result=resolve(r,candidates);results[i]=result
            if result['identity_status']=='VERIFIED':accept(r,result);changed+=1
        if not changed:break
    dossier=[]
    for club,pids in club_players.items():
        for pid in pids:
            c=byid[str(pid)];own=[r for r in assigned.get(str(pid),[]) if str(club) in r.official_clubs];management=management_state(own,on,prior.get((int(club),int(pid))))
            related=[result for result in results.values() if any(x['player_id']==str(pid) for x in result.get('candidates',[]))]
            verified=any(r.raw_fields.get('_verified_identity') for r in c.records)
            dossier.append(dict(club_id=int(club),player_id=str(pid),name=c.names[0],identity_status='VERIFIED' if verified else 'UNRESOLVED',management=management,verified_source_records=[r.evidence() for r in own],candidate_source_records=related,remaining_requirements=[] if verified and management['state']!='UNKNOWN' else management.get('remaining_requirements',[])+([] if verified else ['Independent stable-ID/biography verification']),roster_impact='INCLUDED' if management['included'] else management['state']))
    return dict(players=dossier,source_results=list(results.values()),unresolved_source_records=[r for r in results.values() if r['identity_status']!='VERIFIED'],candidate_population=len(candidates),source_record_count=len(records),known_answers_seeded=False)
