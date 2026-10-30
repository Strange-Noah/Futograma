import unittest
from datetime import datetime,timezone
from pathlib import Path
from ops.cloud.production_report import delta,publish
from squad_pipeline.cloud.driver import formal_due
class ProductionReporting(unittest.TestCase):
 def test_zero_additions_and_updates_are_distinct(self):
  old={('CLUB','m'):'a',('NATIONAL','n'):'b'}
  self.assertEqual(delta(old,old)['TOTAL'],dict(new=0,updated=0,unchanged=2))
  self.assertEqual(delta(old,{**old,('CLUB','m'):'changed',('NATIONAL','z'):'new'})['TOTAL'],dict(new=1,updated=1,unchanged=1))
 def test_no_removed_accepted_matches(self):
  with self.assertRaisesRegex(ValueError,'ACCEPTED_MATCH_REMOVED'):delta({('CLUB','m'):'a'}, {})
 def test_reports_cannot_advance_authority(self):
  with self.assertRaisesRegex(ValueError,'REPORT_ONLY_REF_REQUIRED'):publish(None,{'run_id':'1'},ref='main')
 def test_collection_guard_remains_exact(self):
  self.assertFalse(formal_due(datetime.fromisoformat('2026-10-31T07:59:59+00:00')))
  self.assertTrue(formal_due(datetime.fromisoformat('2026-10-31T08:00:00+00:00')))
 def test_promotion_does_not_share_collection_clock_gate(self):
  source=Path('ops/cloud/release_ops.py').read_text()
  self.assertNotIn('ROLLOUT_NOT_DUE',source)
  for gate in ['REVIEWED_CANDIDATE_CHANGED','PRODUCTION_BASE_CHANGED','ROLLBACK_POINTER_NOT_RETAINED','EXPLICIT_EXACT_VERSION_PROMOTION_APPROVAL_REQUIRED']:self.assertIn(gate,source)
