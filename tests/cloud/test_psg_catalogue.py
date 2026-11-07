import json,unittest
from squad_pipeline.live_parse import psg_catalogue
class PsgCatalogue(unittest.TestCase):
 url='https://www.psg.fr/en/mens-football/fixtures/season/2024-25'
 detail='/en/matches/mens-football/20242025/published-match'
 def data(self):
  t={'id':'team-id','Name_official':'Paris Saint-Germain','Gender':'Male','Local_section':'Pros'}
  m={'id':'native-id','id_statsperf':'native-id','home_team_id':t,'away_team_id':{'id':'other-id'},'timeUtc':'2024-10-01T18:45:00Z','localmatchdatetime':'2024-10-01T20:45:00+00:00','matchDate':'2024-10-01','match_status':'Played','home_team_score':1,'away_team_score':0}
  return {'seasonId':'2024-25','teamIds':['team-id'],'initialItems':[{'contentType':'matchSheet','route':{'path':self.detail+'/'},'properties':{'mdmMatchFixture':'native-id','mdmMatch':m}}]}
 def raw(self,d,visible=True):return '<script>self.__next_f.push('+json.dumps([1,'1:'+json.dumps(d)+'\n'])+')</script>'+('<a href="'+self.detail+'">Match Center</a>' if visible else '')
 def parse(self,d,visible=True):return psg_catalogue(self.raw(d,visible),self.url,9847,'2024-25')
 def test_native_id_and_original_conflicting_time_fields_are_preserved(self):
  r=self.parse(self.data())[0];self.assertEqual(r['provider_match_id'],'native-id');self.assertEqual(r['kickoff'],'2024-10-01T18:45:00Z');self.assertEqual(r['source_local_datetime'],'2024-10-01T20:45:00+00:00');self.assertFalse(r['canonical_adjudication'])
 def test_wrong_returned_season_is_not_historical_evidence(self):
  d=self.data();d['seasonId']='2026-27'
  with self.assertRaises(ValueError):self.parse(d)
 def test_native_id_disagreement_or_duplicate_is_held(self):
  d=self.data();d['initialItems'][0]['properties']['mdmMatchFixture']='other-id'
  with self.assertRaises(ValueError):self.parse(d)
  d=self.data();d['initialItems']*=2
  with self.assertRaises(ValueError):self.parse(d)
 def test_visible_coverage_and_first_team_scope_required(self):
  with self.assertRaises(ValueError):self.parse(self.data(),False)
  d=self.data();d['initialItems'][0]['properties']['mdmMatch']['home_team_id']['Gender']='Female'
  with self.assertRaises(ValueError):self.parse(d)
