"""Durable classified retry queue. Unknown competition is never assigned a guessed competition."""
import json,hashlib,sqlite3,re
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from .core import ROOT
TEMP={'TEMPORARY_SOURCE_INCOMPLETE','TEMPORARY_SOURCE_UNAVAILABLE'}
CLASSES=TEMP|{'UNSUPPORTED_OR_UNKNOWN_COMPETITION','ROSTER_IDENTITY_CONFLICT','PARSER_OR_SCHEMA_REGRESSION','DATA_INTEGRITY_FAILURE'}
DEFAULT={'initial_hours':24,'alternative_after_hours':72,'escalate_after_hours':168,'daily_hour':16,'timezone':'Asia/Shanghai','max_recent_days':7}
DDL="""CREATE TABLE IF NOT EXISTS match_retry_queue(match_key TEXT PRIMARY KEY,club_id INTEGER,provider TEXT,provider_match_id TEXT,canonical_match_id TEXT,url TEXT,match_date TEXT,first_observed_at TEXT,match_completion_at TEXT,last_attempt_at TEXT,attempt_count INTEGER DEFAULT 0,failure_classification TEXT,evidence_json TEXT,next_retry_at TEXT,escalation_status TEXT,state TEXT,last_error TEXT,candidate_json TEXT,resolved_at TEXT);
CREATE TABLE IF NOT EXISTS match_retry_history(event_id TEXT PRIMARY KEY,match_key TEXT,observed_at TEXT,attempt_token TEXT,result_json TEXT);
CREATE INDEX IF NOT EXISTS match_retry_due ON match_retry_queue(state,next_retry_at);"""
def policy():
 p=ROOT/'ops/cloud/retry-policy.json';return DEFAULT| (json.loads(p.read_text()) if p.is_file() else {})
def migrate(db):
 db.executescript(DDL)
 if not db.execute("SELECT 1 FROM sqlite_master WHERE name='live_tasks'").fetchone():return
 for old in db.execute("SELECT * FROM live_tasks WHERE status NOT IN ('NEW_MATCH','UPDATED_MATCH','UNCHANGED')").fetchall():
  old=dict(old);c=json.loads(old['payload_json'] or '{}')
  if not all(k in c for k in ['club_id','provider','url','date']) or old.get('last_error') in ('NOT_CONFIRMED_FULL_TIME','SKY_NOT_FULL_TIME','FUTURE_KICKOFF'):continue
  if db.execute('SELECT 1 FROM match_retry_queue WHERE match_key=?',(key(c),)).fetchone():continue
  c['legacy_first_observation_precision']='EARLIEST_RETAINED_RUN_OR_MIGRATION_NOT_ORIGINAL_FIRST_FAILURE';run=db.execute('SELECT started_at FROM live_runs WHERE run_id=?',(old.get('last_run'),)).fetchone();at=dt(run[0]) if run and run[0] else clock();observe(db,c,dict(status=old['status'],error=old.get('last_error')),'legacy-migration:'+str(old.get('last_run')),at);db.execute('UPDATE match_retry_queue SET attempt_count=MAX(attempt_count,?) WHERE match_key=?',(old.get('attempts') or 0,key(c)))
 db.commit()
