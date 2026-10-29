import unittest
from unittest.mock import Mock,patch
from squad_pipeline.cloud.export_publish import publish_with_export_preflight
class ExportPublicationTests(unittest.TestCase):
 def test_export_failure_does_not_advance_pointer(self):
  store=Mock(ref='acceptance/test')
  with patch('squad_pipeline.cloud.export_publish.inspect',return_value={'sha256':'db'}),patch('squad_pipeline.cloud.export_publish.export_all_matches',side_effect=RuntimeError('consumer schema missing')):
   with self.assertRaisesRegex(RuntimeError,'consumer schema'):publish_with_export_preflight(store,'not-a-real-local-file','not-a-real-gzip','a'*40,'b'*40,'not-a-real-directory','2026-10-29')
  store.publish.assert_not_called()
 def test_count_or_version_failure_does_not_advance_pointer(self):
  store=Mock(ref='acceptance/test')
  with patch('squad_pipeline.cloud.export_publish.inspect',return_value={'sha256':'db'}),patch('squad_pipeline.cloud.export_publish.export_all_matches'),patch('squad_pipeline.cloud.export_publish.validate_export',side_effect=ValueError('version mismatch')):
   with self.assertRaisesRegex(ValueError,'version'):publish_with_export_preflight(store,'unused','unused','a'*40,'b'*40,'unused','2026-10-29')
  store.publish.assert_not_called()
