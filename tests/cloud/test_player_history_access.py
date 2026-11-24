"""Opt-in access boundary, independent of canonical football acceptance."""
import json,tempfile,unittest
from pathlib import Path
from squad_pipeline.source_interface import EvidenceStore,SourceService,SourceRequest

class HistoryAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=EvidenceStore(Path(self.tmp.name))
        self.calls=[]
        def transport(url,headers):
            self.calls.append(url)
            return (200,{},b'User-agent: *\nDisallow: /prod/') if url.endswith('/robots.txt') else (200,{},b'{}')
        self.service=SourceService(self.store,'test-history',{'www.fotmob.com','pub.fotmob.com'},transport=transport,sleeper=lambda x:None)
        self.url='https://pub.fotmob.com/prod/db/api/player/41618/matches?before=1778342400&leagueId=&teamId='
        self.request=SourceRequest('FotMob','profile','https://www.fotmob.com/players/41618','observed',context={'club_id':8636})
        self.body=b'<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'data':{'id':41618,'initialMatchesPage':{'matches':[],'previous':self.url}}}}}).encode()+b'</script>'
        self.parent=self.store.import_body(self.request,self.body,{'http_status':200})
    def admit(self,parent=None,url=None,observed=None):
        return self.service.admit_player_history_link(parent or self.parent,url or self.url,{8636:['41618']} if observed is None else observed)
    def test_default_denies_without_network(self):
        self.assertEqual(self.service.client.fetch(self.url,'test')[0]['error'],'SOURCE_POLICY_RESTRICTED');self.assertEqual(self.calls,[])
    def test_disclosure_admitted_but_robots_retained(self):
        self.admit();self.assertIn(self.url,self.service.client.player_history_allowlist)
        self.assertEqual(self.service.client.fetch(self.url,'test')[0]['error'],'ROBOTS_DISALLOWED');self.assertEqual(len(self.calls),1)
    def test_identity_scope_and_url_variants_rejected(self):
        for url in [self.url.replace('41618','41619'),self.url+'&x=1',self.url.replace('pub.fotmob.com','www.fotmob.com'),self.url+'#x',self.url.replace('leagueId=','leagueId=1')]:
            with self.assertRaises(ValueError):self.admit(url=url)
        with self.assertRaises(ValueError):self.admit(observed={})
        self.assertEqual(self.calls,[])
    def test_tampered_parent_rejected(self):
        (self.store.root/self.parent.locator).write_bytes(b'changed')
        with self.assertRaises(ValueError):self.admit()
    def test_followup_requires_chain_and_decreasing_cursor(self):
        lower=self.url.replace('1778342400','1770000000')
        request=SourceRequest('FotMob','player_history',self.url,'observed',context={'club_id':8636})
        page=self.store.import_body(request,json.dumps({'matches':[],'previous':lower}).encode(),{'http_status':200})
        with self.assertRaises(ValueError):self.admit(page,lower)
        self.admit();self.admit(page,lower)
        same=self.store.import_body(request,json.dumps({'matches':[],'previous':self.url}).encode(),{'http_status':200})
        with self.assertRaises(ValueError):self.admit(same)
        with self.assertRaises(ValueError):self.admit(page,lower.replace('1770000000','1719791999'))
        self.assertEqual(self.calls,[])
