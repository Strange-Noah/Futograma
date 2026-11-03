"""Offline, fail-closed policy; never dispatches or mutates production."""
from datetime import datetime,timezone,timedelta

def instant(s):
 d=datetime.fromisoformat(s.replace('Z','+00:00'))
 if d.tzinfo is None:raise ValueError('TIMEZONE_REQUIRED')
 return d.astimezone(timezone.utc)

def timeline(run, schedule_slots=((8,0),)):
 c=instant(run['created_at']);candidates=[c.replace(hour=h,minute=m,second=0,microsecond=0)-timedelta(days=day) for day in (0,1) for h,m in schedule_slots];e=max(t for t in candidates if t<=c)
 if run.get('event')!='schedule':e=None
 starts=[instant(j['started_at']) for j in run.get('jobs',[]) if j.get('started_at')];ends=[instant(j['completed_at']) for j in run.get('jobs',[]) if j.get('completed_at')]
 return dict(run_id=run['id'],expected_trigger=e.isoformat() if e else None,created_at=c.isoformat(),first_job_start=min(starts).isoformat() if starts else None,last_job_end=max(ends).isoformat() if ends else None,pre_creation_delay_seconds=(c-e).total_seconds() if e else None,created_to_first_job_seconds=(min(starts)-c).total_seconds() if starts else None,execution_seconds=(max(ends)-min(starts)).total_seconds() if starts and ends else None,attribution='PRE_CREATION_UNOBSERVABLE;EXPECTED_LATEST_SLOT_INFERRED;MULTISLOT_EVENT_ORIGIN_UNKNOWN')

def freshness(now,successful_publication,active=False,authority_matches=False):
 """Zero-addition publications count. Recheck under existing authority lock.
 Recommendation only; does not promise exactly-once dispatch.
 """
 now=instant(now)
 if successful_publication is None:return dict(state='UNKNOWN',action='INVESTIGATE_MISSING_RECEIPT')
 age=(now-instant(successful_publication)).total_seconds()
 if age<0:raise ValueError('FUTURE_PUBLICATION')
 state='BREACH' if age>108000 else 'WARNING' if age>93600 else 'FRESH'
 action='NONE' if state=='FRESH' else 'WAIT_ACTIVE_RUN' if active else 'VERIFY_VERSION_ALIGNMENT' if not authority_matches else 'CATCH_UP_ELIGIBLE_UNDER_EXISTING_LOCK'
 return dict(state=state,publication_age_seconds=age,action=action,warning_hours=26,slo_hours=30)
