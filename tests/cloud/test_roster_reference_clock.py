import unittest
from datetime import datetime
from ops.cloud.production_entry import roster_day
class RosterReferenceClock(unittest.TestCase):
 def test_current_owner_date_not_previous_utc_day(self):
  self.assertEqual(roster_day(datetime.fromisoformat('2026-10-30T20:00:00+00:00')),'2026-10-31')
 def test_daily_update_slot_date(self):
  self.assertEqual(roster_day(datetime.fromisoformat('2026-10-31T08:00:00+00:00')),'2026-10-31')
