"""Regression coverage for historical delivery audit projection."""
import sqlite3
import unittest

from ops.cloud.historical_delivery import audit_followup, sync_delivery_matrix


class HistoricalDeliveryAuditTests(unittest.TestCase):
    def test_shared_fixture_request_keeps_independent_claims(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('''CREATE TABLE canonical_fixture_sources (
                domain TEXT, match_id TEXT, provider TEXT,
                provider_match_id TEXT, evidence_json TEXT)''')
            db.execute('INSERT INTO canonical_fixture_sources VALUES (?,?,?,?,?)', (
                'CLUB', 'canonical-1', 'Sky', '123',
                '{"url":"https://www.skysports.com/football/home-vs-away/teams/123"}'))
            audit = dict(
                final_opportunities=[
                    dict(domain='CLUB', canonical='canonical-1', on='2026-04-04'),
                    dict(domain='CLUB', canonical='canonical-2', on='2026-04-11'),
                ],
                resolutions=[
                    dict(trigger='R4', domain='CLUB', canonical='canonical-1',
                         state='SOURCE_WORK_REQUIRED', claim_key='r4-one'),
                    dict(trigger='R3', domain='CLUB',
                         opportunities=['canonical-1', 'canonical-2'],
                         state='SOURCE_WORK_REQUIRED', claim_key='r3-one'),
                    dict(trigger='R4', domain='CLUB', canonical='canonical-1',
                         state='RESOLVED', claim_key='resolved'),
                ])
            result = audit_followup(db, audit)

        self.assertEqual(result['exact_fixture_groups'], 2)
        self.assertEqual(len(result['requests']), 1)
        self.assertEqual(result['requests'][0]['claims'], ['r3-one', 'r4-one'])
        self.assertEqual(result['unmapped'][0]['claims'], ['r3-one'])

    def test_matrix_sync_does_not_require_new_rows(self):
        row = dict(club_id=9823, unit='2025/26-H2', R1_R6='NOT TESTED / UNKNOWN',
                   data_accepted=False, canonical_matches=3, canonical_player_rows=59,
                   fixture_catalogue_completion='PASS / VERIFIED')
        audit = dict(stages=['R1', 'R2', 'R5', 'R3_R4', 'R6'],
                     metrics={'opportunities': 100},
                     resolution_states={'SOURCE_WORK_REQUIRED': 10, 'RESOLVED': 2})
        report = dict(unit='9823:2025/26-H2', audit=audit, run='test-action',
                      club_coverage_columns=['competition_id', 'matches', 'appearances',
                                             'numeric_minutes', 'missing_kickoff_rows'],
                      club_coverage=[[209, 3, 59, 59, 0]])
        sync_delivery_matrix(dict(units=[row]), report)
        self.assertEqual(row['R1_R6'], 'EXECUTED / OPEN: 10 unresolved audit obligations')
        self.assertEqual(row['club_competition_coverage'][0]['numeric_minutes'], 59)
        self.assertFalse(row['data_accepted'])

        audit['resolution_states'] = {}
        sync_delivery_matrix(dict(units=[row]), report)
        self.assertEqual(row['R1_R6'], 'EXECUTED / WHOLE-UNIT ACCEPTANCE PENDING')
        self.assertFalse(row['data_accepted'])
