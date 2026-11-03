import sqlite3
import unittest
from squad_pipeline import quality_lifecycle as q

E = dict(provider='Provider', source_url='https://example.org/retained', sha256='a' * 64)

class VerificationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.obligations = {'fixture_identity': q.obligation('SATISFIED', [E]),
                            'score_status': q.obligation('SATISFIED', [E])}
        self.required = list(self.obligations)
        self.fp = q.fingerprint({'identity': 1, 'score': [2, 1]})
        self.review = dict(fingerprint=self.fp, independent=True, reviewer_id='replay-runner',
                           method='INDEPENDENT_REPLAY', evidence=[E])

    def evaluate(self, **kw):
        return q.evaluate(self.obligations, self.required, self.fp, **kw)

    def test_complete_identity_is_not_complete_fixture(self):
        self.obligations['score_status'] = q.obligation('MISSING', reason='NO_ACCEPTED_SCORE')
        r = self.evaluate(stability_review=self.review)
        self.assertEqual(r['state'], 'PENDING_VERIFICATION')
        self.assertEqual(r['missing'], ['score_status'])

    def test_unproven_populated_fields_are_pending(self):
        self.obligations['score_status'] = q.obligation('SATISFIED', [{'provider': 'P'}])
        self.assertEqual(self.evaluate()['state'], 'PENDING_VERIFICATION')

    def test_repetition_never_promotes(self):
        prior = None
        for _ in range(8):
            prior = self.evaluate(previous=prior)
        self.assertEqual(prior['state'], 'VERIFIED')

    def test_independent_review_closes_obligations(self):
        r = self.evaluate(stability_review=self.review)
        self.assertEqual(r['state'], 'STABLE')
        repeated = self.evaluate(previous=r)
        self.assertEqual(repeated['state'], 'STABLE')
        self.assertEqual(repeated['revalidation'], [])

    def test_changed_evidence_reopens_stable(self):
        previous = self.evaluate(stability_review=self.review)
        changed = q.evaluate(self.obligations, self.required, 'new-fingerprint', previous=previous)
        self.assertEqual(changed['state'], 'VERIFIED')
        self.assertEqual(changed['revalidation'], self.required)
        self.assertIsNone(changed['stability_review'])

    def test_explicit_audit_reopens_without_reusing_old_review(self):
        prior = self.evaluate(stability_review=self.review)
        r = self.evaluate(previous=prior, trigger='PERIODIC_AUDIT')
        self.assertEqual(r['state'], 'VERIFIED')
        self.assertEqual(r['revalidation'], self.required)

    def test_conflicts_block_even_independent_review(self):
        self.obligations['fixture_identity'] = q.obligation('CONFLICTED', [E])
        self.assertEqual(self.evaluate(stability_review=self.review)['state'], 'CONFLICTED')

    def test_required_not_applicable_cannot_hide_gap(self):
        self.obligations['score_status'] = q.obligation('NOT_APPLICABLE', [E])
        self.assertEqual(self.evaluate()['decision'], 'QUARANTINE')

    def test_changed_membership_can_target_revalidation(self):
        prior = self.evaluate(stability_review=self.review)
        self.assertEqual(q.revalidation_plan(prior, self.fp, 'MEMBERSHIP_CHANGED', ['score_status']), ['score_status'])

    def test_persistence_repeat_and_history_append_only(self):
        db = sqlite3.connect(':memory:')
        result = self.evaluate(stability_review=self.review)
        a = q.persist(db, 'fixture:1', result, '2026-11-03T00:00:00Z')
        b = q.persist(db, 'fixture:1', self.evaluate(previous=result), '2026-11-04T00:00:00Z')
        self.assertTrue(a['changed']); self.assertFalse(b['changed'])
        self.assertEqual(db.execute('SELECT COUNT(*) FROM quality_transitions').fetchone()[0], 1)
        self.obligations['score_status'] = q.obligation('CONFLICTED', [E])
        c = q.persist(db, 'fixture:1', self.evaluate(previous=result, trigger='CORRECTION'), '2026-11-05T00:00:00Z')
        self.assertTrue(c['changed'])
        self.assertEqual(q.load(db, 'fixture:1')['state'], 'CONFLICTED')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'APPEND_ONLY'):
            db.execute('DELETE FROM quality_transitions')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'APPEND_ONLY'):
            db.execute("UPDATE quality_transitions SET new_state='STABLE'")

    def test_additive_schema_does_not_commit_callers_transaction(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE existing_facts(id TEXT)')
        db.execute("INSERT INTO existing_facts VALUES('keep')")
        q.migrate(db)
        db.rollback()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM existing_facts').fetchone()[0], 0)

    def test_bad_timezone_rejected(self):
        with self.assertRaisesRegex(ValueError, 'TIMEZONE'):
            q.persist(sqlite3.connect(':memory:'), 'x', self.evaluate(), '2026-11-03T00:00:00')
