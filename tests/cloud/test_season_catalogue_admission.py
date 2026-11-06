import unittest,tempfile
from pathlib import Path
from squad_pipeline.core import ROOT
from squad_pipeline.source_interface import SourceService,EvidenceStore,SourceRequest
class SeasonCatalogueAdmission(unittest.TestCase):
 def setUp(self):
  base=ROOT/'.cloud';base.mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=base);self.store=EvidenceStore(self.tmp.name);self.calls=[]
  def transport(url,headers):self.calls.append(url);return 404,{},b''
  self.service=SourceService(self.store,'catalogue-test',{'torlytics.de'},transport=transport,sleeper=lambda n:None)
  self.url='https://torlytics.de/verein/example?season=178';self.parent=self.store.import_body(SourceRequest('Torlytics','fixture_catalogue','https://torlytics.de/verein/example','catalogue'),f'<a href="{self.url}">2024/2025</a>'.encode(),dict(http_status=200))
 def tearDown(self):self.tmp.cleanup()
 def test_default_query_restriction_remains(self):
  self.service.client.fetch(self.url,'catalogue');self.assertEqual(self.calls,[])
 def test_exact_disclosed_link_grant_only(self):
  grant=self.service.admit_season_catalogue_link(self.parent,self.url);self.assertEqual(grant['parent_sha256'],self.parent.content_sha256)
  self.service.client.fetch(self.url,'catalogue');self.assertIn(self.url,self.calls)
  before=len(self.calls);self.service.client.fetch(self.url+'&other=1','catalogue');self.assertEqual(len(self.calls),before)
 def test_undisclosed_and_other_club_and_api_rejected(self):
  for url in [self.url.replace('178','184'),self.url.replace('example','other'),'https://torlytics.de/api/data?season=178']:
   with self.assertRaises(ValueError):self.service.admit_season_catalogue_link(self.parent,url)
 def test_corrupt_parent_does_not_grant_access(self):
  (Path(self.tmp.name)/self.parent.locator).write_bytes(b'tampered')
  with self.assertRaises(ValueError):self.service.admit_season_catalogue_link(self.parent,self.url)

 def test_empty_catalogue_does_not_prove_no_matches(self):
  request=SourceRequest('Sky','fixture_catalogue','https://www.skysports.com/example-scores-fixtures/2025-01-01','catalogue',context=dict(club_id=1,as_of='2025-01-01'))
  a=self.store.import_body(request,b'<html><body>No fixtures shown</body></html>',dict(http_status=200));_,parsed=self.service.observe(request,a)
  self.assertEqual(parsed.outcome,'INCOMPLETE');self.assertFalse(parsed.completeness['known_no_fixtures']);self.assertFalse(parsed.completeness['season_complete'])

 def test_mislabelled_other_origin_cannot_grant(self):
  from dataclasses import replace
  parent=replace(self.parent,request=replace(self.parent.request,url='https://other.example/verein/example'))
  with self.assertRaises(ValueError):self.service.admit_season_catalogue_link(parent,self.url)
