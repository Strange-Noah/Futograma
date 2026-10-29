"""Additive international facts. Never mutates club facts or legacy reference payloads."""
import json,hashlib,sqlite3
from datetime import datetime,timezone
from zoneinfo import ZoneInfo

def encoded(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(value):return hashlib.sha256((value if isinstance(value,str) else encoded(value)).encode()).hexdigest()
DDL="""
CREATE TABLE IF NOT EXISTS international_schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT,design TEXT);
CREATE TABLE IF NOT EXISTS football_teams(team_key TEXT PRIMARY KEY,team_type TEXT NOT NULL CHECK(team_type IN('CLUB','NATIONAL')),level TEXT NOT NULL,display_name TEXT NOT NULL,federation TEXT,club_id INTEGER REFERENCES clubs(club_id),verification_status TEXT NOT NULL,CHECK((team_type='CLUB' AND club_id IS NOT NULL) OR (team_type='NATIONAL' AND club_id IS NULL)),UNIQUE(club_id));
CREATE TABLE IF NOT EXISTS persistent_source_evidence(evidence_id TEXT PRIMARY KEY,provider TEXT,source_url TEXT,fetched_at TEXT,body_sha256 TEXT,retained_asset_ref TEXT,source_locator TEXT,evidence_scope TEXT,verification_status TEXT,unresolved_hold INTEGER NOT NULL DEFAULT 0 CHECK(unresolved_hold IN(0,1)),payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS football_team_provider_ids(provider TEXT,provider_team_id TEXT,team_key TEXT NOT NULL REFERENCES football_teams,evidence_id TEXT REFERENCES persistent_source_evidence,valid_from TEXT,end_exclusive TEXT,PRIMARY KEY(provider,provider_team_id));
CREATE TABLE IF NOT EXISTS international_matches(match_id TEXT PRIMARY KEY,home_team_key TEXT NOT NULL REFERENCES football_teams,away_team_key TEXT NOT NULL REFERENCES football_teams,kickoff_utc TEXT NOT NULL,madrid_date TEXT NOT NULL,competition_key TEXT NOT NULL,stage TEXT,match_state TEXT,score TEXT,duration_minutes INTEGER,extra_time_status TEXT,identity_status TEXT NOT NULL,metadata_json TEXT NOT NULL,CHECK(home_team_key<>away_team_key),CHECK(duration_minutes IS NULL OR duration_minutes BETWEEN 1 AND 150));
CREATE TABLE IF NOT EXISTS international_match_provider_ids(provider TEXT,provider_match_id TEXT,match_id TEXT NOT NULL REFERENCES international_matches,evidence_id TEXT REFERENCES persistent_source_evidence,PRIMARY KEY(provider,provider_match_id));
CREATE TABLE IF NOT EXISTS international_appearances(match_id TEXT REFERENCES international_matches,player_id INTEGER REFERENCES players,represented_team_key TEXT NOT NULL REFERENCES football_teams,participation_status TEXT NOT NULL,minutes INTEGER,reported_minutes INTEGER,derived_minutes INTEGER,minutes_precision TEXT,verification_status TEXT NOT NULL,evidence_scope TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,metadata_json TEXT NOT NULL,PRIMARY KEY(match_id,player_id),CHECK(minutes IS NULL OR minutes BETWEEN 0 AND 150),CHECK(participation_status NOT IN('UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD') OR minutes IS NULL OR minutes=0));
CREATE TABLE IF NOT EXISTS international_fact_evidence(match_id TEXT,player_id INTEGER,evidence_id TEXT REFERENCES persistent_source_evidence,field_name TEXT NOT NULL,claim_json TEXT NOT NULL,accepted INTEGER NOT NULL,decision_reason TEXT,PRIMARY KEY(match_id,player_id,evidence_id,field_name),FOREIGN KEY(match_id,player_id) REFERENCES international_appearances);
CREATE TABLE IF NOT EXISTS national_affiliation_events(event_id TEXT PRIMARY KEY,player_id INTEGER NOT NULL REFERENCES players,team_key TEXT NOT NULL REFERENCES football_teams,effective_from TEXT,end_exclusive TEXT,observed_on TEXT,affiliation_kind TEXT,verification_status TEXT,evidence_id TEXT REFERENCES persistent_source_evidence,date_precision TEXT);
CREATE TABLE IF NOT EXISTS player_discovery_coverage(player_id INTEGER REFERENCES players,window_start TEXT,window_end TEXT,scope TEXT,history_status TEXT,affiliation_status TEXT,fixture_status TEXT,lineup_status TEXT,last_checked_at TEXT,continuation_json TEXT,unresolved_count INTEGER,evidence_json TEXT,PRIMARY KEY(player_id,window_start,window_end,scope));
CREATE TABLE IF NOT EXISTS international_acquisition_queue(task_key TEXT PRIMARY KEY,entity_type TEXT,provider TEXT,provider_entity_id TEXT,url TEXT,player_id INTEGER REFERENCES players,team_key TEXT REFERENCES football_teams,match_id TEXT REFERENCES international_matches,first_observed_at TEXT,completion_at TEXT,last_attempt_at TEXT,attempt_count INTEGER NOT NULL DEFAULT 0,next_retry_at TEXT,failure_classification TEXT,state TEXT,payload_json TEXT);
CREATE TABLE IF NOT EXISTS international_acquisition_history(event_id TEXT PRIMARY KEY,task_key TEXT REFERENCES international_acquisition_queue,attempt_token TEXT,observed_at TEXT,result_json TEXT);
CREATE TABLE IF NOT EXISTS international_reference_bridge(player_id INTEGER,legacy_match_id TEXT,match_id TEXT REFERENCES international_matches,original_payload_sha256 TEXT NOT NULL,integration_state TEXT NOT NULL,evidence_scope TEXT NOT NULL,migration_version INTEGER NOT NULL,PRIMARY KEY(player_id,legacy_match_id),FOREIGN KEY(player_id,legacy_match_id) REFERENCES validation_national_reference(player_id,match_id));
CREATE TABLE IF NOT EXISTS international_fact_revisions(revision_id TEXT PRIMARY KEY,match_id TEXT,player_id INTEGER,previous_json TEXT,new_json TEXT,evidence_json TEXT,observed_at TEXT);
CREATE TRIGGER IF NOT EXISTS international_own_side BEFORE INSERT ON international_appearances BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM international_matches WHERE match_id=NEW.match_id AND NEW.represented_team_key IN(home_team_key,away_team_key)) THEN RAISE(ABORT,'REPRESENTED_TEAM_NOT_IN_MATCH') END;END;
CREATE TRIGGER IF NOT EXISTS international_only_national BEFORE INSERT ON international_matches BEGIN SELECT CASE WHEN (SELECT team_type FROM football_teams WHERE team_key=NEW.home_team_key)<>'NATIONAL' OR (SELECT team_type FROM football_teams WHERE team_key=NEW.away_team_key)<>'NATIONAL' THEN RAISE(ABORT,'NATIONAL_TEAMS_REQUIRED') END;END;
CREATE VIEW IF NOT EXISTS player_all_match_facts AS
SELECT p.player_id,p.match_id,m.kickoff AS kickoff_utc,m.local_date AS madrid_date,'CLUB' AS match_type,'SENIOR' AS team_level,p.club_id AS managing_club_at_record,'club:'||p.club_id AS represented_team_key,c.name AS represented_team,CAST(m.competition_id AS TEXT) AS competition_key,p.participation_status,p.minutes,'RECORDED_CLUB_FACT' AS verification_status,'LEGACY_CLUB_FACT' AS evidence_scope
FROM player_match_minutes p JOIN matches m USING(match_id) JOIN clubs c ON c.club_id=p.club_id
UNION ALL
SELECT a.player_id,a.match_id,m.kickoff_utc,m.madrid_date,'NATIONAL',t.level,NULL,a.represented_team_key,t.display_name,m.competition_key,a.participation_status,a.minutes,a.verification_status,a.evidence_scope
FROM international_appearances a JOIN international_matches m USING(match_id) JOIN football_teams t ON t.team_key=a.represented_team_key;
"""

def migrate(db):
    if db.in_transaction:raise ValueError('MIGRATION_REQUIRES_CLEAN_TRANSACTION')
    db.execute('PRAGMA foreign_keys=ON')
    try:
        db.executescript('BEGIN IMMEDIATE;'+DDL)
        db.execute('INSERT OR IGNORE INTO international_schema_migrations VALUES(?,?,?)',(1,datetime.now(timezone.utc).isoformat(),'PHASE3_P2_SCHEMA_REVIEW_20261029_APPROVED_ISOLATED'))
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('FOREIGN_KEY_CHECK_FAILED')
        db.commit()
    except BaseException:
        db.rollback();raise

def evidence(db,source,scope='TARGET_SPECIFIC_LEGACY',verification='RETAINED_PRIOR_VERIFICATION',hold=0):
    eid=source.get('evidence_id') or 'nt_e_'+digest(source)[:24]
    url=source.get('url') or source.get('source_url');sha=source.get('sha256') or source.get('body_sha256') or source.get('sha256_of_extract')
    payload=encoded(source);existing=db.execute('SELECT payload_json FROM persistent_source_evidence WHERE evidence_id=?',(eid,)).fetchone()
    if existing and existing[0]!=payload:raise ValueError('IMMUTABLE_EVIDENCE_COLLISION')
    db.execute('INSERT OR IGNORE INTO persistent_source_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)',(eid,source.get('provider'),url,source.get('acquired_at') or source.get('response_timestamp'),sha,source.get('retained_asset_ref'),source.get('json_path') or source.get('location'),scope,verification,hold,payload));return eid

def team(db,name,level,eid,provider=None,provider_id=None):
    if not name or not level:raise ValueError('VERIFIED_TEAM_NAME_AND_LEVEL_REQUIRED')
    key='national:'+digest([name.casefold(),level])[:24]
    if provider and provider_id is not None:
        old=db.execute('SELECT team_key FROM football_team_provider_ids WHERE provider=? AND provider_team_id=?',(provider,str(provider_id))).fetchone()
        if old:
            check=db.execute('SELECT level FROM football_teams WHERE team_key=?',(old[0],)).fetchone()
            if check[0]!=level:raise ValueError('NATIONAL_TEAM_LEVEL_CONFLICT')
            return old[0]
    db.execute('INSERT OR IGNORE INTO football_teams VALUES(?,?,?,?,?,?,?)',(key,'NATIONAL',level,name,None,None,'EVIDENCED_MATCH_PARTICIPANT'))
    if provider and provider_id is not None:db.execute('INSERT OR IGNORE INTO football_team_provider_ids VALUES(?,?,?,?,?,?)',(provider,str(provider_id),key,eid,None,None))
    return key

def put_match(db,match,provider,provider_id,eid):
    prior=db.execute('SELECT match_id FROM international_match_provider_ids WHERE provider=? AND provider_match_id=?',(provider,str(provider_id))).fetchone()
    key=prior[0] if prior else match['match_id']
    old=db.execute('SELECT * FROM international_matches WHERE match_id=?',(key,)).fetchone()
    if db.execute('SELECT 1 FROM matches WHERE match_id=?',(key,)).fetchone():raise ValueError('CROSS_DOMAIN_MATCH_COLLISION')
    immutable=('home_team_key','away_team_key','kickoff_utc','competition_key')
    if old and any(old[k]!=match[k] for k in immutable):raise ValueError('PHYSICAL_MATCH_IDENTITY_CONFLICT')
    if not old:
        cols=['match_id','home_team_key','away_team_key','kickoff_utc','madrid_date','competition_key','stage','match_state','score','duration_minutes','extra_time_status','identity_status','metadata_json']
        db.execute('INSERT INTO international_matches VALUES('+','.join('?' for _ in cols)+')',[key]+[match.get(k) for k in cols[1:]])
    db.execute('INSERT OR IGNORE INTO international_match_provider_ids VALUES(?,?,?,?)',(provider,str(provider_id),key,eid));return key

def put_appearance(db,appearance):
    fields=['match_id','player_id','represented_team_key','participation_status','minutes','reported_minutes','derived_minutes','minutes_precision','verification_status','evidence_scope','revision','metadata_json']
    old=db.execute('SELECT * FROM international_appearances WHERE match_id=? AND player_id=?',(appearance['match_id'],appearance['player_id'])).fetchone()
    if old:
        if any(old[k]!=appearance.get(k) for k in fields if k!='revision'):raise ValueError('INTERNATIONAL_FACT_CHANGE_REQUIRES_CORROBORATION_AND_REVISION')
        return False
    db.execute('INSERT INTO international_appearances VALUES('+','.join('?' for _ in fields)+')',[appearance.get(k) for k in fields]);return True

def bridge_references(db):
    summary=[]
    for row in db.execute('SELECT * FROM validation_national_reference ORDER BY player_id,match_id').fetchall():
        pid,legacy,raw=row['player_id'],row['match_id'],row['payload_json'];payload=json.loads(raw);sha=digest(raw);old=db.execute('SELECT original_payload_sha256 FROM international_reference_bridge WHERE player_id=? AND legacy_match_id=?',(pid,legacy)).fetchone()
        if old:
            if old[0]!=sha:raise ValueError('LEGACY_REFERENCE_PAYLOAD_CHANGED')
            continue
        db.execute('SAVEPOINT national_reference')
        try:
            if payload.get('team_type') not in ('NATIONAL','NATIONAL_YOUTH') or payload.get('player_id')!=pid:raise ValueError('UNTYPED_OR_WRONG_PLAYER_REFERENCE')
            refs=[]
            for rid in payload.get('source_refs',[]):
                s=db.execute('SELECT payload_json FROM validation_source_evidence WHERE evidence_id=?',(rid,)).fetchone()
                if s:refs.append(evidence(db,dict(json.loads(s[0]),evidence_id=rid)))
                elif db.execute('SELECT 1 FROM persistent_source_evidence WHERE evidence_id=?',(rid,)).fetchone():refs.append(rid)
            if not refs:raise ValueError('PERSISTENT_REFERENCE_EVIDENCE_MISSING')
            # Do not invent a youth age band when the legacy evidence lacks it.
            level='SENIOR' if payload['team_type']=='NATIONAL' else payload.get('team_level')
            if not level:raise ValueError('YOUTH_LEVEL_UNVERIFIED')
            own=payload['represented_team'];ownid=payload.get('represented_team_id');eid=refs[0]
            sides={n:team(db,payload[n+'_team'],level,eid,'FotMob' if payload[n+'_team']==own and ownid is not None else None,ownid if payload[n+'_team']==own else None) for n in ('home','away')}
            if own not in (payload['home_team'],payload['away_team']):raise ValueError('REPRESENTED_TEAM_NOT_A_SIDE')
            kick=datetime.fromisoformat(payload['kickoff'].replace('Z','+00:00')).astimezone(timezone.utc).isoformat();date=datetime.fromisoformat(kick).astimezone(ZoneInfo('Europe/Madrid')).date().isoformat()
            if date!=payload['match_date']:raise ValueError('REFERENCE_MADRID_DATE_CONFLICT')
            match=dict(match_id='nt_'+legacy,home_team_key=sides['home'],away_team_key=sides['away'],kickoff_utc=kick,madrid_date=date,competition_key=str(payload['competition_id']),stage=None,match_state=payload.get('match_state'),score=payload.get('score'),duration_minutes=None,extra_time_status='LEGACY_TARGET_SCOPE;NO_FULL_DURATION_CLAIM',identity_status='RETAINED_PRIOR_REFERENCE_VERIFICATION',metadata_json=encoded({'legacy_match_id':legacy,'competition':payload.get('competition'),'source_refs':payload.get('source_refs',[])}))
            mid=put_match(db,match,'FotMob',payload['provider_match_id'],eid);ownkey=sides['home'] if own==payload['home_team'] else sides['away']
            selected=payload.get('selected_minutes') if payload.get('minutes_available') else None
            appearance=dict(match_id=mid,player_id=pid,represented_team_key=ownkey,participation_status=payload['participation_status'],minutes=selected,reported_minutes=payload.get('reported_minutes'),derived_minutes=payload.get('derived_minutes'),minutes_precision=payload.get('original_minute_precision') or payload.get('minute_precision'),verification_status='RETAINED_PRIOR_TARGET_VERIFICATION',evidence_scope=payload.get('selected_evidence_quality') or payload.get('evidence_quality') or 'TARGET_SPECIFIC_LEGACY',revision=1,metadata_json=encoded({'legacy_payload_sha256':sha,'original_selection_method':payload.get('selection_method'),'flags':payload.get('flags',[]),'issues':payload.get('issues',[]),'original_numeric_claims':payload.get('original_numeric_claims',[]),'note':'Preserves prior target-specific acceptance; not fresh complete-squad verification'}));put_appearance(db,appearance)
            for ref in refs:db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(mid,pid,ref,'minutes_and_selection',encoded({'minutes':selected,'original_payload_sha256':sha}),1,'Retained original adjudication and evidence scope'))
            event=digest([pid,ownkey,legacy]);db.execute('INSERT OR IGNORE INTO national_affiliation_events VALUES(?,?,?,?,?,?,?,?,?,?)',(event,pid,ownkey,None,None,date,'EVIDENCED_HISTORICAL_MATCH_WINDOW','RETAINED_PRIOR_REFERENCE',eid,'MATCH_DATE_ONLY;NOT_CURRENT_CALLUP'))
            db.execute('INSERT INTO international_reference_bridge VALUES(?,?,?,?,?,?,?)',(pid,legacy,mid,sha,'INTEGRATED_PRIOR_TARGET_VERIFICATION',appearance['evidence_scope'],1));db.execute('RELEASE national_reference');summary.append(dict(player_id=str(pid),legacy_match_id=legacy,match_id=mid,minutes=selected,state='INTEGRATED_PRIOR_TARGET_VERIFICATION'))
        except (ValueError,KeyError,sqlite3.IntegrityError) as exc:
            db.execute('ROLLBACK TO national_reference');db.execute('RELEASE national_reference');db.execute('INSERT INTO international_reference_bridge VALUES(?,?,?,?,?,?,?)',(pid,legacy,None,sha,'PENDING:'+str(exc),'UNRESOLVED_LEGACY_REFERENCE',1));summary.append(dict(player_id=str(pid),legacy_match_id=legacy,state='PENDING',error=str(exc)))
    return summary


