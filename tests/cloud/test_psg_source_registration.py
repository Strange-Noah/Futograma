import unittest,sqlite3
from squad_pipeline.identity_pipeline import DDL
from squad_pipeline.source_player_registration import register
from squad_pipeline.identity_registry import lookup
class PsgSourceRegistration(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.db.executescript(DDL+'CREATE TABLE players(player_id INTEGER PRIMARY KEY,display_name TEXT,identity_status TEXT);CREATE TABLE validation_player_aliases(provider_key TEXT PRIMARY KEY,player_id INTEGER,identity_json TEXT);')
 def tearDown(self):self.db.close()
 def player(self):return dict(provider_key='PSG_STATS_PERFORM:nativeid',provider_player_id='nativeid',provider_id_namespace='PSG_STATS_PERFORM',name='Observed Example Player')
 def source(self):return dict(url='https://www.psg.fr/en/matches/mens-football/20242025/published-match',sha256='a'*64,club_id=9847,provider='PSG',complete_squad=True,source_identity_verified=True,provider_match_id='native-match',native_player_id='nativeid',response_timestamp='2025-01-01')
 def test_actual_source_namespace_reuses_existing_writer_without_management(self):
  pid=register(self.db,9847,self.player(),self.source());self.assertLess(pid,0);self.assertEqual(lookup(self.db,9847,self.player()),pid);self.assertIsNone(lookup(self.db,9847,dict(name=self.player()['name'],provider_key='sky-name:9847:observed')));self.assertEqual(self.db.execute('SELECT provider FROM verified_identity_links').fetchone()[0],'PSG_STATS_PERFORM')
 def test_numeric_cms_profile_id_is_not_the_match_player_namespace(self):
  p=self.player();p['provider_key']='PSG:7'
  with self.assertRaises(ValueError):register(self.db,9847,p,self.source())
 def test_incomplete_or_wrong_native_proof_is_rejected(self):
  for field,value in [('complete_squad',False),('native_player_id','different'),('club_id',9825),('url','https://www.psg.fr/joueurs/observed-example-player')]:
   s=self.source();s[field]=value
   with self.assertRaises(ValueError):register(self.db,9847,self.player(),s)
 def test_possible_existing_person_is_held_not_merged(self):
  self.db.execute('INSERT INTO players VALUES(1,?,?)',(self.player()['name'],'EXISTING'))
  with self.assertRaisesRegex(ValueError,'POSSIBLE_EXISTING_CANONICAL'):register(self.db,9847,self.player(),self.source())
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM players').fetchone()[0],1)
