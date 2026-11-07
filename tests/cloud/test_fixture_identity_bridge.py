import copy,unittest
from squad_pipeline.identity_pipeline import Candidate,Record,verify_identity
class FixtureIdentityBridge(unittest.TestCase):
 def data(self):
  fixture=dict(match_id='accepted-match',domain='CLUB',home_key='home',away_key='club:9847',competition_key='53',kickoff_utc='2025-01-01T18:00:00+00:00',status='FINISHED',home_score=1,away_score=5)
  anchor=Record('https://torlytics.de/spiel/accepted-match','b'*64,'bench','Torlytics',['Observed Player'],provider_ids={'Torlytics':'https://torlytics.de/spieler/observed-player'},raw_fields={'_verified_identity':True})
  target=dict(name='Observed Player',shirt='42',role='bench',occurrences=1)
  b=dict(canonical_player_id='-7',raw_bodies_hash_verified=True,source_resource_identity_verified=True,anchor_resource_identity_verified=True,source_squad_complete=True,anchor_squad_complete=True,source_starter_count=11,anchor_starter_count=11,source_namespace='PSG_STATS_PERFORM',source_player_id='native-id',anchor_provider='Torlytics',anchor_player_id=anchor.provider_ids['Torlytics'],anchor_source_url=anchor.source_url,anchor_source_sha256=anchor.source_sha256,accepted_fixture=copy.deepcopy(fixture),source_fixture=copy.deepcopy(fixture),anchor_fixture=copy.deepcopy(fixture),source_target=copy.deepcopy(target),anchor_target=copy.deepcopy(target),corroborated_teammates=[dict(canonical_player_id=str(i),source_id='native-'+str(i),anchor_id='source-'+str(i)) for i in range(8)])
  r=Record('https://www.psg.fr/en/matches/mens-football/20242025/accepted-match','a'*64,'native-squad','OfficialClub',['Observed Player'],provider_ids={'PSG_STATS_PERFORM':'native-id'},authority='OFFICIAL',scope='IDENTITY_ONLY',raw_fields={'same_fixture_identity_bridge':b})
  return r,Candidate(-7,['Observed Player'],[anchor])
 def test_closed_context_proves_source_id_correspondence_only(self):
  r,c=self.data();out=verify_identity(r,c);self.assertTrue(out['verified']);self.assertIn('NO_MANAGEMENT_INTERVAL',out['proof'][0]['scope'])
 def test_wrong_fixture_target_or_incomplete_squad_cannot_name_merge(self):
  for kind in ['fixture','shirt','name','occurrence','squad']:
   r,c=self.data();b=r.raw_fields['same_fixture_identity_bridge']
   if kind=='fixture':b['source_fixture']['match_id']='another'
   elif kind=='squad':b['source_squad_complete']=False
   elif kind=='shirt':b['source_target']['shirt']='43'
   elif kind=='name':b['source_target']['name']='Other Player'
   else:b['source_target']['occurrences']=2
   self.assertFalse(verify_identity(r,c)['verified'])
 def test_shared_body_unknown_anchor_or_duplicate_teammates_is_insufficient(self):
  for kind in ['body','anchor','teammates']:
   r,c=self.data();b=r.raw_fields['same_fixture_identity_bridge']
   if kind=='body':b['anchor_source_sha256']=r.source_sha256
   elif kind=='anchor':c.records[0].raw_fields['_verified_identity']=False
   else:b['corroborated_teammates'][1]=b['corroborated_teammates'][0]
   self.assertFalse(verify_identity(r,c)['verified'])
 def test_wrong_native_namespace_or_canonical_person_is_insufficient(self):
  for field,value in [('source_namespace','PSG'),('canonical_player_id','-8'),('source_player_id','other'),('source_player_id',None),('anchor_provider',None),('anchor_player_id',None)]:
   r,c=self.data();r.raw_fields['same_fixture_identity_bridge'][field]=value;self.assertFalse(verify_identity(r,c)['verified'])
