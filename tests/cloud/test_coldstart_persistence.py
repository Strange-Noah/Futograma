"""Controlled persistence boundaries; no real football facts."""
import sqlite3,unittest,json
from squad_pipeline.core import ROOT
from squad_pipeline import quality_pipeline as pipeline,quality_lifecycle as life
from ops.cloud.coldstart_acceptance import apply_appearance
class PersistenceBoundaries(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.db.executescript((ROOT/'squad_pipeline/cloud/bootstrap_schema.sql').read_text());self.db.execute("insert into matches(match_id) values('controlled')");self.source=dict(provider='Controlled',url='https://example.com/m',sha256='a'*64,size=0)
  self.player=dict(provider_player_id='1',name='Controlled',role='starters',participation_status='STARTER',minutes=80)
 def tearDown(self):self.db.close()
 def apply(self,player):return apply_appearance(self.db,99,'controlled',player,self.source,'2026-11-03','ONBOARD')
 def test_conflicted_nonparticipant_not_inserted(self):
  result=self.apply(dict(self.player,participation_status='UNUSED_SUBSTITUTE',minutes=5));self.assertEqual(result['state'],'QUARANTINED');self.assertEqual(self.db.execute('select count(*) from player_match_minutes').fetchone()[0],0)
 def test_old_fact_and_quality_survive_new_conflict(self):
  self.apply(self.player);before=life.load(self.db,'appearance:controlled:1');result=self.apply(dict(self.player,minutes=90));self.assertEqual(result['state'],'RETAINED_ACCEPTED_CONFLICT');self.assertEqual(self.db.execute('select minutes from player_match_minutes').fetchone()[0],80);self.assertEqual(life.load(self.db,'appearance:controlled:1'),before);self.assertEqual(self.db.execute('select count(*) from quality_source_issues').fetchone()[0],1)
 def test_minor_difference_uses_existing_policy(self):
  self.apply(self.player);result=self.apply(dict(self.player,minutes=83));self.assertEqual(result['state'],'RETAINED_ACCEPTED_WITHIN_TOLERANCE');self.assertEqual(self.db.execute('select minutes from player_match_minutes').fetchone()[0],80);self.assertEqual(self.db.execute('select count(*) from minute_policy_decisions').fetchone()[0],1)
