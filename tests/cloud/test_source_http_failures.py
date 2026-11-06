import unittest,tempfile,http.client
from pathlib import Path
from squad_pipeline.core import ROOT
from squad_pipeline.source_interface import EvidenceStore,SourceService,SourceRequest
class SourceHTTPFailureTests(unittest.TestCase):
 def test_truncated_bytes_are_retained_charged_and_never_parsed_as_success(self):
  root=ROOT/'.cloud';root.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=root) as tmp:
   calls=[]
   def transport(url,headers):
    calls.append(url)
    if url.endswith('/robots.txt'):return 404,{},b''
    raise http.client.IncompleteRead(b'partial',100)
   service=SourceService(EvidenceStore(tmp),'truncated',{'source.example'},max_calls=3,max_bytes=100,transport=transport,sleeper=lambda _:None)
   request=SourceRequest('Controlled','match','https://source.example/m','fixture',policy='LIVE_ALLOWED');a,p=service.observe(request)
   self.assertIsNone(p);self.assertNotEqual(a.outcome,'FOUND');self.assertEqual(service.client.calls,3);self.assertEqual(service.bytes,14)
   self.assertEqual([r['size'] for r in service.client.records],[0,7,7]);self.assertTrue(all(r['status']!='SUCCESS' for r in service.client.records));self.assertEqual(service.client.records[-1]['error'],'IncompleteRead: truncated HTTP response')
 def test_other_http_protocol_failure_is_a_recorded_transport_failure(self):
  from squad_pipeline.live_sources import Client
  root=ROOT/'.cloud';root.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=root) as tmp:
   def transport(url,headers):raise http.client.BadStatusLine('invalid response')
   client=Client('protocol-failure',budget=2,transport=transport,sleeper=lambda _:None,storage_root=tmp)
   record,body=client.raw('https://source.example/m','fixture');self.assertEqual(client.calls,2);self.assertEqual(record['status'],'SOURCE_UNAVAILABLE');self.assertEqual(body,b'')
