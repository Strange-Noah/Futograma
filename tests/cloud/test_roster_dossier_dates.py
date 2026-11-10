import json
import sqlite3
import unittest
from dataclasses import asdict

from squad_pipeline.identity_pipeline import DDL,Record
from squad_pipeline.managed_roster import migrate,add_event
from squad_pipeline.roster_policy import roster_decision,latest


class HistoricalDossierDates(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
  self.db.executescript(DDL);migrate(self.db)

 def tearDown(self):self.db.close()

 def dossier(self,record,management=None,academy=False):
  proof=asdict(record);state=management or dict(state='INCLUDED',included=True,status='FIRST_TEAM',evidence=[proof])
  payload=dict(management=state,verified_source_records=[proof],academy_affiliation_status='VERIFIED_CURRENT_ACADEMY_EVIDENCE'if academy else '')
  self.db.execute('insert into roster_verification_dossiers values(?,?,?,?,?,?,?,?,?)',('d',1,7,'2026-11-01','VERIFIED','VERIFIED','VERIFIED','VERIFIED',json.dumps(payload)))

 def source(self,**changes):
  return Record('https://club.example/first-team','a'*64,'player-row','OfficialClub',['Example Player'],authority='OFFICIAL',scope='FIRST_TEAM_CARD',observed_on='2026-11-01',**changes)

 def test_future_snapshot_does_not_create_historical_management_even_with_cached_latest(self):
  self.dossier(self.source());r=roster_decision(self.db,1,7,'2024-10-01',latest(self.db))
  self.assertFalse(r['included']);self.assertEqual(r['cohort'],'UNKNOWN')

 def test_actual_snapshot_day_retains_first_team_decision(self):
  self.dossier(self.source());r=roster_decision(self.db,1,7,'2026-11-01')
  self.assertTrue(r['included']);self.assertEqual(r['cohort'],'FIRST_TEAM')

 def test_later_retrieval_can_document_an_explicit_earlier_interval(self):
  self.dossier(self.source(effective_from='2024-07-01',end_exclusive='2025-07-01'))
  self.assertTrue(roster_decision(self.db,1,7,'2024-10-01')['included'])
  self.assertFalse(roster_decision(self.db,1,7,'2025-07-01')['included'])

 def test_future_academy_snapshot_does_not_backfill_historical_cohort(self):
  record=self.source();record.scope='ACADEMY_AFFILIATION'
  self.dossier(record,dict(state='YOUTH',included=False,status='YOUTH',academy_affiliation=[asdict(record)]),academy=True)
  self.assertEqual(roster_decision(self.db,1,7,'2024-10-01')['cohort'],'UNKNOWN')
  self.assertEqual(roster_decision(self.db,1,7,'2026-11-01')['cohort'],'YOUTH')

 def test_verified_departure_authority_is_preserved(self):
  self.dossier(self.source());add_event(self.db,1,7,'TRANSFERRED_OUT','2024-09-01','2024-09-01',[{'accepted':'departure'}],'Verified departure',precision='EXPLICIT_SIGNING_DAY')
  r=roster_decision(self.db,1,7,'2024-10-01');self.assertFalse(r['included']);self.assertEqual(r['cohort'],'EXCLUDED')
