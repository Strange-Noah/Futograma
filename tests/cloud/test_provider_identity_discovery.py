import unittest,json
from squad_pipeline.provider_identity_discovery import team_identity_records,verify_team_identities
from squad_pipeline.identity_pipeline import Record
class ProviderDiscoveryTests(unittest.TestCase):
 def raw(self,dob='2000-01-02'):
  return '<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'fallback':{'team-9':{'details':{'id':9},'squad':{'squad':[{'title':'coach','members':[{'id':2,'name':'Coach','dateOfBirth':dob}]},{'title':'forwards','members':[{'id':700,'name':'Example Player','dateOfBirth':dob}]}]}}}}}})+'</script>'
 def dossier(self):
  r=Record('https://www.uefa.com/person','official-sha','birth','UEFA',['Example Player'],birth_date='2000-01-02',official_clubs=['9'],authority='OFFICIAL',provider_ids={'UEFA':'45'})
  return [dict(player_id='-45',name='Example Player',identity_status='VERIFIED',verified_source_records=[r.evidence()])]
 def test_negative_canonical_id_preserved(self):
  records=verify_team_identities(self.raw(),dict(url='https://www.fotmob.com/teams/9/squad',sha256='raw',response_timestamp='2026-10-30'),9,self.dossier());self.assertEqual(len(records),1);record,result=records[0];self.assertEqual(result['player_id'],'-45');self.assertEqual(result['identity_status'],'VERIFIED');self.assertEqual(record.scope,'IDENTITY_ONLY');self.assertIn('INDEPENDENT_DOB',str(result['identity_proof']))
 def test_name_alone_never_maps(self):
  result=verify_team_identities(self.raw('2001-01-02'),dict(url='https://www.fotmob.com/teams/9/squad',sha256='raw'),9,self.dossier())[0][1];self.assertNotEqual(result['identity_status'],'VERIFIED')
 def test_wrong_team_rejected(self):
  with self.assertRaisesRegex(ValueError,'EXACT_TEAM'):team_identity_records(self.raw(),dict(url='x',sha256='raw'),10)
 def test_previously_unresolved_official_biography_can_be_cross_verified(self):
  raw=self.dossier();record=raw[0]['verified_source_records'][0];raw[0]['verified_source_records']=[];raw[0]['identity_status']='UNRESOLVED';result=verify_team_identities(self.raw(),dict(url='https://www.fotmob.com/teams/9/squad',sha256='new-raw'),9,raw,[dict(source_record=record)])[0][1];self.assertEqual(result['identity_status'],'VERIFIED');self.assertEqual(result['player_id'],'-45');self.assertFalse(any(p['method']=='SHARED_STABLE_PROVIDER_ID' for p in result['identity_proof']))
