"""Role-neutral international retry; shares club policy/classification, never a club_id key."""
import json,hashlib
from datetime import timedelta
from .pending_matches import classify,policy,next_slot,dt,clock,iso
from .international_store import encoded

def key(task):return hashlib.sha256(encoded([task['entity_type'],task['provider'],str(task.get('provider_entity_id') or task['url'])]).encode()).hexdigest()
def observe(db,task,result,token,at=None):
    at=at or clock();k=key(task);old=db.execute('SELECT * FROM international_acquisition_queue WHERE task_key=?',(k,)).fetchone();old=dict(old) if old else {};success=result['status'] in ('NEW_MATCH','UPDATED_MATCH','UNCHANGED','DISCOVERED')
    event=hashlib.sha256(encoded([k,token,result]).encode()).hexdigest()
    if db.execute('SELECT 1 FROM international_acquisition_history WHERE event_id=?',(event,)).fetchone():return
    first=old.get('first_observed_at') or iso(at);completion=old.get('completion_at') or task.get('completion_at');age=at-dt(completion or first);p=policy()
    if success:classification=None;state='RESOLVED';slot=None
    else:
        classification,retryable=classify(result,dict(date=task.get('date','')),at)
        if (result.get('error') or '')=='SOURCE_MINUTES_INCOMPLETE':classification,retryable='TEMPORARY_SOURCE_INCOMPLETE',True
        if any(x in (result.get('error') or '') for x in ('RETURNED_MATCH_ID_MISMATCH','ORDERED_TEAM_ID_MISMATCH','MATCH_KICKOFF_MISMATCH','MATCH_COMPETITION_ID_MISMATCH')):classification,retryable='ROSTER_IDENTITY_CONFLICT',False
        state='HUMAN_REVIEW' if age>=timedelta(hours=p['escalate_after_hours']) else 'PENDING' if retryable else 'QUARANTINED';slot=next_slot(at,p) if state=='PENDING' else None
    policy_clear=success and task['entity_type']=='PLAYER_MINUTES' and old.get('failure_classification')=='DATA_INTEGRITY_FAILURE' and result.get('policy_revalidation')=='FIVE_MINUTE_SOURCE_PRIORITY_V1'
    if old.get('state') in ('QUARANTINED','HUMAN_REVIEW') and not result.get('human_review_authorized') and not policy_clear:state=old['state'];classification=old['failure_classification'];slot=None
    row=(k,task['entity_type'],task['provider'],str(task.get('provider_entity_id') or ''),task['url'],task.get('player_id'),task.get('team_key'),result.get('match_id') or old.get('match_id'),first,completion,iso(at),old.get('attempt_count',0)+1,slot,classification,state,encoded(task))
    db.execute('INSERT INTO international_acquisition_queue VALUES('+','.join('?' for _ in row)+') ON CONFLICT(task_key) DO UPDATE SET match_id=COALESCE(excluded.match_id,match_id),last_attempt_at=excluded.last_attempt_at,attempt_count=excluded.attempt_count,next_retry_at=excluded.next_retry_at,failure_classification=excluded.failure_classification,state=excluded.state,payload_json=excluded.payload_json',row)
    db.execute('INSERT INTO international_acquisition_history VALUES(?,?,?,?,?)',(event,k,str(token),iso(at),encoded(result)))

def due(db,at=None):
    at=at or clock();p=policy();items=[]
    for row in db.execute("SELECT * FROM international_acquisition_queue WHERE state='PENDING' AND next_retry_at<=?",(iso(at),)).fetchall():
        age=at-dt(row['completion_at'] or row['first_observed_at'])
        if age>=timedelta(hours=p['escalate_after_hours']):db.execute("UPDATE international_acquisition_queue SET state='HUMAN_REVIEW',next_retry_at=NULL WHERE task_key=?",(row['task_key'],));continue
        task=json.loads(row['payload_json']);task.update(durable_pending_retry=True,alternative_sources_allowed=age>=timedelta(hours=p['alternative_after_hours']),window_start_utc=None);items.append(task)
    return items
