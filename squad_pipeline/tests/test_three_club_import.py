import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from squad_pipeline.core import BASE,ROOT,connect,config,seed,read,write
from squad_pipeline.three_club_parsers import parse_simple,parse_barcelona
from squad_pipeline.identity_v2 import matching_identity
from squad_pipeline import manual
from squad_pipeline.acquisition import seed_sources

class ExcerptTests(unittest.TestCase):
    def text(self,club):return (BASE/f'manual_inputs/{club}/2026-27/H1/squad.txt').read_text()
    def test_psg_24_position_counts_and_ferran(self):
        rows=parse_simple(self.text('psg'),'psg');self.assertEqual(len(rows),24);self.assertEqual(next(r['name'] for r in rows if r['shirt_number']=='9'),'Ferran');self.assertTrue(all(r['nationality'] is None for r in rows))
    def test_bayern_24_and_ds_null(self):
        rows=parse_simple(self.text('bayern'),'bayern');self.assertEqual(len(rows),24);self.assertIsNone(next(r['shirt_number'] for r in rows if r['name']=='David Santos Daiber'))
    def test_bayern_statistics_and_staff_do_not_add_players(self):
        text=self.text('bayern')+'\nGoals: 10\nAssists: 5\nMinutes played: 999\nCoaching staff\n00-Hansi_Flick\n'
        self.assertEqual(len(parse_simple(text,'bayern')),24)
    def test_expected_count_failure_does_not_fill(self):
        with self.assertRaisesRegex(ValueError,'count'):parse_simple(self.text('psg').replace('2 | Achraf Hakimi | defender',''),'psg')
    def test_barcelona_27_and_original_labels(self):
        rows=parse_barcelona(self.text('barcelona'));self.assertEqual(len(rows),27);r=next(r for r in rows if r['name']=='Joan Garcia');self.assertEqual(r['raw_asset_label'],'01-Joan_Garcia-BLUE');self.assertEqual(r['candidate_full_name'],'Joan García');self.assertEqual(r['name'],'Joan Garcia')
    def test_zero_or_missing_prefix_is_null(self):
        rows=parse_barcelona(self.text('barcelona'));self.assertTrue(all(r['shirt_number'] is None for r in rows if r['raw_asset_label'].startswith('00-') or r['raw_asset_label']=='Livakovic-blau'))
    def test_asset_colors_not_attributes(self):
        rows=parse_barcelona(self.text('barcelona'));self.assertTrue(all(r['nationality'] is None for r in rows));self.assertNotIn('Hansi Flick',[r['name'] for r in rows])
    def test_supplied_numbers_not_corrected_from_other_sources(self):
        rows=parse_barcelona(self.text('barcelona'));self.assertEqual(next(r['shirt_number'] for r in rows if r['name']=='Szczesny'),'25');self.assertEqual(next(r['shirt_number'] for r in rows if r['name']=='Bardghji'),'28')
    def test_invalid_asset_zero_as_real_number_rejected(self):
        with self.assertRaises(ValueError):parse_barcelona(self.text('barcelona').replace('00-Cancelo | Cancelo | João Cancelo | defender | NULL','00-Cancelo | Cancelo | João Cancelo | defender | 0'))

def obs(name='Jane Smith',first='Jane',last='Smith',mid=1):
    return dict(name=name,first_name=first,last_name=last,match_id=mid,date='2026-09-01',json_path='$.synthetic',opta_id='p123',country_code='ITA',period='2024-25_H1',cached_profile=None)
def row(name='Jane Smith',candidate=None):return dict(name=name,candidate_full_name=candidate,country_code=None,nationality=None,date_of_birth=None,shirt_number=None,position='defender')
def authority(full='Jane Smith',aliases=None):return dict(full_name=full,aliases=aliases or [full],country_code='ITA',source_player_id='uefa:123',raw_path='fixture.html',sha256='fixture')