def iso(d):return d.astimezone(timezone.utc).isoformat()
def dt(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def clock():return datetime.now(timezone.utc)
def key(c):
 if c.get('retry_queue_key'):return c['retry_queue_key']
 stable=c.get('catalog_provider_id') or c.get('provider_match_id') or c['url'].rstrip('/').rsplit('/',1)[-1]
 return hashlib.sha256(json.dumps([c['club_id'],c['provider'],str(stable)]).encode()).hexdigest()
def next_slot(at,p):
 local=at.astimezone(ZoneInfo(p['timezone']));slot=local.replace(hour=p['daily_hour'],minute=0,second=0,microsecond=0)
 if slot<=local:slot+=timedelta(days=1)
 return iso(slot)
def classify(result,candidate,at=None):
 at=at or clock();p=policy();error=result.get('error') or '';context=result.get('source_context') or {};status=result['status'];recent=False
 try:recent=0<=(at.date()-datetime.fromisoformat(candidate['date']).date()).days<=p['max_recent_days']
 except (ValueError,KeyError):pass
 if error.startswith('Conflict:') or any(x in error for x in ['IntegrityError','DatabaseError','FOREIGN KEY','INTEGRITY','FACT_OVERWRITE','CONTRADICT','CORROBORATED_CORRECTION','NEGATIVE_PLAYING','MINUTES_OUT_OF_RANGE']):return 'DATA_INTEGRITY_FAILURE',False
 if context.get('identity_conflict') or any(x in error for x in ['IDENTITY','ROSTER_SIDE_MISMATCH','ROSTER_HEADER_TEAM_MISMATCH','PLAYER_ON_BOTH','DUPLICATE_PLAYER','AMBIGUOUS','REQUESTED_CLUB_NOT_ON']):return 'ROSTER_IDENTITY_CONFLICT',False
 if error.startswith(('ParseError:UNSUPPORTED_COMPETITION','UNSUPPORTED_COMPETITION')):
  if error.endswith(':None') and recent and context.get('confirmed_full_time') and context.get('catalog_competition_known') and context.get('fixture_identity_agrees') and context.get('competition_field_empty'):return 'TEMPORARY_SOURCE_INCOMPLETE',True
  return 'UNSUPPORTED_OR_UNKNOWN_COMPETITION',False
 if status=='SOURCE_UNAVAILABLE':
  transient=any(x in error for x in ['HTTP_429','HTTP_500','HTTP_502','HTTP_503','HTTP_504','Timeout','timed out','RemoteDisconnected','ConnectionReset','NETWORK_BUDGET_EXHAUSTED'])
  return 'TEMPORARY_SOURCE_UNAVAILABLE',transient
 if error=='DAILY_DETAIL_BUDGET_DEFERRED':return 'TEMPORARY_SOURCE_UNAVAILABLE',True
 if status=='PENDING' and recent and context.get('confirmed_full_time') and context.get('fixture_identity_agrees') and any(x in error for x in ['XI_OR_FULL_BENCH_UNAVAILABLE','SKY_TWO_LINEUP_COLUMNS_REQUIRED','SOURCE_MINUTES_INCOMPLETE']):return 'TEMPORARY_SOURCE_INCOMPLETE',True
 return 'PARSER_OR_SCHEMA_REGRESSION',False

def observe(db,candidate,result,attempt_token,at=None):
 at=at or clock();p=policy();k=key(candidate);old=db.execute('SELECT * FROM match_retry_queue WHERE match_key=?',(k,)).fetchone();success=result['status'] in ('NEW_MATCH','UPDATED_MATCH','UNCHANGED')
 # A fixture skipped merely because it is old is not a successful source retry.
 if result.get('reason')=='BEFORE_EXACT_72H_OR_CATCHUP_BOUND;NO_FACT_WRITE':return
 if result.get('dry_run') and result['status']=='VALIDATED_DRY_RUN':return
 if success and not old:return
 evidence=dict(retry_phase='FIRST_24_HOURS' if not old or at-dt(dict(old).get('match_completion_at') or dict(old)['first_observed_at'])<timedelta(hours=p['initial_hours']) else 'DAILY_24_TO_72' if at-dt(dict(old).get('match_completion_at') or dict(old)['first_observed_at'])<timedelta(hours=p['alternative_after_hours']) else 'DAILY_WITH_VERIFIED_ALTERNATIVES',source=result.get('source'),context=result.get('source_context'),error=result.get('error'));event=hashlib.sha256(json.dumps([k,attempt_token,result],sort_keys=True,default=str).encode()).hexdigest()
 if db.execute('SELECT 1 FROM match_retry_history WHERE event_id=?',(event,)).fetchone():return
 db.execute('INSERT INTO match_retry_history VALUES(?,?,?,?,?)',(event,k,iso(at),str(attempt_token),json.dumps(result,ensure_ascii=False,sort_keys=True)))
 if success and old['state'] in ('QUARANTINED','HUMAN_REVIEW') and not result.get('human_review_authorized'):return
 if success:
  db.execute("UPDATE match_retry_queue SET state='RESOLVED',resolved_at=?,last_attempt_at=?,attempt_count=attempt_count+1,canonical_match_id=COALESCE(?,canonical_match_id),next_retry_at=NULL,escalation_status='RESOLVED' WHERE match_key=?",(iso(at),iso(at),result.get('match_id'),k));return
 classification,retryable=classify(result,candidate,at);old=dict(old) if old else {};first=old.get('first_observed_at') or iso(at);completion=old.get('match_completion_at') or candidate.get('match_completion_at');age=at-dt(completion or first);escalated=age>=timedelta(hours=p['escalate_after_hours']);state='HUMAN_REVIEW' if escalated else 'PENDING' if retryable else 'QUARANTINED';escalation='MAX_RETRY_AGE_REACHED' if escalated else 'REQUIRES_REVIEW' if not retryable else 'NONE';attempted=result.get('error')!='DAILY_DETAIL_BUDGET_DEFERRED';count=old.get('attempt_count',0)+int(attempted)
 values=(k,candidate['club_id'],candidate['provider'],str(candidate.get('catalog_provider_id') or candidate.get('provider_match_id') or candidate['url'].rstrip('/').rsplit('/',1)[-1]),result.get('match_id') or old.get('canonical_match_id') or candidate.get('match_id'),candidate['url'],candidate['date'],first,completion,iso(at) if attempted else old.get('last_attempt_at'),count,classification,json.dumps(evidence,ensure_ascii=False),next_slot(at,p) if state=='PENDING' else None,escalation,state,result.get('error'),json.dumps(candidate,ensure_ascii=False),None)
 db.execute('INSERT INTO match_retry_queue VALUES('+','.join('?' for _ in values)+') ON CONFLICT(match_key) DO UPDATE SET canonical_match_id=COALESCE(excluded.canonical_match_id,canonical_match_id),last_attempt_at=excluded.last_attempt_at,attempt_count=excluded.attempt_count,match_completion_at=COALESCE(match_completion_at,excluded.match_completion_at),failure_classification=excluded.failure_classification,evidence_json=excluded.evidence_json,next_retry_at=excluded.next_retry_at,escalation_status=excluded.escalation_status,state=excluded.state,last_error=excluded.last_error,candidate_json=excluded.candidate_json,resolved_at=NULL',values)

def should_attempt(db,candidate,at=None):
 r=db.execute('SELECT state,next_retry_at FROM match_retry_queue WHERE match_key=?',(key(candidate),)).fetchone()
 return not r or (r['state']=='PENDING' and r['next_retry_at'] and dt(r['next_retry_at'])<=(at or clock()))
def due_candidates(db,at=None):
 at=at or clock();p=policy();out=[]
 for row in db.execute("SELECT * FROM match_retry_queue WHERE state='PENDING' ORDER BY next_retry_at,first_observed_at").fetchall():
  r=dict(row);age=at-dt(r['match_completion_at'] or r['first_observed_at'])
  if age>=timedelta(hours=p['escalate_after_hours']):
   db.execute("UPDATE match_retry_queue SET state='HUMAN_REVIEW',escalation_status='MAX_RETRY_AGE_REACHED',next_retry_at=NULL WHERE match_key=?",(r['match_key'],));continue
  if dt(r['next_retry_at'])>at:continue
  c=json.loads(r['candidate_json']);c.update(retry_queue_key=r['match_key'],durable_pending_retry=True,window_start_utc=None,alternative_sources_allowed=age>=timedelta(hours=p['alternative_after_hours']));out.append(c)
  # Only previously corroborated alternative URLs; every response still passes full parser/team validation.
  if c['alternative_sources_allowed']:
   for alt in c.get('verified_alternative_sources',[]):
    if alt.get('provider') in ('Sky','Torlytics') and alt.get('verified_same_fixture'):
     out.append(c|{'url':alt['url'],'provider':alt['provider'],'alternative_source_for':r['match_key']})
 return out

def sky_context(raw,candidate):
 from .live_parse import Tree,COMP,canon,uk_datetime
 try:
  root=Tree(raw).root;h=root.one('sdc-site-match-header');names=h.all('sdc-site-match-header__team-name-block-target');label=h.one('sdc-site-match-header__detail-fixture').txt();known=next((x for x in COMP if re.search(r'\.\s*'+re.escape(x)+r'\b',label)),None);kick=uk_datetime(h.one('sdc-site-match-header__detail-time').txt());same=len(names)==2 and [canon(x.txt()) for x in names]==[candidate.get('catalog_home'),candidate.get('catalog_away')] and dt(kick).astimezone(ZoneInfo('Europe/Madrid')).date().isoformat()==candidate['date']
  return dict(confirmed_full_time=h.attrs.get('data-status')=='4',fixture_identity_agrees=same,catalog_competition_known=candidate.get('catalog_competition') in COMP,competition_field_empty=known is None and len(names)==2 and not re.sub(r'(?i)\b(?:vs?|versus)\b|[. ;:–—-]','',label.replace(names[0].txt(),'').replace(names[1].txt(),'' )).strip(),identity_conflict=bool(root.all('sdc-site-team-lineup__col')) and ([canon(col.one('sdc-site-team-lineup__team-name').txt()) for col in root.all('sdc-site-team-lineup__col')]!=[canon(n.txt()) for n in names]),detail_label=label,catalog_competition=candidate.get('catalog_competition'),kickoff=kick)
 except Exception:return dict(confirmed_full_time=False,fixture_identity_agrees=False,competition_field_empty=False)
