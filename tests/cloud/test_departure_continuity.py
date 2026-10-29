import unittest
from squad_pipeline.identity_pipeline import management_state,Record
class DepartureContinuityTests(unittest.TestCase):
    def record(self):return Record('https://official.example/player','TEST','h1','Official',['Controlled Person'],official_clubs=['1'],scope='FIRST_TEAM_CARD',observed_on='2026-10-29',authority='OFFICIAL')
    def test_prior_loan_out_survives_stale_ownership_profile(self):
        prior=dict(state='EXCLUDED',included=False,status='LOAN_OUT',effective_from='2026-08-01');self.assertEqual(management_state([self.record()],'2026-10-29',prior)['state'],'EXCLUDED')
    def test_prior_transfer_survives_source_failure(self):
        prior=dict(state='EXCLUDED',included=False,status='TRANSFERRED_OUT',effective_from='2026-08-01');self.assertEqual(management_state([],'2026-10-29',prior)['state'],'EXCLUDED')
    def test_expired_loan_does_not_restore_on_profile_presence(self):
        prior=dict(state='UNKNOWN',included=False,status='RETURN_UNVERIFIED');self.assertEqual(management_state([self.record()],'2026-10-29',prior)['status'],'RETURN_UNVERIFIED')
