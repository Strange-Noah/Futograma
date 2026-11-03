import unittest,sqlite3,json,tempfile
from pathlib import Path
from unittest.mock import patch
from fixtures import database
from squad_pipeline.live_ingest import apply_sheet,connect
from squad_pipeline.live_parse import Pending
from squad_pipeline.quality_live_bridge import require_complete_score

class LiveQualityBridgeTests(unittest.TestCase):
 def test_missing_finished_score_rejected_before_any_canonical_write(self):
  db=sqlite3.connect(':memory:');db.execute('create table caller_work(value text)');db.execute("insert into caller_work values('retain')")
  with patch('squad_pipeline.live_ingest.bind_fixture') as bind:
   with self.assertRaisesRegex(Pending,'COMPLETED_SCORE_INCOMPLETE'):apply_sheet(db,dict(match_state='COMPLETED',score=None),{},'test','2026-11-04')
  bind.assert_not_called();self.assertTrue(db.in_transaction);self.assertEqual([r[0] for r in db.execute("select name from sqlite_master where type='table'")],['caller_work']);self.assertEqual(db.execute('select count(*) from caller_work').fetchone()[0],1);db.rollback();db.close()
 def test_zero_score_valid_and_exception_not_fabricated(self):
  require_complete_score(dict(match_state='COMPLETED',score='0 - 0'));f=dict(match_state='POSTPONED',score=None);require_complete_score(f);self.assertIsNone(f['score'])
 def test_unknown_membership_history_retained_and_caller_rollback(self):
  with tempfile.TemporaryDirectory() as td:
   path=Path(td)/'synthetic.sqlite';database(path);db=connect(path)
   source=dict(url='https://example.com/controlled-complete-match',sha256='a'*64,acquired_at='2026-10-29T09:00:00+00:00',path=None)
   f=dict(match_id='controlled-fixture',competition_id=47,competition='Premier League',kickoff='2026-10-24T12:00:00Z',scheduled_kickoff='2026-10-24T12:00:00Z',match_date='2026-10-24',home_team='manchestercity',away_team='arsenal',score='0-0',match_state='COMPLETED')
   players=[dict(provider_key='controlled:'+str(i),name=f'Controlled Test 0 Player {i}',shirt=str(i),role='starters' if i<=11 else 'bench',status='STARTER' if i<=11 else 'UNUSED_SUBSTITUTE',derived_minutes=90 if i<=11 else 0,reported_minutes=None,json_path='SYNTHETIC') for i in range(1,14)]
   sheet=dict(club_id=8456,provider='Controlled',players=players,complete_squad=True,complete_events=True,source=source,issues=[])
   before=[tuple(r) for r in db.execute('select * from player_match_minutes order by club_id,match_id,player_id')];db.execute('BEGIN');result=apply_sheet(db,f,sheet,'controlled-test','2026-10-29');self.assertEqual(result['status'],'UNCHANGED');self.assertEqual(before,[tuple(r) for r in db.execute('select * from player_match_minutes order by club_id,match_id,player_id')])
   self.assertTrue(db.execute('select 1 from quality_membership_decisions where player_id=13').fetchone());review=json.loads(db.execute('select payload_json from quality_live_fixture_reviews').fetchone()[0]);self.assertEqual(review['state'],'LEGACY_PENDING_REVALIDATION');self.assertTrue(db.in_transaction);db.rollback()
   self.assertEqual(before,[tuple(r) for r in db.execute('select * from player_match_minutes order by club_id,match_id,player_id')]);self.assertFalse(db.execute("select 1 from sqlite_master where name='quality_live_fixture_reviews'").fetchone());db.close()
