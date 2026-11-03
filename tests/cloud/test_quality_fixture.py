import copy,json,sqlite3,unittest
from squad_pipeline.quality_fixture import evaluate,canonical_observation
from squad_pipeline import canonical_fixture as c
from squad_pipeline.cloud.canonical_export import enrich_records

class FixtureQualityTests(unittest.TestCase):
 def setUp(self):
  self.f=dict(match_id='retained-1',provider='VerifiedSource',provider_match_id='123',actual_provider_match_id='123',home=dict(id='club:1',name='Real Madrid'),away=dict(id='club:2',name='Opponent'),competition_key='L',season='2026/27',kickoff_utc='2026-10-30T20:00:00Z',status='FINISHED',home_score=3,away_score=1)
 def proof(self,f):return dict(field_evidence={k:dict(value=v,sha256='a'*64,url='https://source.example/match/123',state='VERIFIED') for k,v in f.items()})
 def test_fields_without_proof_not_accepted(self):
  r=evaluate(self.f);self.assertEqual(r['decision'],'PENDING_VERIFICATION');self.assertEqual(r['identity_state'],'PENDING_VERIFICATION')
 def test_finished_requires_both_scores(self):
  f=dict(self.f,away_score=None);r=evaluate(f,self.proof(f));self.assertIn('away_score:VALID_NUMERIC_SCORE_REQUIRED',r['obligations']);self.assertTrue(r['retry_eligible'])
 def test_scores_do_not_prove_identity(self):
  f=dict(self.f,actual_provider_match_id='wrong');r=evaluate(f,self.proof(f));self.assertEqual(r['decision'],'CONFLICTED');self.assertFalse(r['retry_eligible'])
 def test_wrong_orientation_proof_is_conflict(self):
  f=copy.deepcopy(self.f);p=self.proof(f);f['home'],f['away']=f['away'],f['home'];self.assertEqual(evaluate(f,p)['decision'],'CONFLICTED')
 def test_unknown_team_never_accepted(self):
  f=dict(self.f,away=dict(id='temporary',name='Unknown'));self.assertNotEqual(evaluate(f,self.proof(f))['decision'],'ACCEPT')
 def test_no_naive_kickoff(self):
  f=dict(self.f,kickoff_utc='2026-10-30T20:00:00');self.assertIn('EXPLICIT_KICKOFF_TIMEZONE_REQUIRED',evaluate(f,self.proof(f))['obligations'])
 def test_postponed_has_explained_unavailable_score(self):
  f=dict(self.f,status='POSTPONED',home_score=None,away_score=None,score_unavailable_reason='No result: fixture postponed');self.assertEqual(evaluate(f,self.proof(f))['decision'],'ACCEPT');self.assertIsNone(evaluate(f,self.proof(f))['fixture']['home_score'])
 def test_cancelled_without_explanation_pending(self):
  f=dict(self.f,status='CANCELLED',home_score=None,away_score=None);self.assertNotEqual(evaluate(f,self.proof(f))['decision'],'ACCEPT')
 def test_scheduled_uncertain_kickoff_preserved(self):
  f=dict(self.f,status='SCHEDULED',kickoff_utc=None,kickoff_uncertainty='Fixture date TBD',home_score=None,away_score=None);self.assertEqual(evaluate(f,self.proof(f))['decision'],'ACCEPT')
 def test_bool_score_rejected(self):
  f=dict(self.f,home_score=False);self.assertNotEqual(evaluate(f,self.proof(f))['decision'],'ACCEPT')
 def test_source_to_canonical_to_existing_export(self):
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;c.migrate(db)
  for side in ('home','away'):c.entity(db,'TEAM',self.f[side]['id'],self.f[side]['name'])
  c.entity(db,'COMPETITION','L','La Liga')
  observation=canonical_observation(self.f,self.proof(self.f));r=c.reconcile(db,observation,registered_id=self.f['match_id']);self.assertEqual(r['match_id'],'retained-1')
  records={'1':[dict(match_id=r['match_id'],club_id=1,date='2026-10-30',match_type='CLUB')]};enrich_records(db,records,'CLUB');row=json.loads(json.dumps(records))['1'][0]
  self.assertEqual((row['home_team'],row['away_team'],row['home_score'],row['away_score'],row['match_status']),('Real Madrid','Opponent',3,1,'FINISHED'));self.assertEqual(row['competition'],'La Liga');self.assertEqual(row['score_state'],'VERIFIED')
  second=c.reconcile(db,observation,registered_id=self.f['match_id']);self.assertEqual(second['operation'],'REDUNDANT');self.assertEqual(db.execute('select count(*) from canonical_fixtures').fetchone()[0],1);db.close()
 def test_changed_score_cannot_silently_replace_old(self):
  db=sqlite3.connect(':memory:');c.migrate(db)
  for s in ('home','away'):c.entity(db,'TEAM',self.f[s]['id'],self.f[s]['name'])
  c.entity(db,'COMPETITION','L','La Liga');c.reconcile(db,canonical_observation(self.f,self.proof(self.f)),registered_id='retained-1')
  f=dict(self.f,home_score=4);c.reconcile(db,canonical_observation(f,self.proof(f)),registered_id='retained-1');self.assertEqual(c.value(db,'retained-1','home_score')[1],'SOURCE_CONFLICT');db.close()
 def test_catalogue_includes_unplayed_fixture_without_appearance(self):
  from squad_pipeline.quality_fixture import export_fixture_catalogue
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;c.migrate(db)
  for s in ('home','away'):c.entity(db,'TEAM',self.f[s]['id'],self.f[s]['name'])
  c.entity(db,'COMPETITION','L','La Liga')
  f=dict(self.f,status='SCHEDULED',home_score=None,away_score=None);c.reconcile(db,canonical_observation(f,self.proof(f)),registered_id=f['match_id'])
  rows=json.loads(json.dumps(export_fixture_catalogue(db,'CLUB')));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['status'],'SCHEDULED');self.assertEqual(rows[0]['verification']['decision'],'ACCEPT');self.assertIsNone(rows[0]['home_score']);self.assertEqual(rows[0]['record_kind'],'FIXTURE_FACT');db.close()
 def test_catalogue_does_not_hide_score_conflict(self):
  from squad_pipeline.quality_fixture import export_fixture_catalogue
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;c.migrate(db)
  for s in ('home','away'):c.entity(db,'TEAM',self.f[s]['id'],self.f[s]['name'])
  c.entity(db,'COMPETITION','L','La Liga');c.reconcile(db,canonical_observation(self.f,self.proof(self.f)),registered_id=self.f['match_id'])
  f=dict(self.f,away_score=4);c.reconcile(db,canonical_observation(f,self.proof(f)),registered_id=f['match_id'])
  self.assertEqual(export_fixture_catalogue(db)[0]['verification']['decision'],'CONFLICTED');db.close()
 def test_acceptance_ledger_export_rechecks_exact_fields(self):
  from squad_pipeline.quality_fixture import export_accepted_catalogue
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;c.migrate(db)
  for s in ('home','away'):c.entity(db,'TEAM',self.f[s]['id'],self.f[s]['name'])
  c.entity(db,'COMPETITION','L','La Liga');c.reconcile(db,canonical_observation(self.f,self.proof(self.f)),registered_id=self.f['match_id'])
  db.execute('create table quality_fixture_acceptance(match_id text,payload_json text,result_json text)');db.execute('insert into quality_fixture_acceptance values(?,?,?)',(self.f['match_id'],json.dumps(self.f),json.dumps(evaluate(self.f,self.proof(self.f)))))
  out=export_accepted_catalogue(db);self.assertEqual(len(out['accepted']),1);self.assertEqual(out['pending'],[]);self.assertTrue(out['accepted'][0]['source_provenance'])
  tampered=dict(self.f,away_score=9);db.execute('update quality_fixture_acceptance set payload_json=?',(json.dumps(tampered),));out=export_accepted_catalogue(db);self.assertEqual(out['accepted'],[]);self.assertEqual(len(out['pending']),1);db.close()
 def catalogue_db(self):
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;c.migrate(db)
  for s in ('home','away'):c.entity(db,'TEAM',self.f[s]['id'],self.f[s]['name'])
  c.entity(db,'COMPETITION','L','La Liga');c.reconcile(db,canonical_observation(self.f,self.proof(self.f)),registered_id=self.f['match_id']);db.execute('create table quality_fixture_acceptance(match_id text,payload_json text,result_json text)');db.execute('insert into quality_fixture_acceptance values(?,?,?)',(self.f['match_id'],json.dumps(self.f),json.dumps(evaluate(self.f,self.proof(self.f)))));return db
 def test_catalogue_stable_ids_allow_different_source_display_labels(self):
  from squad_pipeline.quality_fixture import export_accepted_catalogue
  db=self.catalogue_db();db.execute("update canonical_entities set name='realmadrid' where kind='TEAM' and entity_id='club:1'")
  out=export_accepted_catalogue(db);self.assertEqual(len(out['accepted']),1);self.assertEqual(out['pending'],[])
  row=out['accepted'][0];self.assertEqual(row['home'],dict(id='club:1',name='Real Madrid'));self.assertEqual(row['canonical_home_team'],'realmadrid');self.assertEqual(row['source_team_labels']['home']['name'],'Real Madrid');self.assertEqual(row['source_provenance']['field_evidence']['home']['value']['id'],'club:1');db.close()
 def test_catalogue_same_name_different_stable_id_is_conflict(self):
  from squad_pipeline.quality_fixture import export_accepted_catalogue
  db=self.catalogue_db();changed=dict(self.f,home=dict(id='club:999',name='Real Madrid'));db.execute('update quality_fixture_acceptance set payload_json=?,result_json=?',(json.dumps(changed),json.dumps(evaluate(changed,self.proof(changed)))))
  out=export_accepted_catalogue(db);self.assertEqual(out['accepted'],[]);self.assertIn('CANONICAL_LEDGER_CONFLICT:home',out['pending'][0]['obligations']);db.close()

 def test_malformed_hash_cannot_satisfy_any_field_contract(self):
  for bad in ('not-a-hash',True,1,'a'*63,'g'*64):
   proof=self.proof(self.f)
   for p in proof['field_evidence'].values():p['sha256']=bad
   self.assertEqual(evaluate(self.f,proof)['decision'],'PENDING_VERIFICATION')
 def test_snapshot_hash_and_explicit_internal_ref_remain_compatible(self):
  proof=self.proof(self.f)
  for p in proof['field_evidence'].values():
   p.pop('sha256');p.pop('url');p.update(snapshot_sha256='b'*64,source_ref='release:current/snapshots/asset-123#fixture')
  self.assertEqual(evaluate(self.f,proof)['decision'],'ACCEPT')
 def test_empty_provider_proof_is_not_evidence(self):
  proof=self.proof(self.f)
  for p in proof['field_evidence'].values():p['provider']=''
  self.assertNotEqual(evaluate(self.f,proof)['decision'],'ACCEPT')
 def test_bad_reference_or_credential_url_does_not_close_obligation(self):
  for location in ('not-a-url','https://user:secret@source.example/match','https://source.example/match with spaces'):
   proof=self.proof(self.f)
   for p in proof['field_evidence'].values():p['url']=location
   self.assertNotEqual(evaluate(self.f,proof)['decision'],'ACCEPT')
 def test_invalid_declared_hash_cannot_hide_behind_snapshot_fallback(self):
  proof=self.proof(self.f)
  for p in proof['field_evidence'].values():p.update(sha256=True,snapshot_sha256='a'*64)
  self.assertNotEqual(evaluate(self.f,proof)['decision'],'ACCEPT')
