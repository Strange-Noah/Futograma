import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from squad_pipeline.core import BASE,connect,config,seed,artifact
from squad_pipeline import roster_import as r

def person(name='Jane Smith',birth=None):return dict(official_member_id='local:1',name=name,shirt_number='9',position='前锋',nationality='意大利',country_code='ITA',date_of_birth=birth,official_source_player_id=None)
def observation(name='Jane Smith',country='ITA',period='2024-25_H1'):
    return dict(name=name,first_name='',last_name='',country_code=country,period=period,position_id=2,shirt_number='77',date='2024-09-01',age=25,opta_id='p900')

class IdentityPolicyTests(unittest.TestCase):
    def test_exact_name_nationality_and_club_evidence(self):self.assertEqual(r.resolve_identity(person(),{100:[observation()]})['player_id'],100)
    def test_position_not_hard_constraint(self):self.assertEqual(r.resolve_identity(person(),{100:[observation()]})['status'],'CONFIRMED_AUTOMATIC')
    def test_no_current_season_appearance_required(self):self.assertEqual(r.resolve_identity(person(),{100:[observation(period='2024-25_H1')]})['status'],'CONFIRMED_AUTOMATIC')
    def test_shirt_number_does_not_select_id(self):
        x=r.resolve_identity(person(),{100:[observation()],101:[dict(observation(),shirt_number='9')]});self.assertEqual(x['reason'],'MULTIPLE_PLAUSIBLE_IDS');self.assertIsNone(x['player_id'])
    def test_nationality_conflict(self):self.assertEqual(r.resolve_identity(person(),{100:[observation(country='FRA')]})['status'],'UNRESOLVED')
    def test_date_of_birth_conflict(self):
        e=dict(observation(),date_of_birth='2000-01-01');self.assertEqual(r.resolve_identity(person(birth='1999-01-01'),{100:[e]})['status'],'UNRESOLVED')
    def test_same_dob_different_iso_formats(self):
        e=dict(observation(),date_of_birth='1999-01-01T00:00:00Z');self.assertEqual(r.resolve_identity(person(birth='1999-01-01'),{100:[e]})['status'],'CONFIRMED_AUTOMATIC')
    def test_bare_alias_is_weak(self):
        self.assertEqual(r.resolve_identity(person(name='Jane Smith'),{100:[observation(name='Jane')]})['status'],'UNRESOLVED')
    def test_documented_alias_stable_id_and_age(self):
        row=person(name='Jane dos Santos');e=observation(name='Jane');a=dict(full_name='Jane Maria dos Santos Silva',aliases=['Jane'],country_code='ITA',date_of_birth='1999-07-07',source_player_id='provider:stable900',raw_path='cached.json',sha256='fixture')
        x=r.resolve_identity(row,{100:[e]},[a]);self.assertEqual(x['status'],'CONFIRMED_AUTOMATIC');self.assertEqual(x['reason'],'DOCUMENTED_ALIAS_STABLE_IDS_NATIONALITY_AGE')
    def test_alias_age_conflict_blocks_confirmation(self):
        row=person(name='Jane dos Santos');e=observation(name='Jane');a=dict(full_name='Jane dos Santos Silva',aliases=['Jane'],country_code='ITA',date_of_birth='1989-07-07',source_player_id='provider900')
        self.assertEqual(r.resolve_identity(row,{100:[e]},[a])['status'],'UNRESOLVED')
    def test_missing_identity_is_explicit(self):self.assertEqual(r.resolve_identity(person(),{})['reason'],'NO_RELIABLE_FOTMOB_IDENTITY')
    def test_missing_nationality_does_not_confirm_unique_name(self):self.assertEqual(r.resolve_identity(person(),{100:[observation(country=None)]})['status'],'UNRESOLVED')

