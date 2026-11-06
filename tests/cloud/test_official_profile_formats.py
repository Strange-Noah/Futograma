import json,unittest
from squad_pipeline.identity_sources import person_profile,profile_birth_date
class OfficialProfileFormats(unittest.TestCase):
 def capture(self,url):return dict(url=url,sha256='a'*64,response_timestamp='2026-11-06T17:00:00Z',status='SUCCESS')
 def next(self,data):return '<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':data}})+'</script>'
 def test_dob_formats_normalize_without_slicing_human_month(self):
  self.assertEqual(profile_birth_date('26 February 2002'),'2002-02-26');self.assertEqual(profile_birth_date('2002-02-26T00:00:00.000Z'),'2002-02-26')
  for value in ['26 Februar','30 February 2002','unknown']:
   with self.assertRaises(ValueError):profile_birth_date(value)
 def test_jsonld_english_profile_date_is_iso(self):
  raw='<script type="application/ld+json">'+json.dumps({'@type':'Person','name':'Observed Player','birthDate':'15 November 2005'})+'</script>';r=person_profile(raw,self.capture('https://www.fcbarcelona.com/en/football/first-team/players/7/observed'),8634)[0];self.assertEqual(r.birth_date,'2005-11-15');self.assertEqual(r.raw_fields['raw_birth_date'],'15 November 2005')
 def city(self):return {'id':7,'name':'Common Name','firstName':'Full','lastName':'Legal Name','urlName':'/common-name','dateOfBirth':'2000-01-01','team':'mens','isMensTeam':True,'isWomensTeam':False,'isCoach':False}
 def test_city_dedicated_entity_ignores_related_players_and_preserves_explicit_names(self):
  raw=self.next({'playerProfileData':self.city(),'playersListData':{'unrelated':{'dateOfBirth':'1990-01-01'}}});r=person_profile(raw,self.capture('https://www.mancity.com/players/common-name'),8456)[0];self.assertEqual(r.provider_ids,{'City':'7'});self.assertEqual(r.names,['Common Name','Full Legal Name']);self.assertEqual(r.scope,'IDENTITY_ONLY')
 def test_city_other_slug_or_scope_is_held(self):
  for field,value in [('urlName','/someone-else'),('isMensTeam',False),('isCoach',True)]:
   d=self.city();d[field]=value
   with self.assertRaises(ValueError):person_profile(self.next({'playerProfileData':d}),self.capture('https://www.mancity.com/players/common-name'),8456)
 def inter(self):return {'sys':{'id':'native-7'},'firstName':'Full','lastName':'Legal Name','slug':'common-name','dateOfBirth':'2000-01-01T00:00:00.000Z','team':{'slug':'first-team'},'role':{'name':'Midfielders'}}
 def test_inter_native_id_full_legal_name_and_date(self):
  r=person_profile(self.next({'player':self.inter()}),self.capture('https://www.inter.it/en/teams/first-team/player/common-name'),8636)[0];self.assertEqual(r.names,['Full Legal Name']);self.assertEqual(r.birth_date,'2000-01-01');self.assertEqual(r.provider_ids,{'Inter':'native-7'})
 def test_inter_wrong_slug_or_team_not_accepted(self):
  for field,value in [('slug','another'),('team',{'slug':'women'})]:
   p=self.inter();p[field]=value
   with self.assertRaises(ValueError):person_profile(self.next({'player':p}),self.capture('https://www.inter.it/en/teams/first-team/player/common-name'),8636)
 def psg(self,people):
  frame=[1,'1:'+json.dumps(people)];return '<meta property="og:title" content="Full Name"><script>self.__next_f.push('+json.dumps(frame)+');</script>'
 def person(self,pid=7):return {'id':pid,'type':'Player','gender':'Male','fullName':'Full Legal Name','birthDate':'2000-01-01','teamId':'team','teamType':'Football'}
 def test_psg_reuses_frame_decoder_and_deduplicates_same_person(self):
  r=person_profile(self.psg([self.person(),self.person()]),self.capture('https://www.psg.fr/joueurs/full-name'),9847)[0];self.assertEqual(r.provider_ids,{'PSG':'7'});self.assertEqual(r.names,['Full Legal Name','Full Name'])
 def test_psg_ambiguous_native_people_or_wrong_resource_held(self):
  for raw,url in [(self.psg([self.person(),self.person(8)]),'https://www.psg.fr/joueurs/full-name'),(self.psg([self.person()]),'https://www.psg.fr/joueurs/someone-else')]:
   with self.assertRaises(ValueError):person_profile(raw,self.capture(url),9847)

 def test_pure_source_profile_has_no_roster_writer_import(self):
  import builtins
  from unittest.mock import patch
  original=builtins.__import__
  def guarded(name,*args,**kwargs):
   if 'roster_import' in name:raise ImportError('WRITER_IMPORT_FORBIDDEN')
   return original(name,*args,**kwargs)
  with patch('builtins.__import__',side_effect=guarded):
   self.assertTrue(person_profile(self.psg([self.person()]),self.capture('https://www.psg.fr/joueurs/full-name'),9847))
 def test_legacy_independent_alias_caller_keeps_existing_evidence_fields(self):
  import tempfile,hashlib
  from pathlib import Path
  from unittest.mock import patch
  from squad_pipeline.core import ROOT
  from squad_pipeline import roster_import
  with tempfile.TemporaryDirectory(dir=ROOT/'.cloud') as folder:
   row={'teamSlug':'inter','playerId':7,'mediaFirstName':'Full','mediaLastName':'Name','shortName':'F. Name','nationalityIsoCode':'ZZZ','dateOfBirth':'2000-01-01'}
   frame=[1,'1:'+json.dumps({'player':row})];body=('<script>self.__next_f.push('+json.dumps(frame)+')</script>').encode();Path(folder,'cache.html').write_bytes(body)
   with patch.object(roster_import,'ROOT',Path(folder)):
    out=roster_import.independent_aliases([{'provider':'seriea','path':'cache.html','sha256':hashlib.sha256(body).hexdigest(),'url':'https://official.example/cache'}],'inter')
   self.assertEqual(out[0]['full_name'],'Full Name');self.assertEqual(out[0]['field_path'],'script[0]/frame_line[0]/$.player');self.assertEqual(out[0]['source_player_id'],7)

 def test_psg_handball_cannot_become_football_profile(self):
  d=self.person();d['teamType']='Handball'
  with self.assertRaisesRegex(ValueError,'FOOTBALL'):person_profile(self.psg([d]),self.capture('https://www.psg.fr/joueurs/full-name'),9847)
