import unittest,sqlite3,copy,hashlib
from test_international_fotmob import data,fixture,html
from squad_pipeline.international_store import migrate
from squad_pipeline.coldstart_national import offlineapply

class ColdstartNationalTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
  self.db.executescript('CREATE TABLE clubs(club_id INTEGER PRIMARY KEY,name TEXT);CREATE TABLE players(player_id INTEGER PRIMARY KEY);CREATE TABLE matches(match_id TEXT PRIMARY KEY,kickoff TEXT,local_date TEXT,competition_id INTEGER);CREATE TABLE player_match_minutes(player_id INTEGER,match_id TEXT,club_id INTEGER,participation_status TEXT,minutes INTEGER);CREATE TABLE validation_national_reference(player_id INTEGER,match_id TEXT,payload_json TEXT,PRIMARY KEY(player_id,match_id));CREATE TABLE validation_source_evidence(evidence_id TEXT PRIMARY KEY,payload_json TEXT);INSERT INTO players VALUES(100);')
  migrate(self.db);self.discovery=dict(fixtures={'10':fixture()},claims={'10':[]},existing_quarantines_not_retried=[])
 def tearDown(self):self.db.close()
 def capture(self,d=None):
  d=d or data();d['header']['status']['scoreStr']='2 - 1';raw=html(d).encode();return dict(provider_match_id='10',url='https://www.fotmob.com/matches/a#10',provider='FotMob',sha256=hashlib.sha256(raw).hexdigest(),status='SUCCESS',body=raw)
 def apply(self,cap=None):return offlineapply(self.db,self.discovery,[cap or self.capture()],{100},'2026-11-04','ONBOARD')
 def test_wrong_match_ids_never_insert(self):
  d=data();d['general']['matchId']=11;r=self.apply(self.capture(d));self.assertEqual(r['new_player_facts'],0);self.assertEqual(self.db.execute('select count(*) from international_matches').fetchone()[0],0);self.assertEqual(len(r['source_failures']),1)
 def test_repeat_reuses_and_does_not_rewrite_old_canonical(self):
  r=self.apply();self.assertEqual(r['new_player_facts'],1);old={t:[tuple(x) for x in self.db.execute('select * from '+t)] for t in ['international_matches','international_appearances','canonical_field_values','canonical_observations','persistent_source_evidence']}
  r=self.apply();self.assertEqual((r['new_player_facts'],r['new_matches']),(0,0));self.assertEqual(r['existing_player_facts_preserved'],1)
  for t,rows in old.items():self.assertEqual(rows,[tuple(x) for x in self.db.execute('select * from '+t)])
 def test_existing_fact_is_retained_even_when_source_differs(self):
  self.apply();self.db.execute('update international_appearances set minutes=5,reported_minutes=5');old=tuple(self.db.execute('select * from international_appearances').fetchone());d=data();d['content']['playerStats']={'100':dict(id=100,teamId=1,name='Player 100',stats=[dict(stats={'minutes':dict(key='minutes_played',stat=dict(value=90))})])}
  r=self.apply(self.capture(d));self.assertEqual(tuple(self.db.execute('select * from international_appearances').fetchone()),old);self.assertEqual(r['new_player_facts'],0);self.assertTrue(self.db.execute("select 1 from quality_source_issues where reason='EXISTING_PLAYER_FACT_DIFFERENCE_RETAINED'").fetchone())
 def test_quarantines_not_retried_or_closed(self):
  self.discovery['existing_quarantines_not_retried']=['10'];r=self.apply();self.assertEqual(r['blocked_existing_quarantines'],1);self.assertEqual(r['new_matches'],0);self.assertEqual(r['source_failures'],[])
 def test_history_claim_must_exist_in_hashed_profile_body(self):
  import tempfile
  from pathlib import Path
  row=dict(id=10,teamId=1,opponentTeamId=2,isHomeTeam=True,matchDate=dict(utcTime=fixture()['kickoff']),minutesPlayed=12,playedInMatch=True,onBench=False)
  body=html(dict(id=100,recentMatches=[row])).encode()
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'profile.response';p.write_bytes(body);source=dict(path=str(p),url='https://www.fotmob.com/players/100',sha256=hashlib.sha256(body).hexdigest())
   claim=dict(provider_match_id='10',player_provider_id='100',source=source,discovery_claim=dict(row,minutesPlayed=13));self.discovery['claims']['10']=[claim]
   result=self.apply();self.assertEqual(result['new_player_facts'],0);self.assertIn('PERSONAL_HISTORY_CLAIM_NOT_IN_RETAINED_BODY',result['source_failures'][0]['reason'])
   claim['discovery_claim']=row;result=self.apply();self.assertEqual(result['new_player_facts'],1);self.assertEqual(self.db.execute('select minutes from international_appearances').fetchone()[0],12)
 def test_explicit_nonparticipation_conflicting_with_starter_never_inserts(self):
  import tempfile
  from pathlib import Path
  row=dict(id=10,teamId=1,opponentTeamId=2,isHomeTeam=True,matchDate=dict(utcTime=fixture()['kickoff']),minutesPlayed=0,playedInMatch=False,onBench=True)
  body=html(dict(id=100,recentMatches=[row])).encode()
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'profile.response';p.write_bytes(body);source=dict(path=str(p),url='https://www.fotmob.com/players/100',sha256=hashlib.sha256(body).hexdigest());self.discovery['claims']['10']=[dict(provider_match_id='10',player_provider_id='100',source=source,discovery_claim=row)]
   result=self.apply();self.assertEqual(result['new_player_facts'],0);self.assertEqual(result['accepted_fixtures'],0);self.assertIn('PERSONAL_HISTORY_LINEUP_PARTICIPATION_CONFLICT',result['source_failures'][0]['reason']);self.assertEqual(self.db.execute('select count(*) from international_matches').fetchone()[0],0);self.assertEqual(self.db.execute('select count(*) from international_appearances').fetchone()[0],0)
