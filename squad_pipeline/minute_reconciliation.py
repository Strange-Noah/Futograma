"""Audited policy application to typed facts; no edits to original reference payloads."""
import json
from datetime import datetime,timezone
from .minute_policy import select,audit,VERSION
from .international_store import encoded,digest

def apply_existing(db):
    changes=[]
    for r in db.execute('SELECT * FROM international_appearances').fetchall():
        if any(marker in r['evidence_scope'] for marker in ('APPEARANCE_STATUS_CONTRADICTION','PARTICIPATION_CONFLICT','ZERO_MINUTE_PARTICIPATION_UNVERIFIED')):continue
        old=dict(r);observations=[]
        if r['minutes'] is not None:observations.append(dict(status=r['participation_status'],minutes=r['minutes'],provider='RETAINED_ACCEPTED'))
        for field,provider in [('reported_minutes','FotMob'),('derived_minutes','DERIVED_CLOCK')]:
            if r[field] is not None:observations.append(dict(status=r['participation_status'],minutes=r[field],provider=provider))
        result=select(observations)
        if not result['accepted']:continue
        audit(db,'NATIONAL',r['match_id'],r['player_id'],result)
        if r['minutes'] is not None:continue
        new=dict(old,minutes=result['minutes'],minutes_precision='SOURCE_PRIORITY_WITHIN_FIVE_MINUTES',verification_status='POLICY_ACCEPTED_VALID_NUMERICAL_MINUTES',evidence_scope=VERSION,revision=r['revision']+1)
        db.execute('INSERT OR IGNORE INTO international_fact_revisions VALUES(?,?,?,?,?,?,?)',(digest([old,new]),r['match_id'],r['player_id'],encoded(old),encoded(new),encoded(result),datetime.now(timezone.utc).isoformat()))
        db.execute('UPDATE international_appearances SET minutes=?,minutes_precision=?,verification_status=?,evidence_scope=?,revision=? WHERE match_id=? AND player_id=?',(new['minutes'],new['minutes_precision'],new['verification_status'],new['evidence_scope'],new['revision'],r['match_id'],r['player_id']))
        # Only the minute discrepancy task is resolved; never clear an identity/source contradiction.
        for task in db.execute("SELECT * FROM international_acquisition_queue WHERE entity_type='PLAYER_MINUTES' AND match_id=? AND player_id=? AND failure_classification='DATA_INTEGRITY_FAILURE'",(r['match_id'],r['player_id'])).fetchall():
            from .international_retry import observe
            payload=json.loads(task['payload_json']);observe(db,payload,dict(status='UNCHANGED',match_id=r['match_id'],policy_revalidation=VERSION),'minute-policy:'+digest([r['match_id'],r['player_id'],result]))
        changes.append(dict(match_id=r['match_id'],player_id=r['player_id'],minutes=new['minutes'],decision=result))
    return changes
