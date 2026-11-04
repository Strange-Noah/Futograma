import unittest
from squad_pipeline.quality_membership import evaluate as membership
from squad_pipeline.quality_lifecycle import evaluate, obligation

E = dict(provider='Official', source_url='https://club.example/record', sha256='a'*64)

class HardeningTests(unittest.TestCase):
    def test_unproven_or_inverted_loan_interval_cannot_certify_membership(self):
        for start, end in [(None, '2027-01-01'), ('2026-08-01', '2026-08-01'), ('2026-08-01', '2026-07-01')]:
            with self.subTest(start=start, end=end):
                observation = dict(assertion='LOAN_IN', identity_status='VERIFIED',
                    verification_status='VERIFIED', source_scope='VERIFIED_TRANSFER',
                    effective_from=start, end_exclusive=end, evidence=[E])
                result = membership({}, [observation], '2026-11-04')
                self.assertFalse(result['included'])
                self.assertEqual(result['events'], [])
                self.assertIn('HISTORICAL_INTERVAL_UNVERIFIED', result['pending_reasons'])

    def test_expanding_scope_requires_new_independent_review(self):
        review = dict(fingerprint='fixture', independent=True, reviewer_id='audit',
                      method='INDEPENDENT_REPLAY', evidence=[E])
        old = evaluate({'fixture_identity': obligation('SATISFIED', [E])},
                       ['fixture_identity'], 'fixture', stability_review=review)
        self.assertEqual(old['state'], 'STABLE')
        obligations = dict(old['obligations'], minutes=obligation('SATISFIED', [E]))
        new = evaluate(obligations, ['fixture_identity', 'minutes'], 'fixture', previous=old)
        self.assertEqual(new['state'], 'VERIFIED')
        self.assertIsNone(new['stability_review'])
        unchanged = evaluate(old['obligations'], old['required'], 'fixture', previous=old)
        self.assertEqual(unchanged['state'], 'STABLE')

    def test_changed_evidence_requires_new_review(self):
        review = dict(fingerprint='fixture', independent=True, reviewer_id='audit',
                      method='INDEPENDENT_REPLAY', evidence=[E])
        old = evaluate({'fixture_identity': obligation('SATISFIED', [E])},
                       ['fixture_identity'], 'fixture', stability_review=review)
        changed = dict(E, sha256='b'*64)
        new = evaluate({'fixture_identity': obligation('SATISFIED', [changed])},
                       old['required'], 'fixture', previous=old)
        self.assertEqual(new['state'], 'VERIFIED')
