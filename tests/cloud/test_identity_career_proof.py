import unittest
from squad_pipeline.identity_pipeline import Candidate,Record,verify_identity,management_state
class CareerIdentityProofTests(unittest.TestCase):
 def test_retained_barcelona_profile_keeps_identity_only_scope(self):
  from squad_pipeline.identity_sources import current_official_profile
  url='https://www.fcbarcelona.com/en/football/first-team/players/123/example'
  capture={'url':url,'sha256':'official-sha','status':'SUCCESS'}
  raw='<link rel="canonical" href="'+url+'"><span class="player-hero__just-name">Example <strong>Person</strong></span><div class="player-strip__info"><div class="player-strip__label">Date of birth</div><div class="player-strip__data">03/02/2001</div></div>'
  record=current_official_profile(raw,capture,8634)[0]
  self.assertEqual(record.birth_date,'2001-02-03');self.assertEqual(record.scope,'IDENTITY_ONLY');self.assertFalse(management_state([record],'2026-11-09')['included'])
  self.assertEqual(current_official_profile(raw.replace('href="'+url+'"','href="'+url+'-other"'),capture,8634),[])
 def test_departed_laliga_native_biography_is_not_missing_statistics_or_membership(self):
  import json
  from squad_pipeline.identity_sources import laliga_profile
  capture={'url':'https://www.laliga.com/en-GB/player/example','sha256':'official-sha','status':'SUCCESS'}
  person={'id':123,'slug':'example','name':'Example Person','nickname':'Example','date_of_birth':'2001-02-03T00:00:00Z','roles':[{'slug':'jugador'}]}
  def page():return '<p>We do not have statistics</p><script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'player':person}}})+'</script>'
  record=laliga_profile(page(),capture)[0];self.assertEqual(record.birth_date,'2001-02-03');self.assertEqual(record.official_clubs,[])
  person['slug']='other'
  self.assertEqual(laliga_profile(page(),capture),[])
  person['slug']='example';person['date_of_birth']=None
  self.assertEqual(laliga_profile(page(),capture),[])
 def test_laliga_legal_club_name_requires_same_native_object(self):
  import json
  from squad_pipeline.identity_sources import laliga_profile
  capture={'url':'https://www.laliga.com/en-GB/player/example','sha256':'official-sha','status':'SUCCESS'}
  native={'name':'Example Person','date_of_birth':'2001-02-03T00:00:00Z','squads':[{'team':{'name':'Legal Club Name','nickname':'FC Barcelona'}}]}
  def page(player):return '<h1>Example Person</h1><h2>Legal Club Name</h2><p>DATE OF BIRTH 03-02-2001</p><script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'player':player}}})+'</script>'
  self.assertEqual(laliga_profile(page(native),capture)[0].official_clubs,['8634'])
  native['squads'][0]['team']['name']='Another Legal Club'
  self.assertEqual(laliga_profile(page(native),capture)[0].official_clubs,[])
  native['date_of_birth']='2000-02-03'
  with self.assertRaisesRegex(ValueError,'DOM_NATIVE_IDENTITY_CONFLICT'):laliga_profile(page(native),capture)
 def test_official_historical_identity_reuses_verified_departed_player_career(self):
  profile=Record('https://www.fotmob.com/players/123','profile-sha','profile','FotMob',['Example Person'],provider_ids={'FotMob':'123'},birth_date='1995-02-06',official_clubs=['999'],raw_fields={'career_clubs':['9823'],'_verified_identity':True})
  historical=Record('https://official.example/2024-25/person','official-sha','historical-card','DFB',['Example Person'],birth_date='1995-02-06',official_clubs=['9823'],authority='OFFICIAL',scope='IDENTITY_ONLY_OBSERVED_MATCHDAY_ENTITY')
  candidate=Candidate(123,['Example Person'],[profile]);result=verify_identity(historical,candidate)
  self.assertTrue(result['verified']);self.assertIn('DOCUMENTED_CAREER',str(result['proof']));self.assertFalse(management_state([historical],'2026-11-09')['included'])
  for field,value in [('birth_date','1994-02-06'),('official_clubs',['888']),('names',['Unrelated Individual']),('scope','FIRST_TEAM_CARD')]:
   from dataclasses import replace
   self.assertFalse(verify_identity(replace(historical,**{field:value}),candidate)['verified'])
  profile.raw_fields['_verified_identity']=False
  self.assertFalse(verify_identity(historical,candidate)['verified'])
 def test_youth_primary_team_and_dated_senior_career_only_verify_identity(self):
  official=Record('https://official.example/person','official-sha','dob','Official',['Example Person'],birth_date='2008-02-08',official_clubs=['8456'],authority='OFFICIAL');candidate=Candidate(-1,['Example Person'],[official]);fresh=Record('https://www.fotmob.com/players/123','fresh-sha','profile','FotMob',['Example Person'],provider_ids={'FotMob':'123'},birth_date='2008-02-08',official_clubs=['860800'],scope='IDENTITY_ONLY',raw_fields={'career_clubs':['8456']});proof=verify_identity(fresh,candidate);self.assertTrue(proof['verified']);self.assertIn('DOCUMENTED_CAREER',str(proof['proof']));self.assertFalse(management_state([fresh],'2026-10-29')['included'])
 def test_matching_career_never_excuses_wrong_birthday(self):
  official=Record('https://official.example/person','official-sha','dob','Official',['Example Person'],birth_date='2008-02-08',official_clubs=['8456'],authority='OFFICIAL');candidate=Candidate(-1,['Example Person'],[official]);fresh=Record('https://www.fotmob.com/players/123','fresh-sha','profile','FotMob',['Example Person'],provider_ids={'FotMob':'123'},birth_date='2007-02-08',official_clubs=['860800'],scope='IDENTITY_ONLY',raw_fields={'career_clubs':['8456']});self.assertFalse(verify_identity(fresh,candidate)['verified'])
