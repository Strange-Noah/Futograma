import unittest,json,hashlib
from squad_pipeline.cloud.release_gate import require_ci,verify_frontend,status_matrix
class ReleaseGates(unittest.TestCase):
    def fixture(self):
        version=dict(data_commit='data',sqlite_sha256='hash',data_version='v',code_commit='code')
        body=json.dumps(dict(schema_version=2,version=version)).encode()
        return dict(version=version,schema_version=2,browser_sha256=hashlib.sha256(body).hexdigest()),dict(current=dict(database=dict(sha256='hash'),version='v',code_commit='code')),body
    def test_same_commit_ci_required(self):
        with self.assertRaisesRegex(ValueError,'NOT_FOUND'):require_ci([], 'new')
        run=dict(id=1,head_sha='new',path='.github/workflows/ci.yml',head_repository=dict(full_name='Strange-Noah/Futograma'),run_number=1,status='completed',conclusion='failure')
        with self.assertRaisesRegex(ValueError,'NOT_SUCCESSFUL'):require_ci([run],'new')
        run['conclusion']='success';self.assertEqual(require_ci([run],'new'),1)
    def test_newer_failed_rerun_blocks_old_success(self):
        base=dict(head_sha='s',path='.github/workflows/ci.yml',head_repository=dict(full_name='Strange-Noah/Futograma'),run_number=1,status='completed')
        with self.assertRaises(ValueError):require_ci([dict(base,id=1,run_attempt=1,conclusion='success'),dict(base,id=1,run_attempt=2,conclusion='failure')],'s')
    def test_source_drift_is_blocked(self):
        m,p,b=self.fixture()
        with self.assertRaisesRegex(ValueError,'FRONTEND'):verify_frontend(m,p,'data','old',b)
    def test_same_source_and_data_pass(self):
        m,p,b=self.fixture();self.assertEqual(verify_frontend(m,p,'data','code',b)['frontend_commit'],'code')
    def test_tampering_blocks_publication(self):
        m,p,b=self.fixture()
        with self.assertRaisesRegex(ValueError,'HASH'):verify_frontend(m,p,'data','code',b+b' ')
    def test_skipped_not_pass_and_degraded_not_ready(self):
        r=status_matrix(dict(software='PASS',live_sources='DEGRADED',production_authority='DISABLED'))
        self.assertFalse(r['production_ready']);self.assertEqual(r['website'],'PENDING')
