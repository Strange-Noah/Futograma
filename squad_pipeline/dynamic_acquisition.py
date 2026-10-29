"""Isolated incremental engine, evidence versions, explicit review and append-only decisions."""
import json,sqlite3,argparse
from collections import defaultdict
from datetime import timedelta
from .core import ROOT,BASE,read,write,digest,within,now
from .personal_workload import connect as workload_connect,artifact as register_artifact,discovery_id,resolve,upsert
from .dynamic_sources import cutoff,event_day,PLAYERS,available,trustworthy
from futbol.common import instant
DEFAULT=BASE/'state/dynamic/event_date.sqlite'

def encoded(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False)
def key(*values):return digest(encoded(values).encode())
def connect(path=DEFAULT):
    db=workload_connect(path);db.executescript((BASE/'dynamic_schema.sql').read_text());return db

def artifact(db,ev):
    """Snapshot consumed source versions without touching legacy bytes or changing source locators."""
    register_artifact(db,ev)
    source=(ROOT/ev['path']).resolve()
    if not source.is_relative_to(ROOT):raise ValueError('Evidence outside Futbol')
    if not source.exists():
        if ev['path']=='controlled_fixture':return
        raise ValueError('Missing source body')
    body=source.read_bytes()
    if digest(body)!=ev['sha256']:raise ValueError('Source changed between indexing and ingestion')
    folder=(source.parent/'immutable'/ev['sha256']) if source.is_relative_to(BASE/'tests/runtime') else BASE/'raw/dynamic/evidence'/ev['sha256'];folder.mkdir(exist_ok=True,parents=True);snapshot=folder/'response.json'
    if not snapshot.exists():snapshot.write_bytes(body)
    meta=source.with_suffix('.meta.json');rawmeta=meta.read_bytes() if meta.exists() else encoded(dict(acquisition_timestamp_unknown=True,original_path=ev['path'])).encode();mh=digest(rawmeta);ms=folder/(mh+'.meta.json')
    if not ms.exists():ms.write_bytes(rawmeta)
    db.execute('INSERT OR IGNORE INTO dynamic_source_versions VALUES(?,?,?,?,?,?)',(ev['artifact_id'],mh,ev['path'],str(snapshot.relative_to(ROOT)),str(ms.relative_to(ROOT)),ev['sha256']))

def emit(db,mode,pid,mid,day,previous,state,payload):
    eid=key(mode,pid,mid,day,state,payload)
    db.execute('INSERT OR IGNORE INTO dynamic_events VALUES(?,?,?,?,?,?,?,?)',(eid,mode,pid,mid,day,previous,state,encoded(payload)))

def review(db,mode,pid,mid,day,issue,payload,record=None):
    record=record or {};rid=key(mode,pid,mid,issue);old=db.execute('SELECT * FROM dynamic_reviews WHERE review_id=?',(rid,)).fetchone()
    status=old['review_status'] if old else 'OPEN'
    if old and json.loads(old['payload_json']).get('candidate_values')!=payload.get('candidate_values') and status not in ('OPEN','DEFERRED','EVIDENCE_REQUESTED'):status='OPEN'
    db.execute('INSERT INTO dynamic_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(review_id) DO UPDATE SET last_seen=excluded.last_seen,payload_json=excluded.payload_json,review_status=excluded.review_status', (rid,mode,pid,PLAYERS.get(pid,str(pid)),mid,record.get('match_date'),str(record.get('represented_team_id') or record.get('teams') or ''),record.get('competition_name'),issue,day,day,status,encoded(payload)))
    return rid

def decisions(db,pid,mid,day):
    rows=[]
    for r in db.execute('SELECT * FROM dynamic_decisions ORDER BY decided_at,decision_id'):
        s=json.loads(r['scope_json'])
        if s.get('player_id')==pid and s.get('match_id')==mid and instant(r['decided_at'])<=cutoff(day):rows.append(dict(r))
    return sorted(rows,key=lambda r:(instant(r['decided_at']),r['decision_id']))