def import_reference_evidence_package(db,manifest,asset_ref):
    """Repair portable evidence links, leaving original legacy records/index unchanged."""
    if manifest.get('kind')!='LEGACY_NATIONAL_REFERENCE_EVIDENCE_PORTABILITY_REPAIR' or manifest.get('missing_source_ids'):raise ValueError('INVALID_LEGACY_EVIDENCE_PACKAGE')
    expected={(int(r['player_id']),r['match_id']):r['payload_sha256'] for r in manifest['references']}
    actual={(r['player_id'],r['match_id']):digest(r['payload_json']) for r in db.execute('SELECT * FROM validation_national_reference')}
    if expected!=actual:raise ValueError('REFERENCE_EVIDENCE_PACKAGE_BASELINE_MISMATCH')
    needed={rid for r in db.execute('SELECT payload_json FROM validation_national_reference') for rid in json.loads(r[0]).get('source_refs',[])}
    supplied={r['source_id'] for r in manifest['source_records']}
    if not needed<=supplied:raise ValueError('LEGACY_EVIDENCE_CLOSURE_INCOMPLETE')
    for row in manifest['source_records']:
        if row.get('verification_warning'):raise ValueError('LEGACY_EVIDENCE_BODY_REQUIRES_REVIEW')
        source=dict(row['original_evidence'],evidence_id=row['source_id'],retained_asset_ref=asset_ref+'#'+row['retained_body_path'],body_sha256=row['retained_body_sha256'])
        evidence(db,source,'ORIGINAL_TARGET_SPECIFIC_SCOPE_AND_REVIEWED_EXTRACT_QUALIFICATIONS','ORIGINAL_SOURCE_OR_REVIEWED_EXTRACT_AS_LABELLED',hold=1)
    return {'source_records':len(supplied),'legacy_payloads_unchanged':True,'asset_ref':asset_ref}
