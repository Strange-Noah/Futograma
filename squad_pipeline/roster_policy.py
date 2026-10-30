"""Roster cohorts are independent of identity and of representative-team appearances."""
import json
from .managed_roster import decision
from .identity_pipeline import management_state,Record

def classify(dossier,prior=None):
    management=dossier.get('management') or prior or {}
    state=management.get('state')
    if state=='INCLUDED':return dict(management,cohort='FIRST_TEAM',collect_internationals=True)
    if state=='EXCLUDED' or management.get('status')=='RETURN_UNVERIFIED':return dict(management,cohort='EXCLUDED' if state=='EXCLUDED' else 'UNKNOWN',collect_internationals=False)
    academy=dossier.get('academy_affiliation_status','')
    reliable=academy in {'VERIFIED_CURRENT_ACADEMY_EVIDENCE','VERIFIED_LEGACY_ACADEMY_ASSOCIATION'} or bool(management.get('academy_affiliation'))
    if reliable:return dict(state='YOUTH',included=False,status='YOUTH',cohort='YOUTH',collect_internationals=False,sources=management.get('academy_affiliation',[]),reason='Verified youth/development affiliation; no continuous first-team-management proof required')
    return dict(management,state=state or 'UNKNOWN',included=False,cohort='UNKNOWN',collect_internationals=False)

def latest(db):
    rows={}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='roster_verification_dossiers'").fetchone():
        for r in db.execute('SELECT * FROM roster_verification_dossiers ORDER BY observed_at,dossier_id'):rows[(r['club_id'],r['player_id'])]=json.loads(r['payload_json'])
    return rows

def roster_decision(db,club_id,player_id,on,dossiers=None):
    prior=decision(db,club_id,player_id,on);d=(dossiers if dossiers is not None else latest(db)).get((club_id,player_id),{})
    # Explicit current events win over a historic dossier; age or U21 eligibility never downgrades a first-team player.
    if prior['state'] in ('INCLUDED','EXCLUDED') or prior.get('status')=='RETURN_UNVERIFIED':return classify(dict(d,management=prior))
    return classify(d,prior)

def population(db,on):
    dossiers=latest(db);result=[]
    keys=set(dossiers)
    keys|={(r[0],r[1]) for r in db.execute('SELECT club_id,player_id FROM validation_roster_membership')}
    for cid,pid in sorted(keys):result.append(dict(club_id=cid,player_id=pid,**roster_decision(db,cid,pid,on,dossiers)))
    return result


def materialize(db,on):
    db.execute("CREATE TABLE IF NOT EXISTS roster_cohort_current(club_id INTEGER,player_id INTEGER,cohort TEXT NOT NULL CHECK(cohort IN ('FIRST_TEAM','YOUTH','UNKNOWN','EXCLUDED')),classified_on TEXT,policy TEXT,decision_json TEXT,PRIMARY KEY(club_id,player_id))")
    rows=population(db,on)
    for r in rows:
        db.execute('INSERT INTO roster_cohort_current VALUES(?,?,?,?,?,?) ON CONFLICT(club_id,player_id) DO UPDATE SET cohort=excluded.cohort,classified_on=excluded.classified_on,policy=excluded.policy,decision_json=excluded.decision_json',(r['club_id'],r['player_id'],r['cohort'],on,'FIRST_TEAM_AND_YOUTH_V1',json.dumps(r,ensure_ascii=False,sort_keys=True)))
    return rows
