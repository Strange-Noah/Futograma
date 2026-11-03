"""Source-task obligations are independent of previously accepted fact contracts.

No task is resolved, retried or used to manufacture an appearance by this review.
"""
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .quality_lifecycle import obligation

EXISTING_PLAYER = 'EXISTING_PLAYER_FACT_VERIFICATION_DEBT'
EXISTING_FIXTURE = 'EXISTING_FIXTURE_VERIFICATION_DEBT'
MISSING_PLAYER = 'EXISTING_FIXTURE_PLAYER_FACT_UNVERIFIED'
MISSING_FIXTURE = 'NO_CANONICAL_SOURCE_UNVERIFIED'


def debt_obligations(task, current_fact_category):
    classification = task.get('failure_classification')
    obligations = {}
    blocks = dict(source_provenance=True, fixture_completeness=False,
                  player_match_completeness=False, accepted_fact_accuracy=False)
    if classification == 'SOURCE_MATCH_ID_MISMATCH':
        obligations['provider_fixture_identity'] = obligation('CONFLICTED', reason='STRICT_PROVIDER_MATCH_OR_LINEUP_ID_CHECK_FAILED')
        if current_fact_category == MISSING_FIXTURE:
            obligations['fixture_identity'] = obligation('MISSING', reason='NO_VERIFIED_CANONICAL_MAPPING_FOR_THIS_TASK')
            blocks['fixture_completeness'] = True
            if task.get('player_id') is not None:
                blocks['player_match_completeness'] = True
                obligations['participation'] = obligation('MISSING', reason='SOURCE_CLAIM_NOT_VERIFIED;NOT_PROOF_OF_APPEARANCE')
                obligations['minutes'] = obligation('MISSING', reason='NO_ACCEPTED_PARTICIPATION_MINUTES')
        elif current_fact_category == MISSING_PLAYER:
            blocks['player_match_completeness'] = True
            obligations['participation'] = obligation('MISSING', reason='SOURCE_CLAIM_NOT_VERIFIED;NOT_PROOF_OF_APPEARANCE')
            obligations['minutes'] = obligation('MISSING', reason='NO_ACCEPTED_PARTICIPATION_MINUTES')
        elif current_fact_category not in (EXISTING_PLAYER, EXISTING_FIXTURE):
            obligations['fixture_identity'] = obligation('MISSING', reason='FACT_COVERAGE_NOT_ESTABLISHED')
    elif classification == 'ROSTER_IDENTITY_CONFLICT':
        obligations['player_identity'] = obligation('CONFLICTED', reason='SOURCE_PLAYER_BRIDGE_UNRESOLVED')
        obligations['membership'] = obligation('MISSING', reason='DO_NOT_INFER_CLUB_MEMBERSHIP_FROM_SOURCE_NAME')
    elif classification == 'FIXTURE_IDENTITY_CONFLICT':
        obligations['provider_fixture_identity'] = obligation('CONFLICTED', reason='CONTRADICTORY_FIXTURE_OBSERVATIONS')
    else:
        obligations['source_completeness'] = obligation('MISSING', reason='SOURCE_EVIDENCE_UNAVAILABLE_OR_UNCLASSIFIED')
    return dict(scope='SOURCE_TASK_ONLY', fact_category=current_fact_category,
                obligations=obligations, required=list(obligations), blocks=blocks,
                accepted_facts_invalidated=False, retry_authorized=False,
                resolution='UNRESOLVED', classification=classification)


def source_url(url):
    p = urlsplit(url or '')
    return urlunsplit((p.scheme, p.hostname or '', p.path, '', p.fragment))


def review(snapshot):
    snapshot = Path(snapshot)
    before = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    db = sqlite3.connect('file:' + str(snapshot.resolve()) + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    rows = []
    for table in ('match_retry_queue', 'international_acquisition_queue'):
        for row in db.execute("SELECT * FROM " + table + " WHERE state!='RESOLVED'"):
            task = dict(row)
            expected = task.get('provider_match_id') or task.get('provider_entity_id')
            category = 'FACT_COVERAGE_NOT_ESTABLISHED'
            mid = task.get('canonical_match_id') or task.get('match_id')
            has_fact = False
            if table == 'international_acquisition_queue' and task.get('failure_classification') == 'SOURCE_MATCH_ID_MISMATCH':
                fixture = json.loads(task['payload_json']).get('fixture', {})
                expected = fixture.get('provider_match_id', expected)
                mapped = db.execute('SELECT match_id FROM international_match_provider_ids WHERE provider=? AND provider_match_id=?', (task['provider'], expected)).fetchone()
                mid = mapped[0] if mapped else None
                has_fact = bool(mid and task.get('player_id') is not None and db.execute('SELECT 1 FROM international_appearances WHERE match_id=? AND player_id=?', (mid, task['player_id'])).fetchone())
                category = EXISTING_PLAYER if has_fact else MISSING_PLAYER if mid and task.get('player_id') is not None else EXISTING_FIXTURE if mid else MISSING_FIXTURE
            rows.append(dict(queue=table, task_key=task.get('task_key') or task.get('match_key'),
                             provider=task.get('provider'), expected_id=expected,
                             player_id=task.get('player_id'), canonical_match_id=mid,
                             existing_player_fact=has_fact, source_url=source_url(task.get('url')),
                             source_state=task.get('state'), **debt_obligations(task, category)))
    db.close()
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == before
    mismatches = [r for r in rows if r['classification'] == 'SOURCE_MATCH_ID_MISMATCH']
    return dict(schema_version=1, snapshot_sha256=before, snapshot_unchanged=True,
                total_tasks=len(rows), classifications=dict(Counter(r['classification'] for r in rows)),
                mismatch_tasks=len(mismatches), mismatch_fact_categories=dict(Counter(r['fact_category'] for r in mismatches)),
                source_tasks_resolved=0, accepted_facts_changed=0, rows=rows)
