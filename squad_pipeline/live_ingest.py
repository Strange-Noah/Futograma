"""Live six-club public HTML ingestion. Invoke from the fixed launchd wrapper."""
import argparse,json,sqlite3,fcntl,os,traceback,time,re,copy
from pathlib import Path
from datetime import datetime,date,timedelta,timezone
from zoneinfo import ZoneInfo
from collections import Counter,defaultdict
from . import pending_matches as pending
from .core import ROOT,BASE,now,write,read,digest
from .live_sources import Client,CLUB_FEEDS,OUT
from .live_parse import catalogs,torly_detail,sky_detail,ParseError,Pending,canon,norm
from .six_club_validation import CLUBS
from .six_club_experiment import addplayer,ev,match,fact,incremental_row,rolling,encoded
DB=BASE/'state/live.sqlite'
DDL='''
CREATE TABLE IF NOT EXISTS live_runs(run_id TEXT PRIMARY KEY,started_at TEXT,finished_at TEXT,origin TEXT,status TEXT,payload_json TEXT);
CREATE TABLE IF NOT EXISTS live_tasks(url TEXT PRIMARY KEY,club_id INTEGER,date TEXT,provider TEXT,status TEXT,attempts INTEGER DEFAULT 0,next_retry TEXT,last_error TEXT,last_run TEXT,payload_json TEXT);
CREATE TABLE IF NOT EXISTS live_documents(url TEXT PRIMARY KEY,club_id INTEGER,match_id TEXT,provider TEXT,body_sha256 TEXT,checked_at TEXT,source_json TEXT);
CREATE TABLE IF NOT EXISTS live_pending_sheets(club_id INTEGER,match_id TEXT,provider TEXT,version_sha TEXT,payload_json TEXT,staged_at TEXT,PRIMARY KEY(club_id,match_id,provider,version_sha));
CREATE TABLE IF NOT EXISTS live_sheets(club_id INTEGER,match_id TEXT,provider TEXT,version_sha TEXT,payload_json TEXT,verified_at TEXT,PRIMARY KEY(club_id,match_id,provider,version_sha));
CREATE TABLE IF NOT EXISTS live_scan_windows(club_id INTEGER,provider TEXT,completed_at TEXT,PRIMARY KEY(club_id,provider));
CREATE TABLE IF NOT EXISTS live_checkpoints(club_id INTEGER,scope TEXT,checked_through TEXT,status TEXT,run_id TEXT,PRIMARY KEY(club_id,scope));
CREATE TABLE IF NOT EXISTS live_conflicts(conflict_id TEXT PRIMARY KEY,run_id TEXT,club_id INTEGER,match_id TEXT,kind TEXT,payload_json TEXT,resolved INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS live_catalogs(run_id TEXT,club_id INTEGER,provider TEXT,status TEXT,payload_json TEXT,PRIMARY KEY(run_id,club_id,provider));
CREATE TABLE IF NOT EXISTS live_rollups(club_id INTEGER,player_id INTEGER,as_of TEXT,window_days INTEGER,minutes REAL,unknown_rows INTEGER,scope TEXT DEFAULT 'CLUB_ONLY',PRIMARY KEY(club_id,player_id,as_of,window_days));
'''
def connect(path=None):
 if path is None:path=Path(os.environ.get('FUTBOL_DATABASE_PATH',str(DB)))
 c=sqlite3.connect(path,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.executescript(DDL);pending.migrate(c);c.commit();return c
class IdentityError(ParseError):pass
class Conflict(ParseError):pass

def resolve_player(db,tid,p):
 key=p['provider_key'];alias=db.execute('SELECT player_id FROM validation_player_aliases WHERE provider_key=?',(key,)).fetchone()
 if alias:return alias[0]
 # Exact identity names from retained provider/official aliases, restricted to this club's prior observations.
 target=norm(p['name']);candidates=set()
 for r in db.execute('SELECT DISTINCT p.player_id,p.display_name FROM players p JOIN validation_roster_membership v USING(player_id) WHERE v.club_id=?',(tid,)):
  names=[r['display_name']]
  for a in db.execute('SELECT identity_json FROM validation_player_aliases WHERE player_id=?',(r['player_id'],)):
   try:names+=json.loads(a[0]).get('names',[])
   except (ValueError,TypeError):pass
  if any(norm(n)==target for n in names):candidates.add(r['player_id'])
  for legal in db.execute("SELECT identity_json FROM validation_player_aliases WHERE player_id=? AND provider_key LIKE 'pl-registered:%'",(r['player_id'],)):
   import unicodedata
   tok=lambda t:set(re.findall('[a-z]+',unicodedata.normalize('NFKD',t).encode('ascii','ignore').decode().lower().replace("'",'')))
   ld=json.loads(legal[0]);pt=tok(p['name']);legal_names=ld.get('names',[])[:1]
   if len(pt)>=2 and any(pt<=tok(n) for n in legal_names):candidates.add(r['player_id'])
 if len(candidates)>1:raise IdentityError('AMBIGUOUS_EXACT_CLUB_NAME:'+p['name'])
 if candidates:pid=next(iter(candidates))
 else:
  if not key.startswith('https://torlytics.de/spieler/'):raise IdentityError('NEW_PLAYER_WITHOUT_STABLE_PROVIDER_ID:'+p['name'])
  pid=-int(digest(key.encode())[:15],16)
  if db.execute('SELECT 1 FROM players WHERE player_id=?',(pid,)).fetchone():raise IdentityError('LOCAL_ID_COLLISION')
  addplayer(db,dict(player_id=pid,display_name=p['name'],identity_status='LIVE_PUBLIC_PROVIDER_IDENTITY;INDEPENDENT_REVIEW_PENDING',provider_keys=[key],source_names=[p['name']],fotmob_id=None))
 db.execute('INSERT OR IGNORE INTO validation_player_aliases VALUES(?,?,?)',(key,pid,encoded(dict(names=[p['name']],method='EXACT_KNOWN_FULL_NAME_WITH_CLUB_CONTEXT' if candidates else 'STABLE_SOURCE_URL_NO_FOTMOB_ID_INVENTED'))));return pid

def bind_fixture(db,f,url):
 alias=db.execute('SELECT match_id FROM validation_match_aliases WHERE alias=?',('url:'+url,)).fetchone()
 if alias:
  old=db.execute('SELECT * FROM matches WHERE match_id=?',(alias[0],)).fetchone()
  if old:
   meta=json.loads(old['metadata_json'])
   if (canon(meta['home_team']),canon(meta['away_team']),str(old['competition_id']))!=(f['home_team'],f['away_team'],str(f['competition_id'])):raise Conflict('PROVIDER_BINDING_TEAM_OR_COMPETITION_CHANGED')
   f['match_id']=old['match_id'];return f
 exact=db.execute('SELECT * FROM matches WHERE match_id=?',(f['match_id'],)).fetchone()
 if exact:return f
 # Prevent a shifted kickoff/opponent spelling from becoming a duplicate physical match.
 nearby=[]
 for old in db.execute('SELECT * FROM matches WHERE competition_id=? AND ABS(julianday(local_date)-julianday(?))<=2',(f['competition_id'],f['match_date'])):
  meta=json.loads(old['metadata_json'])
  if (canon(meta['home_team']),canon(meta['away_team']))==(f['home_team'],f['away_team']):nearby.append(old)
 if nearby:
  if len(nearby)==1 and nearby[0]['match_state']!='COMPLETED':f['match_id']=nearby[0]['match_id'];return f
  raise Conflict('KICKOFF_DIFFERS_EXISTING_MATCH;RESCHEDULE_OR_SOURCE_CONFLICT')
 return f

def semantic(row):return (row['participation_status'],row['minutes']) if row else None
def correction_rollups(db,tid,pid,changed_day,asof):
 dates={r[0] for r in db.execute('SELECT DISTINCT as_of FROM live_rollups WHERE club_id=? AND player_id=? AND as_of>=? AND as_of<=date(?,\'+27 days\')',(tid,pid,changed_day,changed_day))};dates.add(asof)
 for d in dates:
  for w in [7,14,28]:
   r=rolling(db,tid,pid,d,w);db.execute('INSERT OR REPLACE INTO live_rollups(club_id,player_id,as_of,window_days,minutes,unknown_rows) VALUES(?,?,?,?,?,?)',tuple(r.values()))
 # Historical verification rollups in the live copy must also receive applicable corrections.
 for old in list(db.execute('SELECT as_of,window_days FROM validation_rolling WHERE club_id=? AND player_id=? AND as_of>=? AND as_of<=date(?,\'+27 days\')',(tid,pid,changed_day,changed_day))):
  r=rolling(db,tid,pid,old[0],old[1]);db.execute('UPDATE validation_rolling SET minutes=?,unknown_rows=? WHERE club_id=? AND player_id=? AND as_of=? AND window_days=?',(r['minutes'],r['unknown_rows'],tid,pid,old[0],old[1]))

def stage_sheet(db,f,s):
 # Evidence staging is durable even when a contradictory fact transaction is rolled back.
 bound=bind_fixture(db,copy.deepcopy(f),s['source']['url']);candidate=copy.deepcopy(s);candidate['match_id']=bound['match_id'];candidate['acceptance_status']='STRUCTURE_VALIDATED;FACTS_NOT_ACCEPTED';db.execute('INSERT OR IGNORE INTO live_pending_sheets VALUES(?,?,?,?,?,?)',(s['club_id'],bound['match_id'],s['provider'],s['source']['sha256'],encoded(candidate),now()));db.commit()

def apply_sheet(db,f,s,runid,asof,dry=False):
 tid=s['club_id'];f=bind_fixture(db,copy.deepcopy(f),s['source']['url']);mid=f['match_id'];s=copy.deepcopy(s);s['match_id']=mid
 for p in s['players']:p['player_id']=resolve_player(db,tid,p)
 if len({p['player_id'] for p in s['players']})!=len(s['players']):raise IdentityError('POST_MAPPING_DUPLICATE_ID')
 # Store latest provider snapshots separately, preserving every source version.
 prior=[json.loads(x[0]) for x in db.execute('SELECT payload_json FROM validation_match_sheets WHERE club_id=? AND match_id=?',(tid,mid)) if json.loads(x[0])['provider']!=s['provider']]
 other={}
 for x in db.execute('SELECT provider,payload_json FROM live_sheets WHERE club_id=? AND match_id=? ORDER BY verified_at',(tid,mid)):
  if x[0]!=s['provider']:other[x[0]]=json.loads(x[1])
 for staged in db.execute('SELECT provider,payload_json FROM live_pending_sheets WHERE club_id=? AND match_id=? ORDER BY staged_at',(tid,mid)):
  if staged[0]==s['provider']:continue
  candidate=json.loads(staged[1])
  for p in candidate['players']:p['player_id']=resolve_player(db,tid,p)
  if staged[0] not in other or candidate['source']['acquired_at']>=other[staged[0]]['source']['acquired_at']:other[staged[0]]=candidate
 prior=[x for x in prior if x['provider'] not in other];combined=prior+list(other.values())+[s]
 new=db.execute('SELECT 1 FROM matches WHERE match_id=?',(mid,)).fetchone() is None
 rows=[];pids={p['player_id'] for p in s['players']}
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='player_eligibility'").fetchone():
  pids|={x[0] for x in db.execute("SELECT player_id FROM player_eligibility WHERE club_id=? AND regular_member=1 AND verification_status='VERIFIED' AND (start_date IS NULL OR start_date<=?) AND (end_date IS NULL OR end_date>=?)",(tid,f['match_date'],f['match_date']))}
 # A complete new squad omitting a previously verified participant is a conflict, not silent erasure.
 pids|={x[0] for x in db.execute('SELECT player_id FROM player_match_minutes WHERE club_id=? AND match_id=?',(tid,mid))}
 for pid in pids:
  row=incremental_row(tid,pid,f,combined);old=db.execute('SELECT * FROM player_match_minutes WHERE club_id=? AND match_id=? AND player_id=?',(tid,mid,pid)).fetchone()
  fresh=[s]+[v for v in other.values() if v['source']['acquired_at'][:10]>=asof]
  votes=[incremental_row(tid,pid,f,[v]) for v in fresh]
  if len({v['provider'] for v in fresh})>=2 and votes and len({(v['status'],v['minutes']) for v in votes})==1 and votes[0]['status']!='UNKNOWN' and votes[0]['minutes'] is not None:
   row=incremental_row(tid,pid,f,fresh);row['observations'].append(dict(superseded_older_source_versions=[v['source'] for v in prior],reason='TWO_CURRENT_PROVIDERS_AGREE;PRIOR_SOURCES_RETAINED'))
  if row['status']=='UNKNOWN' or row['minutes'] is None or row['issues']:raise Conflict('UNRESOLVED_PARTICIPATION_OR_MINUTES:'+str(pid))
  if old and semantic(old)!=(row['status'],row['minutes']):
   # Corrections need two current public providers agreeing exactly; otherwise freeze accepted values.
   fresh=[s]+[v for v in other.values() if v['source']['acquired_at'][:10]>=asof]
   votes=[incremental_row(tid,pid,f,[v]) for v in fresh]
   if len({v['provider'] for v in fresh})<2 or any((v['status'],v['minutes'])!=(row['status'],row['minutes']) for v in votes):raise Conflict('ACCEPTED_FACT_CHANGE_REQUIRES_CURRENT_CORROBORATION:'+str(pid))
  rows.append((row,old))
 if dry:return dict(status='UNCHANGED' if not new else 'VALIDATED_DRY_RUN',match_id=mid,club_id=tid,players=len(s['players']),dry_run=True)
 existing=db.execute('SELECT * FROM matches WHERE match_id=?',(mid,)).fetchone()
 if existing and existing['match_state']=='COMPLETED' and existing['result'] and existing['result']!=f['score']:raise Conflict('ACCEPTED_RESULT_DIFFERS_NEW_SOURCE')
 isnew=match(db,f) if not existing or existing['match_state']!='COMPLETED' else False;changed=0;inserted=0
 if isnew or existing and existing['match_state']!='COMPLETED':
  d=date.fromisoformat(f['match_date']);sy=d.year if d.month>=7 else d.year-1;half='H1' if d.month>=7 else 'H2';period=str(sy)+'-'+str(sy+1)[2:]+'_'+half;begin=f'{sy}-07-01' if half=='H1' else f'{sy+1}-01-01';end=f'{sy+1}-01-01' if half=='H1' else f'{sy+1}-07-01';db.execute('INSERT OR IGNORE INTO periods VALUES(?,?,?,?,?,?)',(period,str(sy)+'/'+str(sy+1)[2:],half,begin,end,'Europe/Madrid'));db.execute('UPDATE matches SET period_id=? WHERE match_id=?',(period,mid))
 # Recording a same-value new observation must not churn accepted fact evidence/revisions.
 for row,old in rows:
  if old and semantic(old)==(row['status'],row['minutes']):continue
  result=fact(db,row,asof,'LIVE_VALIDATED_CORROBORATED_CORRECTION' if old else 'LIVE_NEW_COMPLETE_MATCH');changed+=result=='UPDATED';inserted+=result=='INSERTED';correction_rollups(db,tid,row['player_id'],f['match_date'],asof)
 aid=ev(db,s['source'])
 for p in s['players']:
  if db.execute("SELECT 1 FROM sqlite_master WHERE name='player_affiliations'").fetchone() and not db.execute('SELECT 1 FROM player_affiliations WHERE club_id=? AND player_id=?',(tid,p['player_id'])).fetchone():
   from .eligibility import add
   add(db,p['player_id'],tid,'UNVERIFIED','UNVERIFIED',None,None,None,asof,s['source'],'LIVE_MATCHDAY_OBSERVATION_IS_NOT_PROOF_OF_REGULAR_MEMBERSHIP',status='UNVERIFIED',precision='MATCH_OBSERVED_ONLY')
  db.execute('INSERT OR REPLACE INTO squad_observations VALUES(?,?,?,?,?,?,?,?,?)',(tid,mid,p['player_id'],p['role'],p.get('shirt'),p['name'],p['json_path'],aid,s['source']['acquired_at']))
  db.execute("INSERT INTO validation_roster_membership VALUES(?,?, 'OBSERVED_MATCHDAY_UNION',?,?,NULL,NULL,?) ON CONFLICT(club_id,player_id,kind) DO UPDATE SET first_seen=MIN(first_seen,excluded.first_seen),last_seen=MAX(last_seen,excluded.last_seen)",(tid,p['player_id'],f['match_date'],f['match_date'],encoded(s['source'])))
 db.execute('INSERT OR IGNORE INTO live_sheets VALUES(?,?,?,?,?,?)',(tid,mid,s['provider'],s['source']['sha256'],encoded(s),now()));db.execute('INSERT OR REPLACE INTO live_documents VALUES(?,?,?,?,?,?,?)',(s['source']['url'],tid,mid,s['provider'],s['source']['sha256'],now(),encoded(s['source'])));db.execute('INSERT OR IGNORE INTO validation_match_aliases VALUES(?,?,?)',('url:'+s['source']['url'],mid,'LIVE_PUBLIC_PROVIDER_BINDING'))
 return dict(status='NEW_MATCH' if isnew else 'UPDATED_MATCH' if changed or inserted else 'UNCHANGED',club_id=tid,match_id=mid,inserted_rows=inserted,updated_rows=changed,players=len(s['players']),source=s['source'])

def task(db,c,result,runid):
 pending.observe(db,c,result,runid)
 queued=db.execute('SELECT * FROM match_retry_queue WHERE match_key=?',(pending.key(c),)).fetchone()
 if queued:
  result['failure_classification']=queued['failure_classification'];result['retry_state']=queued['state']
  if queued['state']=='PENDING' and result['status']=='VALIDATION_FAILED':result['status']='PENDING'
 status=result['status'];attempts=queued['attempt_count'] if queued else (db.execute('SELECT attempts FROM live_tasks WHERE url=?',(c['url'],)).fetchone() or [0])[0]+1;retry=queued['next_retry_at'] if queued else None
 db.execute('INSERT INTO live_tasks VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET status=excluded.status,attempts=excluded.attempts,next_retry=excluded.next_retry,last_error=excluded.last_error,last_run=excluded.last_run,payload_json=excluded.payload_json',(c['url'],c['club_id'],c['date'],c['provider'],status,attempts,retry,result.get('error'),runid,encoded(c)));db.commit()
def weekend_report(db):
 runs=[dict(x) for x in db.execute("SELECT * FROM live_runs ORDER BY started_at")];after=[]
 for r in runs:
  p=json.loads(r['payload_json'] or '{}')
  if any('2026-10-31'<=x.get('match_date','')<='2026-11-01' and x['status'] in ['NEW_MATCH','UPDATED_MATCH','UNCHANGED'] for x in p.get('results',[])):after.append(p)
 settings=json.loads(Path(os.environ.get('FUTBOL_SETTINGS_PATH',str(ROOT/'ops/live/settings.json'))).read_text());formal=[json.loads(r['payload_json']) for r in runs if r['payload_json']!='{}' and not json.loads(r['payload_json']).get('preflight',True) and datetime.fromisoformat(r['started_at'])>=datetime.fromisoformat(settings['first_formal_run'])]
 report=ROOT/'reports/FIRST_LIVE_INGESTION.md';report.parent.mkdir(exist_ok=True)
 lines=['# 首轮周末真实采集','', '目标比赛日期：2026-10-31—2026-11-01（Europe/Madrid）。',f"计划：每日北京时间16:00（{settings['timezone']}），常规回查{settings['lookback_hours']}小时；首次正式采集{settings['first_formal_run']}。",f'更新于 {now()}。','', '状态：'+('已发生目标窗口比赛的网络运行，详见下列证据；仍需按验收项核对。' if after else '已记录正式运行，但目标周末比赛尚未完成验收；不能把无新增与来源失败混同。' if formal else 'WAITING_FOR_WEEKEND。当前尚无目标周末比赛的正式采集证据，不能把部署预检当成周末成功。'),'','运行入口区分 launchd_automatic / manual_launchctl_trigger / preflight；RunAtLoad 与日历触发不能混为一谈。后台桌面权限：'+settings['background_desktop_permission'],'']
 for p in formal:lines+=['```json',json.dumps(p,ensure_ascii=False,indent=2),'```','']
 lines+=['全部运行日志：`logs/live/*.json`；数据库 `squad_pipeline/state/live.sqlite` 的 live_runs / live_tasks / live_conflicts。','', '日常任务发现目标日期的比赛后自动写入此报告。首次新比赛会立即执行一次保存响应的重复入库检查（不重复请求），下一日真实网络运行另行记录。尚未执行的验收项不得预先标为通过。']
 report.write_text('\n'.join(lines))

def run(args):
 runid=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');origin=os.environ.get('FUTBOL_RUN_ORIGIN','manual');settings=json.loads(Path(os.environ.get('FUTBOL_SETTINGS_PATH',str(ROOT/'ops/live/settings.json'))).read_text());current=datetime.now(timezone.utc);first=datetime.fromisoformat(settings['first_formal_run']);window_start=current-timedelta(hours=settings['lookback_hours']);asof=current.astimezone(ZoneInfo('Europe/Madrid')).date().isoformat()
 if not args.preflight and current<first:
  result=dict(run_id=runid,origin=origin,status='WAITING_FOR_FIRST_FORMAL_RUN',first_formal_run=settings['first_formal_run'],timezone=settings['timezone'],lookback_hours=settings['lookback_hours'],requests=0,observed_at=now());folder=ROOT/'logs/live';folder.mkdir(exist_ok=True,parents=True);(folder/(runid+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(encoded(result),flush=True);return 0
 db=connect()
 if not args.preflight and origin=='launchd_automatic':
  local=current.astimezone(ZoneInfo(settings['timezone']));slot=local.replace(hour=16,minute=0,second=0,microsecond=0)
  if local<slot:slot-=timedelta(days=1)
  utcslot=slot.astimezone(timezone.utc).isoformat()
  for prior in db.execute("SELECT payload_json FROM live_runs WHERE started_at>=? AND origin='launchd_automatic' AND status!='RUNNING'",(utcslot,)):
   if not json.loads(prior[0]).get('preflight',True):
    print(encoded(dict(status='DAILY_SLOT_ALREADY_ATTEMPTED',slot=slot.isoformat(),requests=0)),flush=True);db.close();return 0
 client=Client(runid,budget=args.budget);started=now();report=dict(run_id=runid,origin=origin,started_at=started,as_of=asof,preflight=args.preflight,timezone=settings['timezone'],lookback_hours=settings['lookback_hours'],window_start_utc=window_start.isoformat(),first_formal_run=settings['first_formal_run'],results=[],catalogs=[],coverage_limitations=[],requests=0)
 db.execute('INSERT INTO live_runs VALUES(?,?,?,?,?,?)',(runid,started,None,origin,'RUNNING','{}'));db.commit();candidates=[]
 for tid,(torly,sky) in CLUB_FEEDS.items():
  for provider,url in [('Torlytics','https://torlytics.de/verein/'+torly),('Sky','https://www.skysports.com/'+sky+'-scores-fixtures')]:
   try:
    r,b=client.fetch(url,'catalog_'+str(tid));items=catalogs(b.decode('utf-8','replace'),provider,tid,asof) if r['status']=='SUCCESS' else [];status='SUCCESS' if items else 'SOURCE_UNAVAILABLE';entry=dict(club_id=tid,provider=provider,status=status,source=r,discovered=len(items));report['catalogs'].append(entry)
    db.execute('INSERT INTO live_catalogs VALUES(?,?,?,?,?)',(runid,tid,provider,status,encoded(entry)));db.execute('INSERT OR REPLACE INTO live_checkpoints VALUES(?,?,?,?,?)',(tid,provider,asof,status,runid));db.commit()
    if not items:report['coverage_limitations'].append(dict(club_id=tid,provider=provider,issue='CATALOG_UNAVAILABLE;NOT_EVIDENCE_NO_MATCHES'));continue
    # Current Sky public directory is month limited. Query previous calendar month using its public navigation route if a gap crosses months.
    checkpoint=db.execute('SELECT completed_at FROM live_scan_windows WHERE club_id=? AND provider=?',(tid,provider)).fetchone();scan_start=min(window_start,datetime.fromisoformat(checkpoint[0])) if checkpoint else window_start;since=scan_start.astimezone(ZoneInfo('Europe/Madrid')).date().isoformat()
    if scan_start<window_start:report.setdefault('missed_window_catchup',[]).append(dict(club_id=tid,provider=provider,from_utc=scan_start.isoformat(),reason='MISSED_PREVIOUS_CATALOG_CHECKPOINT;NORMAL_WINDOW_IS_72_HOURS'))
    if provider=='Sky' and since[:7]!=asof[:7]:
     import html
     links=set(html.unescape(x) for x in re.findall(r'href="([^"]+)"',b.decode('utf-8','replace')));month=date.fromisoformat(since).replace(day=1);stop=date.fromisoformat(asof).replace(day=1);months=0
     while month<stop:
      months+=1;path='/'+sky+'-scores-fixtures/'+month.isoformat()
      if months>6 or path not in links:
       report['coverage_limitations'].append(dict(club_id=tid,provider=provider,issue='PUBLIC_MONTH_NAVIGATION_UNAVAILABLE_OR_BUDGET',from_date=month.isoformat(),to_date=asof));break
      mr,mb=client.fetch('https://www.skysports.com'+path,'catchup_catalog_'+str(tid))
      if mr['status']!='SUCCESS':report['coverage_limitations'].append(dict(club_id=tid,provider=provider,issue='CATCHUP_MONTH_UNAVAILABLE',source=mr));break
      items+=catalogs(mb.decode('utf-8','replace'),provider,tid,month.isoformat());month=(month.replace(day=28)+timedelta(days=4)).replace(day=1)
    eligible=[x for x in items if since<=x['date']<=asof and x.get('catalog_completed',True)]
    for c in eligible:
     c['window_start_utc']=scan_start.isoformat()
     doc=db.execute('SELECT * FROM live_documents WHERE url=?',(c['url'],)).fetchone();oldtask=db.execute('SELECT * FROM live_tasks WHERE url=?',(c['url'],)).fetchone()
     needs=not doc or args.recheck or c['date']>=(date.fromisoformat(asof)-timedelta(days=3)).isoformat()
     if oldtask and oldtask['status'] not in ['NEW_MATCH','UPDATED_MATCH','UNCHANGED'] and oldtask['next_retry'] and oldtask['next_retry']<=now():needs=True
     if needs and pending.should_attempt(db,c):candidates.append(c)
    if not any(x['club_id']==tid and x['provider']==provider for x in report['coverage_limitations']):db.execute('INSERT OR REPLACE INTO live_scan_windows VALUES(?,?,?)',(tid,provider,current.isoformat()));db.commit()
   except Exception as e:
    report['catalogs'].append(dict(club_id=tid,provider=provider,status='SOURCE_UNAVAILABLE',error=type(e).__name__+':'+str(e)))
 # Durable outstanding work survives catalog truncation, but runs are bounded and retry times respected.
 candidates.extend(pending.due_candidates(db));db.commit()
 seen=set();selected=[]
 for c in sorted(candidates,key=lambda x:(x['date'],x['provider']!='Torlytics'),reverse=True):
  if c['url'] not in seen:seen.add(c['url']);selected.append(c)
 if args.preflight:
  # Bounded controlled recheck: one latest fixture per club; fresh HTTP for every selected detail.
  selected=[];seenclubs=set()
  for c in sorted(candidates,key=lambda x:(x['provider']=='Torlytics',x['date']),reverse=True):
   if c['club_id'] not in seenclubs:seenclubs.add(c['club_id']);selected.append(c)
 for c in selected[:args.max_details]:
  result=dict(club_id=c['club_id'],url=c['url'],match_date=c['date'])
  try:
   r,b=client.fetch(c['url'],'match_detail');result['source']=r;result['source_context']=pending.sky_context(b.decode('utf-8','replace'),c) if c['provider']=='Sky' and r['status']=='SUCCESS' else {}
   if r['status']!='SUCCESS':result.update(status='SOURCE_UNAVAILABLE',error=r.get('error') or 'HTTP_'+str(r.get('http_status')),source=r)
   else:
    f,sheets=(torly_detail if c['provider']=='Torlytics' else sky_detail)(b.decode('utf-8','replace'),r);result['match_date']=f['match_date']
    if datetime.fromisoformat(f['kickoff'].replace('Z','+00:00'))>current:raise Pending('FUTURE_KICKOFF')
    if not args.preflight and not c.get('durable_pending_retry') and c.get('window_start_utc') and datetime.fromisoformat(f['kickoff'].replace('Z','+00:00'))<datetime.fromisoformat(c['window_start_utc']):
     result.update(status='UNCHANGED',reason='BEFORE_EXACT_72H_OR_CATCHUP_BOUND;NO_FACT_WRITE');report['results'].append(result);task(db,c,result,runid);continue
    own=next((s for s in sheets if s['club_id']==c['club_id']),None)
    if not own:raise IdentityError('REQUESTED_CLUB_NOT_ON_PARSED_SIDE')
    if not args.preflight:stage_sheet(db,f,own)
    db.execute('BEGIN');result.update(apply_sheet(db,f,own,runid,asof,args.preflight))
    if args.preflight:db.rollback()
    else:
     db.commit()
     if result['status']=='NEW_MATCH':
      # Actual second apply is rolled back; data state must remain semantically identical.
      before=db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0];db.execute('BEGIN');repeat=apply_sheet(db,f,own,runid,asof);after=db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0];db.rollback();result['repeat_apply']=dict(status=repeat['status'],row_count_equal=before==after)
  except Pending as e:db.rollback();result.update(status='PENDING',error=str(e))
  except (ParseError,ValueError,KeyError,StopIteration) as e:
   db.rollback();result.update(status='VALIDATION_FAILED',error=type(e).__name__+':'+str(e));payload=encoded(result);db.execute('INSERT OR IGNORE INTO live_conflicts VALUES(?,?,?,?,?,?,0)',(digest(payload.encode()),runid,c['club_id'],result.get('match_id'),'SOURCE_OR_IDENTITY_CONFLICT',payload));db.commit()
  except Exception as e:db.rollback();result.update(status='SOURCE_UNAVAILABLE',error=type(e).__name__+':'+str(e),traceback=traceback.format_exc())
  report['results'].append(result);task(db,c,result,runid);print(encoded(result),flush=True)
 # Queue any work over the daily detail budget; never silently drop it.
 for c in selected[args.max_details:]:task(db,c,dict(status='PENDING',error='DAILY_DETAIL_BUDGET_DEFERRED'),runid)
 if not args.preflight:
  for tid in CLUBS:
   for p in db.execute('SELECT DISTINCT player_id FROM player_match_minutes WHERE club_id=?',(tid,)):correction_rollups(db,tid,p[0],asof,asof)
   if all(x['status']=='SUCCESS' for x in report['catalogs'] if x['club_id']==tid) and not any(x['club_id']==tid for x in report['coverage_limitations']):db.execute('INSERT OR REPLACE INTO live_checkpoints VALUES(?,?,?,?,?)',(tid,'COMPLETED_RUN',asof,'SUCCESS',runid))
  db.commit()
 report.update(finished_at=now(),requests=client.calls,http_success=sum(r['http_status'] in [200,304] for r in client.records),status_counts=dict(Counter(r['status'] for r in report['results'])),outstanding=db.execute("SELECT COUNT(*) FROM live_tasks WHERE status NOT IN ('NEW_MATCH','UPDATED_MATCH','UNCHANGED')").fetchone()[0]);report['status']='DEGRADED' if report['coverage_limitations'] or any(x['status']!='SUCCESS' for x in report['catalogs']) or any(x['status'] in ['VALIDATION_FAILED','SOURCE_UNAVAILABLE','PENDING'] for x in report['results']) else 'SUCCESS';db.execute('UPDATE live_runs SET finished_at=?,status=?,payload_json=? WHERE run_id=?',(report['finished_at'],report['status'],encoded(report),runid));db.commit();log=ROOT/'logs/live'/str(runid+'.json');log.parent.mkdir(exist_ok=True,parents=True);log.write_text(json.dumps(report,ensure_ascii=False,indent=2));write(OUT/'latest_run.json',report);weekend_report(db);db.close();print(encoded(dict(run_id=runid,status=report['status'],requests=client.calls,counts=report['status_counts'])),flush=True);return 0 if report['status']=='SUCCESS' else 2

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--preflight',action='store_true');p.add_argument('--recheck',action='store_true');p.add_argument('--budget',type=int,default=64);p.add_argument('--max-details',type=int,default=18);args=p.parse_args();lockpath=BASE/'state/live.lock';lockpath.parent.mkdir(exist_ok=True)
 with lockpath.open('a+') as lock:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:print('LOCKED: another ingestion is active',flush=True);return 0
  try:return run(args)
  except Exception as e:
   folder=ROOT/'logs/live';folder.mkdir(exist_ok=True,parents=True);payload=dict(status='FATAL_RUN_ERROR',occurred_at=now(),origin=os.environ.get('FUTBOL_RUN_ORIGIN','manual'),error=type(e).__name__+':'+str(e),traceback=traceback.format_exc());(folder/('fatal_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.json')).write_text(json.dumps(payload,ensure_ascii=False,indent=2));print(encoded(payload),flush=True);return 1
if __name__=='__main__':raise SystemExit(main())
