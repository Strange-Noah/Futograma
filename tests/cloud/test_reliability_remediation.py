import unittest,json
from squad_pipeline.production_reliability import timeline,freshness
from squad_pipeline.source_identity_review import routing_review,shared_routes
class Reliability(unittest.TestCase):
 def test_late_dispatch(self):
  t=timeline(dict(id=1,event='schedule',created_at='2026-11-02T17:38:46Z',jobs=[dict(started_at='2026-11-02T17:39:06Z',completed_at='2026-11-02T17:52:28Z')]))
  self.assertEqual(t['pre_creation_delay_seconds'],34726);self.assertEqual(t['created_to_first_job_seconds'],20)
 def test_zero_additions_fresh(self):self.assertEqual(freshness('2026-11-03T18:00:00Z','2026-11-03T17:00:00Z')['state'],'FRESH')
 def test_lock_alignment(self):
  args=('2026-11-03T18:00:00Z','2026-11-02T10:00:00Z')
  self.assertEqual(freshness(*args,active=True,authority_matches=True)['action'],'WAIT_ACTIVE_RUN');self.assertEqual(freshness(*args)['action'],'VERIFY_VERSION_ALIGNMENT');self.assertEqual(freshness(*args,authority_matches=True)['action'],'CATCH_UP_ELIGIBLE_UNDER_EXISTING_LOCK')
 def test_unknown_future(self):
  self.assertEqual(freshness('2026-11-03T18:00:00Z',None)['state'],'UNKNOWN')
  with self.assertRaises(ValueError):freshness('2026-11-03T18:00:00Z','2026-11-04T18:00:00Z')
 def test_boundaries(self):
  self.assertEqual(freshness('2026-11-03T12:00:00Z','2026-11-02T10:00:00Z')['state'],'FRESH');self.assertEqual(freshness('2026-11-03T16:00:00Z','2026-11-02T10:00:00Z')['state'],'WARNING')
 def test_shared_route(self):
  raw='<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'data':{'general':{'matchId':3},'content':{'lineup':{'matchId':3}}}}}})+'</script>'
  rs=[routing_review(raw,str(i),dict(url='https://www.fotmob.com/matches/a/b#'+str(i),sha256='same')) for i in [1,2]]
  self.assertTrue(shared_routes(rs)[0]['ambiguous_route']);self.assertTrue(all(not r['accept'] and not r['retry_authorized'] for r in rs))

 def test_pages_multiple_slots(self):
  t=timeline(dict(id=2,event='schedule',created_at='2026-11-02T18:41:49Z',jobs=[]),((8,30),(9,30),(10,30)))
  self.assertEqual(t['expected_trigger'],'2026-11-02T10:30:00+00:00')

 def test_durable_receipt_timing_failure(self):
  from ops.cloud.production_report import enrich_timing
  class Broken:
   def api(self,*a):raise RuntimeError('unavailable')
  source=dict(run_id='1',status='COMPLETED',match_counts={'new':0})
  result=enrich_timing(Broken(),source)
  self.assertEqual(result['status'],'COMPLETED');self.assertEqual(result['match_counts'],{'new':0});self.assertEqual(result['timing']['state'],'UNAVAILABLE');self.assertNotIn('timing',source)
 def test_durable_receipt_metadata(self):
  from ops.cloud.production_report import enrich_timing
  class Store:
   base='repo'
   def api(self,method,path):return {'jobs':[]} if '/jobs?' in path else dict(id=1,event='schedule',created_at='2026-11-02T17:38:46Z')
  self.assertEqual(enrich_timing(Store(),dict(run_id='1'))['timing']['pre_creation_delay_seconds'],34726)
