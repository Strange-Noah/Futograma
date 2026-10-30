import unittest,sqlite3
from squad_pipeline.international_incremental import personal_window,initial_history_start
class FirstTeamHistoryTests(unittest.TestCase):
 def setUp(self):self.db=sqlite3.connect(':memory:');self.db.execute('CREATE TABLE pipeline_checkpoints(scope TEXT PRIMARY KEY,completed_at TEXT)')
 def tearDown(self):self.db.close()
 def test_new_first_team_player_has_dynamic_season_bootstrap(self):self.assertEqual(personal_window(self.db,5,'2027-01-15','2027-01-18'),('2026-07-01',True))
 def test_established_player_uses_incremental_window(self):
  self.db.execute('INSERT INTO pipeline_checkpoints VALUES(?,?)',('player-history:5','2027-01-17'));self.assertEqual(personal_window(self.db,5,'2027-01-15','2027-01-18'),('2027-01-15',False))
 def test_old_scan_index_migrates_without_new_coverage_certificate(self):
  self.db.execute('CREATE TABLE player_discovery_coverage(player_id INTEGER,history_status TEXT)');self.db.execute("INSERT INTO player_discovery_coverage VALUES(5,'PUBLIC_HISTORY_SCANNED')");self.assertEqual(personal_window(self.db,5,'2026-10-27','2026-10-30'),('2026-10-27',False))
