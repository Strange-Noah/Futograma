import unittest,sqlite3,json
from squad_pipeline.quality_membership import evaluate,reconcile
from squad_pipeline.managed_roster import migrate,add_event
E=[dict(provider='Official',source_url='https://club.example/squad',sha256='a'*64)]
def obs(assertion,**kw):return dict(player_id=1,club_id=10,assertion=assertion,identity_status='VERIFIED',verification_status='VERIFIED',source_scope='OFFICIAL_ROSTER',effective_from='2026-08-01',observed_on='2026-11-03',evidence=E,**kw)
class MembershipTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.db.execute('CREATE TABLE players(player_id INTEGER PRIMARY KEY)');self.db.execute('INSERT INTO players VALUES(1)');self.db.execute('CREATE TABLE historical_facts(player_id INTEGER,minutes INTEGER)');self.db.execute('INSERT INTO historical_facts VALUES(1,90)');migrate(self.db)
 def tearDown(self):self.db.close()
 def run_reconcile(self,observations=()):return reconcile(self.db,10,[1],'2026-11-03',observations)['decisions'][0]
 def test_provider_squad_is_not_verified_management(self):
  o=obs('FIRST_TEAM');o.update(verification_status='PROVIDER_ASSERTED',source_scope='PROVIDER_SQUAD');r=self.run_reconcile([o]);self.assertEqual(r['obligation']['status'],'MISSING');self.assertEqual(self.db.execute('SELECT count(*) FROM club_management_events').fetchone()[0],0)
 def test_official_current_first_team_and_repeat(self):
  r=self.run_reconcile([obs('FIRST_TEAM')]);self.assertTrue(r['included']);self.run_reconcile([obs('FIRST_TEAM')]);self.assertEqual(self.db.execute('SELECT count(*) FROM quality_membership_decisions').fetchone()[0],1)
 def test_absence_and_injury_never_exclude(self):
  self.run_reconcile([obs('FIRST_TEAM_INJURED')]);self.assertTrue(self.run_reconcile()['included'])
 def test_transfer_preserves_facts(self):
  self.run_reconcile([obs('FIRST_TEAM')]);o=obs('TRANSFERRED_OUT');o['effective_from']='2026-10-01';r=self.run_reconcile([o]);self.assertFalse(r['included']);self.assertEqual(tuple(self.db.execute('SELECT * FROM historical_facts').fetchone()),(1,90))
 def test_future_departure_not_applied(self):
  self.run_reconcile([obs('FIRST_TEAM')]);o=obs('LOAN_OUT');o['effective_from']='2026-12-01';self.assertTrue(self.run_reconcile([o])['included'])
 def test_missing_departure_date_quarantined(self):
  o=obs('LOAN_OUT');o['effective_from']=None;r=self.run_reconcile([o]);self.assertEqual(r['obligation']['status'],'MISSING')
 def test_manual_first_team_not_downgraded_by_youth(self):
  add_event(self.db,10,1,'FIRST_TEAM','2026-10-31','2026-10-31',E,'AUTHORIZED_MANUAL_ROSTER_DECISION');self.assertTrue(self.run_reconcile([obs('YOUTH')])['included'])
 def test_conflict_does_not_append_event(self):
  r=self.run_reconcile([obs('FIRST_TEAM'),obs('LOAN_OUT')]);self.assertEqual(r['obligation']['status'],'CONFLICTED');self.assertEqual(self.db.execute('SELECT count(*) FROM club_management_events').fetchone()[0],0)
 def test_chronology_not_falsely_conflicted(self):
  out=obs('TRANSFERRED_OUT');out['effective_from']='2026-10-01';r=self.run_reconcile([obs('FIRST_TEAM'),out]);self.assertFalse(r['included']);self.assertEqual(r['status'],'TRANSFERRED_OUT')
 def test_ownership_does_not_undo_loan(self):
  self.run_reconcile([obs('LOAN_OUT')]);o=obs('FIRST_TEAM');o['effective_from']='2026-10-01';self.assertFalse(self.run_reconcile([o])['included'])
 def test_explicit_return_reopens_membership(self):
  self.run_reconcile([obs('LOAN_OUT')]);o=obs('EXPLICIT_RETURN');o['effective_from']='2026-10-01';self.assertTrue(self.run_reconcile([o])['included'])
 def test_identity_mismatch_does_not_accept_management(self):
  self.assertEqual(self.run_reconcile([obs('IDENTITY_MISMATCH')])['obligation']['status'],'CONFLICTED')
 def test_reverse_review_includes_unobserved_managed_player(self):
  self.run_reconcile([obs('FIRST_TEAM')]);r=reconcile(self.db,10,[],'2026-11-03');self.assertEqual(len(r['decisions']),1);self.assertTrue(r['decisions'][0]['included'])
 def test_unknown_player_quarantined(self):
  r=reconcile(self.db,10,[2],'2026-11-03')['decisions'][0];self.assertEqual(r['decision'],'QUARANTINED')

 def test_never_belonged_conflicts_with_prior_verified_management(self):
  self.run_reconcile([obs('FIRST_TEAM')]);r=self.run_reconcile([obs('NEVER_BELONGED')]);self.assertEqual(r['obligation']['status'],'CONFLICTED');self.assertTrue(r['included'])

 def test_outer_transaction_rollback_is_preserved(self):
  self.db.commit();self.db.execute('BEGIN');self.run_reconcile([obs('FIRST_TEAM')]);self.db.rollback()
  self.assertEqual(self.db.execute('SELECT count(*) FROM club_management_events').fetchone()[0],0)
  self.assertFalse(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='quality_membership_decisions'").fetchone())
 def test_verified_youth_persists_for_normal_roster_export(self):
  from squad_pipeline.roster_policy import roster_decision
  r=self.run_reconcile([obs('YOUTH')]);self.assertEqual(r['cohort'],'YOUTH')
  self.assertEqual(roster_decision(self.db,10,1,'2026-11-03')['cohort'],'YOUTH')
  self.run_reconcile([obs('YOUTH')]);self.assertEqual(self.db.execute('SELECT count(*) FROM roster_verification_dossiers').fetchone()[0],1)
 def test_youth_dossier_never_downgrades_manual_firstteam(self):
  from squad_pipeline.roster_policy import roster_decision
  add_event(self.db,10,1,'FIRST_TEAM','2026-10-31','2026-10-31',E,'AUTHORIZED_MANUAL_ROSTER_DECISION')
  self.run_reconcile([obs('YOUTH')]);self.assertTrue(roster_decision(self.db,10,1,'2026-11-03')['included'])

 def test_existing_verified_youth_does_not_require_full_reaudit(self):
  self.run_reconcile([obs('YOUTH')]);r=self.run_reconcile();self.assertEqual(r['cohort'],'YOUTH');self.assertEqual(r['obligation']['status'],'SATISFIED')

 def test_persisted_youth_dossier_can_enter_provider_identity_discovery(self):
  from squad_pipeline.identity_pipeline import Record, resolve
  from squad_pipeline.provider_identity_discovery import provider_candidates
  from squad_pipeline.roster_policy import latest

  self.run_reconcile([obs('YOUTH')])
  dossier = dict(latest(self.db)[(10, 1)], name='Example Player')
  candidate, = provider_candidates([dossier])
  self.assertEqual(candidate.player_id, 1)
  self.assertEqual(candidate.records, [])
  self.assertEqual(latest(self.db)[(10, 1)], {k: v for k, v in dossier.items() if k != 'name'})

  profile = Record('https://www.fotmob.com/players/123', 'b' * 64, 'profile',
                   'FotMob', ['Example Player'], provider_ids={'FotMob': '123'})
  self.assertEqual(resolve(profile, [candidate])['identity_status'], 'UNRESOLVED')
