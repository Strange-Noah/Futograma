import unittest,sqlite3,json
from squad_pipeline.international_store import migrate,team,evidence,put_match,put_appearance,bridge_references,complete_missing_minutes
class InternationalStoreTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript('CREATE TABLE clubs(club_id INTEGER PRIMARY KEY,name TEXT);CREATE TABLE players(player_id INTEGER PRIMARY KEY);CREATE TABLE matches(match_id TEXT PRIMARY KEY,kickoff TEXT,local_date TEXT,competition_id INTEGER);CREATE TABLE player_match_minutes(player_id INTEGER,match_id TEXT,club_id INTEGER,participation_status TEXT,minutes INTEGER);CREATE TABLE validation_national_reference(player_id INTEGER,match_id TEXT,payload_json TEXT,PRIMARY KEY(player_id,match_id));CREATE TABLE validation_source_evidence(evidence_id TEXT PRIMARY KEY,payload_json TEXT);INSERT INTO players VALUES(1);INSERT INTO players VALUES(-2);')
        migrate(self.db);self.eid=evidence(self.db,dict(url='https://official.example/match',sha256='raw-sha',provider='Official'),'SYNTHETIC_TEST')
        self.home=team(self.db,'Example A','SENIOR',self.eid,'Test','t1');self.away=team(self.db,'Example B','SENIOR',self.eid,'Test','t2')
        self.match=dict(match_id='nt_test',home_team_key=self.home,away_team_key=self.away,kickoff_utc='2026-10-01T23:00:00+00:00',madrid_date='2026-10-02',competition_key='NATIONAL_FRIENDLY',stage=None,match_state='COMPLETED',score='1-0',duration_minutes=120,extra_time_status='CONFIRMED',identity_status='TEST_ONLY',metadata_json='{}')
        self.mid=put_match(self.db,self.match,'Test','match1',self.eid)
    def tearDown(self):self.db.close()
    def fact(self,**kw):return dict(match_id=self.mid,player_id=1,represented_team_key=self.home,participation_status='STARTER',minutes=120,reported_minutes=120,derived_minutes=None,minutes_precision='NOMINAL',verification_status='TEST_ONLY',evidence_scope='TARGET',revision=1,metadata_json='{}',**kw)
    def test_negative_internal_player_identity_is_preserved(self):
        f=self.fact();f['player_id']=-2;put_appearance(self.db,f);self.assertEqual(self.db.execute('SELECT player_id FROM international_appearances').fetchone()[0],-2)
    def test_extra_time_not_truncated_to90(self):
        put_appearance(self.db,self.fact());self.assertEqual(self.db.execute('SELECT minutes FROM player_all_match_facts').fetchone()[0],120)
    def test_unknown_and_unused_distinct(self):
        f=self.fact();f.update(minutes=None,participation_status='UNKNOWN');put_appearance(self.db,f);self.assertIsNone(self.db.execute('SELECT minutes FROM international_appearances').fetchone()[0])
    def test_wrong_represented_team_is_rejected(self):
        other=team(self.db,'Other','SENIOR',self.eid);f=self.fact();f['represented_team_key']=other
        with self.assertRaisesRegex(sqlite3.IntegrityError,'REPRESENTED_TEAM'):put_appearance(self.db,f)
    def test_one_physical_match_across_players(self):
        put_appearance(self.db,self.fact());f=self.fact();f.update(player_id=-2,represented_team_key=self.away);put_appearance(self.db,f);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM international_matches').fetchone()[0],1)
    def test_same_player_cannot_be_on_both_sides(self):
        put_appearance(self.db,self.fact());f=self.fact();f['represented_team_key']=self.away
        with self.assertRaises(ValueError):put_appearance(self.db,f)
    def test_idempotent_replay(self):
        self.assertTrue(put_appearance(self.db,self.fact()));self.assertFalse(put_appearance(self.db,self.fact()));self.assertEqual(put_match(self.db,self.match,'Test','match1',self.eid),self.mid)
    def test_unreviewed_revision_rejected(self):
        put_appearance(self.db,self.fact());f=self.fact();f['minutes']=90
        with self.assertRaises(ValueError):put_appearance(self.db,f)
    def test_provider_id_collision_not_silently_rebound(self):
        altered=dict(self.match,kickoff_utc='2026-10-02T20:00:00+00:00')
        with self.assertRaisesRegex(ValueError,'IDENTITY_CONFLICT'):put_match(self.db,altered,'Test','match1',self.eid)
    def test_senior_and_youth_team_ids_not_interchangeable(self):
        with self.assertRaisesRegex(ValueError,'LEVEL_CONFLICT'):team(self.db,'Example A U21','U21',self.eid,'Test','t1')
    def test_legacy_bridge_never_guesses_missing_evidence(self):
        raw=json.dumps(dict(team_type='NATIONAL',player_id=1,source_refs=['missing']))
        self.db.execute('INSERT INTO validation_national_reference VALUES(?,?,?)',(1,'legacy',raw));result=bridge_references(self.db);self.assertEqual(result[0]['state'],'PENDING');self.assertEqual(self.db.execute('SELECT payload_json FROM validation_national_reference').fetchone()[0],raw)
    def test_migration_rejects_active_transaction(self):
        with self.assertRaisesRegex(ValueError,'CLEAN_TRANSACTION'):migrate(self.db)
    def test_fill_unknown_minutes_appends_revision(self):
        f=self.fact();f.update(minutes=None,reported_minutes=None);put_appearance(self.db,f);eid=evidence(self.db,dict(url='https://provider.example/exact-match',sha256='a'*64),'EXACT_MATCH_IDS');new=dict(f,minutes=67,reported_minutes=67,verification_status='PROVIDER_ID_AND_MATCH_VERIFIED');self.assertTrue(complete_missing_minutes(self.db,new,eid));row=self.db.execute('SELECT minutes,revision FROM international_appearances').fetchone();self.assertEqual(tuple(row),(67,2));revision=self.db.execute('SELECT previous_json,new_json FROM international_fact_revisions').fetchone();self.assertIsNone(json.loads(revision[0])['minutes']);self.assertEqual(json.loads(revision[1])['minutes'],67)
    def test_fill_never_replaces_known_minutes(self):
        put_appearance(self.db,self.fact());eid=evidence(self.db,dict(url='https://provider.example/exact-match',sha256='b'*64));new=self.fact();new.update(minutes=90,verification_status='PROVIDER_ID_AND_MATCH_VERIFIED')
        with self.assertRaisesRegex(ValueError,'KNOWN_OR_LEGACY'):complete_missing_minutes(self.db,new,eid)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM international_fact_revisions').fetchone()[0],0)
    def test_fill_requires_hashed_evidence(self):
        f=self.fact();f.update(minutes=None,reported_minutes=None);put_appearance(self.db,f);new=dict(f,minutes=90,verification_status='PROVIDER_ID_AND_MATCH_VERIFIED')
        with self.assertRaisesRegex(ValueError,'HASHED_SOURCE'):complete_missing_minutes(self.db,new,self.eid)
    def test_fill_never_revises_legacy_target(self):
        f=self.fact();f.update(minutes=None,reported_minutes=None);put_appearance(self.db,f);self.db.execute('INSERT INTO validation_national_reference VALUES(?,?,?)',(1,'legacy','{}'));self.db.execute('INSERT INTO international_reference_bridge VALUES(?,?,?,?,?,?,?)',(1,'legacy',self.mid,'original','INTEGRATED','TARGET',1));new=dict(f,minutes=90,verification_status='PROVIDER_ID_AND_MATCH_VERIFIED')
        with self.assertRaisesRegex(ValueError,'KNOWN_OR_LEGACY'):complete_missing_minutes(self.db,new,self.eid)
    def test_exact_country_spacing_variant_preserves_team_key(self):
        original=team(self.db,'burkinafaso','SENIOR',self.eid);fresh=team(self.db,'Burkina Faso','SENIOR',self.eid,'FotMob','test-country');self.assertEqual(original,fresh)
    def test_country_and_youth_identities_do_not_collapse(self):
        congo=team(self.db,'Congo','SENIOR',self.eid);dr=team(self.db,'DR Congo','SENIOR',self.eid);youth=team(self.db,'Congo','U21',self.eid);self.assertEqual(len({congo,dr,youth}),3)
