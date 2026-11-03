import unittest
from squad_pipeline.quality_identity_debt import debt_obligations, source_url, EXISTING_PLAYER, EXISTING_FIXTURE, MISSING_PLAYER, MISSING_FIXTURE

class SourceDebtTests(unittest.TestCase):
    def debt(self, category, **kw):
        return debt_obligations(dict(failure_classification='SOURCE_MATCH_ID_MISMATCH', **kw), category)

    def test_existing_fact_source_failure_does_not_invalidate_fact(self):
        for category in (EXISTING_PLAYER, EXISTING_FIXTURE):
            r = self.debt(category)
            self.assertFalse(r['accepted_facts_invalidated'])
            self.assertFalse(r['blocks']['accepted_fact_accuracy'])
            self.assertFalse(r['blocks']['fixture_completeness'])
            self.assertFalse(r['blocks']['player_match_completeness'])
            self.assertEqual(list(r['obligations']), ['provider_fixture_identity'])

    def test_missing_player_is_unknown_not_nonparticipation(self):
        r = self.debt(MISSING_PLAYER, player_id=5)
        self.assertTrue(r['blocks']['player_match_completeness'])
        self.assertEqual(r['obligations']['participation']['status'], 'MISSING')
        self.assertNotIn('minutes_value', r)

    def test_unmapped_match_cannot_be_accepted_from_claim(self):
        r = self.debt(MISSING_FIXTURE)
        self.assertTrue(r['blocks']['fixture_completeness'])
        self.assertFalse(r['retry_authorized'])
        self.assertEqual(r['resolution'], 'UNRESOLVED')

    def test_unmapped_player_dependency_is_not_lost(self):
        r = self.debt(MISSING_FIXTURE, player_id=5)
        self.assertTrue(r['blocks']['fixture_completeness'])
        self.assertTrue(r['blocks']['player_match_completeness'])

    def test_roster_conflict_is_not_automatic_departure(self):
        r = debt_obligations({'failure_classification': 'ROSTER_IDENTITY_CONFLICT'}, 'UNKNOWN')
        self.assertEqual(r['obligations']['membership']['status'], 'MISSING')
        self.assertFalse(r['accepted_facts_invalidated'])

    def test_fixture_and_temporary_failures_remain_separate(self):
        a = debt_obligations({'failure_classification': 'FIXTURE_IDENTITY_CONFLICT'}, 'UNKNOWN')
        b = debt_obligations({'failure_classification': 'TEMPORARY_SOURCE_UNAVAILABLE'}, 'UNKNOWN')
        self.assertEqual(a['obligations']['provider_fixture_identity']['status'], 'CONFLICTED')
        self.assertEqual(b['obligations']['source_completeness']['status'], 'MISSING')

    def test_output_url_drops_credentials_and_query(self):
        self.assertEqual(source_url('https://user:secret@example.org/match?token=secret#123'), 'https://example.org/match#123')
