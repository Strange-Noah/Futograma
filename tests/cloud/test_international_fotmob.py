import unittest,json,copy
from squad_pipeline.international_fotmob import parse_public_match
from squad_pipeline.international_discovery import team_fixture_hints,profile_hints

def html(d):return '<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':d}})+'</script>'
def fixture():return dict(provider_match_id='10',home=dict(id='1',name='A U21'),away=dict(id='2',name='B U21'),kickoff='2026-10-02T18:45:00Z',competition_key='8',level='U21')
def data():
 f=fixture();g=dict(matchId='10',finished=True,gender='male',homeTeam=f['home'],awayTeam=f['away'],matchTimeUTCDate=f['kickoff'],parentLeagueId=8)
 status=dict(finished=True,utcTime=f['kickoff'],reason=dict(short='FT'),halfs=dict(secondHalfEnded='x'))
 teams={s+'Team':dict(id=f[s]['id'],name=f[s]['name'],starters=[dict(id=100*k+i,name='Player '+str(100*k+i)) for i in range(11)],subs=[dict(id=100*k+11,name='Bench '+str(k))]) for k,s in enumerate(('home','away'),1)}
 return dict(general=g,header=dict(teams=[f['home'],f['away']],status=status),content=dict(lineup=dict(matchId='10',**teams),playerStats=None))
class PublicInternationalTests(unittest.TestCase):
 def test_missing_minutes_never_become_zero(self):
  r=parse_public_match(html(data()),{},fixture());self.assertTrue(all(p['minutes'] is None for s in r['sheets'] for p in s['players']));self.assertEqual(r['sheets'][0]['players'][-1]['participation_status'],'BENCH_PARTICIPATION_UNKNOWN')
 def test_wrong_historical_fragment_rejected(self):
  d=data();d['general']['matchId']='11'
  with self.assertRaisesRegex(ValueError,'RETURNED_MATCH_ID_MISMATCH'):parse_public_match(html(d),{},fixture())
 def test_duplicate_opposite_side_id(self):
  d=data();d['content']['lineup']['awayTeam']['starters'][0]['id']=100
  with self.assertRaisesRegex(ValueError,'DUPLICATE'):parse_public_match(html(d),{},fixture())
 def test_stats_wrong_side_rejected(self):
  d=data();d['content']['playerStats']={'100':dict(id=100,teamId=2,name='Player 100',stats=[])}
  with self.assertRaisesRegex(ValueError,'STAT_IDENTITY'):parse_public_match(html(d),{},fixture())
 def test_explicit_target_history_and_zero_minute_used(self):
  d=data();f=fixture();c=dict(player_provider_id='111',provider_match_id='10',source={'url':'profile'},discovery_claim=dict(id=10,teamId=1,opponentTeamId=2,isHomeTeam=True,matchDate=dict(utcTime=f['kickoff']),minutesPlayed=0,playedInMatch=True,onBench=True))
  p=parse_public_match(html(d),{},f,[c])['sheets'][0]['players'][-1];self.assertEqual((p['minutes'],p['participation_status']),(0,'SUBSTITUTE_USED'));self.assertIn('NOT_INDEPENDENT',p['evidence_scope'])
 def test_penalties_do_not_create_extra_time(self):
  d=data();d['header']['status']['whoLostOnPenalties']='away';self.assertEqual(parse_public_match(html(d),{},fixture())['duration_minutes'],90)
 def test_verified_extra_time(self):
  d=data();d['header']['status']['halfs']['firstExtraHalfStarted']='x';self.assertEqual(parse_public_match(html(d),{},fixture())['duration_minutes'],120)
 def test_exact_team_fallback_schema(self):
  aff=dict(provider_team_id='1',level='U21');f=fixture();row=dict(id=10,home=f['home'],away=f['away'],pageUrl='/matches/a#10',status=dict(finished=True,utcTime=f['kickoff']),tournament=dict(leagueId=8,name='U21 Qualification'))
  raw=html(dict(fallback={'notableMatches':{},'team-1':dict(details=dict(id=1),fixtures=dict(allFixtures=dict(fixtures=[row])))}));r=team_fixture_hints(raw,{},aff,'2026-07-01','2026-10-30');self.assertEqual(r[0]['competition_key'],'8')
 def test_historical_national_history_not_lost_when_career_range_ends(self):
  d=dict(id=7,careerHistory=dict(careerItems={'national team':dict(teamEntries=[dict(participantId=7,teamId=1,team='A U21',endDate='2026-01-01')])}),recentMatches=[dict(id=10,teamId=1,teamName='A U21',opponentTeamId=2,opponentTeamName='B U21',isHomeTeam=True,matchDate=dict(utcTime='2026-10-02T18:45:00Z'),matchPageUrl='/matches/a#10',leagueId=8,leagueName='U21')]);r=profile_hints(html(dict(data=d)),{},'2026-07-01','2026-10-30');self.assertEqual(len(r['matches']),1);self.assertEqual(r['affiliations'],[])

 def test_missing_source_id_is_not_a_shared_player(self):
  d=data();d['content']['lineup']['awayTeam']['starters'][0]['id']=0;d['content']['lineup']['awayTeam']['subs'][0]['id']=0
  r=parse_public_match(html(d),{},fixture());self.assertEqual(len(r['identity_issues']),2);self.assertFalse(r['sheets'][1]['complete_squad']);self.assertEqual(len(r['sheets'][0]['players']),12)
