import unittest
from squad_pipeline.identity_sources import person_profile,uefa_profile,dfb_roster
from squad_pipeline.identity_pipeline import management_state
class SourceBoundaryTests(unittest.TestCase):
    def capture(self,url):return dict(url=url,status='SUCCESS',sha256='TEST_ONLY',response_timestamp='2026-10-29T12:00:00Z')
    def test_provider_biography_cannot_become_official_club_evidence(self):
        raw='<script type="application/ld+json">{"@type":"Person","name":"Controlled Person","birthDate":"2000-01-01"}</script>'
        self.assertEqual(person_profile(raw,self.capture('https://torlytics.de/spieler/controlled'),9823),[])
    def test_club_profile_requires_owned_club_host(self):
        raw='<script type="application/ld+json">{"@type":"Person","name":"Controlled Person","birthDate":"2000-01-01"}</script>'
        self.assertEqual(person_profile(raw,self.capture('https://www.arsenal.com/profile/controlled'),9823),[])
    def test_uefa_profile_does_not_use_overwritten_country_url(self):
        url='https://www.uefa.com/uefachampionsleague/clubs/players/123--controlled/'
        raw='<meta property="og:url" content="'+url+'"><meta itemprop="name" content="Controlled Person"><meta itemprop="birthdate" content="2000-01-01"><meta itemprop="url" content="https://www.uefa.com/uefachampionsleague/clubs/52280--arsenal/"><meta itemprop="url" content="https://www.uefa.com/nationalassociations/ENG/">'
        row=uefa_profile(raw,self.capture(url))[0];self.assertEqual(row.provider_ids['UEFA'],'123');self.assertEqual(row.official_clubs,['9825'])
    def test_federation_registration_is_not_management(self):
        url='https://datencenter.dfb.de/competitions/champions-league/seasons/2026-2027/teams/bayern-muenchen';raw='<table><tr><td>47</td><td><a href="/profil/123">Controlled Person</a></td><td>01.01.2000</td></tr></table>';row=dfb_roster(raw,self.capture(url),9823)[0];self.assertFalse(management_state([row],'2026-10-29')['included'])
