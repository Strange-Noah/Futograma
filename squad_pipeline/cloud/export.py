"""Version-bound static JSON contract. No full-season or full-personal-load claim."""
import json,sqlite3,shutil,re
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .quality import inspect,readonly
from ..eligibility import active

def emit(root,path,value):
 p=Path(root)/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
def export(database,directory,data_head,pointer,web_source):
 info=inspect(database);current=pointer['current']
 if info['sha256']!=current['database']['sha256']:raise ValueError('WEBSITE_DATABASE_VERSION_MISMATCH')
 out=Path(directory)
 if out.exists() and any(out.iterdir()):raise ValueError('WEBSITE_OUTPUT_MUST_BE_EMPTY')
 out.mkdir(parents=True,exist_ok=True);db=readonly(database);db.row_factory=sqlite3.Row;asof=datetime.now(ZoneInfo('Europe/Madrid')).date().isoformat();version=dict(data_commit=data_head,data_version=current['version'],generation=pointer['generation'],sqlite_sha256=info['sha256'],code_commit=current['code_commit']);clubs=[dict(club_id=str(r['club_id']),name=r['name'],slug=r['club_key']) for r in db.execute('SELECT * FROM clubs ORDER BY club_id')];players=[]
 for r in db.execute('SELECT * FROM players ORDER BY player_id'):
  alias=db.execute("SELECT provider_key FROM validation_player_aliases WHERE player_id=? AND provider_key LIKE 'fotmob:%' LIMIT 1",(r['player_id'],)).fetchone();players.append(dict(player_id=str(r['player_id']),name=r['display_name'],identity_status=r['identity_status'],fotmob_player_id=alias[0].split(':',1)[1] if alias else None))
 seasons=[dict(period_id=r['period_id'],season=r['season'],half=r['half'],start_date=r['start_date'],end_exclusive=r['end_exclusive']) for r in db.execute('SELECT * FROM periods ORDER BY start_date')];cohort_unknown=0;cohorts=[]
 for c in clubs:
  tid=int(c['club_id']);members=[]
  ids={x[0] for x in db.execute('SELECT DISTINCT player_id FROM validation_roster_membership WHERE club_id=? UNION SELECT DISTINCT player_id FROM player_eligibility WHERE club_id=?',(tid,tid))}
  for pid in sorted(ids):
   a=active(db,tid,pid,asof);verified=a['verification_status']=='VERIFIED';cohort_unknown+=not verified;members.append(dict(player_id=str(pid),classification=a['classification'],regular_member=a['regular_member'],verification_status=a['verification_status'],start_date=a.get('start_date'),end_date=a.get('end_date'),verified_as_of=a.get('verified_as_of')))
  cohorts.append(dict(club_id=str(tid),as_of=asof,population_complete=bool(members) and all(m['verification_status']=='VERIFIED' for m in members),members=members))
 competitions={str(r['competition_id']):r['name'] for r in db.execute('SELECT * FROM competitions')};matches=[]
 for r in db.execute('SELECT * FROM matches ORDER BY kickoff,match_id'):
  meta=json.loads(r['metadata_json'] or '{}');matches.append(dict(match_id=str(r['match_id']),period_id=r['period_id'],competition_id=str(r['competition_id']),competition=competitions.get(str(r['competition_id'])),kickoff_utc=datetime.fromisoformat(r['kickoff'].replace('Z','+00:00')).astimezone(timezone.utc).isoformat(),date_madrid=r['local_date'],home=dict(club_id=str(r['home_id']) if r['home_id'] is not None else None,name=meta.get('home_team')),away=dict(club_id=str(r['away_id']) if r['away_id'] is not None else None,name=meta.get('away_team')),state=r['match_state'],result=r['result']))
 files=[]
 for c in clubs:
  tid=int(c['club_id']);period_ids=[r[0] for r in db.execute('SELECT DISTINCT m.period_id FROM matches m JOIN player_match_minutes p USING(match_id) WHERE p.club_id=?',(tid,))]
  for period_id in period_ids:
   # Period IDs originate in validated local period records; never use an unchecked filesystem path.
   if not re.fullmatch(r'[A-Za-z0-9_-]+',period_id):raise ValueError('UNSAFE_PERIOD_ID')
   rows=[]
   for r in db.execute('SELECT p.*,m.local_date,m.competition_id FROM player_match_minutes p JOIN matches m USING(match_id) WHERE p.club_id=? AND m.period_id=? ORDER BY m.kickoff,p.player_id',(tid,period_id)):
    squad=db.execute('SELECT shirt_number,role FROM squad_observations WHERE club_id=? AND match_id=? AND player_id=?',(tid,r['match_id'],r['player_id'])).fetchone();rows.append(dict(match_id=str(r['match_id']),player_id=str(r['player_id']),shirt_number=squad[0] if squad else None,role=squad[1] if squad else None,status=r['participation_status'],minutes=r['minutes'],minute_method=r['method'],revision=r['revision'],date_madrid=r['local_date'],competition_id=str(r['competition_id']),identity_verified=False))
   path='data/timelines/'+str(tid)+'/'+period_id+'.json';emit(out,path,dict(version=version,club_id=str(tid),period_id=period_id,club_only=True,full_season_coverage_verified=False,rows=rows));files.append(dict(club_id=str(tid),period_id=period_id,path=path.removeprefix('data/')))
 rolling=[]
 for c in clubs:
  tid=int(c['club_id'])
  for (pid,) in db.execute('SELECT DISTINCT player_id FROM player_match_minutes WHERE club_id=?',(tid,)):
   windows=[]
   for days in [7,14,28]:
    r=db.execute("SELECT COALESCE(SUM(p.minutes),0),SUM(p.minutes IS NULL),COUNT(*),COALESCE(SUM(CASE WHEN CAST(m.competition_id AS TEXT) IN ('47','53','54','55','87') THEN p.minutes ELSE 0 END),0),COALESCE(SUM(CASE WHEN CAST(m.competition_id AS TEXT)='42' THEN p.minutes ELSE 0 END),0) FROM player_match_minutes p JOIN matches m USING(match_id) WHERE p.club_id=? AND p.player_id=? AND m.match_state='COMPLETED' AND m.local_date BETWEEN date(?,?) AND ?",(tid,pid,asof,'-'+str(days-1)+' days',asof)).fetchone();windows.append(dict(days=days,club_minutes=r[0],unknown_rows=r[1] or 0,observed_match_rows=r[2],domestic_league_minutes=r[3],champions_league_minutes=r[4],all_personal_matches_complete=False))
   rolling.append(dict(club_id=str(tid),player_id=str(pid),as_of_madrid=asof,windows=windows))
 affiliation=[dict(player_id=str(r['player_id']),club_id=str(r['club_id']),relationship_type=r['relationship_type'],start_date=r['start_date'],end_date=r['end_date'],verified_as_of=r['verified_as_of'],verification_status=r['verification_status'],date_precision=r['date_precision']) for r in db.execute('SELECT * FROM player_affiliations ORDER BY club_id,player_id,start_date')]
 national_rows=db.execute('SELECT COUNT(*) FROM validation_national_reference').fetchone()[0];source_count=db.execute('SELECT COUNT(*) FROM cloud_documents').fetchone()[0] if db.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_documents'").fetchone() else 0
 manifest=dict(schema_version=1,version=version,exported_at=datetime.now(timezone.utc).isoformat(),match_timezone='Europe/Madrid',schedule_timezone='Asia/Shanghai',club_only=True,national_history_complete=False,verified_national_reference_rows=national_rows,first_team_population_complete=cohort_unknown==0,unverified_memberships=cohort_unknown,full_season_coverage_verified=False,identity_review_complete=False,source_index_documents=source_count,endpoints=dict(clubs='clubs.json',players='players.json',seasons='seasons.json',matches='matches.json',cohorts='cohorts.json',affiliations='affiliations.json',rolling='rolling.json'),timelines=files)
 for name,value in [('manifest',manifest),('clubs',clubs),('players',players),('seasons',seasons),('matches',matches),('cohorts',cohorts),('affiliations',affiliation),('rolling',rolling)]:emit(out,'data/'+name+'.json',value)
 for name in ['index.html','app.js','style.css']:shutil.copyfile(Path(web_source)/name,out/name)
 (out/'.nojekyll').write_text('');db.close();return manifest
