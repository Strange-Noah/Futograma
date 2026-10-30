import unittest,json
import test_international_store as fixtures
from squad_pipeline.minute_reconciliation import apply_existing
from squad_pipeline.international_store import put_appearance
class ReconciliationTests(unittest.TestCase):
 def setUp(self):
  self.fixture=fixtures.InternationalStoreTests();self.fixture.setUp();self.db=self.fixture.db
 def tearDown(self):self.fixture.tearDown()
 def test_status_conflict_survives_future_daily_reconciliation(self):
  f=self.fixture.fact();f.update(minutes=None,reported_minutes=1,derived_minutes=0,evidence_scope='APPEARANCE_STATUS_CONTRADICTION;PERSONAL_REPORT_AND_SKY_CLOCKS_DISAGREE');put_appearance(self.db,f)
  self.assertEqual(apply_existing(self.db),[]);self.assertIsNone(self.db.execute('SELECT minutes FROM international_appearances').fetchone()[0])
 def test_minor_null_minutes_are_resolved_once_with_original_observations(self):
  f=self.fixture.fact();f.update(minutes=None,reported_minutes=89,derived_minutes=90);put_appearance(self.db,f);self.assertEqual(len(apply_existing(self.db)),1);self.assertEqual(apply_existing(self.db),[]);self.assertEqual(tuple(self.db.execute('SELECT minutes,reported_minutes,derived_minutes,revision FROM international_appearances').fetchone()),(89,89,90,2))
 def test_canonical_store_accepts_minor_observation_retains_priority(self):
  f=self.fixture.fact();f.update(minutes=78,reported_minutes=78);put_appearance(self.db,f);g=dict(f,minutes=80,reported_minutes=80);self.assertFalse(put_appearance(self.db,g));self.assertEqual(self.db.execute('SELECT minutes FROM international_appearances').fetchone()[0],78);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM minute_policy_decisions').fetchone()[0],1)
 def test_canonical_store_rejects_played_vs_unused(self):
  f=self.fixture.fact();f.update(minutes=1,reported_minutes=1);put_appearance(self.db,f);g=dict(f,minutes=0,reported_minutes=0,participation_status='UNUSED_SUBSTITUTE')
  with self.assertRaises(ValueError):put_appearance(self.db,g)