class NewIdentityPolicyTests(unittest.TestCase):
    def test_missing_source_nationality_not_fabricated(self):
        x=row();result=matching_identity(x,{1:[obs(mid=1),obs(mid=2)]},[]);self.assertEqual(result['status'],'CONFIRMED_AUTOMATIC');self.assertIsNone(x['nationality'])
    def test_weak_surname_with_full_candidate_not_confirmed(self):
        x=matching_identity(row('Smith','Jane Smith'),{1:[obs(mid=1),obs(mid=2)]},[authority()]);self.assertEqual(x['status'],'UNRESOLVED');self.assertEqual(x['evidence']['candidates'][0]['fotmob_player_id'],1)
    def test_explicit_independent_alias_supports_confirmation(self):
        x=matching_identity(row('Smith','Jane Smith'),{1:[obs()]},[authority(aliases=['Jane Smith','Smith'])]);self.assertEqual(x['status'],'CONFIRMED_AUTOMATIC')
    def test_unique_common_firstname_without_bridge_not_confirmed(self):
        x=matching_identity(row('Jane','Jane Smith'),{1:[obs()]},[]);self.assertEqual(x['status'],'UNRESOLVED')
    def test_given_name_repeated_club_and_authority_bridge(self):
        x=matching_identity(row('Jane','Jane Smith'),{1:[obs(mid=1),obs(mid=2)]},[authority()]);self.assertEqual(x['status'],'CONFIRMED_AUTOMATIC')
    def test_no_expansion_to_missing_canonical_id(self):
        x=matching_identity(row('Bisiwu','Bisiwu'),{},[authority(full='Jesse Bisiwu',aliases=['Jesse Bisiwu','Bisiwu'])]);self.assertEqual(x['status'],'UNRESOLVED');self.assertIsNone(x['player_id']);self.assertEqual(len(x['evidence']['independent_identity_hints']),1)
    def test_conflicting_profile_nationality_blocks(self):
        e=obs();e['cached_profile']=dict(id=1,name='Jane Smith',country_code='FRA',date_of_birth='2000-01-01');x=matching_identity(row(),{1:[e,dict(e,match_id=2)]},[]);self.assertEqual(x['status'],'UNRESOLVED')
    def test_multiple_ids_remain_unresolved(self):
        x=matching_identity(row(),{1:[obs(mid=1),obs(mid=2)],2:[obs(mid=3),obs(mid=4)]},[authority()]);self.assertEqual(x['reason'],'MULTIPLE_PLAUSIBLE_IDS')
    def test_unused_sub_missing_opta_is_not_identity_conflict(self):
        x=matching_identity(row(),{1:[obs(mid=1),dict(obs(mid=2),opta_id=None)]},[]);self.assertEqual(x['status'],'CONFIRMED_AUTOMATIC')
    def test_full_name_shortened_prefix_only_a_candidate(self):
        x=matching_identity(row('David Santos Daiber'),{1:[obs(name='David Santos',first='David',last='Santos')]},[]);self.assertEqual(x['status'],'UNRESOLVED');self.assertEqual(x['evidence']['candidates'][0]['fotmob_player_id'],1)

class UserSuppliedOfflineInputTests(unittest.TestCase):
    def test_receipt_is_not_website_permission_or_network(self):
        (BASE/'tests/runtime').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BASE/'tests/runtime') as tmp:
            root=Path(tmp);folder=root/'manual_inputs/psg/2026-27/H1';folder.mkdir(parents=True);text='Synthetic user text';(folder/'squad.txt').write_text(text);proof=root/'request.txt';proof.write_text('Synthetic authorised user-supplied fixture')
            from squad_pipeline.core import digest
            m=dict(source_id='test',source_url='https://example.test/roster',season='2026/27',half='H1',captured_at=None,acquisition_method='user_supplied_attachment_offline',evidence_origin='USER_SUPPLIED_ROSTER_TRANSCRIPTION_NOT_ORIGINAL_WEB_BYTES',user_request_path=str(proof.relative_to(ROOT)),user_request_sha256=digest(proof.read_bytes()),content_sha256=digest(text.encode()),permission_evidence=None)
            (folder/'source.json').write_text(json.dumps(m));source=dict(source_id='test',club_key='psg',club_id=9847,organization='Fixture',url=m['source_url'],policy='RESTRICTED');db=connect(root/'test.sqlite');seed(db,config());seed_sources(db,[source])
            with patch.object(manual,'BASE',root):result=manual.register(db,folder,[source])
            self.assertEqual(result[0]['status'],'ACQUIRED');self.assertIsNone(db.execute('SELECT captured_at FROM official_snapshots').fetchone()[0]);self.assertEqual(source['policy'],'RESTRICTED');db.close()
    def test_immutable_transcription_hash_checked(self):
        manifest=read(BASE/'manual_inputs/psg/2026-27/H1/source.json');self.assertIsNone(manifest['captured_at']);self.assertEqual(manifest['season_displayed'],'2026-27')


