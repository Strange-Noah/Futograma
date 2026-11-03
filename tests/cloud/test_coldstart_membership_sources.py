import unittest,json,hashlib
from squad_pipeline.coldstart_membership_sources import build_observations

def capture(data,name,url):
 raw='<script id="'+name+'">'+json.dumps(data)+'</script>';return raw,dict(url=url,sha256=hashlib.sha256(raw.encode()).hexdigest())
class SourceMembershipTests(unittest.TestCase):
 def fixture(self,dob='2000-01-01',count=1):
  players=[dict(id=i+1,name='Example Player',dateOfBirth='2000-01-01') for i in range(count)]
  f=capture({'props':{'pageProps':{'fallback':{'team-10':{'squad':{'squad':[{'title':'players','members':players}]}}}}}},'__NEXT_DATA__','https://www.fotmob.com/teams/10/squad')
  card=dict(slug='example-player',_path='/players/example-player',nickname='Example')
  o=capture({'abc':{'seasonList':{'items':[{'squad':{'players':[card]}}]}}},'ng-state','https://official.example/first-team')
  profile=dict(card,name='Example',surnames='Player',birthDate=dob)
  d=capture({'abc':{'playerList':{'items':[profile]}}},'ng-state','https://official.example/first-team/players/example-player')
  return f,o,d
 def build(self,f,o,d):return build_observations(f[0],f[1],o[0],o[1],[(d[1],d[0])],10,'2026-11-03')
 def test_independent_dob_official_bridge(self):
  r=self.build(*self.fixture());self.assertEqual(r['verified_membership_count'],1);o=r['observations'][0];self.assertIsNone(o['effective_from']);self.assertEqual(o['observed_on'],'2026-11-03');self.assertNotIn('headers',o['evidence'][0])
 def test_dob_conflict_stays_provider_asserted(self):
  r=self.build(*self.fixture('2001-01-01'));self.assertEqual(r['verified_membership_count'],0);self.assertEqual(r['observations'][0]['verification_status'],'PROVIDER_ASSERTED')
 def test_ambiguous_identity_not_merged(self):self.assertEqual(self.build(*self.fixture(count=2))['verified_membership_count'],0)
 def test_hash_mismatch_rejected(self):
  f,o,d=self.fixture();f=(f[0]+'x',f[1])
  with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):self.build(f,o,d)
 def test_missing_exact_team_rejected(self):
  f,o,d=self.fixture();f=capture({'props':{'pageProps':{'fallback':{'team-11':{}}}}},'__NEXT_DATA__',f[1]['url'])
  with self.assertRaisesRegex(ValueError,'EXACT_PROVIDER_TEAM'):self.build(f,o,d)
 def test_wrong_official_detail_url_rejected_as_missing(self):
  f,o,d=self.fixture();d[1]['url']='https://official.example/first-team/players/wrong-player';r=self.build(f,o,d);self.assertEqual(r['official_profile_count'],0)
 def test_missing_profile_keeps_official_denominator(self):
  f,o,d=self.fixture();r=build_observations(f[0],f[1],o[0],o[1],[],10,'2026-11-03');self.assertEqual(r['official_expected_count'],1);self.assertEqual(r['verified_membership_count'],0)
 def academy_fixture(self,dob='2000-01-01',path='/players/example-player'):
  provider={'id':1,'name':'Example Player','birthDate':{'utcTime':'2000-01-01T00:00:00Z'},'isCoach':False,'gender':'male','primaryTeam':{'teamId':11},'careerHistory':{'careerItems':{'senior':{'teamEntries':[{'participantId':1,'teamId':10}]}}}}
  f=capture({'props':{'pageProps':{'data':provider}}},'__NEXT_DATA__','https://www.fotmob.com/players/1')
  card=dict(slug='example-player',_path='/players/example-player',nickname='Example Player')
  o=capture({'data':{'seasonList':{'items':[{'squad':{'players':[card]}}]}}},'ng-state','https://official.example/football/academy/reserve')
  d=capture({'data':{'playerList':{'items':[dict(card,_path=path,name='Example',surnames='Player',birthDate=dob)]}}},'ng-state','https://official.example/football/academy/reserve/example-player')
  return f,o,d
 def academy_build(self,f,o,d):
  from squad_pipeline.coldstart_membership_sources import build_academy_observations
  return build_academy_observations([(f[1],f[0])],[(o[1],o[0])],[(d[1],d[0])],10,'2026-11-04')
 def test_academy_requires_independent_identity_not_age(self):
  r=self.academy_build(*self.academy_fixture());self.assertEqual(r['verified_youth_count'],1);self.assertEqual(r['observations'][0]['assertion'],'YOUTH');self.assertIsNone(r['observations'][0]['effective_from'])
 def test_academy_dob_conflict_stays_pending(self):self.assertEqual(self.academy_build(*self.academy_fixture('2001-01-01'))['verified_youth_count'],0)
 def test_academy_cms_path_conflict_stays_pending(self):self.assertEqual(self.academy_build(*self.academy_fixture(path='/different-person'))['verified_youth_count'],0)
 def test_cms_locale_is_not_person_identity(self):
  from squad_pipeline.coldstart_membership_sources import cms_identity
  a='/content/dam/portals/site/es-es/sports/football/team/players/example'
  b='/content/dam/portals/site/en-us/sports/football/team/players/example'
  self.assertEqual(cms_identity(a),cms_identity(b));self.assertNotEqual(cms_identity(a),cms_identity(b.replace('/example','/another')))
