"""Versioned cause-specific revalidation; never clears identity conflicts wholesale."""
import json
from datetime import timedelta
from . import canonical_fixture as c
from .pending_matches import policy, clock, dt, iso
from .competition_identity import COMP


def revalidate(db, at=None):
    c.migrate(db);at=at or clock();out=[];p=policy()
    tables=[('match_retry_queue','match_key'),('international_acquisition_queue','task_key')]
    for table,keycol in tables:
        if not c.exists(db,table):continue
        for raw in db.execute("SELECT * FROM "+table+" WHERE state='QUARANTINED'").fetchall():
            r=dict(raw);task=r[keycol];error=r.get('last_error') or '';candidate=json.loads(r.get('candidate_json') or r.get('payload_json') or '{}')
            if table=='international_acquisition_queue':
                history=db.execute('SELECT result_json FROM international_acquisition_history WHERE task_key=? ORDER BY observed_at DESC,event_id DESC LIMIT 1',(task,)).fetchone()
                error=(json.loads(history[0]).get('error') or '') if history else ''
            # Correct diagnostic scope while retaining quarantine and every task.
            wrong_source=any(x in error for x in ('RETURNED_MATCH_ID_MISMATCH','LINEUP_MATCH_ID_MISMATCH'))
            fixture_conflict=any(x in error for x in ('PHYSICAL_MATCH_IDENTITY_CONFLICT','ORDERED_TEAM_ID_MISMATCH','MATCH_KICKOFF_MISMATCH','MATCH_COMPETITION_ID_MISMATCH'))
            if wrong_source or fixture_conflict:
                classification='SOURCE_MATCH_ID_MISMATCH' if wrong_source else 'FIXTURE_IDENTITY_CONFLICT'
                if r['failure_classification']!=classification:
                    evidence=dict(rule=c.VERSION,original_error=error,reason='DIAGNOSTIC_SCOPE_ONLY;NO_RESOLUTION_OR_RETRY')
                    event=c.digest([table,task,classification,'CLASSIFY_ONLY'])
                    db.execute('INSERT OR IGNORE INTO canonical_revalidation_events VALUES(?,?,?,?,?,?,?)',(event,table,task,r['failure_classification'],classification,'CLASSIFY_ONLY',c.encoded(evidence)))
                    db.execute('UPDATE '+table+' SET failure_classification=? WHERE '+keycol+'=?',(classification,task))
                    out.append(dict(queue=table,task_key=task,action='CLASSIFY_ONLY',classification=classification))
                continue
            reason=None
            if error.startswith('ParseError:UNSUPPORTED_COMPETITION') and candidate.get('catalog_competition') in COMP:
                reason='VERIFIED_COMPETITION_ALIAS_ADAPTER_AVAILABLE';classification='TEMPORARY_SOURCE_INCOMPLETE'
            elif r.get('entity_type')=='NATIONAL_TEAM_FIXTURES' and any(x in error for x in ('HTTP_429','HTTP_500','HTTP_502','HTTP_503','HTTP_504')):
                reason='TRANSIENT_TEAM_DIRECTORY_RESPONSE';classification='TEMPORARY_SOURCE_UNAVAILABLE'
            if not reason:continue
            if at-dt(r.get('match_completion_at') or r.get('completion_at') or r['first_observed_at'])>=timedelta(hours=p['escalate_after_hours']):
                action='REQUIRES_REVIEW_RETRY_AGE_EXHAUSTED'
                state='HUMAN_REVIEW';next_at=None
            else:action='PENDING_RETRY';state='PENDING';next_at=iso(at)
            evidence=dict(rule=c.VERSION,reason=reason,original_error=error,original_state=r['state'],first_observed_at=r['first_observed_at'],window_policy=p)
            event=c.digest([table,task,classification,action])
            if db.execute('SELECT 1 FROM canonical_revalidation_events WHERE event_id=?',(event,)).fetchone():continue
            db.execute('INSERT INTO canonical_revalidation_events VALUES(?,?,?,?,?,?,?)',(event,table,task,r['failure_classification'],classification,action,c.encoded(evidence)))
            db.execute('UPDATE '+table+' SET state=?,failure_classification=?,next_retry_at=? WHERE '+keycol+'=?',(state,classification,next_at,task))
            if table=='match_retry_queue':db.execute('UPDATE match_retry_queue SET escalation_status=? WHERE match_key=?',('NONE' if state=='PENDING' else 'MAX_RETRY_AGE_REACHED',task))
            out.append(dict(queue=table,task_key=task,action=action,reason=reason))
    return out
