import unittest,sqlite3,json
from squad_pipeline.identity_pipeline import DDL,Record,persist_verified_alias
from squad_pipeline.identity_registry import lookup
class SourceNamespaceTests(unittest.TestCase):
 def setUp(self):self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.db.executescript(DDL)
 def tearDown(self):self.db.close()
 def record(self,method):
  record=Record('https://torlytics.de/spiel/example','a'*64,'validated-lineup','Torlytics',['Example Player'],provider_ids={'Torlytics':'https://torlytics.de/spieler/example-player'},scope='SOURCE_IDENTITY_ONLY')
  result=dict(identity_status='VERIFIED',player_id='-7',identity_proof=[dict(method=method)],source_record=record.evidence());persist_verified_alias(self.db,record,result,'2026-10-30')
 def test_new_source_identity_remains_reusable_only_by_real_stable_key(self):
  self.record('NEW_STABLE_SOURCE_ENTITY;NOT_A_CROSS_PROVIDER_MERGE')
  self.assertEqual(lookup(self.db,1,dict(name='Example Player',provider_key='https://torlytics.de/spieler/example-player')),-7)
  self.assertIsNone(lookup(self.db,1,dict(name='Example Player',provider_key='sky-name:1:exampleplayer')))
 def test_independently_verified_alias_remains_reusable(self):
  self.record('INDEPENDENT_DOB_NAME_OFFICIAL_AFFILIATION');self.assertEqual(lookup(self.db,1,dict(name='Example Player',provider_key='sky-name:1:exampleplayer')),-7)
