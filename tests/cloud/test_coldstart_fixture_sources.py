import unittest,hashlib
from test_international_fotmob import data,fixture,html
from squad_pipeline.coldstart_fixture_sources import directory_fixture,parse_completed_club
from squad_pipeline.international_fotmob import parse_public_match

class ColdstartClubSourceTests(unittest.TestCase):
 def setup_fixture(self):
  f=fixture();row=dict(id=10,home=dict(f['home'],score=2),away=dict(f['away'],score=1),tournament=dict(leagueId=8),status=dict(finished=True,utcTime=f['kickoff'],scoreStr='2 - 1'));d=data();d['header']['status']['scoreStr']='2 - 1';raw=html(d).encode();source=dict(url='https://www.fotmob.com/matches/a#10',sha256=hashlib.sha256(raw).hexdigest());return row,raw,source
 def test_directory_has_explicit_proof_and_scope(self):
  row,raw,s=self.setup_fixture();r=directory_fixture(row,s,'2026/27','1');self.assertEqual(r['quality']['decision'],'ACCEPT');self.assertEqual(r['fixture']['home_score'],2)
 def test_unfinished_scores_stay_none(self):
  row,raw,s=self.setup_fixture();row['status']['finished']=False;row['status'].pop('scoreStr');row['home'].pop('score');row['away'].pop('score');r=directory_fixture(row,s,'2026/27','1');self.assertIsNone(r['fixture']['home_score']);self.assertEqual(r['fixture']['status'],'SCHEDULED')
 def test_directory_rejects_score_orientation_conflict(self):
  row,raw,s=self.setup_fixture();row['status']['scoreStr']='1 - 2'
  with self.assertRaisesRegex(ValueError,'SCORE_SIDES_CONFLICT'):directory_fixture(row,s,'2026/27','1')
 def test_reuses_strict_adapter_without_default_semantics_change(self):
  row,raw,s=self.setup_fixture();directory=directory_fixture(row,s,'2026/27','1');r=parse_completed_club(raw,s,directory,'1');self.assertEqual(r['quality']['decision'],'ACCEPT');self.assertIsNone(r['appearances'][-1]['minutes']);self.assertEqual(r['appearances'][-1]['participation_status'],'BENCH_PARTICIPATION_UNKNOWN')
  with self.assertRaisesRegex(ValueError,'NATIONAL_TEAM_LEVEL'):parse_public_match(raw.decode(),s,directory['provider_fixture'])
 def test_wrong_detail_id_rejected(self):
  row,raw,s=self.setup_fixture();directory=directory_fixture(row,s,'2026/27','1');d=data();d['general']['matchId']=11;raw=html(d).encode();s=dict(s,sha256=hashlib.sha256(raw).hexdigest())
  with self.assertRaisesRegex(ValueError,'RETURNED_MATCH_ID_MISMATCH'):parse_completed_club(raw,s,directory,'1')
 def test_detail_body_hash_required(self):
  row,raw,s=self.setup_fixture();directory=directory_fixture(row,s,'2026/27','1')
  with self.assertRaisesRegex(ValueError,'BODY_HASH_MISMATCH'):parse_completed_club(raw+b' ',s,directory,'1')
 def test_profile_claims_require_body_url_and_explicit_fields(self):
  import json
  from squad_pipeline.coldstart_fixture_sources import extract_club_history
  row=dict(id=10,teamId=1,opponentTeamId=2,isHomeTeam=True,matchDate=dict(utcTime='2026-10-02T18:45:00Z'),minutesPlayed=0,playedInMatch=False,onBench=True)
  body=html(dict(id=111,recentMatches=[row,dict(id=11,teamId=1)])).encode();s=dict(url='https://www.fotmob.com/players/111/real-player',sha256=hashlib.sha256(body).hexdigest())
  claims=extract_club_history(body,s,'1');self.assertEqual(len(claims),1);self.assertEqual(claims[0]['player_provider_id'],'111');self.assertIs(claims[0]['discovery_claim']['playedInMatch'],False)
  with self.assertRaisesRegex(ValueError,'URL_BODY'):extract_club_history(body,dict(s,url='https://www.fotmob.com/players/112/wrong'),1)
 def test_explicit_unused_profile_history_fills_zero_without_inference(self):
  from squad_pipeline.coldstart_fixture_sources import extract_club_history
  row,raw,s=self.setup_fixture();directory=directory_fixture(row,s,'2026/27','1')
  profile=html(dict(id=111,recentMatches=[dict(id=10,teamId=1,opponentTeamId=2,isHomeTeam=True,matchDate=dict(utcTime=fixture()['kickoff']),minutesPlayed=0,playedInMatch=False,onBench=True)])).encode();ps=dict(url='https://www.fotmob.com/players/111/bench-player',sha256=hashlib.sha256(profile).hexdigest());claims=extract_club_history(profile,ps,1)
  p=parse_completed_club(raw,s,directory,'1',claims)['appearances'][-1];self.assertEqual((p['minutes'],p['participation_status']),(0,'UNUSED_SUBSTITUTE'))
 def test_history_opponent_mismatch_still_rejected(self):
  from squad_pipeline.coldstart_fixture_sources import extract_club_history
  row,raw,s=self.setup_fixture();directory=directory_fixture(row,s,'2026/27','1');profile=html(dict(id=111,recentMatches=[dict(id=10,teamId=1,opponentTeamId=999,isHomeTeam=True,matchDate=dict(utcTime=fixture()['kickoff']),minutesPlayed=0,playedInMatch=False,onBench=True)])).encode();ps=dict(url='https://www.fotmob.com/players/111/bench',sha256=hashlib.sha256(profile).hexdigest())
  with self.assertRaisesRegex(ValueError,'PERSONAL_HISTORY_OPPONENT_MISMATCH'):parse_completed_club(raw,s,directory,'1',extract_club_history(profile,ps,1))
 def test_explicit_did_not_play_cannot_override_starting_lineup(self):
  from squad_pipeline.coldstart_fixture_sources import extract_club_history
  row,raw,s=self.setup_fixture();directory=directory_fixture(row,s,'2026/27','1');profile=html(dict(id=100,recentMatches=[dict(id=10,teamId=1,opponentTeamId=2,isHomeTeam=True,matchDate=dict(utcTime=fixture()['kickoff']),minutesPlayed=0,playedInMatch=False,onBench=True)])).encode();ps=dict(url='https://www.fotmob.com/players/100/starter',sha256=hashlib.sha256(profile).hexdigest())
  with self.assertRaisesRegex(ValueError,'PARTICIPATION_CONFLICT'):parse_completed_club(raw,s,directory,'1',extract_club_history(profile,ps,1))
 def test_unstarted_zero_placeholders_are_not_final_scores(self):
  row,raw,s=self.setup_fixture();row['status'].update(finished=False,started=False,scoreStr='0 - 0');row['home']['score']=row['away']['score']=0;row['tournament']['name']='Super Cup'
  r=directory_fixture(row,s,'2026/27','1');self.assertEqual((r['fixture']['home_score'],r['fixture']['away_score']),(None,None));self.assertEqual(r['discovery_row']['home']['score'],0);self.assertEqual(r['fixture']['competition_label'],'Super Cup');self.assertEqual(r['evidence']['field_evidence']['competition_label']['value'],'Super Cup')
 def test_unstarted_nonzero_conflicts_and_started_zero_is_valid(self):
  row,raw,s=self.setup_fixture();row['status'].update(finished=False,started=False)
  with self.assertRaisesRegex(ValueError,'UNSTARTED_FIXTURE_NONZERO_SCORE_CONFLICT'):directory_fixture(row,s,'2026/27','1')
  row['status'].update(started=True,scoreStr='0 - 0');row['home']['score']=row['away']['score']=0;r=directory_fixture(row,s,'2026/27','1');self.assertEqual((r['fixture']['home_score'],r['fixture']['away_score']),(0,0));self.assertEqual(r['fixture']['status'],'IN_PROGRESS')
