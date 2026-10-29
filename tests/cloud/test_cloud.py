import unittest,tempfile,sqlite3,json,copy,os
from pathlib import Path
from datetime import datetime,timezone,timedelta
from fixtures import database
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.quality import inspect,snapshot,pack,sha
from squad_pipeline.cloud.storage import LocalStore,StaleWriter,UploadFailed
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.retention import migrate,register,cleanup,pin,index_document,bootstrap_indexes,compact_operational_logs
from squad_pipeline.cloud.export import export
from squad_pipeline.cloud.driver import formal_due,validate_monotonic
class CloudTests(unittest.TestCase):
 def setUp(self):
  from squad_pipeline.cloud.hosted_guard import require_hosted
  try:require_hosted()
  except RuntimeError as error:raise unittest.SkipTest(str(error))
  (ROOT/'.cloud/portable-tests').mkdir(parents=True,exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.cloud/portable-tests');self.root=Path(self.tmp.name);self.dbpath=self.root/'synthetic.sqlite';database(self.dbpath);self.gz=self.root/'database.sqlite.gz';pack(self.dbpath,self.gz);self.store=LocalStore(self.root/'store');self.db=sqlite3.connect(self.dbpath);migrate(self.db)
 def tearDown(self):self.db.close();self.tmp.cleanup()
 def file(self,name='expired.response',age=8):
  rel='squad_pipeline/raw/live/controlled/'+name;p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'controlled test evidence');register(self.db,self.root,rel,'RAW_RESPONSE',(datetime.now(timezone.utc)-timedelta(days=age)).isoformat(),declared_temporary=True);self.db.commit();return rel,p
 def publish(self,base=None):
  # Repack after test-only administrative migrations.
  self.db.commit();self.gz.unlink();pack(self.dbpath,self.gz);return self.store.publish(self.dbpath,self.gz,base)
 def test_backup_preserves_all_facts(self):
  target=self.root/'backup.sqlite';self.assertEqual(inspect(self.dbpath)['tables'],snapshot(self.dbpath,target)['tables'])
 def test_restore_verifies_hash_and_schema(self):
  h,p=self.publish();self.store.restore(self.root/'restored.sqlite');self.assertEqual(inspect(self.dbpath)['sha256'],sha(self.root/'restored.sqlite'))
 def test_corrupt_current_can_recover_previous_without_overwriting_authority(self):
  first,p1=self.publish();head,p2=self.publish(first);asset=self.root/'store/versions'/p2['current']['version']/'database.sqlite.gz';asset.write_bytes(b'controlled corruption');self.store.restore(self.root/'safe-previous.sqlite',previous=True);self.assertEqual(sha(self.root/'safe-previous.sqlite'),p1['current']['database']['sha256']);self.assertEqual(self.store.head(),(head,p2))
 def test_refuses_overwrite_restore_target(self):
  self.publish()
  with self.assertRaises(ValueError):self.store.restore(self.dbpath)
 def test_previous_snapshot_recoverable(self):
  first,p1=self.publish();self.publish(first);self.store.restore(self.root/'previous.sqlite',previous=True);self.assertEqual(sha(self.root/'previous.sqlite'),p1['current']['database']['sha256'])
 def test_stale_writer_cannot_overwrite_current(self):
  base,_=self.publish();new,pointer=self.publish(base)
  with self.assertRaises(StaleWriter):self.publish(base)
  self.assertEqual(self.store.head(),(new,pointer))
 def test_failed_upload_does_not_advance_pointer(self):
  head,pointer=self.publish()
  with self.assertRaises(UploadFailed):self.store.publish(self.dbpath,self.gz,head,fail_upload=True)
  self.assertEqual(self.store.head(),(head,pointer))
 def test_corrupt_database_cannot_publish(self):
  h,p=self.publish();bad=self.root/'bad.sqlite';bad.write_bytes(b'not a database')
  with self.assertRaises(sqlite3.DatabaseError):self.store.publish(bad,self.gz,h)
  self.assertEqual(self.store.head(),(h,p))
 def test_corrupt_remote_asset_cannot_restore(self):
  _,p=self.publish();asset=self.root/'store/versions'/p['current']['version']/'database.sqlite.gz';asset.write_bytes(b'corrupt')
  with self.assertRaises(ValueError):self.store.restore(self.root/'restore.sqlite')
 def test_dry_run_keeps_expired_registered_file(self):
  rel,p=self.file();r=cleanup(self.db,self.root);self.assertIn(rel,r['candidates']);self.assertTrue(p.exists());self.assertEqual(r['deleted'],[])
 def test_apply_never_deletes_files(self):
  rel,p=self.file()
  with self.assertRaisesRegex(RuntimeError,'LOCAL_DELETE_FORBIDDEN'):cleanup(self.db,self.root,apply=True)
  self.assertTrue(p.exists())
 def test_unregistered_file_never_cleaned(self):
  p=self.root/'squad_pipeline/raw/live/unregistered.response';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'permanent until classified');cleanup(self.db,self.root,apply=False);self.assertTrue(p.exists())
 def test_archive_cannot_register(self):
  with self.assertRaises(ValueError):register(self.db,self.root,'archive/old.response','RAW_RESPONSE','2020-01-01T00:00:00Z',declared_temporary=True)
 def test_sqlite_cannot_register_even_under_temporary_prefix(self):
  with self.assertRaises(ValueError):register(self.db,self.root,'squad_pipeline/raw/live/formal.sqlite','RAW_RESPONSE','2020-01-01T00:00:00Z',declared_temporary=True)
 def test_symlink_cannot_escape_to_archive(self):
  target=self.root/'archive/owned';target.parent.mkdir();target.write_bytes(b'never touch');link=self.root/'squad_pipeline/raw/live/link.response';link.parent.mkdir(parents=True);link.symlink_to(target)
  with self.assertRaises(ValueError):register(self.db,self.root,str(link.relative_to(self.root)),'RAW_RESPONSE','2020-01-01T00:00:00Z',declared_temporary=True)
  self.assertEqual(target.read_bytes(),b'never touch')
 def test_changed_file_is_protected(self):
  rel,p=self.file();p.write_bytes(b'changed after registration');r=cleanup(self.db,self.root,apply=False);self.assertTrue(p.exists());self.assertTrue(r['protected'])
 def test_unresolved_evidence_survives_older_than_seven_days(self):
  rel,p=self.file();doc=index_document(self.db,dict(url='https://example.com/source',path=rel,sha256=sha(p)));self.db.execute('UPDATE cloud_temporary_files SET document_id=?',(doc,));pin(self.db,self.root,doc,dict(path=rel,sha256=sha(p)),'IDENTITY','controlled','Unresolved synthetic identity');self.db.commit();r=cleanup(self.db,self.root,apply=False);self.assertTrue(p.exists());self.assertEqual(r['protected'][0]['reason'],'UNRESOLVED_EVIDENCE_HOLD');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM cloud_evidence_blobs').fetchone()[0],1)
 def test_malicious_registry_cannot_delete_database(self):
  self.db.execute("INSERT INTO cloud_temporary_files VALUES('squad_pipeline/state/live.sqlite','bad',1,'2020-01-01T00:00:00Z','RAW_RESPONSE',NULL,'PRESENT')");self.db.commit();self.assertTrue(cleanup(self.db,self.root,apply=False)['protected']);inspect(self.dbpath)
 def test_source_index_deduplicates(self):
  source=dict(url='https://example.com/fact',sha256='123',acquired_at='2026-10-28T00:00:00Z');index_document(self.db,source);index_document(self.db,source);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM cloud_documents').fetchone()[0],1)
 def test_bootstrap_index_does_not_change_facts(self):
  before=inspect(self.dbpath)['tables'];bootstrap_indexes(self.db,self.root);self.assertEqual(before,inspect(self.dbpath)['tables'])
 def test_first_formal_time_guard_exact(self):
  self.assertFalse(formal_due(datetime.fromisoformat('2026-10-31T15:59:59+08:00')));self.assertTrue(formal_due(datetime.fromisoformat('2026-10-31T16:00:00+08:00')))
 def test_deleted_fact_is_detected(self):
  target=self.root/'copy.sqlite';snapshot(self.dbpath,target);c=sqlite3.connect(target);c.execute('DELETE FROM player_match_minutes WHERE player_id=1');c.commit();c.close()
  with self.assertRaises(ValueError):validate_monotonic(self.dbpath,target)
 def test_export_binds_committed_version_and_string_ids(self):
  head,pointer=self.publish();out=self.root/'site';m=export(self.dbpath,out,head,pointer,ROOT/'web');self.assertEqual(m['version']['data_commit'],head);self.assertEqual(m['version']['sqlite_sha256'],sha(self.dbpath));self.assertFalse(m['national_history_complete']);self.assertFalse(m['first_team_population_complete']);players=json.loads((out/'data/players.json').read_text());self.assertIn('-9007199254740993',[p['player_id'] for p in players])
 def test_export_rejects_wrong_snapshot_version(self):
  head,p=self.publish();p['current']['database']['sha256']='wrong'
  with self.assertRaises(ValueError):export(self.dbpath,self.root/'site',head,p,ROOT/'web')
 def test_only_explicit_repository_allowed(self):
  for repo in ['', 'main', '../private', 'https://github.com/user/data']:
   with self.assertRaises(ValueError):GitHubStore(repo,'CONTROLLED_TEST_TOKEN')
 def test_old_log_compaction_keeps_minimal_receipt_and_facts(self):
  before=inspect(self.dbpath)['tables'];payload=json.dumps(dict(run_id='controlled-old-run',origin='github_actions_automatic',preflight=False,requests=20,results=[dict(club_id=8456,match_id='controlled-fixture',status='NEW_MATCH',match_date='2026-10-24',source={'url':'https://example.com/controlled','sha256':'abc'},unneeded_large_detail='x'*10000)],catalogs=[]));self.db.execute("INSERT INTO live_runs VALUES('controlled-old-run','2020-01-01T00:00:00Z','2020-01-01T00:01:00Z','github_actions_automatic','SUCCESS',?)",(payload,));self.db.commit();r=compact_operational_logs(self.db);self.assertEqual(r['compacted_run_details'],1);receipt=json.loads(self.db.execute("SELECT payload_json FROM live_runs WHERE run_id='controlled-old-run'").fetchone()[0]);self.assertEqual(receipt['results'][0]['match_id'],'controlled-fixture');self.assertNotIn('unneeded_large_detail',receipt['results'][0]);self.assertEqual(before,inspect(self.dbpath)['tables'])
 def test_workflow_schedule_and_serial_publication(self):
  d=json.loads((ROOT/'ops/cloud/templates/daily.yml').read_text());self.assertEqual(d['on']['schedule'],[{'cron':'0 8 * * *'}]);self.assertFalse(d['concurrency']['cancel-in-progress']);self.assertEqual(d['permissions'],{'contents':'read'});self.assertIn('FUTBOL_CLOUD_ENABLED',d['jobs']['collect']['if']);self.assertEqual(d['on']['workflow_dispatch']['inputs']['mode']['default'],'preflight')
 def test_workflow_does_not_commit_sqlite_or_force_push(self):
  raw=(ROOT/'ops/cloud/templates/daily.yml').read_text();self.assertNotIn('git push',raw);self.assertNotIn('--force',raw);self.assertIn('site',raw);self.assertIn('Verify public site data',raw)
 def test_workflow_uses_only_pinned_official_actions(self):
  import re
  for filename in ['ci.yml','daily.yml']:
   d=json.loads((ROOT/('.github/workflows' if filename=='ci.yml' else 'ops/cloud/templates')/filename).read_text())
   for job in d['jobs'].values():
    for step in job['steps']:
     if 'uses' in step:self.assertRegex(step['uses'],r'^actions/[a-z-]+@[a-f0-9]{40}$')
 def test_update_before_first_run_never_opens_storage(self):
  from unittest.mock import patch
  from squad_pipeline.cloud import driver
  work=self.root/'guard-only'
  with patch.object(driver,'require_mode'),patch.object(driver,'formal_due',return_value=False),patch.object(driver,'get_store',side_effect=AssertionError('Storage must not be touched')),patch('sys.argv',['driver','update','--backend','github','--work',str(work)]):
   import contextlib,io
   with contextlib.redirect_stdout(io.StringIO()):r=driver.main()
  self.assertEqual(r['requests'],0);self.assertEqual(r['status'],'WAITING_FOR_FIRST_FORMAL_RUN');self.assertEqual(list(work.glob('*.sqlite')),[])
 def test_monthly_health_receipt_is_not_daily_database_commit(self):
  from ops.cloud.health_receipt import due
  at=datetime.fromisoformat('2026-10-29T08:00:00+00:00');self.assertTrue(due(None,at));self.assertFalse(due({'recorded_at':'2026-10-28T08:00:00+00:00'},at));self.assertTrue(due({'recorded_at':'2026-09-01T08:00:00+00:00'},at))
 def test_declared_temporary_is_mandatory(self):
  with self.assertRaises(ValueError):register(self.db,self.root,'logs/live/anything.json','RUN_LOG','2020-01-01T00:00:00Z')
 def test_release_retention_plan_preserves_current_and_previous(self):
  h,p=self.publish();h,p=self.publish(h);plan=self.store.prune_plan();self.assertEqual(set(plan['keep']),{p['current']['version'],p['previous']['version']});self.assertEqual(plan['mode'],'DRY_RUN_ONLY_LOCAL')
if __name__=='__main__':unittest.main(verbosity=2)
