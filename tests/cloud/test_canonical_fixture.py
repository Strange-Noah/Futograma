import copy
import json
import sqlite3
import unittest
from squad_pipeline import canonical_fixture as c
from squad_pipeline.pending_matches import classify
from squad_pipeline.match_diagnostics import inspect_match


class CanonicalFixtureTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;c.migrate(self.db)
        for key,name in [('A','Club A'),('B','Club B'),('C','Club C')]:c.entity(self.db,'TEAM',key,name)
        c.entity(self.db,'COMPETITION','L','League')
        self.obs=dict(domain='CLUB',provider='SourceA',provider_match_id='event1',home_key='A',away_key='B',competition_key='L',edition='2026',fields=dict(kickoff='2026-10-31T20:00:00Z',match_status='FINISHED'),evidence=dict(sha256='a'*64,url='https://source.example/event1'))
    def tearDown(self):self.db.close()
    def put(self,obs=None,**kw):return c.reconcile(self.db,obs or self.obs,**kw)
    def test_01_empty_then_complete_is_enrichment(self):
        a=self.put(create=True);b=self.put(dict(self.obs,fields=dict(self.obs['fields'],home_score=2,away_score=1)))
        self.assertEqual(a['match_id'],b['match_id']);self.assertEqual(b['operation'],'ENRICH')
    def test_02_complete_then_empty_does_not_erase_score(self):
        a=self.put(dict(self.obs,fields=dict(self.obs['fields'],home_score=2,away_score=1)),create=True)
        self.put();self.assertEqual(c.value(self.db,a['match_id'],'home_score'),(2,'VERIFIED'))
    def test_03_two_providers_one_fixture(self):
        a=self.put(create=True);b=self.put(dict(self.obs,provider='SourceB',provider_match_id='different'))
        self.assertEqual(a['match_id'],b['match_id']);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM canonical_fixtures').fetchone()[0],1)
    def test_04_collected_on_different_days_is_same_entity(self):
        a=self.put(create=True);o=copy.deepcopy(self.obs);o['evidence']['collected_at']='2026-11-02';b=self.put(o)
        self.assertEqual(a['match_id'],b['match_id']);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM canonical_observations').fetchone()[0],1)
    def test_05_evidence_backed_team_alias_only(self):
        with self.assertRaisesRegex(ValueError,'EVIDENCE'):c.assert_alias(self.db,'TEAM','Test','FC A','A',{})
        c.assert_alias(self.db,'TEAM','Test','FC A','A',dict(sha256='b'*64),provider_id='123')
        self.assertEqual(c.resolve_entity(self.db,'TEAM','Test',provider_id='123'),'A');self.assertIsNone(c.resolve_entity(self.db,'TEAM','Test','F.C. A'))
    def test_06_competition_alias_is_not_similarity(self):
        c.assert_alias(self.db,'COMPETITION','Test','League full','L',dict(sha256='b'*64),provider_id='99')
        self.assertEqual(c.resolve_entity(self.db,'COMPETITION','Test','League full'),'L');self.assertIsNone(c.resolve_entity(self.db,'COMPETITION','Other','League full'))
    def test_07_verified_provider_kickoff_correction_preserves_id(self):
        a=self.put(create=True);o=copy.deepcopy(self.obs);o['fields']['kickoff']='2026-11-02T20:00:00Z';o['verified_kickoff_correction']=True;o['evidence']['correction_basis']='EXACT_PROVIDER_EVENT_ID_RECONFIRMED';o['evidence']['correction_revision']=1;b=self.put(o)
        self.assertEqual(a['match_id'],b['match_id']);self.assertEqual(b['operation'],'CORRECT')
    def test_08_score_conflict_not_last_write(self):
        a=self.put(dict(self.obs,fields=dict(self.obs['fields'],home_score=2)),create=True);b=self.put(dict(self.obs,provider='B',fields=dict(self.obs['fields'],home_score=3)))
        self.assertEqual(b['operation'],'SOURCE_CONFLICT');self.assertEqual(c.value(self.db,a['match_id'],'home_score')[1],'SOURCE_CONFLICT')
    def test_09_distinct_same_teams_close_date_not_merged(self):
        a=self.put(create=True);o=copy.deepcopy(self.obs);o['provider_match_id']='second';o['fields']['kickoff']='2026-11-01T20:00:00Z';b=self.put(o,create=True);self.assertNotEqual(a['match_id'],b['match_id'])
    def test_10_repeated_source_observation_idempotent(self):
        a=self.put(create=True)
        for i in range(4):self.assertEqual(self.put()['match_id'],a['match_id'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM canonical_observations').fetchone()[0],1)
    def test_11_unknown_player_is_not_appearance(self):
        self.put(create=True);self.assertFalse(c.exists(self.db,'player_match_minutes'))
    def test_12_ordered_home_away_are_distinct(self):
        a=self.put(create=True);o=dict(self.obs,home_key='B',away_key='A',provider_match_id='away');b=self.put(o,create=True);self.assertNotEqual(a['match_id'],b['match_id'])
    def test_13_provider_id_cannot_switch_clubs(self):
        self.put(create=True)
        with self.assertRaisesRegex(ValueError,'IDENTITY_CONFLICT'):self.put(dict(self.obs,away_key='C'))
    def test_14_missing_timezone_is_not_guessed(self):
        with self.assertRaisesRegex(ValueError,'TIMEZONE'):self.put(dict(self.obs,fields=dict(kickoff='2026-10-31T20:00:00')),create=True)
    def test_15_reordered_updates_converge_on_seed(self):
        seed=self.put(create=True)['match_id'];observations=[dict(self.obs,provider='B',fields=dict(self.obs['fields'],home_score=2)),dict(self.obs,provider='C',fields=dict(self.obs['fields'],away_score=1))]
        for o in observations+list(reversed(observations)):self.assertEqual(self.put(o)['match_id'],seed)
        self.assertEqual(c.value(self.db,seed,'home_score')[0],2);self.assertEqual(c.value(self.db,seed,'away_score')[0],1)
    def test_16_retained_id_is_unchanged(self):
        a=self.put(registered_id='existing_historical_id');self.assertEqual(a['match_id'],'existing_historical_id')
    def test_17_actual_returned_match_id_remains_strict(self):
        with self.assertRaisesRegex(ValueError,'RETURNED_MATCH_ID_MISMATCH'):self.put(dict(self.obs,actual_provider_match_id='wrong'),create=True)
    def test_18_fragment_routing_diagnostic(self):
        raw='<script id="__NEXT_DATA__">'+json.dumps({'props':{'pageProps':{'data':{'general':{'matchId':999},'content':{'lineup':{'matchId':999}}}}}})+'</script>'
        d=inspect_match(raw,'123',dict(url='https://example.com/series#123',sha256='c'*64))
        self.assertEqual(d['actual_id'],'999');self.assertTrue(d['fragment_only_routing_risk']);self.assertEqual(d['request_url_without_fragment'],'https://example.com/series')

    def test_19_season_half_uses_match_date_not_collection_date(self):
        self.assertEqual(c.club_period('2027-01-02')[0],'2026-27_H2')
        self.assertEqual(c.club_period('2025-10-03')[0],'2025-26_H1')
    def test_20_two_competitions_not_same_fixture(self):
        a=self.put(create=True);b=self.put(dict(self.obs,competition_key='CUP',provider_match_id='cup'),create=True)
        self.assertNotEqual(a['match_id'],b['match_id'])
