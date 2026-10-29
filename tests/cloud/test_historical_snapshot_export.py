import unittest,sqlite3,json,tempfile
from pathlib import Path
from fixtures import database
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.quality import inspect
from squad_pipeline.live_ingest import connect
from squad_pipeline.managed_roster import migrate as management_migrate
from squad_pipeline.international_store import migrate
from squad_pipeline.cloud.all_match_export import export_all_matches
class HistoricalSnapshotExportTests(unittest.TestCase):
 def test_old_snapshot_without_retry_queue_exports_readonly(self):
  try:require_hosted()
  except RuntimeError as e:raise unittest.SkipTest(str(e))
  folder=ROOT/'.cloud/test-historical-export';folder.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(dir=folder) as directory:
   path=Path(directory)/'fixture.sqlite';database(path);db=connect(path);management_migrate(db);db.execute('DROP TABLE match_retry_queue');db.commit();migrate(db);db.close();before=inspect(path);pointer=dict(current=dict(database=before,version='synthetic-only',code_commit='a'*40));out=Path(directory)/'site';manifest=export_all_matches(path,out,'b'*40,pointer,'2026-10-29',True);data=json.loads((out/'data/browser.json').read_text());self.assertEqual(data['club_retry_queue_status'],'NOT_INITIALIZED;NO_COMPLETENESS_CLAIM');self.assertEqual(before['sha256'],inspect(path)['sha256']);self.assertEqual(manifest['schema_version'],3)
