"""Shared evidence obligations and additive verification history.

This module validates the shape and closure of supplied evidence obligations;
provider adapters remain responsible for proving the underlying source facts.
Repeated observations never constitute independent verification.
"""
import hashlib
import json
import re
from datetime import datetime

DIMENSIONS = ('club_identity', 'player_identity', 'fixture_identity',
              'provider_fixture_identity', 'membership', 'participation',
              'minutes', 'score_status', 'export')
STATES = ('DISCOVERED', 'PENDING_VERIFICATION', 'VERIFIED', 'STABLE', 'CONFLICTED')
STATUSES = ('SATISFIED', 'MISSING', 'CONFLICTED', 'NOT_APPLICABLE')
REVIEW_METHODS = ('INDEPENDENT_REPLAY', 'INDEPENDENT_SOURCE', 'AUTHORIZED_AUDIT')


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def valid_evidence(item):
    return (isinstance(item, dict) and bool(item.get('provider'))
            and bool(item.get('source_url'))
            and re.fullmatch('[0-9a-fA-F]{64}', str(item.get('sha256', ''))) is not None)


def obligation(status, evidence=(), reason=None):
    if status not in STATUSES:
        raise ValueError('UNKNOWN_OBLIGATION_STATUS')
    return dict(status=status, evidence=list(evidence), reason=reason)


def revalidation_plan(previous, observation_fingerprint, trigger=None, affected=None):
    if previous and previous.get('state') == 'STABLE' and previous.get('fingerprint') == observation_fingerprint and not trigger:
        return []
    required = list((previous or {}).get('required', DIMENSIONS))
    if affected is not None:
        return [key for key in required if key in affected]
    return required


def evaluate(obligations, required, observation_fingerprint, previous=None,
             stability_review=None, trigger=None):
    required = list(dict.fromkeys(required))
    if not observation_fingerprint:
        raise ValueError('OBSERVATION_FINGERPRINT_REQUIRED')
    normalized = {}
    for key, item in obligations.items():
        status = item.get('status')
        if status not in STATUSES:
            raise ValueError('UNKNOWN_OBLIGATION_STATUS:' + key)
        normalized[key] = dict(item, evidence=list(item.get('evidence', [])))
    missing, conflicts = [], []
    for key in required:
        item = normalized.get(key, {})
        status = item.get('status', 'MISSING')
        if status == 'CONFLICTED':
            conflicts.append(key)
        elif status != 'SATISFIED' or not item.get('evidence') or not all(valid_evidence(e) for e in item['evidence']):
            # Required obligations cannot disappear by claiming NOT_APPLICABLE.
            missing.append(key)
    review = stability_review or {}
    if not review and previous and previous.get('fingerprint') == observation_fingerprint and not trigger:
        review = previous.get('stability_review') or {}
    stable = (review.get('fingerprint') == observation_fingerprint
              and review.get('independent') is True and bool(review.get('reviewer_id'))
              and review.get('method') in REVIEW_METHODS
              and bool(review.get('evidence'))
              and all(valid_evidence(e) for e in review['evidence']))
    state = ('CONFLICTED' if conflicts else 'PENDING_VERIFICATION' if missing
             else 'DISCOVERED' if not required else 'STABLE' if stable else 'VERIFIED')
    plan = revalidation_plan(previous, observation_fingerprint, trigger) if previous else list(required)
    return dict(state=state, decision='ACCEPT' if state in ('VERIFIED', 'STABLE') else 'QUARANTINE',
                fingerprint=observation_fingerprint, required=required,
                obligations=normalized, missing=missing, conflicts=conflicts,
                stability_review=review if stable else None,
                revalidation=plan, trigger=trigger)


DDL = '''
CREATE TABLE IF NOT EXISTS quality_records(entity_key TEXT PRIMARY KEY,event_id TEXT NOT NULL,result_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quality_transitions(event_id TEXT PRIMARY KEY,entity_key TEXT NOT NULL,prior_event_id TEXT,observed_at TEXT NOT NULL,old_state TEXT,new_state TEXT NOT NULL,result_json TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS quality_history_no_update BEFORE UPDATE ON quality_transitions BEGIN SELECT RAISE(ABORT,'QUALITY_HISTORY_APPEND_ONLY'); END;
CREATE TRIGGER IF NOT EXISTS quality_history_no_delete BEFORE DELETE ON quality_transitions BEGIN SELECT RAISE(ABORT,'QUALITY_HISTORY_APPEND_ONLY'); END;
'''


def migrate(db):
    # executescript commits an enclosing ingestion transaction; avoid it.
    db.execute('CREATE TABLE IF NOT EXISTS quality_records(entity_key TEXT PRIMARY KEY,event_id TEXT NOT NULL,result_json TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS quality_transitions(event_id TEXT PRIMARY KEY,entity_key TEXT NOT NULL,prior_event_id TEXT,observed_at TEXT NOT NULL,old_state TEXT,new_state TEXT NOT NULL,result_json TEXT NOT NULL)')
    for operation in ('UPDATE', 'DELETE'):
        db.execute("CREATE TRIGGER IF NOT EXISTS quality_history_no_" + operation.lower()
                   + " BEFORE " + operation + " ON quality_transitions BEGIN SELECT RAISE(ABORT,'QUALITY_HISTORY_APPEND_ONLY'); END")


def load(db, entity_key):
    migrate(db)
    row = db.execute('SELECT result_json FROM quality_records WHERE entity_key=?', (entity_key,)).fetchone()
    return json.loads(row[0]) if row else None


def persist(db, entity_key, result, at):
    if result.get('state') not in STATES:
        raise ValueError('UNKNOWN_VERIFICATION_STATE')
    if datetime.fromisoformat(at.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('EXPLICIT_OBSERVATION_TIMEZONE_REQUIRED')
    migrate(db)
    prior = db.execute('SELECT event_id,result_json FROM quality_records WHERE entity_key=?', (entity_key,)).fetchone()
    # Computed work plans do not create false state transitions on repeat replay.
    semantic = {k: v for k, v in result.items() if k not in ('revalidation', 'trigger')}
    if prior:
        old = json.loads(prior[1])
        old_semantic = {k: v for k, v in old.items() if k not in ('revalidation', 'trigger')}
        if old_semantic == semantic:
            return dict(event_id=prior[0], changed=False, result=result)
    event = fingerprint([entity_key, prior[0] if prior else None, semantic])
    body = encoded(result)
    db.execute('INSERT INTO quality_transitions VALUES(?,?,?,?,?,?,?)',
               (event, entity_key, prior[0] if prior else None, at,
                json.loads(prior[1])['state'] if prior else None, result['state'], body))
    db.execute('INSERT INTO quality_records VALUES(?,?,?) ON CONFLICT(entity_key) DO UPDATE SET event_id=excluded.event_id,result_json=excluded.result_json', (entity_key, event, body))
    return dict(event_id=event, changed=True, result=result)
