"""Controlled persistence boundaries; no real football facts."""
import sqlite3,unittest,json
from squad_pipeline.core import ROOT
from squad_pipeline import quality_pipeline as pipeline,quality_lifecycle as life
from ops.cloud.coldstart_acceptance import apply_appearance
class PersistenceBoundaries(unittest.TestCase):
 def test_replayed_packet_reaches_existing_writer_and_repeat_is_unchanged(self):
  import tempfile
  from pathlib import Path
  from dataclasses import asdict
  from test_international_fotmob import data
  from squad_pipeline.source_interface import EvidenceStore,SourceService,SourceRequest
  from ops.cloud.obligation_execution import adjudicate
  d=data();d['content']['playerStats']={'100':dict(id=100,teamId=1,name='Player 100',stats=[dict(stats={'minutes':dict(key='minutes_played',stat=dict(value=80))})])}
  self.db.execute("UPDATE matches SET home_id=1,away_id=2,kickoff=?,competition_id=8 WHERE match_id='controlled'",(d['general']['matchTimeUTCDate'],))
  self.db.execute('INSERT INTO players(player_id,display_name) VALUES(100,?)',('Player 100',))
  self.db.execute('CREATE TABLE verified_identity_links(provider TEXT,provider_player_id TEXT,player_id INTEGER)')
  self.db.execute("INSERT INTO verified_identity_links VALUES('FotMob','100',100)")
  player=dict(provider_player_id='100',provider_key='fotmob:100',name='Player 100',role='starters',participation_status='STARTER',minutes=None)
  apply_appearance(self.db,1,'controlled',player,self.source,'2026-11-03','BACKFILL');self.db.commit()
  root=ROOT/'.cloud';root.mkdir(exist_ok=True)
  with tempfile.TemporaryDirectory(dir=root) as tmp:
   store=EvidenceStore(Path(tmp));service=SourceService(store,'controlled',set(),max_calls=0)
   request=SourceRequest('FotMob','match','https://source.example/match?matchId=10','detail',expected_ids=dict(match='10'),context=dict(home_id=1,away_id=2,kickoff=d['general']['matchTimeUTCDate'],competition_id=8))
   artifact=store.import_body(request,json.dumps(d).encode(),dict(http_status=200,response_timestamp='2026-11-03T00:00:00Z'))
   _,parsed=service.observe(request,artifact);native=next(p for sh in parsed.native['sheets'] for p in sh['players'] if p['provider_player_id']=='100')
   packet=dict(domain='CLUB',match_id='controlled',team='1',managing_club_id=1,player_id=100,player=dict(native,provider_key='fotmob:100',minutes=80),source=dict(self.source,provider='FotMob',url=request.url,sha256=artifact.content_sha256),artifact=asdict(artifact))
   first=adjudicate(self.db,packet,service,'2026-11-03');self.assertEqual(first['state'],'SUPPLEMENTED_UNKNOWN')
   second=adjudicate(self.db,packet,service,'2026-11-03');self.assertEqual(second['state'],'UNCHANGED_ACCEPTED')
   self.assertEqual(service.client.calls,0);self.assertEqual(self.db.execute('SELECT minutes FROM player_match_minutes').fetchone()[0],80)

 def registry(self,pid=1):
  self.db.execute('CREATE TABLE IF NOT EXISTS verified_identity_links(provider TEXT,provider_player_id TEXT,player_id INTEGER)')
  self.db.execute('INSERT INTO verified_identity_links VALUES(?,?,?)',('FotMob','1',pid))
 def test_unknown_supplement_reuses_revision_writer_and_is_repeat_safe(self):
  self.apply(dict(self.player,minutes=None));self.registry();self.db.commit()
  first=apply_appearance(self.db,99,'controlled',self.player,self.source,'2026-11-03','BACKFILL',canonical_player_id=1,supplement_unknown=True)
  self.assertEqual(first['state'],'SUPPLEMENTED_UNKNOWN');self.assertEqual(self.db.execute('select minutes from player_match_minutes').fetchone()[0],80)
  count=self.db.execute('select count(*) from validation_fact_revisions').fetchone()[0]
  again=apply_appearance(self.db,99,'controlled',self.player,self.source,'2026-11-03','BACKFILL',canonical_player_id=1,supplement_unknown=True)
  self.assertEqual(again['state'],'RECORDED');self.assertEqual(self.db.execute('select count(*) from validation_fact_revisions').fetchone()[0],count)
  payload=json.loads(self.db.execute('select evidence_json from player_match_minutes').fetchone()[0]);self.assertIsNone(payload['evidence'][0]['retained_before']['minutes'])
 def test_explicit_canonical_mapping_does_not_invent_provider_numbered_player(self):
  self.db.execute('insert into players(player_id,display_name) values(-99,?)',('Canonical',));self.registry(-99);self.db.commit()
  r=apply_appearance(self.db,99,'controlled',self.player,self.source,'2026-11-03','BACKFILL',canonical_player_id=-99)
  self.assertEqual(r['player_id'],-99);self.assertIsNone(self.db.execute('select 1 from players where player_id=1').fetchone())
 def test_unverified_canonical_override_is_rejected(self):
  self.registry(2);self.db.commit()
  with self.assertRaisesRegex(ValueError,'VERIFIED_CANONICAL'):apply_appearance(self.db,99,'controlled',self.player,self.source,'2026-11-03','BACKFILL',canonical_player_id=-99)
  self.assertEqual(self.db.execute('select count(*) from player_match_minutes').fetchone()[0],0)
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.db.executescript((ROOT/'squad_pipeline/cloud/bootstrap_schema.sql').read_text());self.db.execute("insert into matches(match_id) values('controlled')");self.source=dict(provider='Controlled',url='https://example.com/m',sha256='a'*64,size=0)
  self.player=dict(provider_player_id='1',name='Controlled',role='starters',participation_status='STARTER',minutes=80)
 def tearDown(self):self.db.close()
 def apply(self,player):return apply_appearance(self.db,99,'controlled',player,self.source,'2026-11-03','ONBOARD')
 def test_conflicted_nonparticipant_not_inserted(self):
  result=self.apply(dict(self.player,participation_status='UNUSED_SUBSTITUTE',minutes=5));self.assertEqual(result['state'],'QUARANTINED');self.assertEqual(self.db.execute('select count(*) from player_match_minutes').fetchone()[0],0)
 def test_old_fact_and_quality_survive_new_conflict(self):
  self.apply(self.player);before=life.load(self.db,'appearance:controlled:1');result=self.apply(dict(self.player,minutes=90));self.assertEqual(result['state'],'RETAINED_ACCEPTED_CONFLICT');self.assertEqual(self.db.execute('select minutes from player_match_minutes').fetchone()[0],80);self.assertEqual(life.load(self.db,'appearance:controlled:1'),before);self.assertEqual(self.db.execute('select count(*) from quality_source_issues').fetchone()[0],1)
 def test_minor_difference_uses_existing_policy(self):
  self.apply(self.player);result=self.apply(dict(self.player,minutes=83));self.assertEqual(result['state'],'RETAINED_ACCEPTED_WITHIN_TOLERANCE');self.assertEqual(self.db.execute('select minutes from player_match_minutes').fetchone()[0],80);self.assertEqual(self.db.execute('select count(*) from minute_policy_decisions').fetchone()[0],1)