class DatabaseImportTests(unittest.TestCase):
    def setUp(self):
        (BASE/'tests/runtime').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=BASE/'tests/runtime');self.path=Path(self.tmp.name);self.db=connect(self.path/'test.sqlite');seed(self.db,config());r.import_schema(self.db)
        p=self.path/'source.txt';p.write_text('Synthetic source fixture only');aid=artifact(self.db,p,'test')
        self.db.execute('INSERT INTO acquisition_sources VALUES(?,?,?,?,?)',('test',8524,'Synthetic','https://example.test/roster','{}'))
        self.db.execute('INSERT INTO official_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',('snap','test',8524,'2026-27_H1',aid,None,None,None,'ACQUIRED','PARSED_PENDING_IDENTITY','USER_ASSIGNED_SEASON_UNLABELLED'))
        self.native=[person(),dict(person(name='Rob Jones'),official_member_id='local:2',shirt_number='10')]
        for x in self.native:self.db.execute('INSERT INTO official_membership VALUES(?,?,?,?,?,?,?)',('snap',x['official_member_id'],x['name'],None,x['shirt_number'],None,'original_provenance'))
        for pid,name in [(100,'Jane Smith'),(101,'Rob Jones'),(102,'Youth Player')]:self.db.execute('INSERT INTO players(player_id,display_name) VALUES(?,?)',(pid,name))
        self.db.commit();self.snapshot=dict(self.db.execute('SELECT * FROM official_snapshots').fetchone());self.source=dict(source_id='test',url='https://example.test/roster')
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def rows(self,unresolved=False):
        solutions=[r.resolve_identity(self.native[0],{100:[observation()]}),r.resolve_identity(self.native[1],{} if unresolved else {101:[observation(name='Rob Jones')]})]
        return r.standardize(self.native,solutions,self.snapshot,self.source,'2026/27','H1',dict(season_assignment_basis='USER_ASSIGNED',season_displayed=None))
    def test_transaction_rollback(self):
        rows=self.rows();rows[1]['fotmob_player_id']=999999
        with self.assertRaises(sqlite3.IntegrityError):r.commit_roster(self.db,rows)
        for t in ('standardized_official_roster','provider_identities','identity_decisions'):self.assertEqual(self.db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM official_membership WHERE fotmob_player_id IS NOT NULL').fetchone()[0],0)
    def test_idempotent_commit(self):
        rows=self.rows();r.commit_roster(self.db,rows);r.commit_roster(self.db,rows)
        for t in ('standardized_official_roster','provider_identities','identity_decisions'):self.assertEqual(self.db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0],2)
    def test_unresolved_membership_retained(self):
        r.commit_roster(self.db,self.rows(unresolved=True));self.assertEqual(self.db.execute('SELECT COUNT(*) FROM official_membership').fetchone()[0],2);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM standardized_official_roster WHERE fotmob_player_id IS NULL').fetchone()[0],1)
    def test_unknown_capture_and_source_names_preserved(self):
        rows=self.rows();rows[0]['player_name_fotmob']='J. Smith';r.commit_roster(self.db,rows)
        x=self.db.execute('SELECT * FROM standardized_official_roster WHERE official_member_id=?',('local:1',)).fetchone();self.assertIsNone(x['captured_at']);self.assertIsNone(x['source_season_label']);self.assertIsNone(x['official_source_player_id']);self.assertEqual(x['player_name_official'],'Jane Smith');self.assertEqual(x['player_name_fotmob'],'J. Smith');self.assertEqual(self.db.execute('SELECT provenance FROM official_membership LIMIT 1').fetchone()[0],'original_provenance')
    def test_conflicting_mapping_not_silently_replaced(self):
        r.commit_roster(self.db,self.rows());rows=self.rows();rows[0]['fotmob_player_id']=102
        with self.assertRaises(sqlite3.IntegrityError):r.commit_roster(self.db,rows)
        self.assertEqual(self.db.execute("SELECT fotmob_player_id FROM official_membership WHERE official_player_id='local:1'").fetchone()[0],100)
    def test_unique_identity_per_snapshot(self):
        rows=self.rows();rows[1]['fotmob_player_id']=100
        with self.assertRaises(sqlite3.IntegrityError):r.commit_roster(self.db,rows)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM standardized_official_roster').fetchone()[0],0)
    def add_match(self):
        aid=self.snapshot['artifact_id'];self.db.execute('INSERT INTO competitions VALUES(?,?,?,?)',(55,'Serie A','DomesticLeague','ITA'));self.db.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?,?,?,?)',(1,8524,999,'2026-09-01T19:00:00Z','2026-09-01','2026-27_H1',55,'0-0',aid,'test',55));self.db.execute('INSERT INTO squad_observations VALUES(?,?,?,?,?,?,?,?,?)',(8524,1,102,'subs','99','Youth Player','$.synthetic',aid,'test'));self.db.commit()
    def test_official_only_matchday_only_and_unresolved_separate(self):
        r.commit_roster(self.db,self.rows(unresolved=True));self.add_match();combined,counts,unresolved=r.reconcile(self.db,self.snapshot,'2026-27_H1',dict(expected_known_matches=8,all_official_competitions_complete=False))
        self.assertEqual(counts['official_only'],1);self.assertEqual(counts['matchday_only'],1);self.assertEqual(counts['combined_unique_fotmob_ids'],2);self.assertEqual(counts['unresolved_official'],1);self.assertTrue(counts['comparison_provisional']);self.assertEqual(counts['missing_lineup_matches'],7);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM current_import_roster').fetchone()[0],2)
    def test_unknown_coverage_provisional(self):
        r.commit_roster(self.db,self.rows());_,counts,_=r.reconcile(self.db,self.snapshot,'2026-27_H1',None);self.assertTrue(counts['comparison_provisional']);self.assertIsNone(counts['known_matches'])
    def test_partial_snapshot_confirmed_ids_visible(self):
        r.commit_roster(self.db,self.rows(unresolved=True));self.assertEqual(self.db.execute('SELECT COUNT(*) FROM verified_baseline_membership').fetchone()[0],1)

    def test_blocked_identity_stage_resumes_after_source_and_parsing(self):
        folder=self.path/'manual';folder.mkdir();(folder/'source.json').write_text(json.dumps({'season_assignment_basis':'USER_ASSIGNED','season_displayed':None}))
        adapter=dict(parser='synthetic',period='2026-27_H1',manual_folder=str(folder.relative_to(BASE)))
        settings=dict(adapters={'atalanta':adapter},independent_identity_caches=[]);out=self.path/'result'
        source=dict(source_id='test',club_key='atalanta',preferred=True,url='https://example.test/roster')
        real_read=r.read
        def reader(path):return [source] if Path(path).name=='official_sources.json' else real_read(path)
        def parser(*args):return self.native,self.snapshot,dict(parsing='SYNTHETIC_ALREADY_PARSED_FIXTURE')
        with patch.object(r,'read',reader),patch.dict(r.PARSERS,{'synthetic':parser}),patch.object(r,'raw_identity_pool',side_effect=r.ImportBlocked('Required raw record corrupt')):
            first=r.run_import(self.db,'atalanta','2026-27','H1',settings,out,register_inputs=False)
        self.assertEqual(first['status'],'BLOCKED');self.assertEqual(first['blocked_stage'],'IDENTITY_VALIDATION');self.assertTrue((out/'parsed_stage.json').exists());self.assertEqual(self.db.execute('SELECT COUNT(*) FROM standardized_official_roster').fetchone()[0],0)
        with patch.object(r,'read',reader),patch.dict(r.PARSERS,{'synthetic':parser}),patch.object(r,'raw_identity_pool',return_value={100:[observation()],101:[observation(name='Rob Jones')]}):
            second=r.run_import(self.db,'atalanta','2026-27','H1',settings,out,register_inputs=False)
        self.assertEqual(second['counts']['confirmed_official'],2);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM standardized_official_roster').fetchone()[0],2)
    def test_unconfigured_parser_keeps_source_registration(self):
        source=dict(source_id='test',club_key='atalanta',preferred=True,url='https://example.test/roster');real_read=r.read
        with patch.object(r,'read',lambda p:[source] if Path(p).name=='official_sources.json' else real_read(p)):
            result=r.run_import(self.db,'atalanta','2026-27','H1',dict(adapters={},independent_identity_caches=[]),self.path/'blocked',register_inputs=False)
        self.assertEqual(result['status'],'BLOCKED');self.assertIn('PARSER_NOT_CONFIGURED',result['reason']);self.assertEqual(self.db.execute("SELECT COUNT(*) FROM roster_import_steps WHERE stage='SOURCE_REGISTRATION'").fetchone()[0],1)
