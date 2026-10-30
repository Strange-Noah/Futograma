import unittest
from squad_pipeline.international_sky_fallback import month_links,target_minutes,competition_aliases
class IndependentSkyTests(unittest.TestCase):
 def fixture(self):return dict(provider_match_id='10',home=dict(id='1',name='France'),away=dict(id='2',name='Italy'),kickoff='2026-10-02T18:45:00Z',competition_key='9806',competition_label='UEFA Nations League A Grp. 1')
 def test_only_observed_bounded_month_links(self):
  raw='<a href="/france-scores-fixtures/2026-09-01">Sep</a><a href="https://evil.example/france-scores-fixtures/2026-09-01">bad</a><a href="/france-scores-fixtures/2027-09-01">future</a>';self.assertEqual(month_links(raw,'https://www.skysports.com/france-results',{'2026-09'}),['https://www.skysports.com/france-scores-fixtures/2026-09-01'])
 def test_empty_events_never_confirm_unused(self):
  p=dict(role='bench',derived_minutes=0);r=target_minutes(p,None,self.fixture(),'home',90);self.assertIsNone(r['minutes']);self.assertEqual(r['participation_status'],'BENCH_PARTICIPATION_UNKNOWN')
 def test_exact_personal_report_and_independent_clock(self):
  f=self.fixture();c=dict(discovery_claim=dict(id=10,teamId=1,opponentTeamId=2,isHomeTeam=True,matchDate=dict(utcTime=f['kickoff']),minutesPlayed=67,playedInMatch=True));r=target_minutes(dict(role='starters',derived_minutes=67),c,f,'home',90);self.assertEqual(r['minutes'],67);self.assertIn('CLOCKS_AGREE',r['evidence_scope'])
 def test_disagreement_is_not_averaged(self):
  f=self.fixture();c=dict(discovery_claim=dict(id=10,teamId=1,opponentTeamId=2,isHomeTeam=True,matchDate=dict(utcTime=f['kickoff']),minutesPlayed=78,playedInMatch=True));r=target_minutes(dict(role='starters',derived_minutes=80),c,f,'home',90);self.assertEqual(r['minutes'],78);self.assertEqual(r['reported_minutes'],78);self.assertEqual(r['derived_minutes'],80);self.assertIn('FIVE_MINUTE_SOURCE_PRIORITY',r['evidence_scope'])
 def test_no_guessed_competition_alias(self):
  f=self.fixture();f['competition_key']='unknown';self.assertEqual(competition_aliases(f),())
