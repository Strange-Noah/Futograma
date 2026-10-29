"""Executed fixture-first database and independent daily updater using retained match sheets."""
import json,re,sqlite3,time,copy,hashlib
from pathlib import Path
from collections import defaultdict,Counter
from datetime import date,datetime,timedelta,timezone
from .six_club_validation import OUT,BASE,ROOT,CLUBS,CUT,TZ,read,write,digest,now,day,dt,canon,source

def encoded(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def exported(db,sql,args=()):return [dict(r) for r in db.execute(sql,args)]
def snapshot(db):
 tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")];return digest(encoded({t:[list(r) for r in db.execute('SELECT * FROM "'+t+'" ORDER BY rowid')] for t in tables}).encode())
def create(path):
 if path.exists():raise RuntimeError('Refuse overwrite experiment database '+str(path))
 db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript((BASE/'schema.sql').read_text().replace('matches(match_id INTEGER PRIMARY KEY','matches(match_id TEXT PRIMARY KEY').replace('competitions(competition_id INTEGER PRIMARY KEY','competitions(competition_id TEXT PRIMARY KEY'))
 # Additive staging-only columns. Existing club/roster/match separation is retained.
 for ddl in ['ALTER TABLE matches ADD COLUMN scheduled_kickoff TEXT','ALTER TABLE matches ADD COLUMN match_state TEXT','ALTER TABLE matches ADD COLUMN metadata_json TEXT','ALTER TABLE player_match_minutes ADD COLUMN participation_status TEXT','ALTER TABLE player_match_minutes ADD COLUMN reported_minutes REAL','ALTER TABLE player_match_minutes ADD COLUMN derived_minutes REAL','ALTER TABLE player_match_minutes ADD COLUMN method TEXT','ALTER TABLE player_match_minutes ADD COLUMN evidence_json TEXT','ALTER TABLE player_match_minutes ADD COLUMN revision INTEGER DEFAULT 1'] :db.execute(ddl)
 db.executescript('''
 CREATE TABLE validation_player_aliases(provider_key TEXT PRIMARY KEY,player_id INTEGER REFERENCES players,identity_json TEXT);
 CREATE TABLE validation_match_aliases(alias TEXT PRIMARY KEY,match_id TEXT REFERENCES matches,reason TEXT);
 CREATE TABLE validation_roster_membership(club_id INTEGER,player_id INTEGER REFERENCES players,kind TEXT,first_seen TEXT,last_seen TEXT,effective_from TEXT,effective_to TEXT,source_json TEXT,PRIMARY KEY(club_id,player_id,kind));
 CREATE TABLE validation_source_evidence(evidence_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL);
 CREATE TABLE validation_reference_definition(club_id INTEGER PRIMARY KEY,definition_json TEXT);
 CREATE TABLE validation_fact_revisions(revision_id TEXT PRIMARY KEY,club_id INTEGER,match_id TEXT,player_id INTEGER,logical_date TEXT,reason TEXT,before_json TEXT,after_json TEXT);
 CREATE TABLE validation_applied_inputs(input_id TEXT PRIMARY KEY,logical_date TEXT,payload_hash TEXT);
 CREATE TABLE validation_daily_logs(logical_date TEXT PRIMARY KEY,payload_json TEXT);
 CREATE TABLE validation_rolling(club_id INTEGER,player_id INTEGER,as_of TEXT,window_days INTEGER,minutes REAL,unknown_rows INTEGER,PRIMARY KEY(club_id,player_id,as_of,window_days));
 CREATE TABLE validation_national_reference(player_id INTEGER,match_id TEXT,payload_json TEXT,PRIMARY KEY(player_id,match_id));
 ''')
 for t,(name,slug) in CLUBS.items():db.execute('INSERT INTO clubs VALUES(?,?,?,?)',(t,slug,name,None))
 db.execute('INSERT INTO periods VALUES(?,?,?,?,?,?)',('2026-27_H1','2026/27','H1','2026-07-01','2027-01-01','Europe/Madrid'))
 db.commit();return db

def addplayer(db,p):
 db.execute('INSERT OR IGNORE INTO players(player_id,display_name,identity_status) VALUES(?,?,?)',(p['player_id'],p['display_name'],p['identity_status']))
 for key in p['provider_keys']:db.execute('INSERT OR IGNORE INTO validation_player_aliases VALUES(?,?,?)',(key,p['player_id'],encoded(dict(names=p['source_names'],fotmob_id=p.get('fotmob_id')))))
def ev(db,e):
 key='e_'+digest(encoded(e).encode());db.execute('INSERT OR IGNORE INTO raw_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(key,None,e.get('path'),e.get('sha256') or digest(encoded(e).encode()),None,'SOURCE_REFERENCE' if e.get('path') else 'DERIVED_FACT_EVIDENCE',e.get('acquired_at'),None,None));db.execute('INSERT OR IGNORE INTO validation_source_evidence VALUES(?,?)',(key,encoded(e)));return key

def match(db,f):
 old=db.execute('SELECT * FROM matches WHERE match_id=?',(f['match_id'],)).fetchone()
 data=(f.get('home_id') or next((t for t,v in CLUBS.items() if v[1]==f['home_team']),None),f.get('away_id') or next((t for t,v in CLUBS.items() if v[1]==f['away_team']),None),f['kickoff'],f['match_date'],'2026-27_H1',f['competition_id'],f.get('score'),f['scheduled_kickoff'],f['match_state'],encoded(f))
 db.execute('INSERT OR IGNORE INTO competitions VALUES(?,?,?,?)',(f['competition_id'],f['competition'],'COMPETITIVE',None))
 if old:
  current=tuple(old[k] for k in ['home_id','away_id','kickoff','local_date','period_id','competition_id','result','scheduled_kickoff','match_state','metadata_json'])
  if current!=data:db.execute('UPDATE matches SET home_id=?,away_id=?,kickoff=?,local_date=?,period_id=?,competition_id=?,result=?,scheduled_kickoff=?,match_state=?,metadata_json=? WHERE match_id=?',data+(f['match_id'],))
 else:db.execute('INSERT INTO matches(match_id,home_id,away_id,kickoff,local_date,period_id,competition_id,result,scheduled_kickoff,match_state,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(f['match_id'],)+data)
 for alias in [f.get('discovery_match_id'),f.get('previous_match_id'),('fotmob:'+str(f['provider_match_id'])) if f.get('provider_match_id') else None]:
  if alias:db.execute('INSERT OR IGNORE INTO validation_match_aliases VALUES(?,?,?)',(alias,f['match_id'],'PROVIDER_BINDING_OR_DOCUMENTED_CANONICAL_ALIAS'))
 return old is None

def rolling(db,tid,pid,asof,window):
 start=(date.fromisoformat(asof)-timedelta(days=window-1)).isoformat()
 row=db.execute('SELECT COALESCE(SUM(p.minutes),0),SUM(CASE WHEN p.minutes IS NULL THEN 1 ELSE 0 END) FROM player_match_minutes p JOIN matches m USING(match_id) WHERE p.club_id=? AND p.player_id=? AND m.local_date BETWEEN ? AND ? AND m.match_state=\'COMPLETED\'',(tid,pid,start,asof)).fetchone()
 return dict(club_id=tid,player_id=pid,as_of=asof,window_days=window,minutes=row[0],unknown_rows=row[1] or 0)
def refresh(db,tid,pid,asof):
 for window in [7,28]:
  r=rolling(db,tid,pid,asof,window);db.execute('INSERT INTO validation_rolling VALUES(?,?,?,?,?,?) ON CONFLICT(club_id,player_id,as_of,window_days) DO UPDATE SET minutes=excluded.minutes,unknown_rows=excluded.unknown_rows WHERE minutes IS NOT excluded.minutes OR unknown_rows IS NOT excluded.unknown_rows',tuple(r.values()))

def fact(db,r,logical_day,reason='NEW_MATCH_EVIDENCE'):
 key=(r['club_id'],r['match_id'],r['player_id']);old=db.execute('SELECT * FROM player_match_minutes WHERE club_id=? AND match_id=? AND player_id=?',key).fetchone();payload=dict(status=r['status'],minutes=r['minutes'],reported_minutes=r.get('reported_minutes'),derived_minutes=r.get('derived_minutes'),method=r.get('minute_method'),evidence=r.get('observations',[]),sources=r.get('source_refs',[]),issues=r.get('issues',[]));encoded_payload=encoded(payload)
 if old and old['evidence_json']==encoded_payload:return 'UNCHANGED'
 aid=ev(db,payload);played=1 if r['status'] in ['STARTER','SUBSTITUTE_USED'] else 0 if r['status']!='UNKNOWN' else None
 if old:
  db.execute('INSERT INTO validation_fact_revisions VALUES(?,?,?,?,?,?,?,?)',('rev_'+digest(encoded([key,logical_day,encoded_payload,old['evidence_json']]).encode()),*key,logical_day,reason,old['evidence_json'],encoded_payload))
  db.execute('UPDATE player_match_minutes SET minutes=?,minutes_status=?,played=?,artifact_id=?,participation_status=?,reported_minutes=?,derived_minutes=?,method=?,evidence_json=?,revision=revision+1 WHERE club_id=? AND match_id=? AND player_id=?',(r['minutes'],'RESOLVED' if r['minutes'] is not None else 'UNKNOWN',played,aid,r['status'],r.get('reported_minutes'),r.get('derived_minutes'),r.get('minute_method'),encoded_payload,*key));return 'UPDATED'
 db.execute('INSERT INTO player_match_minutes(club_id,match_id,player_id,minutes,minutes_status,played,artifact_id,participation_status,reported_minutes,derived_minutes,method,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(*key,r['minutes'],'RESOLVED' if r['minutes'] is not None else 'UNKNOWN',played,aid,r['status'],r.get('reported_minutes'),r.get('derived_minutes'),r.get('minute_method'),encoded_payload));return 'INSERTED'

def backfill_row(tid,pid,f,ss):
 obs=[dict(p,provider=s['provider'],source=s['source']) for s in ss for p in s['players'] if p['player_id']==pid];states={p['status'] for p in obs if p['status']!='UNKNOWN'};complete=any(s['complete_squad'] for s in ss)
 status=next(iter(states)) if len(states)==1 else 'NOT_IN_MATCHDAY_SQUAD' if not obs and complete else 'UNKNOWN'
 reported=[p['reported_minutes'] for p in obs if p['reported_minutes'] is not None];derived=[p['derived_minutes'] for p in obs if p['derived_minutes'] is not None];vals=reported+derived
 issues=['MATERIAL_MINUTE_SOURCE_CONFLICT'] if vals and max(vals)-min(vals)>=5 else []
 minute=0 if status in ['UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD'] else vals[0] if vals and not issues and status!='UNKNOWN' else None
 return dict(club_id=tid,player_id=pid,match_id=f['match_id'],status=status,minutes=minute,reported_minutes=reported[0] if reported else None,derived_minutes=derived[0] if derived else None,observations=obs,source_refs=[s['source'] for s in ss],issues=issues,minute_method='SOURCE_REPORTED' if reported else 'EVENT_CLOCK_NOMINAL_REGULATION' if minute is not None and status in ['STARTER','SUBSTITUTE_USED'] else 'VERIFIED_NONAPPEARANCE' if minute==0 else 'UNKNOWN')

def incremental_row(tid,pid,f,ss):
 # Independent reducer consumes raw-sheet observations, never backfill matrix or rows.
 positive=[];evidence=[];reports=[];derived=[];covered=False
 for sheet in ss:
  covered=covered or bool(sheet['complete_squad'])
  for obs in sheet['players']:
   if obs['player_id']!=pid:continue
   evidence.append(dict(obs,provider=sheet['provider'],source=sheet['source']))
   if obs['status']!='UNKNOWN':positive.append(obs['status'])
   if obs.get('reported_minutes') is not None:reports.append(obs['reported_minutes'])
   if obs.get('derived_minutes') is not None:derived.append(obs['derived_minutes'])
 distinct=set(positive);status='UNKNOWN'
 if len(distinct)==1:status=positive[0]
 elif not evidence and covered:status='NOT_IN_MATCHDAY_SQUAD'
 choices=reports+derived;issues=[];minutes=None
 if choices and max(choices)-min(choices)>=5:issues.append('MATERIAL_MINUTE_SOURCE_CONFLICT')
 if status in ['UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD']:minutes=0
 elif status!='UNKNOWN' and choices and not issues:minutes=choices[0]
 return dict(club_id=tid,match_id=f['match_id'],player_id=pid,status=status,minutes=minutes,reported_minutes=reports[0] if reports else None,derived_minutes=derived[0] if derived else None,minute_method='SOURCE_REPORTED' if reports else 'EVENT_CLOCK_NOMINAL_REGULATION' if minutes is not None and status in ['STARTER','SUBSTITUTE_USED'] else 'VERIFIED_NONAPPEARANCE' if minutes==0 else 'UNKNOWN',observations=evidence,source_refs=[s['source'] for s in ss],issues=issues)

def sources_enrich(fs,ss):
 # Resolve final score from raw sheet itself, not from accepted player facts.
 for s in ss:
  f=next(f for f in fs if f['match_id']==s['match_id']);src=s['source'];p=ROOT/src['path'];raw=p.read_text()
  if s['provider']=='Torlytics':
   import re
   from .six_club_validation import text,take
   score=text(take(r'match-score__value">(.*?)</span>',raw));score=re.sub(r'\s*[–—-]\s*','-',score)
   if re.fullmatch(r'\d+-\d+',score):f['score']=score
  elif s['provider']=='Sky':
   import re
   ext=next(x for x in read(p) if x['source_url']==src['url']);lines=ext['source_lines'];scores=re.findall(r'^L\d+: #### .*?\s(\d+)\s*$',lines,re.M)
   if len(scores)==2:f['score']='-'.join(scores)
   penalty=re.search(r'win (\d+)-(\d+) on penalties',lines)
   if penalty:f['penalty_score_home_away']=penalty[1]+'-'+penalty[2];f['extra_time']=False;f['penalty_evidence']=dict(source=s['source'],location='L24')
  if s['provider']=='FotMob':
   meta=p.with_suffix('.meta.json')
   if meta.exists():
    m=read(meta)
    if m.get('mode')=='network' and m.get('sha256')==digest(p.read_bytes()) and m.get('fetched_at'):src['acquired_at']=m['fetched_at'];src['availability_proof']=source(meta,location='fetched_at;mode;verified body sha256')
  # No mtime inference. Dates of current HTML acquisition and reviewed web extracts are real observation times.
  if src.get('acquired_at'):s['available_date']=day(src['acquired_at'])
  else:s['available_date']=None
 return fs,ss

def store_sheet(db,s):
 aid=ev(db,s['source'])
 for p in s['players']:
  db.execute('INSERT OR IGNORE INTO squad_observations VALUES(?,?,?,?,?,?,?,?,?)',(s['club_id'],s['match_id'],p['player_id'],p['role'],p.get('shirt'),p['name'],p['json_path'],aid,s['source'].get('acquired_at')))
 db.execute('INSERT OR IGNORE INTO match_processing VALUES(?,?,?,?,?,?,?)',(s['club_id'],s['match_id'],aid,'CACHED_RAW_EVIDENCE','STRUCTURE_COMPLETE' if s['complete_squad'] else 'PARTIAL',encoded(dict(complete_events=s['complete_events'],issues=s['issues'])),s['source'].get('acquired_at')))

def build_backfill(fs,ss,players,rosters):
 path=OUT/'backfill/database.sqlite';db=create(path);rows=[]
 for p in players.values():
  if any(p['player_id'] in ids for ids in rosters.values()):addplayer(db,p)
 for f in fs:
  match(db,f)
  if f['match_state']!='COMPLETED':continue
  for tid,(name,slug) in CLUBS.items():
   if slug not in [f['home_team'],f['away_team']]:continue
   selected=[s for s in ss if s['club_id']==tid and s['match_id']==f['match_id']]
   for s in selected:store_sheet(db,s)
   for pid in rosters[str(tid)]:
    r=backfill_row(tid,pid,f,selected);fact(db,r,CUT);rows.append(r)
 for tid in CLUBS:
  for pid in rosters[str(tid)]:
   seen=[f['match_date'] for f in fs if f['match_state']=='COMPLETED' and any(s['match_id']==f['match_id'] and s['club_id']==tid and any(p['player_id']==pid for p in s['players']) for s in ss)]
   db.execute('INSERT INTO validation_roster_membership VALUES(?,?,?,?,?,?,?,?)',(tid,pid,'OBSERVED_MATCHDAY_UNION',min(seen),max(seen),None,None,'First/last observed are NOT transfer dates'))
   refresh(db,tid,pid,CUT)
 db.commit();write(OUT/'backfill/resolved/matrix.json',rows);return db

class Updater:
 def __init__(self,path,fs,ss,players,refs,mode):
  self.db=create(path);self.fs=fs;self.ss=ss;self.players=players;self.refs=refs;self.mode=mode;self.logs=[]
 def update(self,logical_day,fail_after=None):
  started=time.perf_counter();db=self.db;completed=[f for f in self.fs if f['match_state']=='COMPLETED' and f['match_date']<=logical_day]
  eligible=[]
  for s in self.ss:
   f=next(f for f in completed if f['match_id']==s['match_id']) if any(f['match_id']==s['match_id'] for f in completed) else None
   if not f:continue
   if self.mode=='STRICT' and (s['provider']!='FotMob' or not s.get('available_date') or s['available_date']>logical_day):continue
   eligible.append(s)
  # Strict replay uses verified knowledge on the current day. Identity bridges are only applied on execution day.
  if self.mode=='STRICT' and logical_day<CUT:
   eligible=copy.deepcopy(eligible)
   for s in eligible:
    for p in s['players']:p['player_id']=p.get('discovery_player_id',p['player_id'])
  newinputs=[]
  for s in eligible:
   token='sheet:'+digest(encoded(s).encode())
   if not db.execute('SELECT 1 FROM validation_applied_inputs WHERE input_id=?',(token,)).fetchone():newinputs.append((token,s))
  roster_inputs=[]
  if self.mode=='RETROSPECTIVE':
   roster_inputs=self.refs
  else:
   roster_inputs=[]  # Strict diagnostic uses raw source IDs only; later roster identity bridges are excluded.
  batchhash=digest(encoded([self.mode,logical_day,[x[0] for x in newinputs],[(r['club_id'],r['player_id']) for r in roster_inputs]]).encode())
  batch='day:'+self.mode+':'+logical_day
  if db.execute('SELECT 1 FROM validation_daily_logs WHERE logical_date=?',(logical_day,)).fetchone():
   if not newinputs:return dict(repeated=True,mutations=0)
  initialchanges=db.total_changes;log=dict(logical_date=logical_day,mode=self.mode,new_matches=0,new_players=0,new_player_match_records=0,updated_records=0,unchanged_records=0,network_requests=0,cache_hits=len(newinputs),unresolved_records=0);touched=set()
  try:
   db.execute('BEGIN')
   for r in roster_inputs:
    if db.execute('SELECT 1 FROM validation_roster_membership WHERE club_id=? AND player_id=? AND kind=?',(r['club_id'],r['player_id'],'REFERENCE')).fetchone():continue
    addplayer(db,self.players[r['player_id']]);db.execute('INSERT INTO validation_roster_membership VALUES(?,?,?,?,?,?,?,?)',(r['club_id'],r['player_id'],'REFERENCE',logical_day,logical_day,r['effective_from'],r['effective_to'],encoded(r)));db.execute('INSERT OR IGNORE INTO validation_reference_definition VALUES(?,?)',(r['club_id'],encoded(dict(definition=r['definition'],captured_at=r['captured_at'],effective_from=r['effective_from'],effective_to=r['effective_to']))));touched.add(r['club_id'])
   for token,s in newinputs:
    f=next(f for f in self.fs if f['match_id']==s['match_id'])
    if self.mode=='STRICT':
     raw=read(ROOT/s['source']['path']);g=raw['general'];f=dict(f,source_evidence=[s['source']],kickoff=g['matchTimeUTCDate'],scheduled_kickoff=g['matchTimeUTCDate'],match_date=day(g['matchTimeUTCDate']),score=raw.get('header',{}).get('status',{}).get('scoreStr'))
    log['new_matches']+=int(match(db,f));tid=s['club_id'];touched.add(tid)
    for p in s['players']:
     pid=p['player_id']
     if pid in self.players:player=self.players[pid]
     else:
      player=dict(player_id=pid,display_name=p['name'],identity_status='STRICT_RAW_PROVIDER_IDENTITY_BEFORE_FUTURE_BRIDGE',provider_keys=[p['provider_key']],source_names=[p['name']])
     addplayer(db,player)
     if not db.execute('SELECT 1 FROM validation_roster_membership WHERE club_id=? AND player_id=? AND kind=\'OBSERVED_MATCHDAY_UNION\'',(tid,pid)).fetchone():
      was_known=db.execute('SELECT 1 FROM validation_roster_membership WHERE club_id=? AND player_id=?',(tid,pid)).fetchone();log['new_players']+=int(not was_known);db.execute('INSERT INTO validation_roster_membership VALUES(?,?,?,?,?,?,?,?)',(tid,pid,'OBSERVED_MATCHDAY_UNION',f['match_date'],f['match_date'],None,None,encoded(s['source'])))
     else:db.execute('UPDATE validation_roster_membership SET last_seen=MAX(last_seen,?) WHERE club_id=? AND player_id=? AND kind=\'OBSERVED_MATCHDAY_UNION\' AND last_seen<?',(f['match_date'],tid,pid,f['match_date']))
    store_sheet(db,s);db.execute('INSERT INTO validation_applied_inputs VALUES(?,?,?)',(token,logical_day,digest(encoded(s).encode())))
    if fail_after is not None and log['cache_hits']>=fail_after:raise RuntimeError('CONTROLLED_TRANSACTION_INTERRUPTION')
   # Only new/changed contexts and late new-player gaps. Existing historical facts are not rebuilt.
   changed={(s['club_id'],s['match_id']) for token,s in newinputs}
   for tid in touched:
    members={r[0] for r in db.execute('SELECT DISTINCT player_id FROM validation_roster_membership WHERE club_id=?',(tid,))}
    for f in completed:
     if CLUBS[tid][1] not in [f['home_team'],f['away_team']]:continue
     if not db.execute('SELECT 1 FROM matches WHERE match_id=?',(f['match_id'],)).fetchone():continue
     selected=[s for s in eligible if s['club_id']==tid and s['match_id']==f['match_id']]
     if not selected:continue
     for pid in members:
      exists=db.execute('SELECT 1 FROM player_match_minutes WHERE club_id=? AND match_id=? AND player_id=?',(tid,f['match_id'],pid)).fetchone()
      if exists and (tid,f['match_id']) not in changed:continue
      r=incremental_row(tid,pid,f,selected);result=fact(db,r,logical_day,'NEW_SHEET_OR_NEW_PLAYER_PRIOR_MATCH_BACKFILL');log[{'INSERTED':'new_player_match_records','UPDATED':'updated_records','UNCHANGED':'unchanged_records'}[result]]+=1
      if result!='UNCHANGED':refresh(db,tid,pid,logical_day)
   log['unresolved_records']=db.execute("SELECT count(*) FROM player_match_minutes WHERE participation_status='UNKNOWN' OR minutes IS NULL").fetchone()[0];log['runtime_seconds']=time.perf_counter()-started
   db.execute('INSERT OR REPLACE INTO validation_daily_logs VALUES(?,?)',(logical_day,encoded(log)));db.commit();log['mutations']=db.total_changes-initialchanges;self.logs.append(log);return log
  except Exception:db.rollback();raise

def main():
 fs,ss=sources_enrich(read(OUT/'backfill/resolved/fixtures.json'),read(OUT/'backfill/resolved/sheets.json'));players={p['player_id']:p for p in read(OUT/'backfill/resolved/players.json')};roster=read(OUT/'backfill/resolved/observed_rosters.json');refs=read(OUT/'reference_rosters.json')
 write(OUT/'execution_inputs.json',dict(fixtures=fs,sheets=ss,players=list(players.values()),reference_rosters=refs));b=build_backfill(fs,ss,players,roster)
 results={}
 start=min(f['match_date'] for f in fs if f['match_state']=='COMPLETED')
 for mode,filename in [('RETROSPECTIVE','retrospective.sqlite'),('STRICT','strict.sqlite')]:
  updater=Updater(OUT/'incremental'/filename,fs,ss,players,refs,mode);current=date.fromisoformat(start);checks=[];recovery=[]
  while current<=date.fromisoformat(CUT):
   d=current.isoformat();before=snapshot(updater.db)
   if mode=='RETROSPECTIVE' and d in ['2026-08-12','2026-09-09','2026-10-27']:
    try:updater.update(d,fail_after=1)
    except RuntimeError as e:
     assert str(e)=='CONTROLLED_TRANSACTION_INTERRUPTION';assert before==snapshot(updater.db);recovery.append(dict(day=d,rollback_equal=True,integrity=updater.db.execute('PRAGMA integrity_check').fetchone()[0]))
   log=updater.update(d);finger=snapshot(updater.db);changes=updater.db.total_changes;repeat=updater.update(d);assert snapshot(updater.db)==finger and updater.db.total_changes==changes;checks.append(dict(day=d,same_state=True,second_run_mutations=0));current+=timedelta(days=1)
  # Upcoming fixtures remain scheduled only; no facts are generated for these IDs.
  for f in fs:
   if f['match_state']=='SCHEDULED':match(updater.db,f)
  updater.db.commit();write(OUT/'incremental'/(mode.lower()+'_daily_logs.json'),updater.logs);write(OUT/'incremental'/(mode.lower()+'_idempotence.json'),checks);write(OUT/'incremental'/(mode.lower()+'_recovery.json'),recovery)
  results[mode]=dict(path=filename,days=len(checks),update_days=sum(bool(l['new_matches'] or l['new_player_match_records'] or l['updated_records']) for l in updater.logs),matches=updater.db.execute("SELECT COUNT(*) FROM matches WHERE match_state='COMPLETED'").fetchone()[0],players=updater.db.execute('SELECT COUNT(*) FROM players').fetchone()[0],rows=updater.db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0],new_players=sum(l['new_players'] for l in updater.logs),updates=sum(l['updated_records'] for l in updater.logs),runtime=sum(l['runtime_seconds'] for l in updater.logs),cache_hits=sum(l['cache_hits'] for l in updater.logs),network_requests=0,idempotence_passes=len(checks),recovery_passes=len(recovery),unresolved=updater.db.execute("SELECT COUNT(*) FROM player_match_minutes WHERE participation_status='UNKNOWN' OR minutes IS NULL").fetchone()[0])
  updater.db.close()
 b.close();write(OUT/'incremental/results.json',results);print(encoded(results))
def cli():
 import argparse,shutil
 global OUT
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run-name',help='New isolated child run under audit/six_club/runs; existing directories are refused');args=parser.parse_args()
 if args.run_name:
  if not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}',args.run_name):parser.error('Use letters, digits, underscore or hyphen')
  original=OUT;target=OUT/'runs'/args.run_name
  if target.exists():parser.error('Refuse overwrite existing run')
  for folder in ['backfill/resolved','incremental']:(target/folder).mkdir(parents=True,exist_ok=True)
  for name in ['fixtures.json','sheets.json','players.json','observed_rosters.json']:shutil.copyfile(original/'backfill/resolved'/name,target/'backfill/resolved'/name)
  shutil.copyfile(original/'reference_rosters.json',target/'reference_rosters.json');OUT=target
 main()
if __name__=='__main__':
 import re
 cli()
