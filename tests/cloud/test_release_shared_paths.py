import unittest,sqlite3,json
from squad_pipeline.six_club_experiment import incremental_row
from squad_pipeline.roster_policy import classify
class SharedClubPolicyTests(unittest.TestCase):
 def sheet(self,provider,minutes,status='STARTER'):return dict(provider=provider,source=dict(url='https://example.org/'+provider),complete_squad=True,players=[dict(player_id=7,status=status,reported_minutes=minutes,derived_minutes=None)])
 def test_club_inclusive_five_and_source_order(self):
  a=self.sheet('Sky',90);b=self.sheet('FotMob',85);r=incremental_row(1,7,dict(match_id='x'),[a,b]);self.assertEqual(r['minutes'],85);self.assertEqual(r['issues'],[]);self.assertEqual(incremental_row(1,7,dict(match_id='x'),[b,a])['minutes'],85)
 def test_club_played_unused_conflict_never_tolerated(self):
  r=incremental_row(1,7,dict(match_id='x'),[self.sheet('FotMob',1,'SUBSTITUTE_USED'),self.sheet('Sky',0,'UNUSED_SUBSTITUTE')]);self.assertIsNone(r['minutes']);self.assertTrue(r['issues'])
 def test_club_more_than_five_remains_unresolved(self):self.assertTrue(incremental_row(1,7,dict(match_id='x'),[self.sheet('FotMob',84),self.sheet('Sky',90)])['issues'])
