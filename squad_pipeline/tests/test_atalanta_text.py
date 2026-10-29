import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from squad_pipeline import atalanta_text as a,manual
from squad_pipeline.core import BASE,connect,config,seed

class TextParserTests(unittest.TestCase):
    def setUp(self):self.text=(BASE/'manual_inputs/atalanta/2026-27/H1/squad.txt').read_text()
    def test_25_rows_and_group_counts(self):
        rows=a.parse_text(self.text);self.assertEqual(len(rows),25);self.assertEqual(dict(__import__('collections').Counter(r['position'] for r in rows)),a.EXPECTED)
    def test_names_preserved_not_ascii_replaced(self):
        names=[r['full_name'] for r in a.parse_text(self.text)];self.assertIn('Sead Kolašinac',names);self.assertIn('Éderson dos Santos',names)
    def test_coach_not_in_members(self):self.assertNotIn('Maurizio Sarri',[r['full_name'] for r in a.parse_text(self.text)])
    def test_duplicate_number_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate shirt'):a.parse_text(self.text.replace('29\nMarco Carnesecchi','22\nMarco Carnesecchi'))
    def test_group_count_change_rejected(self):
        with self.assertRaisesRegex(ValueError,'count'):a.parse_text(self.text.replace('门将（3人）','门将（4人）'))
    def test_truncated_record_rejected(self):
        with self.assertRaisesRegex(ValueError,'Incomplete|Truncated'):a.parse_text(self.text.replace('Nikola Krstović\n黑山','Nikola Krstović'))
    def test_unknown_nationality_rejected(self):
        with self.assertRaisesRegex(ValueError,'nationality'):a.parse_text(self.text.replace('黑山','未知国籍'))
    def test_no_selection_by_shirt_number(self):
        row=a.parse_text(self.text)[0];e=dict(name=row['full_name'],first_name='',last_name='',country_code='ITA',position_id=0,period='2026-27_H1',shirt_number='99')
        results=a.candidates(row,{1:[e],2:[dict(e,shirt_number=str(row['shirt_number']))]},'2026-27_H1');self.assertEqual(len(results),2);self.assertTrue(all(r['status']=='AMBIGUOUS_MULTIPLE_IDS' for r in results))
    def test_position_difference_not_identity_conflict(self):
        row=a.parse_text(self.text)[0];e=dict(name=row['full_name'],first_name='',last_name='',country_code='ITA',position_id=2,period='2026-27_H1',shirt_number='22')
        self.assertEqual(a.candidates(row,{1:[e]},'2026-27_H1')[0]['status'],'POSITION_CLASSIFICATION_REVIEW_REQUIRED')
    def test_alias_only_candidate_not_verification(self):
        row=next(r for r in a.parse_text(self.text) if r['full_name']=='Éderson dos Santos');e=dict(name='Éderson',first_name='',last_name='Éderson',country_code='BRA',position_id=2,period='2026-27_H1',shirt_number='13')
        self.assertEqual(a.candidates(row,{957203:[e]},'2026-27_H1')[0]['status'],'ALIAS_REVIEW_REQUIRED')
    def test_idempotent_import_and_no_confirmed_id(self):
        (BASE/'tests/runtime').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BASE/'tests/runtime') as tmp:
            root=Path(tmp);folder=root/'manual_inputs/atalanta/2026-27/H1';folder.mkdir(parents=True);(folder/'squad.txt').write_text(self.text);(folder/'source.json').write_bytes((BASE/'manual_inputs/atalanta/2026-27/H1/source.json').read_bytes());(root/'config').mkdir();(root/'config/official_sources.json').write_bytes((BASE/'config/official_sources.json').read_bytes());db=connect(root/'test.sqlite');seed(db,config())
            with patch.object(a,'BASE',root),patch.object(manual,'BASE',root),patch.object(a,'raw_identity_pool',lambda *x:{}):
                first=a.load_baseline(db,folder,root/'output');second=a.load_baseline(db,folder,root/'output')
            self.assertEqual(first['snapshot_id'],second['snapshot_id']);self.assertEqual(db.execute('SELECT COUNT(*) FROM official_membership').fetchone()[0],25);self.assertEqual(db.execute('SELECT COUNT(*) FROM official_membership WHERE fotmob_player_id IS NOT NULL').fetchone()[0],0);self.assertEqual(db.execute('SELECT COUNT(*) FROM players').fetchone()[0],0);self.assertEqual(db.execute('SELECT captured_at FROM official_snapshots').fetchone()[0],None);db.close()
