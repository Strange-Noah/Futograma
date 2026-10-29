"""Interfaces only; no fuzzy/name/shirt based cross-provider matching."""
from .core import digest

def propose_mapping(db,*,source_id,official_player_id,fotmob_player_id,full_name,date_of_birth,club_id,season,confidence,evidence_artifact_id):
    if confidence not in ('UNREVIEWED','VERIFIED'):raise ValueError('Explicit review status required')
    if not db.execute('SELECT 1 FROM raw_artifacts WHERE artifact_id=?',(evidence_artifact_id,)).fetchone():raise ValueError('Evidence artifact required')
    if not db.execute('SELECT 1 FROM acquisition_sources WHERE source_id=?',(source_id,)).fetchone():raise ValueError('Unknown provider namespace')
    old=db.execute("SELECT fotmob_player_id FROM provider_identities WHERE source_id=? AND official_player_id=? AND season=? AND review_status='VERIFIED'",(source_id,official_player_id,season)).fetchall()
    if any(r[0]!=fotmob_player_id for r in old):raise ValueError('Conflicting verified provider identity')
    key=digest(f'{source_id}:{official_player_id}:{fotmob_player_id}:{season}:{club_id}'.encode())
    db.execute('INSERT OR REPLACE INTO provider_identities VALUES(?,?,?,?,?,?,?,?,?,?,?)',(key,source_id,official_player_id,fotmob_player_id,full_name,date_of_birth,club_id,season,confidence,evidence_artifact_id,confidence))
    return key

def parse_official_snapshot(*args,**kwargs):
    raise NotImplementedError('Inspect the acquired provider file before implementing a source-specific parser')
