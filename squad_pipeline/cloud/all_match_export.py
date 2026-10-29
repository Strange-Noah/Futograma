"""Version-three club -> managed player -> club and international timeline export."""
import json,sqlite3,hashlib
from pathlib import Path
from .browser_export import export_browser

def export_all_matches(database,out,data_head,pointer,as_of,acceptance=True):
    out=Path(out);club_out=out/'club-export';export_browser(database,club_out,data_head,pointer,as_of,acceptance)
    result=json.loads((club_out/'data/browser.json').read_text());manifest=json.loads((club_out/'data/manifest.json').read_text());db=sqlite3.connect('file:'+str(Path(database).resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
    for rows in result['records'].values():
        for row in rows:row.update(match_type='CLUB',represented_team=row['club'],team_level='SENIOR',management_relation='CLUB_MATCH_RECORD')
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='international_appearances'").fetchone():raise ValueError('INTERNATIONAL_SCHEMA_REQUIRED_FOR_V3_EXPORT')
    for row in db.execute('SELECT a.*,m.kickoff_utc,m.madrid_date,m.competition_key,m.metadata_json AS match_metadata,t.display_name,t.level FROM international_appearances a JOIN international_matches m USING(match_id) JOIN football_teams t ON t.team_key=a.represented_team_key ORDER BY m.kickoff_utc DESC'):
        pid=str(row['player_id']);meta=json.loads(row['match_metadata']);source=db.execute('SELECT e.source_url FROM international_fact_evidence f JOIN persistent_source_evidence e USING(evidence_id) WHERE f.match_id=? AND f.player_id=? AND e.source_url LIKE ? ORDER BY f.evidence_id LIMIT 1',(row['match_id'],row['player_id'],'https://%')).fetchone();fixture=db.execute('SELECT home_team_key,away_team_key FROM international_matches WHERE match_id=?',(row['match_id'],)).fetchone();opponent_key=fixture['away_team_key'] if fixture['home_team_key']==row['represented_team_key'] else fixture['home_team_key'];opponent=db.execute('SELECT display_name FROM football_teams WHERE team_key=?',(opponent_key,)).fetchone()[0]
        minutes=row['minutes'];result['records'].setdefault(pid,[]).append(dict(match_id=row['match_id'],date=row['madrid_date'],kickoff_utc=row['kickoff_utc'],competition=meta.get('competition') or row['competition_key'],opponent=opponent,club_id=None,club=row['display_name'],represented_team=row['display_name'],match_type='NATIONAL',team_level=row['level'],managing_club=None,management_relation='REPRESENTATIVE_APPEARANCE_DOES_NOT_CHANGE_CLUB_MANAGEMENT',minutes=minutes,minute_state='UNKNOWN' if minutes is None else 'CONFIRMED_ZERO' if minutes==0 else 'RECORDED_MINUTES',participation_status=row['participation_status'],verification_status=row['verification_status'],evidence_scope=row['evidence_scope'],source_url=source[0] if source else None,pending=False))
    result['international_coverage']={}
    for row in db.execute('SELECT * FROM player_discovery_coverage ORDER BY last_checked_at'):
        result['international_coverage'][str(row['player_id'])]=dict(row)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='roster_verification_dossiers'").fetchone():
        for row in db.execute('SELECT * FROM roster_verification_dossiers ORDER BY observed_at'):
            dossier=json.loads(row['payload_json']);pid=str(row['player_id'])
            if pid in result['players']:result['players'][pid]['verification_dossier']=dossier;result['players'][pid]['identity_status']=row['identity_status']
    for rows in result['records'].values():rows.sort(key=lambda r:(r['date'],r.get('kickoff_utc',''),r['match_id']),reverse=True)
    result.update(schema_version=3,scope='RECORDED_CLUB_AND_NATIONAL_MATCHES;SEE_PLAYER_COVERAGE',national_history_complete=False)
    data=out/'data';data.mkdir(parents=True,exist_ok=False);body=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()
    with (data/'browser.json').open('xb') as f:f.write(body)
    counts=dict(db.execute('SELECT integration_state,COUNT(*) FROM international_reference_bridge GROUP BY integration_state').fetchall());manifest.update(schema_version=3,club_only=False,national_history_complete=False,national_reference_bridge=counts,browser_sha256=hashlib.sha256(body).hexdigest(),scope=result['scope'])
    with (data/'manifest.json').open('x') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
    db.close();return manifest
