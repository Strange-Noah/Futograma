"""Additive quality bridge for normal club ingestion; caller owns transaction.

Legacy accepted facts are retained while new verification obligations remain
explicit. Populated old columns are never promoted to field-level evidence.
"""
import json
from .canonical_fixture import score_fields,metadata,club_period,encoded,digest
from .live_parse import Pending


def require_complete_score(fixture):
    state=str(fixture.get('match_state') or '').upper()
    if state in {'COMPLETED','FINISHED','FT'} and set(score_fields(fixture.get('score')))!={'home_score','away_score'}:
        raise Pending('COMPLETED_SCORE_INCOMPLETE;TARGETED_SCORE_RETRY_REQUIRED')


def audit_legacy_fixture(db,fixture,sheet,on):
    from . import quality_pipeline as pipeline
    pipeline.migrate(db);m=metadata(db,fixture['match_id']) or {};source=sheet['source'];scores=score_fields(fixture.get('score'));fields=m.get('fields',{})
    row=db.execute('SELECT edition FROM canonical_fixtures WHERE match_id=?',(fixture['match_id'],)).fetchone()
    f=dict(match_id=fixture['match_id'],provider=sheet.get('provider'),provider_match_id=str(source.get('directory_match_id') or source.get('provider_match_id') or source.get('url') or ''),actual_provider_match_id=source.get('actual_provider_match_id'),home=dict(id=m.get('home_team_id'),name=m.get('home_team')),away=dict(id=m.get('away_team_id'),name=m.get('away_team')),competition_key=str(fixture.get('competition_id') or ''),season=row[0] if row else fixture.get('competition_edition'),kickoff_utc=fixture.get('kickoff'),status=fixture.get('match_state'),home_score=scores.get('home_score'),away_score=scores.get('away_score'))
    # Only adapter-supplied field proofs may satisfy the new contract. A source
    # hash for a whole legacy row is not proof of every field or participant.
    proof=dict(field_evidence=source.get('field_evidence') or {})
    result=pipeline.process_fixture(db,f,proof,on,mode='INCREMENTAL')
    db.execute('CREATE TABLE IF NOT EXISTS quality_live_fixture_reviews(review_id TEXT PRIMARY KEY,match_id TEXT NOT NULL,payload_json TEXT NOT NULL)')
    review=dict(state='VERIFIED' if result['decision']=='ACCEPT' else 'LEGACY_PENDING_REVALIDATION',fixture=f,contract=result,historical_facts_preserved=True)
    db.execute('INSERT OR IGNORE INTO quality_live_fixture_reviews VALUES(?,?,?)',(digest(review),fixture['match_id'],encoded(review)))
    return review


def audit_membership(db,tid,players,source,on):
    from .quality_pipeline import reconcile_membership
    return reconcile_membership(db,tid,[p['player_id'] for p in players],on,source.get('membership_observations') or (),mode='INCREMENTAL')


def audit_participation(db,mid,row,players,source,on):
    from .quality_pipeline import verify_participation
    p=next((p for p in players if p['player_id']==row['player_id']),{})
    typed=dict(provider_player_id=p.get('provider_key'),participation_status=row['status'],minutes=row['minutes'])
    result=verify_participation(db,mid,typed,source,on,mode='INCREMENTAL')
    if result['state']=='CONFLICTED':raise ValueError('PARTICIPATION_CONTRACT_CONFLICT')
    return result
