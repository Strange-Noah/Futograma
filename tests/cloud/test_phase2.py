import unittest,tempfile,sqlite3,json
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from fixtures import database
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.quality import inspect
from squad_pipeline.managed_roster import migrate as roster_migrate,add_event,decision
from squad_pipeline.pending_matches import migrate,observe,due_candidates,classify,key,should_attempt
from squad_pipeline.cloud.browser_export import minute_state
from squad_pipeline.live_ingest import connect,apply_sheet

class Phase2Tests(unittest.TestCase):
 def setUp(self):
  try:require_hosted()
  except RuntimeError as e:raise unittest.SkipTest(str(e))
  (ROOT/'.cloud/tests-phase2').mkdir(parents=True,exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.cloud/tests-phase2');self.path=Path(self.tmp.name)/'test.sqlite';database(self.path);self.db=connect(self.path);roster_migrate(self.db);migrate(self.db);self.now=datetime(2026,10,29,9,tzinfo=timezone.utc);self.c=dict(club_id=8456,provider='Sky',catalog_provider_id='test-1',url='https://example.com/controlled-test-1',date='2026-10-24',match_completion_at='2026-10-24T14:00:00+00:00');self.source=[dict(url='https://example.com/official-test',test_only=True)]
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def add(self,status,start='2026-08-01',pid=1,**kw):return add_event(self.db,8456,pid,status,start,'2026-10-29',self.source,'Controlled synthetic evidence only',**kw)
 def test_loan_in_included(self):self.add('LOAN_IN');self.assertTrue(decision(self.db,8456,1,'2026-10-29')['included'])
 def test_loan_out_excluded_despite_ownership_snapshot(self):
  self.add('FIRST_TEAM');self.add('LOAN_OUT','2026-08-27');self.add('FIRST_TEAM','2026-10-01',precision='ROSTER_SNAPSHOT');self.assertEqual(decision(self.db,8456,1,'2026-10-29')['state'],'EXCLUDED')
 def test_sold_after_date_historical_preserved(self):
  before=inspect(self.path)['tables'];self.add('FIRST_TEAM');self.add('TRANSFERRED_OUT','2026-08-31');self.db.commit();self.assertTrue(decision(self.db,8456,1,'2026-08-30')['included']);self.assertFalse(decision(self.db,8456,1,'2026-08-31')['included']);self.assertEqual(before,inspect(self.path)['tables'])
 def test_unknown_never_included(self):self.assertEqual(decision(self.db,8456,1,'2026-10-29')['state'],'UNKNOWN')
 def test_injured_player_retained(self):self.add('FIRST_TEAM_INJURED');self.assertTrue(decision(self.db,8456,1,'2026-10-29')['included'])
 def test_explicit_return_required(self):
  self.add('LOAN_OUT','2026-08-27');self.add('FIRST_TEAM','2026-10-01',precision='EXPLICIT_RETURN_DAY');self.assertTrue(decision(self.db,8456,1,'2026-10-29')['included'])
 def test_only_justified_empty_competition_is_pending(self):
  result=dict(status='VALIDATION_FAILED',error='ParseError:UNSUPPORTED_COMPETITION:None');self.assertEqual(classify(result,self.c,self.now),('UNSUPPORTED_OR_UNKNOWN_COMPETITION',False));result['source_context']=dict(confirmed_full_time=True,catalog_competition_known=True,fixture_identity_agrees=True,competition_field_empty=True);self.assertEqual(classify(result,self.c,self.now),('TEMPORARY_SOURCE_INCOMPLETE',True));result['source_context']['identity_conflict']=True;self.assertEqual(classify(result,self.c,self.now),('ROSTER_IDENTITY_CONFLICT',False))
 def test_pending_due_beyond_72_hours(self):
  result=dict(status='SOURCE_UNAVAILABLE',error='HTTP_503');observe(self.db,self.c,result,'first',self.now-timedelta(days=4));self.db.commit();items=due_candidates(self.db,self.now);self.assertEqual(len(items),1);self.assertTrue(items[0]['durable_pending_retry']);self.assertIsNone(items[0]['window_start_utc']);self.assertTrue(items[0]['alternative_sources_allowed'])
 def test_restart_preserves_pending_and_attempts(self):
  observe(self.db,self.c,dict(status='SOURCE_UNAVAILABLE',error='HTTP_503'),'first',self.now);self.db.commit();self.db.close();self.db=connect(self.path);r=self.db.execute('SELECT * FROM match_retry_queue').fetchone();self.assertEqual(r['attempt_count'],1);self.assertEqual(r['state'],'PENDING')
 def test_duplicate_attempt_is_idempotent(self):
  r=dict(status='SOURCE_UNAVAILABLE',error='HTTP_503');observe(self.db,self.c,r,'same',self.now);observe(self.db,self.c,r,'same',self.now);self.assertEqual(self.db.execute('SELECT attempt_count FROM match_retry_queue').fetchone()[0],1)
 def test_after_seven_days_escalates(self):
  observe(self.db,self.c,dict(status='SOURCE_UNAVAILABLE',error='HTTP_503'),'old',self.now-timedelta(days=4));self.db.commit();self.assertEqual(due_candidates(self.db,self.now+timedelta(days=4)),[]);self.assertEqual(self.db.execute('SELECT state FROM match_retry_queue').fetchone()[0],'HUMAN_REVIEW')
 def test_identity_conflict_never_silently_resolves(self):
  observe(self.db,self.c,dict(status='VALIDATION_FAILED',error='ParseError:SKY_ROSTER_SIDE_MISMATCH'),'bad',self.now);observe(self.db,self.c,dict(status='UNCHANGED'),'unapproved-success',self.now);self.assertEqual(self.db.execute('SELECT state FROM match_retry_queue').fetchone()[0],'QUARANTINED');self.assertFalse(should_attempt(self.db,self.c,self.now+timedelta(days=1)))
 def test_integrity_failure_not_normal_retry(self):self.assertEqual(classify(dict(status='VALIDATION_FAILED',error='Conflict:ACCEPTED_FACT_CHANGE_REQUIRES_CURRENT_CORROBORATION'),self.c,self.now),('DATA_INTEGRITY_FAILURE',False))
 def test_zero_unknown_and_pending_distinct(self):
  self.assertEqual(minute_state('UNUSED_SUBSTITUTE',0),'CONFIRMED_ZERO');self.assertEqual(minute_state('UNKNOWN',0),'UNKNOWN');self.assertEqual(minute_state('STARTER',None),'UNKNOWN')
 def test_successful_retry_apply_is_idempotent_and_keeps_history(self):
  c=self.c|dict(date='2026-10-24');observe(self.db,c,dict(status='SOURCE_UNAVAILABLE',error='HTTP_503'),'failure',self.now);source=dict(url='https://example.com/controlled-complete-match',sha256='synthetic-source-only',acquired_at=self.now.isoformat(),path=None);f=dict(match_id='controlled-fixture',competition_id=47,competition='Premier League',kickoff='2026-10-24T12:00:00Z',scheduled_kickoff='2026-10-24T12:00:00Z',match_date='2026-10-24',home_team='manchestercity',away_team='arsenal',score='0-0',match_state='COMPLETED');players=[dict(provider_key='controlled:'+str(i),name=f'Controlled Test 0 Player {i}',shirt=str(i),role='starters' if i<=11 else 'bench',status='STARTER' if i<=11 else 'UNUSED_SUBSTITUTE',derived_minutes=90 if i<=11 else 0,reported_minutes=None,json_path='SYNTHETIC') for i in range(1,14)];s=dict(club_id=8456,provider='Controlled',players=players,complete_squad=True,complete_events=True,source=source,issues=[]);before=self.db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0]
  for token in ['success-1','success-2']:
   result=apply_sheet(self.db,f,s,'controlled-test','2026-10-29');observe(self.db,c,result,token,self.now);self.db.commit()
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0],before);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM matches').fetchone()[0],1);self.assertEqual(self.db.execute('SELECT state FROM match_retry_queue').fetchone()[0],'RESOLVED');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM match_retry_history').fetchone()[0],3)
 def test_refuses_access_denied_normal_retry(self):self.assertEqual(classify(dict(status='SOURCE_UNAVAILABLE',error='HTTP_403'),self.c,self.now),('TEMPORARY_SOURCE_UNAVAILABLE',False))

 def test_expired_loan_out_never_revives_old_snapshot(self):
  self.add('FIRST_TEAM');self.add('LOAN_OUT','2026-08-27',end_exclusive='2026-10-01');self.assertEqual(decision(self.db,8456,1,'2026-10-29')['state'],'UNKNOWN')
 def test_browser_handles_null_source_url_and_hides_clock_probe(self):
  from squad_pipeline.cloud.browser_export import export_browser
  from squad_pipeline.cloud.quality import sha
  self.add('FIRST_TEAM');self.db.execute("UPDATE matches SET metadata_json=?",(json.dumps(dict(home_team='manchestercity',away_team='arsenal',source_evidence=[dict(url=None)])),));observe(self.db,self.c|dict(acceptance_test_only=True),dict(status='SOURCE_UNAVAILABLE',error='HTTP_503'),'probe',self.now);self.db.commit();pointer=dict(current=dict(database=dict(sha256=sha(self.path)),version='TEST_ONLY',code_commit='0'*40),dataset_ref='acceptance/controlled');out=Path(self.tmp.name)/'browser';export_browser(self.path,out,'a'*40,pointer,'2026-10-29');data=json.loads((out/'data/browser.json').read_text());self.assertEqual(data['pending'],[]);self.assertTrue(data['records'])

 def test_quarantined_conflict_cannot_become_temporary_pending(self):
  observe(self.db,self.c,dict(status='VALIDATION_FAILED',error='ParseError:SKY_ROSTER_SIDE_MISMATCH'),'bad',self.now);observe(self.db,self.c,dict(status='SOURCE_UNAVAILABLE',error='HTTP_503'),'later',self.now);r=self.db.execute('SELECT state,failure_classification FROM match_retry_queue').fetchone();self.assertEqual(tuple(r),('QUARANTINED','ROSTER_IDENTITY_CONFLICT'))
 def test_registration_scope_never_crosses_into_other_club_or_under21(self):
  from ops.cloud.phase2_acceptance import legal_senior
  html='<h5><a>Arsenal</a></h5><p>Senior, One<br>Senior, Two</p><p>U21 players</p><p>Youth, Three</p><h5>Other</h5><p>Other, Four</p>';self.assertEqual(legal_senior(html,'Arsenal'),['Senior, One','Senior, Two'])
 def test_verified_alternative_source_retried_after_72_hours_only(self):
  c=self.c|dict(match_completion_at=(self.now-timedelta(days=1)).isoformat(),verified_alternative_sources=[dict(provider='Torlytics',url='https://example.com/alternative',verified_same_fixture=True)]);observe(self.db,c,dict(status='SOURCE_UNAVAILABLE',error='HTTP_503'),'first',self.now-timedelta(days=1));self.assertEqual(len(due_candidates(self.db,self.now)),1);self.assertEqual(len(due_candidates(self.db,self.now+timedelta(days=3))),2)

 def test_explicit_german_cup_label_is_supported_without_none_fallback(self):
  from squad_pipeline.live_parse import fixture
  f=fixture('German DFB Cup','2026-09-02T18:45:00+00:00','VfL Osnabrück','Bayern Munich','1-4',dict(test_only=True));self.assertEqual(f['competition_id'],209)

 def test_real_new_player_identity_error_is_quarantined_as_identity(self):
  r=dict(status='VALIDATION_FAILED',error='IdentityError:NEW_PLAYER_WITHOUT_STABLE_PROVIDER_ID:Ismael Saibari Ben El Basra');self.assertEqual(classify(r,self.c,self.now),('ROSTER_IDENTITY_CONFLICT',False))
