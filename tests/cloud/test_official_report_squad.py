import json,unittest,tempfile
from squad_pipeline.live_parse import mancity_report_squad

class OfficialReportSquad(unittest.TestCase):
 url='https://www.mancity.com/news/mens/published-report'
 def body(self,xi=None,bench='Listed One, Listed Two',url=None):
  a={'@type':'NewsArticle','mainEntityOfPage':{'@id':url or self.url},'datePublished':'2025-01-01'}
  xi=xi or 'Keeper, Back One, Back Two, Back Three, Back Four, Mid One (C), Mid Two (Used One 55 - Used Two 100), Wide One, Wide Two, Forward One, Forward Two'
  return '<script type="application/ld+json">'+json.dumps(a)+'</script><p>CITY XI: '+xi+'</p><p>Subs : '+bench+'</p><p>OTHER XI: Opponent</p><p>Subs: Other</p>'
 def test_retains_chained_substitution_and_avoids_unused_or_minute_inference(self):
  r=mancity_report_squad(self.body(),self.url,8456)
  self.assertEqual(len(r['starters']),11);self.assertEqual(r['listed_bench'],['Listed One','Listed Two']);self.assertEqual(r['replacement_mentions'],['Used One 55 - Used Two 100']);self.assertFalse(r['unused_substitutes_confirmed']);self.assertFalse(r['complete_squad'])
 def test_wrong_returned_article_identity_is_held(self):
  with self.assertRaises(ValueError):mancity_report_squad(self.body(url=self.url+'-other'),self.url,8456)
 def test_wrong_club_or_path_is_held(self):
  for url,cid in [(self.url,9825),(self.url.replace('/mens/','/womens/'),8456)]:
   with self.assertRaises(ValueError):mancity_report_squad(self.body(),url,cid)
 def test_partial_or_duplicate_xi_is_held(self):
  for body in [self.body(xi='Only One'),self.body()+'<p>CITY XI: Another</p>',self.body().replace('Back One','Back Two'),self.body().replace('Subs :','Replacements :')]:
   with self.assertRaises(ValueError):mancity_report_squad(body,self.url,8456)
 def test_numeric_or_unclear_bench_is_held(self):
  with self.assertRaises(ValueError):mancity_report_squad(self.body(bench='Someone (55)'),self.url,8456)
 def test_retained_city_headings_and_explicit_unused_wording(self):
  for heading,bench in [('CITY:','Subs not used:'),('CITY |','Unused subs:')]:
   r=mancity_report_squad(self.body().replace('CITY XI:',heading).replace('Subs :',bench),self.url,8456)
   self.assertEqual(len(r['starters']),11);self.assertEqual(r['bench_wording']+':',bench);self.assertFalse(r['canonical_adjudication'])
 def test_narrative_city_or_opponent_bench_cannot_replace_missing_city_bench(self):
  for body in [self.body().replace('CITY XI:','City enjoyed:'),self.body().replace('<p>Subs :','<p>OTHER XI: Extra</p><p>Subs :',1)]:
   with self.assertRaises(ValueError):mancity_report_squad(body,self.url,8456)
 def test_source_boundary_reports_labels_without_fixture_or_full_squad_acceptance(self):
  from squad_pipeline.source_interface import SourceRequest,EvidenceStore,SourceService
  with tempfile.TemporaryDirectory() as d:
   store=EvidenceStore(d);req=SourceRequest('Mancity','match_report',self.url,'report',context={'club_id':8456},policy='ARCHIVE_ONLY')
   art=store.import_body(req,self.body().encode(),{'status':'SUCCESS','http_status':200})
   service=SourceService(store,'replay',set(),max_calls=0);a,o=service.observe(req,art)
   self.assertEqual(o.outcome,'PARSED');self.assertFalse(o.completeness['complete_squad']);self.assertNotIn('fixture',o.native);self.assertEqual(service.client.calls,0)
