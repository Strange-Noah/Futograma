import unittest,json
from squad_pipeline.international_discovery import team_identity_hints
class TeamIdentityIndexTests(unittest.TestCase):
 def raw(self,team=8456,national=False,duplicate=False):
  player={'id':7,'name':'Observed Person','dateOfBirth':'2000-01-01','role':{'key':'defender'}}
  d={'details':{'id':team},'squad':{'isNationalTeam':national,'squad':[{'title':'coach','members':[{'id':99,'name':'Coach'}]},{'title':'defenders','members':[player,player] if duplicate else [player]}]}}
  return '<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'fallback':{'team-'+str(team):d,'unrelated':{'id':9}}}}})+'</script>'
 def test_exact_team_index_excludes_coach_and_asserts_no_membership(self):
  rows=team_identity_hints(self.raw(),{'url':'https://www.fotmob.com/teams/8456/fixtures'},'8456');self.assertEqual(len(rows),1);self.assertFalse(rows[0]['historical_membership_asserted']);self.assertEqual(rows[0]['provider_player_id'],'7')
 def test_other_request_returned_team_and_national_scope_rejected(self):
  for raw,url in [(self.raw(),'https://www.fotmob.com/teams/9825/fixtures'),(self.raw(team=9825),'https://www.fotmob.com/teams/8456/fixtures'),(self.raw(national=True),'https://www.fotmob.com/teams/8456/fixtures')]:
   with self.assertRaises(ValueError):team_identity_hints(raw,{'url':url},'8456')
 def test_duplicate_source_ids_cannot_become_two_candidates(self):
  with self.assertRaisesRegex(ValueError,'DUPLICATE'):team_identity_hints(self.raw(duplicate=True),{'url':'https://www.fotmob.com/teams/8456/fixtures'},'8456')

 def test_other_origin_cannot_claim_native_team_identity(self):
  for url in ['https://attacker.example/teams/8456/fixtures','http://www.fotmob.com/teams/8456/fixtures']:
   with self.assertRaisesRegex(ValueError,'ORIGIN'):team_identity_hints(self.raw(),{'url':url},'8456')
