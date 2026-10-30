import unittest,copy,json,html
from squad_pipeline.international_parse import certified_sky_team_label
from squad_pipeline.international_sky_fallback import directory_team_labels
class SourceDeclaredTeamLabelTests(unittest.TestCase):
 def context(self):
  cert=dict(provider='Sky',provider_match_id='77',provider_team_id='9',side='home',full_name='Example National Team',short_name='E Nat Team',directory_source_sha256='a'*64,directory_source_url='https://www.skysports.com/example-results',source_locator='HTML.data-state[match.id=77].teams.home')
  return dict(team_label_certificates=dict(home=cert,away=dict(provider_team_id='10')))
 def test_source_declared_label_is_bound_to_exact_fixture_team(self):
  c=self.context();r=certified_sky_team_label('E Nat Team','Example National Team','home',c,dict(url='https://www.skysports.com/football/a-vs-b/teams/77'));self.assertEqual(r['provider_team_id'],'9')
 def test_short_similarity_without_certificate_never_authorizes(self):
  with self.assertRaisesRegex(ValueError,'SIDE_MISMATCH'):certified_sky_team_label('E Nat Team','Example National Team','home',{},dict(url='https://www.skysports.com/football/a-vs-b/teams/77'))
 def test_cross_fixture_or_side_or_country_certificate_rejected(self):
  for key,value in [('provider_match_id','88'),('side','away'),('full_name','Different Country'),('short_name','Other Label'),('directory_source_sha256','missing'),('provider_team_id','10'),('directory_source_url','https://evil.example/source')]:
   with self.subTest(field=key):
    c=self.context();c['team_label_certificates']['home'][key]=value
    with self.assertRaisesRegex(ValueError,'SIDE_MISMATCH'):certified_sky_team_label('E Nat Team','Example National Team','home',c,dict(url='https://www.skysports.com/football/a-vs-b/teams/77'))
 def test_unrelated_full_label_cannot_use_short_certificate(self):
  with self.assertRaisesRegex(ValueError,'SIDE_MISMATCH'):certified_sky_team_label('Different Country','Example National Team','home',self.context(),dict(url='https://www.skysports.com/football/a-vs-b/teams/77'))
 def test_exact_labels_keep_existing_strict_path(self):self.assertIsNone(certified_sky_team_label('France','France','home',{},dict(url='https://www.skysports.com/football/a-vs-b/teams/77')))
 def test_directory_preserves_provider_declared_ids_and_both_labels(self):
  d=dict(id='77',matchURL='/football/a-vs-b/77',teams={side:dict(id=uid,name=dict(full=full,short=short)) for side,uid,full,short in [('home',9,'Example National Team','E Nat Team'),('away',10,'Other Team','Other')]});raw='<div data-state="'+html.escape(json.dumps(d),quote=True)+'"></div>';r=directory_team_labels(raw)['77'];self.assertEqual(r['home']['provider_team_id'],'9');self.assertEqual(r['home']['short_name'],'E Nat Team')
