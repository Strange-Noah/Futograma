"""Raw browser export. Current management and historical match facts are separate dimensions."""
import json,sqlite3,hashlib
from datetime import datetime,timezone
from pathlib import Path
from .quality import inspect,readonly
from ..roster_policy import roster_decision as decision
from ..live_parse import CLUBS

def minute_state(status,minutes):
 if status in ('UNKNOWN','UNVERIFIED','PENDING') or minutes is None:return 'UNKNOWN'
 return 'CONFIRMED_ZERO' if minutes==0 else 'RECORDED_MINUTES'
def export_browser(database,out,data_head,pointer,as_of,acceptance=True):
 info=inspect(database)
 if info['sha256']!=pointer['current']['database']['sha256']:raise ValueError('BROWSER_DATABASE_VERSION_MISMATCH')
 db=readonly(database);db.row_factory=sqlite3.Row;clubs=[];players={};records={};competitions={str(r['competition_id']):r['name'] for r in db.execute('SELECT * FROM competitions')}
 for c in db.execute('SELECT * FROM clubs ORDER BY name'):
  cid=c['club_id'];entries=[]
  ids={r[0] for r in db.execute('SELECT player_id FROM validation_roster_membership WHERE club_id=? UNION SELECT player_id FROM player_eligibility WHERE club_id=? UNION SELECT player_id FROM club_management_events WHERE club_id=? UNION SELECT player_id FROM player_match_minutes WHERE club_id=?',(cid,cid,cid,cid))}
  for pid in sorted(ids):
   p=db.execute('SELECT * FROM players WHERE player_id=?',(pid,)).fetchone()
   if not p:continue
   key=str(pid);d=decision(db,cid,pid,as_of);players[key]=dict(player_id=key,name=p['display_name'],identity_status=p['identity_status']);entries.append(dict(player_id=key,management=d))
  clubs.append(dict(club_id=str(cid),name=c['name'],players=entries,managed_count=sum(x['management']['included'] for x in entries),unknown_count=sum(x['management']['state']=='UNKNOWN' for x in entries),youth_count=sum(x['management']['state']=='YOUTH' for x in entries)))
 club_names={str(r['club_id']):r['name'] for r in db.execute('SELECT * FROM clubs')}
 for r in db.execute('SELECT p.*,m.kickoff,m.local_date,m.competition_id,m.metadata_json,m.home_id,m.away_id FROM player_match_minutes p JOIN matches m USING(match_id) ORDER BY m.kickoff DESC,p.club_id,p.player_id'):
  pid=str(r['player_id']);cid=str(r['club_id']);meta=json.loads(r['metadata_json'] or '{}');own_home=r['home_id']==r['club_id'];opponent=meta.get('away_team') if own_home else meta.get('home_team');sources=meta.get('source_evidence') or [];url=next((x.get('url') for x in sources if (x.get('url') or '').startswith('https://')),None)
  if not url:
   source=db.execute('SELECT url FROM live_documents WHERE club_id=? AND match_id=? LIMIT 1',(r['club_id'],r['match_id'])).fetchone();url=source[0] if source else None
  state=minute_state(r['participation_status'],r['minutes']);rows=records.setdefault(pid,[]);rows.append(dict(match_id=str(r['match_id']),date=r['local_date'],competition=competitions.get(str(r['competition_id'])) or meta.get('competition') or '未知赛事',opponent=opponent or '未知对手',club_id=cid,club=club_names.get(cid,cid),minutes=r['minutes'] if state!='UNKNOWN' else None,minute_state=state,participation_status=r['participation_status'],verification_status='RECORDED_FACT;INDEPENDENT_IDENTITY_REVIEW_NOT_COMPLETE',source_url=url,management_at_match=decision(db,r['club_id'],r['player_id'],r['local_date'])['state'],pending=False))
 pending=[]
 queue_initialized=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='match_retry_queue'").fetchone())
 retry_rows=db.execute("SELECT * FROM match_retry_queue WHERE state!='RESOLVED' ORDER BY match_date DESC") if queue_initialized else []
 for r in retry_rows:
  r=dict(r);c=json.loads(r['candidate_json']);
  if c.get('acceptance_test_only'):continue
  pending.append({k:r[k] for k in ['match_key','club_id','url','match_date','failure_classification','state','next_retry_at','escalation_status','attempt_count']});club=next((x for x in clubs if x['club_id']==str(r['club_id'])),None)
  if not club:continue
  for p in club['players']:
   if not p['management']['included']:continue
   rows=records.setdefault(p['player_id'],[])
   if r['canonical_match_id'] and any(x['match_id']==r['canonical_match_id'] and x['club_id']==str(r['club_id']) for x in rows):
    for x in rows:
     if x['match_id']==r['canonical_match_id'] and x['club_id']==str(r['club_id']):x['new_source_warning']=r['failure_classification']
    continue
   # This is a team fixture with unknown player selection, never a fabricated player appearance.
   own=c.get('catalog_home');away=c.get('catalog_away');opponent=away if own==CLUBS.get(r['club_id'],(None,None))[1] else own or c.get('opponent') or '待核实'
   rows.append(dict(match_id=r['match_key'],date=r['match_date'],competition=(c.get('catalog_competition')+'（待阵容页核实）') if c.get('catalog_competition') else '赛事待核实',opponent=opponent,club_id=str(r['club_id']),club=club['name'],minutes=None,minute_state='PENDING_SOURCE' if r['state']=='PENDING' else 'REQUIRES_REVIEW',participation_status='PLAYER_SELECTION_UNCONFIRMED',verification_status=r['failure_classification'],source_url=r['url'],pending=True,queue_state=r['state'],next_retry_at=r['next_retry_at']))
 for rows in records.values():rows.sort(key=lambda x:(x['date'],x['club']),reverse=True)
 version=dict(data_commit=data_head,data_version=pointer['current']['version'],sqlite_sha256=info['sha256'],code_commit=pointer['current']['code_commit']);result=dict(schema_version=2,as_of=as_of,generated_at=datetime.now(timezone.utc).isoformat(),acceptance_dataset=acceptance,production_ingestion_enabled=False,authority_handover=False,scope='RECORDED_CLUB_MATCHES_ONLY;NOT_COMPLETE_PERSONAL_WORKLOAD',version=version,clubs=clubs,players=players,records=records,pending=pending,club_retry_queue_status='INITIALIZED' if queue_initialized else 'NOT_INITIALIZED;NO_COMPLETENESS_CLAIM')
 p=Path(out)/'data/browser.json';p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x') as f:json.dump(result,f,ensure_ascii=False,separators=(',',':'))
 manifest=dict(browser_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),schema_version=2,exported_at=result['generated_at'],as_of=as_of,version=version,acceptance_dataset=acceptance,dataset_ref=pointer.get('dataset_ref'),club_only=True,national_history_complete=False,first_team_population_complete=all(c['unknown_count']==0 for c in clubs),endpoint='browser.json',club_retry_queue_status=result['club_retry_queue_status'])
 with (p.parent/'manifest.json').open('x') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
 db.close();return manifest
