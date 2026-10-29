import unittest,sqlite3,copy,json,tempfile,contextlib,io
from pathlib import Path
from datetime import datetime
from squad_pipeline.core import ROOT,BASE,read,write,digest,now
from squad_pipeline.live_parse import torly_detail,sky_detail,catalogs,ParseError,Pending,finish
from squad_pipeline.live_sources import Client,OUT
from squad_pipeline.live_ingest import connect,apply_sheet,bind_fixture,resolve_player,IdentityError,Conflict,correction_rollups,stage_sheet
from squad_pipeline.six_club_experiment import encoded,fact,rolling
from squad_pipeline.eligibility import active,absence_semantics,DDL,add

class LiveTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.probes=read(OUT/'detail_probe.json');cls.parsed=[]
  for r in cls.probes:cls.parsed.append((sky_detail if 'skysports' in r['url'] else torly_detail)((ROOT/r['path']).read_text(),r))
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='regression_',dir=OUT);self.path=Path(self.tmp.name)/'test.sqlite';a=sqlite3.connect(BASE/'state/live.sqlite');b=sqlite3.connect(self.path);a.backup(b);a.close();b.close();self.db=connect(self.path)
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def clone(self):return copy.deepcopy(self.parsed[0])
 def test_fresh_provider_agreement(self):
  a,b=self.parsed;self.assertEqual(a[0]['match_id'],b[0]['match_id']);self.assertEqual(sorted((p['status'],p['derived_minutes']) for p in a[1][0]['players']),sorted((p['status'],p['derived_minutes']) for p in b[1][0]['players']));self.assertEqual(len(a[1][0]['players']),20)
 def test_own_side_id(self):
  f,ss=self.clone();self.assertEqual(f['away_id'],8456);self.assertEqual(ss[0]['club_id'],8456);self.assertEqual(sum(p['role']=='starters' for p in ss[0]['players']),11)
 def test_duplicate_player_rejected(self):
  _,ss=self.clone();s=ss[0];s['players'].append(copy.deepcopy(s['players'][-1]))
  with self.assertRaises(ParseError):finish(s['players'],s['events'],s['source'],8456,'Torlytics')
 def test_yellow_card_is_not_substitution(self):
  _,ss=self.parsed[1];p=next(p for p in ss[0]['players'] if p['name']=='Elliot Anderson');self.assertEqual(p['status'],'STARTER');self.assertEqual(p['derived_minutes'],90)
 def test_repeated_ingestion_no_fact_churn(self):
  f,ss=self.clone();before=[tuple(r) for r in self.db.execute('SELECT * FROM player_match_minutes ORDER BY club_id,match_id,player_id')];a=apply_sheet(self.db,f,ss[0],'TEST',f['match_date']);self.db.commit();b=apply_sheet(self.db,f,ss[0],'TEST',f['match_date']);self.db.commit();after=[tuple(r) for r in self.db.execute('SELECT * FROM player_match_minutes ORDER BY club_id,match_id,player_id')];self.assertEqual(a['status'],'UNCHANGED');self.assertEqual(b['status'],'UNCHANGED');self.assertEqual(before,after)
 def test_weak_conflict_cannot_overwrite(self):
  f,ss=self.clone();ss[0]['players'][0]['reported_minutes']=1
  with self.assertRaises(Conflict):apply_sheet(self.db,f,ss[0],'TEST',f['match_date'])
  self.db.rollback();self.assertEqual(self.db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0],2222)
 def test_kickoff_disagreement_not_duplicate(self):
  f,_=self.clone();f['kickoff']='2026-10-25T11:30:00Z';f['match_date']='2026-10-25';f['match_id']='m_test_date_conflict'
  with self.assertRaises(Conflict):bind_fixture(self.db,f,'https://torlytics.de/spiel/test-date-conflict')
 def test_unknown_sky_identity_quarantined(self):
  with self.assertRaises(IdentityError):resolve_player(self.db,8456,dict(provider_key='sky-name:8456:unidentified',name='Unidentified Controlled Test Player'))
 def test_new_stable_identity_no_fotmob_forgery(self):
  pid=resolve_player(self.db,8456,dict(provider_key='https://torlytics.de/spieler/controlled-test-only',name='Controlled Test Only'));self.assertLess(pid,0);self.assertFalse(self.db.execute("SELECT 1 FROM validation_player_aliases WHERE player_id=? AND provider_key LIKE 'fotmob:%'",(pid,)).fetchone())
 def test_new_match_second_apply(self):
  f,ss=self.clone();f.update(match_id='m_controlled_test_20260802',kickoff='2026-08-02T11:30:00Z',scheduled_kickoff='2026-08-02T11:30:00Z',match_date='2026-08-02');ss[0]['source']['url']='https://torlytics.de/spiel/controlled-regression-only';ss[0]['source']['test_only']=True
  a=apply_sheet(self.db,f,ss[0],'CONTROLLED_TEST','2026-10-28');self.db.commit();n=self.db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0];b=apply_sheet(self.db,f,ss[0],'CONTROLLED_TEST','2026-10-28');self.db.commit();self.assertEqual(a['status'],'NEW_MATCH');self.assertEqual(b['status'],'UNCHANGED');self.assertEqual(n,self.db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0])
 def test_interrupted_transaction_rollback(self):
  before=self.db.execute('SELECT COUNT(*) FROM players').fetchone()[0];self.db.execute('BEGIN');resolve_player(self.db,8456,dict(provider_key='https://torlytics.de/spieler/controlled-rollback',name='Controlled Rollback'));self.db.rollback();self.assertEqual(before,self.db.execute('SELECT COUNT(*) FROM players').fetchone()[0])
 def test_affiliation_four_cases(self):
  self.assertEqual(active(self.db,8634,921253,'2026-10-28')['classification'],'LOAN_OUT');self.assertEqual(active(self.db,8634,1656591,'2026-10-28')['regular_member'],0);self.assertEqual(active(self.db,8634,1708842,'2026-10-28')['regular_member'],0);self.assertEqual(active(self.db,9825,1635773,'2026-10-28')['regular_member'],1)
 def test_loan_expiry_does_not_prove_return(self):self.assertEqual(active(self.db,8634,921253,'2027-07-01')['classification'],'UNVERIFIED')
 def test_loan_in_and_academy_date_rules(self):
  add(self.db,921253,8650,'LOAN_IN','LOAN_IN',1,'2026-08-10','2027-06-30','2026-10-28',{'test_only':True},'test');self.assertIsNone(active(self.db,8650,921253,'2026-08-09')['regular_member']);self.assertEqual(active(self.db,8650,921253,'2026-08-10')['regular_member'],1)
 def test_academy_absence_not_injury(self):self.assertIn('NO_INJURY',absence_semantics(self.db,8634,1656591,'2026-10-28'))
 def test_injury_keeps_first_team(self):
  pid=self.db.execute("SELECT player_id FROM players WHERE display_name='William Saliba'").fetchone()[0];self.assertEqual(active(self.db,9825,pid,'2026-10-28')['regular_member'],1)
 def test_7_14_28_and_correction_propagation(self):
  p=self.db.execute("SELECT p.*,m.local_date FROM player_match_minutes p JOIN matches m USING(match_id) WHERE p.club_id=8456 AND p.minutes=90 AND m.local_date='2026-10-24' LIMIT 1").fetchone();pid=p['player_id'];before=rolling(self.db,8456,pid,'2026-10-28',7)['minutes'];self.db.execute('UPDATE player_match_minutes SET minutes=89 WHERE club_id=8456 AND match_id=? AND player_id=?',(p['match_id'],pid));correction_rollups(self.db,8456,pid,p['local_date'],'2026-10-28');rs=list(self.db.execute('SELECT window_days,minutes FROM live_rollups WHERE club_id=8456 AND player_id=? AND as_of=?',(pid,'2026-10-28')));self.assertEqual({x[0] for x in rs},{7,14,28});self.assertEqual(dict(rs)[7],before-1)
 def test_national_scope_separate(self):
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM validation_national_reference').fetchone()[0],21);self.assertEqual(rolling(self.db,8650,921253,'2026-10-28',28)['minutes'],0)
 def client(self,sequence):
  seq=iter(sequence);c=Client('REGRESSION_ONLY_'+Path(self.tmp.name).name,budget=8,transport=lambda u,h:next(seq),sleeper=lambda n:None);c.cachepath=Path(self.tmp.name)/'http_cache.json';c.cache={};return c
 def test_retry_transient_server_error(self):
  c=self.client([(503,{},b'busy'),(200,{},b'<html>ok</html>')]);r,b=c.raw('https://torlytics.de/controlled-regression','TEST_ONLY');self.assertEqual(c.calls,2);self.assertEqual(r['http_status'],200);self.assertEqual(r['transport_mode'],'TEST_TRANSPORT')
 def test_access_denied_no_retry_or_bypass(self):
  c=self.client([(200,{},b'User-agent: *\nAllow: /'),(403,{},b'denied')]);r,b=c.fetch('https://torlytics.de/controlled-regression','TEST_ONLY');self.assertEqual(r['http_status'],403);c.fetch('https://torlytics.de/controlled-other','TEST_ONLY');self.assertEqual(c.calls,2)
 def test_api_policy(self):
  c=self.client([]);r,b=c.fetch('https://torlytics.de/api/test','TEST_ONLY');self.assertEqual(c.calls,0);self.assertEqual(r['status'],'SOURCE_UNAVAILABLE')
 def test_conditional_304_original_hash(self):
  c=self.client([(200,{'ETag':'test'},b'<html>ok</html>'),(304,{},b'')]);c.raw('https://torlytics.de/controlled-regression','TEST_ONLY');r,b=c.raw('https://torlytics.de/controlled-regression','TEST_ONLY');self.assertEqual(b,b'<html>ok</html>');self.assertEqual(r['reused_body_sha256'],digest(b));self.assertEqual(r['conditional_headers'],{'If-None-Match':'test'})
 def test_current_cup_full_roster_and_typed_red(self):
  r=read(OUT/'cup_preflight.json');f,ss=sky_detail((ROOT/r['source']['path']).read_text(),r['source']);self.assertEqual(f['competition_id'],133);self.assertEqual(ss[0]['club_id'],9825);self.assertEqual(len(ss[0]['players']),20);red=[e for e in ss[0]['events'] if e['type']=='red-card'];self.assertEqual(len(red),1);self.assertEqual(red[0]['clock']['base'],85);self.assertEqual(apply_sheet(self.db,f,ss[0],'TEST','2026-10-28',True)['status'],'UNCHANGED')
 def test_concurrent_lock_blocks_second_handle(self):
  import fcntl
  with (Path(self.tmp.name)/'test.lock').open('a+') as a,(Path(self.tmp.name)/'test.lock').open('a+') as b:
   fcntl.flock(a,fcntl.LOCK_EX|fcntl.LOCK_NB)
   with self.assertRaises(BlockingIOError):fcntl.flock(b,fcntl.LOCK_EX|fcntl.LOCK_NB)
 def test_corroborated_correction_updates_rollups(self):
  f,ss=self.clone();s=ss[0];p=next(p for p in s['players'] if p['name']=='Erling Haaland');p['reported_minutes']=89;s['source']['acquired_at']='2026-10-28T16:00:00+00:00';s['source']['controlled_test_only']=True;stage_sheet(self.db,f,s)
  with self.assertRaises(Conflict):apply_sheet(self.db,f,s,'TEST','2026-10-28')
  self.db.rollback();other=copy.deepcopy(s);other['provider']='Sky';other['source']['url']='https://www.skysports.com/football/controlled-test/teams/000';other['source']['sha256']='CONTROLLED_TEST_SECOND_SOURCE';result=apply_sheet(self.db,f,other,'TEST','2026-10-28');self.db.commit();self.assertEqual(result['status'],'UPDATED_MATCH');pid=resolve_player(self.db,8456,p);self.assertEqual(self.db.execute('SELECT minutes FROM player_match_minutes WHERE club_id=8456 AND match_id=? AND player_id=?',(f['match_id'],pid)).fetchone()[0],89);self.assertEqual({x[0] for x in self.db.execute('SELECT window_days FROM live_rollups WHERE club_id=8456 AND player_id=?',(pid,))},{7,14,28})
 def test_schedule_first_run_and_timezone(self):
  import plistlib
  settings=json.loads((ROOT/'ops/live/settings.json').read_text());self.assertEqual(settings['timezone'],'Asia/Shanghai');self.assertEqual(settings['lookback_hours'],72);self.assertEqual(datetime.fromisoformat(settings['first_formal_run']).isoformat(),'2026-10-31T16:00:00+08:00');plist=plistlib.loads((ROOT/'ops/live/org.futbol.live-ingestion.plist').read_bytes());self.assertEqual(plist['StartCalendarInterval'],{'Hour':16,'Minute':0});self.assertEqual(plist['EnvironmentVariables']['TZ'],'Asia/Shanghai')
 def test_exact_72_hour_window_crosses_calendar_days(self):
  from datetime import timedelta,timezone
  end=datetime.fromisoformat('2026-10-31T16:00:00+08:00');start=end-timedelta(hours=72);self.assertEqual(start.isoformat(),'2026-10-28T16:00:00+08:00');self.assertEqual(start.astimezone(timezone.utc).isoformat(),'2026-10-28T08:00:00+00:00')
 def test_future_fixture_not_a_completed_match(self):
  raw=(ROOT/self.probes[0]['path']).read_text().replace('match-score__state">Ende','match-score__state">20:30')
  with self.assertRaises(Pending):torly_detail(raw,self.probes[0])
if __name__=='__main__':unittest.main(verbosity=2)
