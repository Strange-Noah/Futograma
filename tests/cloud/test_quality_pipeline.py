import unittest,sqlite3,copy,json
from squad_pipeline import quality_pipeline as p,quality_lifecycle as life
class IntegratedPipeline(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
  self.f=dict(match_id='new-id',provider='P',provider_match_id='1',actual_provider_match_id='1',competition_key='L',season='2026/27',kickoff_utc='2026-10-01T12:00:00Z',home=dict(id='A',name='Alpha'),away=dict(id='B',name='Beta'),status='FINISHED',home_score=1,away_score=0)
  self.proof=dict(provider='P',field_evidence={k:dict(value=v,url='https://example.com/match/1',sha256='a'*64,state='VERIFIED') for k,v in self.f.items()})
 def tearDown(self):self.db.close()
 def test_all_discovery_modes_use_same_contract(self):
  for mode in p.MODES:self.assertEqual(p.process_fixture(self.db,self.f,self.proof,'2026-11-03',mode)['decision'],'ACCEPT')
  self.assertEqual(self.db.execute('select count(*) from canonical_fixtures').fetchone()[0],1)
 def test_score_incomplete_never_accepted(self):
  f=dict(self.f,home_score=None);r=p.process_fixture(self.db,f,self.proof,'2026-11-03');self.assertEqual(r['decision'],'QUARANTINE');self.assertEqual(self.db.execute('select count(*) from quality_fixture_acceptance').fetchone()[0],0)
 def test_source_id_mismatch_conflicted(self):
  r=p.process_fixture(self.db,dict(self.f,actual_provider_match_id='2'),self.proof,'2026-11-03');self.assertEqual(r['quality']['state'],'CONFLICTED')
 def test_explicit_stable_review_skips_unchanged(self):
  proof=dict(self.proof,provider='P');fp=life.fingerprint([self.f,proof]);review=dict(fingerprint=fp,independent=True,reviewer_id='independent-test',method='INDEPENDENT_REPLAY',evidence=[dict(provider='P',source_url='https://example.com/match/1',sha256='a'*64)])
  first=p.process_fixture(self.db,self.f,proof,'2026-11-03',stability_review=review);self.assertEqual(first['quality']['state'],'STABLE');again=p.process_fixture(self.db,self.f,proof,'2026-11-03');self.assertTrue(again['skipped_full_revalidation'])
  changed=dict(self.f,status='POSTPONED');self.assertFalse(p.process_fixture(self.db,changed,proof,'2026-11-03')['skipped_full_revalidation'])
 def test_bench_unknown_not_zero(self):
  r=p.participation_contract(dict(provider_player_id='1',participation_status='BENCH_PARTICIPATION_UNKNOWN',minutes=None),dict(provider='P',url='https://example.com/m',sha256='a'*64));self.assertEqual(r['participation']['status'],'MISSING');self.assertEqual(r['minutes']['status'],'MISSING')
 def test_nonparticipant_positive_minutes_conflict(self):
  r=p.participation_contract(dict(provider_player_id='1',participation_status='UNUSED_SUBSTITUTE',minutes=10),dict(provider='P',url='https://example.com/m',sha256='a'*64));self.assertEqual(r['minutes']['status'],'CONFLICTED')

 def test_changed_history_reopens_participation(self):
  p.mark_participation_version(self.db,'mid','first')
  evidence=[dict(provider='P',source_url='https://example.com/m',sha256='a'*64)]
  verified=life.evaluate({'minutes':life.obligation('SATISFIED',evidence)},['minutes'],'verified');life.persist(self.db,'appearance:mid:p',verified,'2026-11-03T00:00:00Z')
  self.assertTrue(p.can_reuse_participation(self.db,'mid','first'))
  self.assertFalse(p.can_reuse_participation(self.db,'mid','changed-history'))
  q=life.evaluate({'minutes':life.obligation('MISSING')},['minutes'],'x');life.persist(self.db,'appearance:mid:p',q,'2026-11-03T00:00:00Z')
  self.assertFalse(p.can_reuse_participation(self.db,'mid','first'))

 def test_canonical_conflict_reopens_stable(self):
  from squad_pipeline import canonical_fixture as canonical
  proof=dict(self.proof,provider='P');fp=life.fingerprint([self.f,proof]);review=dict(fingerprint=fp,independent=True,reviewer_id='independent-test',method='INDEPENDENT_REPLAY',evidence=[dict(provider='P',source_url='https://example.com/match/1',sha256='a'*64)])
  p.process_fixture(self.db,self.f,proof,'2026-11-03',stability_review=review)
  self.db.execute("update canonical_field_values set state='SOURCE_CONFLICT' where field='home_score'")
  again=p.process_fixture(self.db,self.f,proof,'2026-11-03');self.assertFalse(again['skipped_full_revalidation']);self.assertEqual(again['quality']['state'],'CONFLICTED')

 def test_source_failure_exports_as_issue_only(self):
  from squad_pipeline.cloud.quality_context_export import issues
  p.record_source_issue(self.db,'CLUB_DETAIL','1','RETURNED_MATCH_ID_MISMATCH',{'source':{'provider':'P','url':'https://example.com/m','sha256':'a'*64}},'2026-11-03')
  rows=issues(self.db);self.assertEqual(len(rows),1);self.assertEqual(rows[0]['record_kind'],'SOURCE_ISSUE');self.assertNotIn('minutes',rows[0]);self.assertEqual(self.db.execute('select count(*) from canonical_fixtures').fetchone()[0],0)

 def test_competition_display_label_requires_matching_proof(self):
  r=p.process_fixture(self.db,dict(self.f,competition_label='Unproven league'),self.proof,'2026-11-03');self.assertEqual(r['decision'],'QUARANTINE')

 def test_verified_registry_correction_reopens_stable(self):
  proof=dict(self.proof,provider='P');fp=life.fingerprint([self.f,proof]);review=dict(fingerprint=fp,independent=True,reviewer_id='independent-test',method='INDEPENDENT_REPLAY',evidence=[dict(provider='P',source_url='https://example.com/match/1',sha256='a'*64)])
  p.process_fixture(self.db,self.f,proof,'2026-11-03',stability_review=review)
  self.db.execute("update canonical_field_values set value_json='2',state='VERIFIED' where field='home_score'")
  again=p.process_fixture(self.db,self.f,proof,'2026-11-03');self.assertFalse(again['skipped_full_revalidation']);self.assertEqual(again['quality']['state'],'CONFLICTED')
 def test_weak_hash_never_admits_ledger(self):
  proof=copy.deepcopy(self.proof)
  for v in proof['field_evidence'].values():v['sha256']='not-a-hash'
  r=p.process_fixture(self.db,self.f,proof,'2026-11-03');self.assertEqual(r['decision'],'QUARANTINE');self.assertEqual(self.db.execute('select count(*) from quality_fixture_acceptance').fetchone()[0],0)
 def test_fractional_minutes_conflicted(self):
  r=p.participation_contract(dict(provider_player_id='1',participation_status='STARTER',minutes=1.5),dict(provider='P',url='https://example.com/m',sha256='a'*64));self.assertEqual(r['minutes']['status'],'CONFLICTED')
 def test_scheduled_tbd_mirrors_without_invented_date(self):
  from ops.cloud.coldstart_acceptance import persist_fixture_row
  from squad_pipeline.core import ROOT
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.executescript((ROOT/'squad_pipeline/cloud/bootstrap_schema.sql').read_text())
  f=dict(self.f,status='SCHEDULED',kickoff_utc=None,kickoff_uncertainty='SOURCE_TBD',home_score=None,away_score=None);proof=dict(provider='P',field_evidence={k:dict(value=v,url='https://example.com/match/1',sha256='a'*64,state='VERIFIED') for k,v in f.items() if v is not None})
  result=p.process_fixture(db,f,proof,'2026-11-03');self.assertEqual(result['decision'],'ACCEPT');persist_fixture_row(db,dict(fixture=f,discovery_row=dict(home=dict(id=1,name='Alpha'),away=dict(id=2,name='Beta'),tournament=dict(name='League')),source=dict(provider='P')),'new-id');row=db.execute('select kickoff,local_date,period_id from matches').fetchone();self.assertEqual(tuple(row),(None,None,None));db.close()

 def test_new_team_reuses_existing_physical_match_before_preferred_id(self):
  original=p.process_fixture(self.db,self.f,self.proof,'2026-11-03');new=dict(self.f,match_id='another-preferred-id',provider='AnotherProvider',provider_match_id='other-id',actual_provider_match_id='other-id');proof=dict(provider='AnotherProvider',field_evidence={k:dict(value=v,url='https://example.com/other',sha256='b'*64,state='VERIFIED') for k,v in new.items()});result=p.process_fixture(self.db,new,proof,'2026-11-03');self.assertEqual(result['match_id'],original['match_id']);self.assertEqual(self.db.execute('select count(*) from canonical_fixtures').fetchone()[0],1)

 def test_legacy_first_observation_does_not_hide_verified_new_provider(self):
  from squad_pipeline import canonical_fixture as canonical,quality_fixture
  p.process_fixture(self.db,self.f,self.proof,'2026-11-03')
  legacy=dict(domain='CLUB',provider='LEGACY_ACCEPTED',provider_match_id='retained',home_key='A',away_key='B',competition_key='L',edition='2026/27')
  self.db.execute('insert into canonical_observations values(?,?,?,?,?,?,?)',('000000','CLUB','LEGACY_ACCEPTED','retained','new-id',json.dumps(legacy),json.dumps({'snapshot_sha256':'c'*64})))
  result=quality_fixture.export_accepted_catalogue(self.db);self.assertEqual(len(result['accepted']),1);self.assertEqual(result['accepted'][0]['provider'],'P')
