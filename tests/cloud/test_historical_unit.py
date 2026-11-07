import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from ops.cloud import historical_unit as job
from squad_pipeline.core import ROOT
from squad_pipeline.source_interface import EvidenceStore, SourceRequest, SourceService
from squad_pipeline.roster_initialization import PeriodUnit


class HistoricalUnitTests(unittest.TestCase):
    def setUp(self):
        parent=ROOT/'.cloud/portable-unit-tests';parent.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=parent);self.root=Path(self.tmp.name)
        self.unit=PeriodUnit(9847,'2024/25','H1','2024-07-01','2025-01-01')
        self.store=EvidenceStore(self.root/'source')
        self.service=SourceService(self.store,'test',set(),max_calls=0)
        self.records=[]
        for i in range(2):
            request=SourceRequest('PSG','article',f'https://www.psg.fr/test/{i}',str(i))
            a=self.store.import_body(request,f'<html><title>Controlled {i}</title></html>'.encode(),{'http_status':200})
            self.records.append(dict(artifact=asdict(a)))
    def tearDown(self):self.tmp.cleanup()
    def test_interrupted_budget_resumes_without_replaying_completed_parse(self):
        work=self.root/'work'
        with job.checkpoint(work,self.unit) as state:
            first,c=job.replay(state,self.service,self.records,1)
            self.assertEqual((c['processed'],c['remaining']),(1,1))
        with job.checkpoint(work,self.unit) as state:
            second,c=job.replay(state,self.service,self.records,1)
            self.assertEqual((c['processed'],c['reused'],c['remaining']),(1,1,0))
            _,c=job.replay(state,self.service,self.records,0)
            self.assertEqual((c['processed'],c['reused'],c['remaining']),(0,2,0))
        self.assertEqual(self.service.client.calls,0)
    def test_duplicate_artifact_has_one_durable_obligation(self):
        with job.checkpoint(self.root/'work',self.unit) as state:
            _,c=job.replay(state,self.service,self.records*3,10)
            self.assertEqual((c['unique_tasks'],c['processed']),(2,2))
    def test_corrupted_body_cannot_reuse_saved_success(self):
        records=self.records[:1];a=job.artifact(records[0])
        with job.checkpoint(self.root/'work',self.unit) as state:
            job.replay(state,self.service,records,1)
            self.store.candidates(a.locator)[0].write_bytes(b'controlled corruption')
            outcomes,c=job.replay(state,self.service,records,1)
            self.assertEqual(c['reused'],0)
            self.assertEqual(outcomes[0]['acquisition'],'INTEGRITY_CONFLICT')
    def test_changed_parser_creates_new_parse_checkpoint(self):
        with job.checkpoint(self.root/'work',self.unit) as state:
            job.replay(state,self.service,self.records,2)
            with patch.object(job,'PARSER_VERSION','changed-parser-for-test'):
                _,c=job.replay(state,self.service,self.records,2)
            self.assertEqual(c['processed'],2)
    def test_different_unit_cannot_reuse_workspace(self):
        work=self.root/'work'
        with job.checkpoint(work,self.unit):pass
        other=PeriodUnit(9847,'2024/25','H2','2025-01-01','2025-07-01')
        with self.assertRaisesRegex(ValueError,'DIFFERENT_UNIT'):
            with job.checkpoint(work,other):pass
    def test_live_policy_and_external_workspace_rejected(self):
        record=json.loads(json.dumps(self.records[0]));record['artifact']['request']['policy']='LIVE_ALLOWED'
        with self.assertRaisesRegex(ValueError,'NO_NEW_COLLECTION'):job.artifact(record)
        with self.assertRaisesRegex(ValueError,'BOUNDARY'):job.within('/etc')
    def test_lock_prevents_concurrent_work_owner(self):
        work=self.root/'work'
        with job.checkpoint(work,self.unit):
            with self.assertRaises(BlockingIOError):
                with job.checkpoint(work,self.unit):pass
    def population(self,swapped=False,offset=False):
        db=sqlite3.connect(':memory:')
        db.executescript("CREATE TABLE canonical_provider_entities(kind,provider,provider_id,entity_id);CREATE TABLE verified_identity_links(provider,provider_player_id,player_id);CREATE TABLE canonical_fixture_sources(domain,provider,provider_match_id,evidence_json);")
        db.executemany('INSERT INTO canonical_provider_entities VALUES(?,?,?,?)',[
            ('TEAM','PSG','own','club:9847'),('TEAM','PSG','other','opponent')])
        db.execute('INSERT INTO verified_identity_links VALUES(?,?,?)',('PSG_STATS_PERFORM','nativeplayer',7))
        f=dict(match_id='accepted-fixture',domain='CLUB',provider='PSG',provider_match_id='native-match',kickoff_utc='2024-09-01T17:00:00Z',status='FINISHED',home=dict(id='club:9847'),away=dict(id='opponent'))
        a=self.records[0]['artifact'];fixture=dict(provider_match_id='native-match',kickoff='2024-09-01T18:00:00Z' if offset else f['kickoff_utc'],source_teams=dict(home=dict(id='other' if swapped else 'own'),away=dict(id='own' if swapped else 'other')))
        n=dict(fixture=fixture,sheets=[dict(configured_club_id=9847,provider_team_id='own',complete_squad=True,players=[dict(provider_key='PSG_STATS_PERFORM:nativeplayer',name='Controlled Person',provider_player_id='nativeplayer',provider_id_namespace='PSG_STATS_PERFORM',role='bench')])])
        o=dict(category='match_squad',provider='PSG',native=n,artifact_key='controlled')
        try:
            with patch.object(job,'export_accepted_catalogue',return_value=dict(accepted=[f])):
                return job.observed_union(db,self.unit,[dict(observation=o,artifact=a)])
        finally:db.close()
    def test_observed_union_reuses_identity_but_never_claims_catalogue_complete(self):
        p,holds=self.population();self.assertEqual(p['player_ids'],[7]);self.assertFalse(p['observed_population_accepted']);self.assertFalse(holds);self.assertEqual(p['canonical_writes'],0)
    def test_same_fixture_id_does_not_override_ordered_team_conflict(self):
        p,holds=self.population(swapped=True);self.assertEqual(p['player_ids'],[]);self.assertEqual(holds[0]['reason'],'ORDERED_SOURCE_TEAM_BINDING_CONFLICT')
    def test_same_fixture_id_does_not_override_kickoff_conflict(self):
        p,holds=self.population(offset=True);self.assertEqual(p['player_ids'],[]);self.assertEqual(holds[0]['reason'],'SOURCE_KICKOFF_BINDING_CONFLICT')
