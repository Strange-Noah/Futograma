import unittest,json
from squad_pipeline.roster_verification import seed_candidates
class ProviderHintTests(unittest.TestCase):
    def test_explicit_fotmob_id_in_other_provider_payload_is_discovered(self):
        candidates=seed_candidates([dict(player_id=7,display_name='Controlled Person')],[dict(player_id=7,provider_key='https://torlytics.de/spieler/controlled',identity_json=json.dumps(dict(fotmob_id=123,names=['Controlled Person'])))])
        self.assertEqual(candidates[0].ids()['FotMob'],{'123'})
