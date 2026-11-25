import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from squad_pipeline.core import ROOT
from ops.cloud.production_entry import run_audits,with_audits

class DailyAuditIntegrationTests(unittest.TestCase):
 def test_daily_wrapper_preserves_original_evidence_and_records_failed_audit(self):
  import tarfile,io,hashlib,contextlib
  (ROOT/'.cloud').mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=ROOT/'.cloud') as directory:
   work=Path(directory);database=work/'candidate.sqlite';sqlite3.connect(database).close()
   archive=work/'original.tar.gz';body=b'{"sha256":"retained-test-source"}'
   with tarfile.open(archive,'x:gz') as target:
    member=tarfile.TarInfo('metadata/source.json');member.size=len(body);target.addfile(member,io.BytesIO(body))
   digest=hashlib.sha256(archive.read_bytes()).hexdigest()
   def original(*args,**kwargs):return dict(evidence_archive=str(archive),formal_ingestion=True,evidence_tag='retained-tag')
   with patch('ops.cloud.production_entry.run_audits',side_effect=RuntimeError('test operational failure')),contextlib.redirect_stdout(io.StringIO()):
    result=with_audits(original)(database,work)
   self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),digest)
   self.assertEqual(result['evidence_tag'],'retained-tag');self.assertTrue(result['formal_ingestion'])
   with tarfile.open(result['evidence_archive'],'r:gz') as target:
    self.assertEqual(target.extractfile('metadata/source.json').read(),body)
    receipt=json.load(target.extractfile('audits/summary.json'))
   self.assertEqual(receipt['operational_errors'][0]['classification'],'OPERATIONAL_FAILURE')
 def exercise(self,fail):
  (ROOT/'.cloud').mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=ROOT/'.cloud') as directory:
   work=Path(directory);database=work/'candidate.sqlite'
   db=sqlite3.connect(database);db.executescript('CREATE TABLE clubs(club_id INTEGER);INSERT INTO clubs VALUES(1);CREATE TABLE accepted_fact(value INTEGER);INSERT INTO accepted_fact VALUES(7);');db.close()
   def audit(db,adapter,start,cutoff,**kwargs):
    if fail:
     db.execute('UPDATE accepted_fact SET value=99')
     raise ValueError('test operational failure')
    return dict(metrics={'resolved':1},calendar_coverage='OBSERVED_ONLY;OFFICIAL_CALENDAR_UNAVAILABLE')
   with patch.dict(os.environ,{'GITHUB_RUN_ID':'test'}),patch('squad_pipeline.reconstruction.engine.run',side_effect=audit) as called,patch('squad_pipeline.reconstruction.retained_adapter.RetainedAdapter'):
    result,records=run_audits(database,work/'audits',[],'2026-11-07','2026-11-10')
   db=sqlite3.connect(database);self.assertEqual(db.execute('SELECT value FROM accepted_fact').fetchone()[0],7);db.close()
   self.assertEqual(called.call_count,1);self.assertEqual(records,[])
   self.assertEqual(json.loads((work/'audits/summary.json').read_text()),result)
   return result
 def test_connected_engine_disposition_is_reported(self):
  result=self.exercise(False);self.assertEqual(result['clubs'][0]['metrics']['resolved'],1);self.assertEqual(result['operational_errors'],[])
 def test_operational_failure_rolls_back_and_stays_separate_from_football(self):
  result=self.exercise(True);self.assertEqual(result['clubs'],[]);self.assertEqual(result['operational_errors'][0]['classification'],'OPERATIONAL_FAILURE')
