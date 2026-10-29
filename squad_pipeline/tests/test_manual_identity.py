import copy,json,unittest
from unittest.mock import patch
from squad_pipeline.core import BASE,read
from squad_pipeline.manual_identity import apply_manual

class ManualAuthorizationTests(unittest.TestCase):
    def fixtures(self):
        parsed=read(BASE/'imports/barcelona/2026-27/H1/parsed_stage.json');row=next(r for r in parsed['rows'] if r['name']=='Cancelo');result=dict(status='UNRESOLVED',reason='WEAK_NAME_OR_UNSUPPORTED_EXPANSION',player_id=None,name_fotmob=None,evidence=dict(policy='roster_identity_v2',official_attributes=copy.deepcopy(row),candidates=[dict(fotmob_player_id=361757,display_name='João Cancelo',strong=False)]));return row,result,parsed['snapshot']
    def test_user_review_is_manual_not_automatic(self):
        row,result,snapshot=self.fixtures();original=copy.deepcopy(row);r=apply_manual([row],[result],snapshot)[0];self.assertEqual(r['status'],'CONFIRMED_MANUAL');self.assertEqual(r['player_id'],361757);self.assertEqual(row,original);self.assertFalse(r['evidence']['automatic_review_before_manual_decision']['automatic_threshold_satisfied']);self.assertFalse(r['evidence']['candidates'][0]['strong']);self.assertEqual(r['evidence']['manual_decision']['decision_provenance'],'EXPLICIT_USER_AUTHORIZED_MANUAL_IDENTITY_REVIEW')
    def test_wrong_official_entry_cannot_consume_authorization(self):
        row,result,snapshot=self.fixtures();row['name']='Different player'
        with self.assertRaisesRegex(ValueError,'scope'):apply_manual([row],[result],snapshot)
    def test_cannot_replace_different_confirmed_id(self):
        row,result,snapshot=self.fixtures();result['player_id']=999
        with self.assertRaisesRegex(ValueError,'conflicts'):apply_manual([row],[result],snapshot)
    def test_authorization_artifact_hash_required(self):
        row,result,snapshot=self.fixtures();decisions=read(BASE/'config/manual_identity_decisions.json');decisions[0]['authorization_sha256']='0'*64
        with patch('squad_pipeline.manual_identity.read',return_value=decisions):
            with self.assertRaisesRegex(ValueError,'corrupt'):apply_manual([row],[result],snapshot)
    def test_unrelated_snapshot_unchanged(self):
        row,result,snapshot=self.fixtures();snapshot['snapshot_id']='different';r=apply_manual([row],[result],snapshot)[0];self.assertEqual(r['status'],'UNRESOLVED');self.assertIsNone(r['player_id'])
