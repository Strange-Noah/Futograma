import unittest
from squad_pipeline.live_parse import certified_catalog_side,COMP
class ClubSourceLabelTests(unittest.TestCase):
 def fixture(self):
  c=dict(catalog_provider_id='7',catalog_home='examplefull',catalog_source=dict(url='https://www.skysports.com/example-results',sha256='a'*64),catalog_team_labels=dict(home=dict(provider_team_id='2',full_name='Example Full',short_name='Example'),away=dict(provider_team_id='3',full_name='Other',short_name='Other')))
  return dict(home_team='examplefull'),dict(url='https://www.skysports.com/football/a-vs-b/teams/7',catalog_candidate=c)
 def test_source_full_short_id_pair_is_accepted(self):
  f,r=self.fixture();self.assertTrue(certified_catalog_side('Example',f,'home',r))
 def test_wrong_match_id_rejected(self):
  f,r=self.fixture();r['url']=r['url'][:-1]+'8';self.assertFalse(certified_catalog_side('Example',f,'home',r))
 def test_wrong_side_or_duplicate_team_id_rejected(self):
  f,r=self.fixture();r['catalog_candidate']['catalog_team_labels']['away']['provider_team_id']='2';self.assertFalse(certified_catalog_side('Example',f,'home',r))
 def test_missing_hash_rejected(self):
  f,r=self.fixture();r['catalog_candidate']['catalog_source']['sha256']='';self.assertFalse(certified_catalog_side('Example',f,'home',r))
 def test_italian_serie_a_is_supported(self):self.assertEqual(COMP['Italian Serie A'],55)