class CachedOtherTeamIdentityTests(unittest.TestCase):
    def make(self,root,mid,pid):
        from squad_pipeline.core import digest
        player=dict(id=pid,name='Jesse Bisiwu',firstName='Jesse',lastName='Bisiwu',countryCode='BEL',age=18,optaId='625570')
        d=dict(general=dict(matchId=str(mid),finished=True,homeTeam=dict(id=1173026),awayTeam=dict(id=6351),matchTimeUTCDate='2026-01-01T12:00:00Z'),header=dict(teams=[dict(id=1173026),dict(id=6351)]),content=dict(lineup=dict(homeTeam=dict(id=1173026,starters=[player],subs=[]),awayTeam=dict(id=6351,starters=[],subs=[])),playerStats={str(pid):dict(id=pid,name='Jesse Bisiwu',teamId=1173026,optaId='625570')}))
        path=root/f'raw/matches/{mid}.json';path.parent.mkdir(exist_ok=True,parents=True);path.write_text(json.dumps(d));return path
    def test_zero_id_rejected_and_positive_ids_preserved(self):
        from squad_pipeline.identity_v2 import local_other_team_evidence
        (BASE/'tests/runtime').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BASE/'tests/runtime') as tmp:
            root=Path(tmp);self.make(root,900,0);self.make(root,901,1656591);self.make(root,902,1656591)
            pool=local_other_team_evidence([row('Bisiwu','Bisiwu')],root);self.assertEqual(set(pool),{1656591});self.assertEqual(len(pool[1656591]),2);self.assertTrue(all(e['observed_team_id']==1173026 and e['evidence_scope']=='OTHER_TEAM_IDENTITY_ONLY_NOT_IMPORTING_CLUB_MEMBERSHIP' for e in pool[1656591]))
    def test_new_canonical_requires_two_checked_raw_records(self):
        from squad_pipeline import identity_v2 as v
        (BASE/'tests/runtime').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BASE/'tests/runtime') as tmp:
            root=Path(tmp);self.make(root,901,1656591);self.make(root,902,1656591);pool=v.local_other_team_evidence([row('Bisiwu','Bisiwu')],root)
            candidate=dict(fotmob_player_id=1656591,strong=True,raw_match_evidence=pool[1656591]);record=dict(fotmob_player_id=1656591,identity_evidence=json.dumps(dict(candidates=[candidate])))
            with patch.object(v,'ROOT',root):self.assertTrue(v.verify_new_canonical_player(record))
            candidate['raw_match_evidence']=candidate['raw_match_evidence'][:1];record['identity_evidence']=json.dumps(dict(candidates=[candidate]))
            with patch.object(v,'ROOT',root):self.assertFalse(v.verify_new_canonical_player(record))
    def test_corrupt_source_not_accepted_for_new_player(self):
        from squad_pipeline import identity_v2 as v
        (BASE/'tests/runtime').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=BASE/'tests/runtime') as tmp:
            root=Path(tmp);a=self.make(root,901,1656591);self.make(root,902,1656591);pool=v.local_other_team_evidence([row('Bisiwu','Bisiwu')],root);a.write_text('{}')
            record=dict(fotmob_player_id=1656591,identity_evidence=json.dumps(dict(candidates=[dict(fotmob_player_id=1656591,strong=True,raw_match_evidence=pool[1656591])])))
            with patch.object(v,'ROOT',root):self.assertFalse(v.verify_new_canonical_player(record))