class Engine:
    def __init__(self,path=DEFAULT):self.db=connect(path)
    def cycle(self,pid,day,mode,results,observations,context,name=None):
        db=self.db;cid=key(mode,day,pid);by={r['path']:r for r in results}
        if mode not in ('TRUE_AS_OF','EVENT_DATE'):raise ValueError('Unknown replay mode')
        for r in results:
            for c in r['candidates']:
                if instant(c['kickoff'])+timedelta(hours=3)>cutoff(day) or not available(c['evidence'],mode,day):raise ValueError('Candidate violates event/acquisition clock')
        for o in observations:
            if instant(o['kickoff'])+timedelta(hours=3)>cutoff(day) or not available(o['evidence'],mode,day):raise ValueError('Observation violates event/acquisition clock')
        input_hash=key(results,observations,{k:dict(getattr(context,k)) for k in ['teams','competitions','catalogs']},[dict(r) for r in db.execute('SELECT * FROM dynamic_decisions ORDER BY decision_id') if instant(r['decided_at'])<=cutoff(day)])
        prior=db.execute('SELECT input_hash FROM dynamic_cycle_inputs WHERE cycle_id=?',(cid,)).fetchone()
        if prior and prior[0]==input_hash:
            return {r['path']:json.loads(r['result_json']) for r in db.execute('SELECT * FROM dynamic_paths WHERE cycle_id=?',(cid,))}
        latest=db.execute('SELECT MAX(logical_date) FROM dynamic_cycles WHERE mode=? AND player_id=?',(mode,pid)).fetchone()[0]
        if latest and day<latest:raise ValueError('Changed historical inputs require a new isolated replay database; cannot inject final states into an earlier clock')
        with db:
            db.execute('INSERT INTO dynamic_cycle_inputs VALUES(?,?) ON CONFLICT(cycle_id) DO UPDATE SET input_hash=excluded.input_hash',(cid,input_hash))
            db.execute('INSERT OR IGNORE INTO workload_players VALUES(?,?)',(pid,name or PLAYERS.get(pid)))
            db.execute('INSERT OR IGNORE INTO dynamic_cycles VALUES(?,?,?,?,?)',(cid,mode,day,pid,now()))
            # All paths are logged even if an adapter fails or the caller omitted it.
            for path in 'ABC':
                if path not in by:by[path]=dict(path=path,status='FAILED',candidates=[],limitations=['ADAPTER_NOT_RETURNED'],checked_artifacts=[],network_requests=0)
                r=by[path]
                if r['status'] not in ['SUCCESS','NO_NEW_RECORDS','SOURCE_UNAVAILABLE','PARTIAL_COVERAGE','FAILED']:raise ValueError('Unknown path outcome')
                if r['status']=='NO_NEW_RECORDS' and (r['candidates'] or not r.get('checked_artifacts')):raise ValueError('NO_NEW_RECORDS needs successful source inspection')
                stored={k:v for k,v in r.items() if k!='candidates'};stored['candidate_match_ids']=sorted({x['match_id'] for x in r['candidates']})
                db.execute('INSERT INTO dynamic_paths VALUES(?,?,?,?) ON CONFLICT(cycle_id,path) DO UPDATE SET status=excluded.status,result_json=excluded.result_json',(cid,path,r['status'],encoded(stored)))
                db.execute('INSERT OR IGNORE INTO dynamic_path_versions VALUES(?,?,?,?,?,?)',(cid,input_hash,path,r['status'],encoded(stored),now()))
                for c in r['candidates']:
                    ev=c['evidence'];artifact(db,ev)
                    db.execute('INSERT OR IGNORE INTO dynamic_discoveries VALUES(?,?,?,?,?,?,?,?)',(mode,pid,c['match_id'],path,ev['artifact_id'],ev['json_path'],day,encoded(c)))
            allrows=list(db.execute('SELECT * FROM dynamic_discoveries WHERE mode=? AND player_id=?',(mode,pid)))
            grouped=defaultdict(list)
            for r in allrows:grouped[r['match_id']].append(dict(r))
            summary={p:dict(status=by[p]['status'],candidates=len({c['match_id'] for c in by[p]['candidates']}),limitations=by[p].get('limitations',[])) for p in 'ABC'}
            review(db,mode,pid,0,day,'COVERAGE_GAP',dict(path_A_result=summary['A'],path_B_result=summary['B'],path_C_result=summary['C'],source_artifacts=[e for r in by.values() for e in r.get('checked_artifacts',[])],candidate_values=[],recommended_action='补齐获准的个人分页、球队目录、相关赛事目录及独立分母；不得据部分缓存宣称完整。'))
            for mid,discovered in grouped.items():
                current=[o for o in observations if o['player_id']==pid and o['match_id']==mid]
                for o in current:
                    ev=o['evidence'];artifact(db,ev)
                    db.execute('INSERT OR IGNORE INTO player_match_discoveries VALUES(?,?,?,?,?,?,?,?)',(discovery_id(o),pid,mid,ev['kind'],ev['artifact_id'],ev['sha256'],ev['json_path'],encoded(o)))
                retained=[json.loads(r[0]) for r in db.execute('SELECT observation_json FROM player_match_discoveries WHERE player_id=? AND match_id=?',(pid,mid))]
                old=db.execute('SELECT * FROM dynamic_states WHERE mode=? AND player_id=? AND match_id=?',(mode,pid,mid)).fetchone()
                candidate=json.loads(discovered[0]['payload_json']);daymatch=event_day(candidate['kickoff']);issues=[];idx=None;app=None
                steps=['CANDIDATE_MATCH','MATCH_FINISHED','APPEARANCE_PENDING']
                if retained:
                    idx,app=resolve(retained,context)
                    badsets={tuple(sorted(json.loads(d['payload_json'])['teams'])) for d in discovered}
                    badtimes={instant(json.loads(d['payload_json'])['kickoff']) for d in discovered}
                    if len(badsets)>1 or len(badtimes)>1:
                        app.update(minutes=None,minutes_status='CONFLICT',verification_status='REVIEW_REQUIRED',conflicts_json=encoded(['DISCOVERY_MATCH_IDENTITY_CONFLICT']));issues.append('IDENTITY_UNRESOLVED')
                    if app['minutes_status']=='CONFLICT':state='CONFLICT';issues.append('MINUTES_CONFLICT' if 'MINUTES' in app['conflicts_json'] else 'IDENTITY_UNRESOLVED')
                    elif idx['eligibility_status']=='EXCLUDE':state='EXCLUDED'
                    elif idx['eligibility_status']=='REVIEW':state='REVIEW_REQUIRED';issues.append('COMPETITION_CLASSIFICATION_UNKNOWN')
                    elif app['played_status']=='CONFIRMED_NONAPPEARANCE':state='CONFIRMED_NONAPPEARANCE';steps.append(state)
                    elif app['played_status']=='CONFIRMED_APPEARANCE':
                        steps.append('CONFIRMED_APPEARANCE');state='VERIFIED_MINUTES' if app['minutes'] is not None else 'MINUTES_PENDING'
                    else:state='APPEARANCE_PENDING'
                    if state=='MINUTES_PENDING':issues.append('MINUTES_PENDING')
                    if state=='APPEARANCE_PENDING':issues.append('APPEARANCE_UNRESOLVED')
                else:state='APPEARANCE_PENDING';issues.append('APPEARANCE_UNRESOLVED')
                if state not in steps:steps.append(state)
                claims=[dict(discovery_id=discovery_id(o),minutes=o['minutes'],played=o['played'],authoritative=o['authoritative_minutes'],source=o['evidence'],rejected=o['rejected']) for o in retained]
                oldpayload=json.loads(old['state_json']) if old else None
                previous_claims={encoded((x['source']['artifact_id'],x['minutes'],x['played'])) for x in oldpayload.get('candidate_values',[])} if oldpayload else set()
                new_claims={encoded((x['source']['artifact_id'],x['minutes'],x['played'])) for x in claims}
                prior_source_correction=bool(oldpayload and oldpayload.get('source_correction_seen'))
                correction=bool(previous_claims and new_claims-previous_claims and oldpayload.get('minutes') is not None and any(x['authoritative'] and x['minutes'] is not None and x['minutes']!=oldpayload['minutes'] for x in claims)) or prior_source_correction
                if correction:issues.append('SOURCE_CORRECTION')
                used=[]
                for dec in decisions(db,pid,mid,day):
                    value=json.loads(dec['value_json']);scope=json.loads(dec['scope_json'])
                    if dec['action']=='accept_value' and app:
                        chosen=next((x for x in claims if x['discovery_id']==value.get('discovery_id') and x['minutes']==value.get('minutes') and not x['rejected']),None)
                        scoped=set(scope.get('source_hashes',[]));currenthash={x['source']['sha256'] for x in claims}
                        if not chosen or currenthash-scoped:issues.append('SOURCE_CORRECTION');continue
                        app.update(minutes=chosen['minutes'],played_status='CONFIRMED_NONAPPEARANCE' if chosen['played'] is False else 'CONFIRMED_APPEARANCE',minutes_source=chosen['source']['kind'],minutes_status='MANUAL_ACCEPTED_SOURCE_VALUE',verification_status='MANUAL_ACCEPTED',source_artifact_id=chosen['source']['artifact_id'],source_hash=chosen['source']['sha256']);state='VERIFIED_MINUTES_MANUAL' if idx['eligibility_status']=='INCLUDE' and 'IDENTITY_UNRESOLVED' not in issues else 'REVIEW_REQUIRED';used.append(dec['decision_id']);issues=[i for i in issues if i not in ('MINUTES_CONFLICT','SOURCE_CORRECTION')]
                    elif dec['action']=='exclude_match':
                        if idx:idx.update(eligibility_status='EXCLUDE',eligibility_reason='MANUAL_DECISION:'+dec['decision_id'])
                        state='EXCLUDED_MANUAL';used.append(dec['decision_id']);issues=[]
                for o in retained:
                    typ=context.team_type(o['team_id'])
                    if typ not in ('NATIONAL','NATIONAL_YOUTH') or o['rejected']:continue
                    ev=o['evidence'];support=context.teams.get(o['team_id'],[]);times=[ev.get('acquired_at')]+[c.get('artifact',{}).get('acquired_at') for c in support];verified_time=max((t for t in times if t),key=instant) if trustworthy(ev) and all(c.get('artifact') and trustworthy(c['artifact']) for c in support) else None
                    db.execute('INSERT OR IGNORE INTO national_affiliations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(key(pid,o['team_id'],ev['artifact_id'],ev['json_path']),pid,o['team_id'],o.get('team_name'),'YOUTH' if typ=='NATIONAL_YOUTH' else 'SENIOR','HISTORICAL_PARTICIPATION_OBSERVED_NOT_CURRENT_CALLUP',ev['path']+' '+ev['json_path'],verified_time,event_day(o['kickoff']),None,None,'SOURCE_ID_AND_TEAM_CONSISTENT',encoded(dict(evidence=ev,team_type_evidence=support,match_id=mid))))
                if idx:
                    for typ,eid,claims_for_type in [('TEAM_TYPE',idx['represented_team_id'],context.teams.get(idx['represented_team_id'],[])),('COMPETITION_TYPE',idx['competition_id'],context.competitions.get(idx['competition_id'],[])),('COMPETITION_METADATA',idx['competition_id'],context.catalogs.get(idx['competition_id'],[]))]:
                        for claim in claims_for_type:
                            if not claim.get('artifact'):continue
                            ev=claim['artifact'];artifact(db,ev);attrs={k:v for k,v in claim.items() if k not in ('artifact','json_path')};context_id=key(typ,eid,ev['artifact_id'],claim['json_path'])
                            db.execute('INSERT OR IGNORE INTO workload_context VALUES(?,?,?,?,?,?)',(context_id,typ,eid,encoded(attrs),ev['artifact_id'],claim['json_path']))
                    upsert(db,'player_match_index',idx,['player_id','match_id']);upsert(db,'player_match_appearances',app,['player_id','match_id'])
                paths=sorted({d['path'] for d in discovered});payload=dict(state=state,stage_sequence=steps,match_date=daymatch,competition_id=idx['competition_id'] if idx else candidate.get('competition_id'),competition_name=idx['competition_name'] if idx else candidate.get('competition_name'),represented_team_id=idx['represented_team_id'] if idx else None,minutes=app['minutes'] if app else None,played_status=app['played_status'] if app else 'UNKNOWN_APPEARANCE',eligibility_status=idx['eligibility_status'] if idx else 'REVIEW',paths=paths,candidate_values=claims,discovery_artifacts=[dict(artifact_id=d['artifact_id'],discovery_path=d['path'],source=dict(db.execute('SELECT * FROM workload_artifacts WHERE artifact_id=?',(d['artifact_id'],)).fetchone()),json_path=d['json_path'],first_seen=d['first_seen']) for d in discovered],source_correction_seen=correction,manual_decision_ids=used)
                if not old or old['state_json']!=encoded(payload):
                    emit(db,mode,pid,mid,day,old['state'] if old else None,state,payload)
                    db.execute('INSERT INTO dynamic_states VALUES(?,?,?,?,?,?,?) ON CONFLICT(mode,player_id,match_id) DO UPDATE SET state=excluded.state,state_json=excluded.state_json,last_changed=excluded.last_changed',(mode,pid,mid,state,encoded(payload),old['first_seen'] if old else day,day))
                # Temporary delay waits 48h after conservative end proxy. Conflicts/classification immediate.
                late=cutoff(day)-(__import__('futbol.common',fromlist=['instant']).instant(candidate['kickoff'])+timedelta(hours=3))>=timedelta(hours=48)
                if len(paths)<3 and late:issues.append('MATCH_MISSING_FROM_PATH')
                active=set()
                for issue in set(issues):
                    if issue in ['MINUTES_PENDING','APPEARANCE_UNRESOLVED'] and not late:continue
                    kind='COVERAGE_GAP' if issue=='MINUTES_PENDING' else issue
                    data=dict(path_A_result=summary['A'],path_B_result=summary['B'],path_C_result=summary['C'],source_artifacts=claims+payload['discovery_artifacts'],candidate_values=claims,recommended_action='查验对应原件或请求获准新增证据；不从替补位置或名单缺席推断0。',missing_paths=[p for p in 'ABC' if p not in paths],manual_decision_ids=used,omission_not_proven_when_path_partial=True)
                    active.add(review(db,mode,pid,mid,day,kind,data,idx or dict(candidate,match_date=daymatch)))
                for r in db.execute('SELECT review_id,issue_type,review_status FROM dynamic_reviews WHERE mode=? AND player_id=? AND match_id=?',(mode,pid,mid)):
                    if r['review_id'] not in active and r['review_status']=='OPEN':db.execute("UPDATE dynamic_reviews SET review_status='RESOLVED_BY_EVIDENCE' WHERE review_id=?",(r['review_id'],))
        return summary

ACTIONS={'accept_value','exclude_match','confirm_affiliation','resolve_identity','defer','request_evidence'}
def record_decision(path,decision):
    required=['review_id','action','scope','value','decided_at','actor','rationale']
    if any(k not in decision for k in required) or decision['action'] not in ACTIONS or not decision['actor'].strip() or not decision['rationale'].strip():raise ValueError('Complete scoped decision with actor/time/rationale required')
    from futbol.common import instant
    instant(decision['decided_at']);db=connect(path);r=db.execute('SELECT * FROM dynamic_reviews WHERE review_id=?',(decision['review_id'],)).fetchone()
    if not r:raise ValueError('Unknown review')
    s=decision['scope']
    if s.get('player_id')!=r['player_id'] or s.get('match_id')!=r['match_id']:raise ValueError('Decision must match review scope')
    claims=json.loads(r['payload_json']).get('candidate_values',[])
    if decision['action']=='accept_value':
        v=decision['value'];chosen=next((x for x in claims if x['discovery_id']==v.get('discovery_id') and x['minutes']==v.get('minutes') and not x['rejected']),None)
        if not chosen or chosen['minutes'] is None or not s.get('source_hashes') or {x['source']['sha256'] for x in claims}-set(s['source_hashes']):raise ValueError('Select an actual source value and explicitly scope source hashes')
    if decision['action']=='resolve_identity' and (int(decision['value'].get('player_id',0))<=0 or not decision['value'].get('evidence_ids')):raise ValueError('Positive verified identity needed')
    if decision['action']=='confirm_affiliation' and ((int(decision['value'].get('national_team_id') or 0)<=0 and decision['value'].get('affiliation_status')!='NO_REPRESENTATION_CONFIRMED') or not decision['value'].get('evidence_ids')):raise ValueError('Affiliation needs positive team ID and preserved evidence IDs')
    if decision['action'] in ('confirm_affiliation','resolve_identity'):
        known={r[0] for r in db.execute('SELECT artifact_id FROM workload_artifacts')}|{r[0] for r in db.execute('SELECT evidence_id FROM national_affiliations')}
        if set(decision['value']['evidence_ids'])-known:raise ValueError('Identity/affiliation decisions must reference preserved evidence IDs')
    if decision['action']=='confirm_affiliation':
        v=decision['value']
        if (not v.get('national_team_name') and v.get('affiliation_status')!='NO_REPRESENTATION_CONFIRMED') or v.get('team_level') not in ('SENIOR','YOUTH','UNKNOWN'):raise ValueError('Explicit national team name and level required')
        from datetime import date
        for k in ('valid_from','valid_to'):
            if v.get(k):date.fromisoformat(v[k])
        if v.get('valid_from') and v.get('valid_to') and v['valid_from']>v['valid_to']:raise ValueError('Invalid affiliation interval')
    did=key(decision)
    with db:
        db.execute('INSERT OR IGNORE INTO dynamic_decisions VALUES(?,?,?,?,?,?,?,?)',(did,r['review_id'],decision['action'],encoded(s),encoded(decision['value']),decision['decided_at'],decision['actor'],decision['rationale']))
        if decision['action']=='confirm_affiliation':
            v=decision['value'];db.execute('INSERT OR IGNORE INTO national_affiliations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(did,s['player_id'],v.get('national_team_id'),v.get('national_team_name'),v['team_level'],v.get('affiliation_status','HUMAN_CONFIRMED_SCOPE'),'MANUAL_DECISION:'+did,decision['decided_at'],None,v.get('valid_from'),v.get('valid_to'),'MANUAL_CONFIRMED',encoded(decision)))
        status={'defer':'DEFERRED','request_evidence':'EVIDENCE_REQUESTED','resolve_identity':'IDENTITY_DECISION_RECORDED','confirm_affiliation':'AFFILIATION_CONFIRMED'}.get(decision['action'],'DECIDED')
        db.execute('UPDATE dynamic_reviews SET review_status=? WHERE review_id=?',(status,r['review_id']))
    db.close();return did

def get_affiliations(player_ids,path=DEFAULT,as_of=None):
    path=within(path);result={}
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) if path.exists() else None
    if db:db.row_factory=sqlite3.Row
    for pid in player_ids:
        rows=[dict(r) for r in db.execute("SELECT * FROM national_affiliations WHERE player_id=? ORDER BY CASE WHEN verification_status='MANUAL_CONFIRMED' THEN 0 ELSE 1 END,observed_match_date DESC,evidence_timestamp DESC,evidence_id",(pid,))] if db else []
        if as_of:rows=[r for r in rows if r['evidence_timestamp'] and __import__('futbol.common',fromlist=['instant']).instant(r['evidence_timestamp'])<=cutoff(as_of) and (not r['observed_match_date'] or r['observed_match_date']<=as_of) and (not r['valid_from'] or r['valid_from']<=as_of) and (not r['valid_to'] or r['valid_to']>=as_of)]
        result[pid]=dict(status=('MANUAL_CONFIRMED' if rows[0]['verification_status']=='MANUAL_CONFIRMED' else 'SOURCE_OBSERVED') if rows else 'UNKNOWN_NOT_EVIDENCE_OF_NO_REPRESENTATION',best_supported=rows[0] if rows else None,historical_evidence=rows)
    if db:db.close()
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['decision','update']);p.add_argument('--db',default=str(DEFAULT));p.add_argument('--input');p.add_argument('--player-ids');p.add_argument('--as-of');p.add_argument('--start',default='2026-08-15');p.add_argument('--mode',choices=['TRUE_AS_OF','EVENT_DATE'],default='TRUE_AS_OF');a=p.parse_args()
    if a.command=='decision':
        if not a.input:p.error('decision needs --input')
        print(record_decision(within(a.db),read(within(a.input))));return
    if not a.player_ids or not a.as_of:p.error('update needs --player-ids and --as-of')
    ids=sorted(set(map(int,a.player_ids.split(','))))
    if not ids or min(ids)<=0 or a.start>a.as_of:raise ValueError('Positive IDs and bounded interval required')
    from .dynamic_sources import CacheSources
    bundle=CacheSources(ids);engine=Engine(within(a.db));ctx=bundle.snapshot_context(a.mode,a.as_of);result={}
    for pid in ids:
        paths=[]
        for route in 'ABC':
            try:paths.append(bundle.check(route,pid,a.mode,a.as_of,a.start))
            except Exception as exc:paths.append(dict(path=route,status='FAILED',candidates=[],checked_artifacts=[],limitations=[str(exc)],network_requests=0))
        mids={c['match_id'] for r in paths for c in r['candidates']}|{r[0] for r in engine.db.execute('SELECT match_id FROM dynamic_states WHERE mode=? AND player_id=?',(a.mode,pid))}
        obs=[o for mid in sorted(mids) for o in bundle.observations(pid,mid,a.mode,a.as_of)]
        result[pid]=engine.cycle(pid,a.as_of,a.mode,paths,obs,ctx)
    engine.db.close();print(encoded(result))
if __name__=='__main__':main()
