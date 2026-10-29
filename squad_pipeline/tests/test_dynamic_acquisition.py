"""Controlled dynamic events only; never counted as real-cache evidence."""
import copy,json,tempfile,unittest,sqlite3
from pathlib import Path
from collections import defaultdict
from squad_pipeline.core import BASE,ROOT,write,digest
from squad_pipeline.dynamic_acquisition import Engine,record_decision,get_affiliations,key,artifact
from squad_pipeline.dynamic_sources import available,trustworthy,completed,national_rank_context
from squad_pipeline.player_history import Context
from squad_pipeline.personal_workload import get_workloads

class DynamicTests(unittest.TestCase):
    def setUp(self):
        (BASE/'tests/runtime').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=BASE/'tests/runtime');self.root=Path(self.tmp.name);self.path=self.root/'dynamic.sqlite';self.engine=Engine(self.path);self.db=self.engine.db
        self.context=Context.__new__(Context);self.context.teams=defaultdict(list,{10:[dict(type='CLUB')],20:[dict(type='NATIONAL')],30:[dict(type='NATIONAL_YOUTH')],40:[dict(type='CLUB')]});self.context.competitions=defaultdict(list,{47:[dict(is_friendly=False)],114:[dict(is_friendly=True)],489:[dict(is_friendly=True)]});self.context.catalogs=defaultdict(list)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def obs(self,minutes=90,played=True,mid=1,team=10,comp=47,sha='a',day='2026-08-16'):
        return dict(player_id=1,match_id=mid,kickoff=day+'T12:00:00Z',team_id=team,team_name='Source team',opponent_team_id=99,competition_id=comp,source_competition_id=comp,competition_name='Provider label',match_status='FINISHED',played=played,minutes=minutes,role='starters' if played else 'subs',authoritative_minutes=True,rejected=[],evidence=dict(artifact_id=sha,path='controlled_fixture',sha256=sha,kind='MATCH_DETAIL',source_url=None,acquired_at=None,json_path='$.player'))
    def paths(self,routes='ABC',o=None):
        o=o or self.obs();candidate=dict(match_id=o['match_id'],kickoff=o['kickoff'],teams=[o['team_id'],o['opponent_team_id']],competition_id=o['competition_id'],competition_name=o['competition_name'],finished=True,evidence=o['evidence'])
        return [dict(path=p,status='SUCCESS' if p in routes else 'SOURCE_UNAVAILABLE',candidates=[candidate] if p in routes else [],checked_artifacts=[o['evidence']] if p in routes else [],limitations=[]) for p in 'ABC']
    def cycle(self,day='2026-08-16',routes='ABC',obs=None):
        obs=[self.obs()] if obs is None else obs
        return self.engine.cycle(1,day,'EVENT_DATE',self.paths(routes,obs[0] if obs else None),obs,self.context,'Fixture')
    def state(self):return dict(self.db.execute('SELECT * FROM dynamic_states WHERE match_id=1').fetchone())
    def facts(self):return [dict(r) for r in self.db.execute('SELECT * FROM personal_match_facts')]
    def issue(self,kind):return self.db.execute('SELECT * FROM dynamic_reviews WHERE issue_type=? AND match_id=1',(kind,)).fetchone()
    def test_A_first_then_B_C(self):
        self.cycle(routes='A');self.cycle('2026-08-17','ABC');self.assertEqual(json.loads(self.state()['state_json'])['paths'],['A','B','C']);self.assertEqual(len(self.facts()),1)
    def test_B_first(self):self.cycle(routes='B');self.assertEqual(json.loads(self.state()['state_json'])['paths'],['B'])
    def test_C_only_recovers_missing_A_B(self):self.cycle(routes='C');self.assertEqual(json.loads(self.state()['state_json'])['paths'],['C']);self.assertEqual(self.facts()[0]['minutes'],90)
    def test_all_paths_one_fact(self):self.cycle();self.assertEqual(len(self.facts()),1);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_discoveries').fetchone()[0],3)
    def test_explicit_DNP_only(self):self.cycle(obs=[self.obs(minutes=0,played=False)]);self.assertEqual(self.state()['state'],'CONFIRMED_NONAPPEARANCE');self.assertEqual(self.facts()[0]['minutes'],0)
    def test_unused_bench_unknown_not_zero(self):self.cycle(obs=[self.obs(minutes=None,played=None)]);self.assertEqual(self.state()['state'],'APPEARANCE_PENDING');self.assertIsNone(self.facts()[0]['minutes'])
    def test_missing_minutes_late_arrival(self):
        self.cycle(obs=[self.obs(minutes=None)]);self.assertEqual(self.state()['state'],'MINUTES_PENDING');self.assertIsNone(self.issue('COVERAGE_GAP'))
        self.cycle('2026-08-17',obs=[self.obs(minutes=75,sha='b')]);self.assertEqual(self.state()['state'],'VERIFIED_MINUTES');self.assertEqual(self.facts()[0]['minutes'],75)
    def test_48h_escalation_not_immediate_conflict(self):
        self.cycle(obs=[]);self.assertIsNone(self.issue('APPEARANCE_UNRESOLVED'));self.cycle('2026-08-19',obs=[]);self.assertIsNotNone(self.issue('APPEARANCE_UNRESOLVED'));self.assertEqual(self.state()['state'],'APPEARANCE_PENDING')
    def test_correction_preserves_old_source_and_state(self):
        self.cycle();self.cycle('2026-08-17',obs=[self.obs(minutes=85,sha='b')]);self.assertEqual(self.state()['state'],'CONFLICT');self.assertIsNone(self.facts()[0]['minutes']);self.assertIsNotNone(self.issue('SOURCE_CORRECTION'));self.assertEqual(self.db.execute('SELECT COUNT(*) FROM player_match_discoveries').fetchone()[0],2);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_events').fetchone()[0],2)
    def test_national_friendly(self):self.cycle(obs=[self.obs(team=20,comp=114)]);self.assertEqual(self.state()['state'],'VERIFIED_MINUTES');self.assertEqual(self.facts()[0]['competition_category'],'NATIONAL_TEAM_FRIENDLY')
    def test_club_friendly_excluded(self):self.cycle(obs=[self.obs(comp=489)]);self.assertEqual(self.state()['state'],'EXCLUDED')
    def test_club_and_national_same_rolling_period(self):
        self.cycle();self.cycle('2026-08-17',obs=[self.obs(mid=2,team=20,comp=114,sha='b',day='2026-08-17')]);self.assertEqual(get_workloads([1],'2026-08-17',[7],self.path)[0]['known_minutes'],180)
    def test_transfer_loan_continuity(self):
        self.cycle();self.cycle('2026-08-17',obs=[self.obs(mid=2,team=40,sha='b',day='2026-08-17')]);self.assertEqual(len(self.facts()),2)
    def test_path_failure_all_three_still_logged(self):
        r=self.paths('A');r[1]['status']='FAILED';self.engine.cycle(1,'2026-08-16','EVENT_DATE',r,[self.obs()],self.context);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_paths').fetchone()[0],3)
    def test_unknown_identity_and_classification_queue(self):
        o=self.obs(comp=999);self.cycle(obs=[o]);self.assertIsNotNone(self.issue('COMPETITION_CLASSIFICATION_UNKNOWN'))
    def test_identity_mismatch_is_quarantined(self):
        o=self.obs();o['rejected']=['STATS_ID_TEAM_NAME_MISMATCH'];self.cycle(obs=[o]);self.assertIsNotNone(self.issue('IDENTITY_UNRESOLVED'));self.assertIsNone(self.facts()[0]['minutes'])
    def make_decision(self,action='defer',value=None,scope=None):
        r=self.issue('MINUTES_CONFLICT');return dict(review_id=r['review_id'],action=action,scope=scope or dict(player_id=1,match_id=1),value=value or {},decided_at='2026-08-17T18:00:00+02:00',actor='controlled test reviewer',rationale='Controlled fixture decision')
    def conflict(self):self.cycle();self.cycle('2026-08-17',obs=[self.obs(minutes=85,sha='b')])
    def test_manual_decision_traceable_idempotent(self):
        self.conflict();dec=self.make_decision();a=record_decision(self.path,dec);b=record_decision(self.path,dec);self.assertEqual(a,b);self.cycle('2026-08-18',obs=[self.obs(minutes=85,sha='b')]);self.assertEqual(self.issue('MINUTES_CONFLICT')['review_status'],'DEFERRED');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_decisions').fetchone()[0],1)
    def test_accept_existing_value_and_reopen_new_version(self):
        self.conflict();r=json.loads(self.issue('MINUTES_CONFLICT')['payload_json']);claims=r['candidate_values'];chosen=claims[0];dec=self.make_decision('accept_value',dict(discovery_id=chosen['discovery_id'],minutes=chosen['minutes']),dict(player_id=1,match_id=1,source_hashes=[c['source']['sha256'] for c in claims]));record_decision(self.path,dec);self.cycle('2026-08-18',obs=[self.obs(minutes=85,sha='b')]);self.assertEqual(self.state()['state'],'VERIFIED_MINUTES_MANUAL');self.assertEqual(get_workloads([1],'2026-08-18',[7],self.path)[0]['known_minutes'],chosen['minutes']);self.cycle('2026-08-19',obs=[self.obs(minutes=80,sha='c')]);self.assertEqual(self.state()['state'],'CONFLICT');self.assertIsNotNone(self.issue('SOURCE_CORRECTION'))
    def test_exclude_manual_preserves_raw(self):
        self.conflict();record_decision(self.path,self.make_decision('exclude_match'));self.cycle('2026-08-18');self.assertEqual(self.state()['state'],'EXCLUDED_MANUAL');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM player_match_discoveries').fetchone()[0],2)
    def test_replay_twice_no_duplicates(self):
        self.cycle();self.cycle('2026-08-19',routes='A');counts=lambda:tuple(self.db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['player_match_index','dynamic_events','dynamic_reviews','dynamic_paths']);before=counts();self.cycle();self.cycle('2026-08-19',routes='A');self.assertEqual(before,counts())
    def test_changed_old_clock_cannot_see_final_states(self):
        self.cycle();self.cycle('2026-08-19');self.assertRaises(ValueError,self.cycle,'2026-08-16','A')
    def test_no_new_requires_successful_check(self):
        r=self.paths();r[0].update(status='NO_NEW_RECORDS',candidates=[],checked_artifacts=[]);self.assertRaises(ValueError,self.engine.cycle,1,'2026-08-16','EVENT_DATE',r,[],self.context)
    def test_true_asof_does_not_use_mtime(self):
        p=self.root/'source.json';p.write_text('{}');ev=dict(path=str(p.relative_to(ROOT)),sha256=digest(p.read_bytes()),source_url='https://example.test/data',acquired_at='2026-10-22T10:00:00Z');self.assertFalse(available(ev,'TRUE_AS_OF','2026-08-16'));write(p.with_suffix('.meta.json'),dict(sha256=ev['sha256'],mode='network'));self.assertTrue(trustworthy(ev));self.assertFalse(available(ev,'TRUE_AS_OF','2026-10-21'));self.assertTrue(available(ev,'TRUE_AS_OF','2026-10-22'));self.assertTrue(available(ev,'EVENT_DATE','2026-08-16'))
    def test_completion_clock(self):self.assertFalse(completed(dict(finished=True,kickoff='2026-08-16T23:00:00Z'),'2026-08-16'));self.assertFalse(completed(dict(finished=False,kickoff='2026-08-16T12:00:00Z'),'2026-08-17'))
    def test_affiliation_unknown_not_nationality(self):self.assertEqual(get_affiliations([1],self.path)[1]['status'],'UNKNOWN_NOT_EVIDENCE_OF_NO_REPRESENTATION')
    def test_youth_and_senior_history_not_collapsed(self):
        for i,level in enumerate(['SENIOR','YOUTH']):self.db.execute('INSERT INTO national_affiliations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(str(i),1,20+i,'Team',level,'OBSERVED','fixture','2026-08-16T12:00:00Z','2026-08-15',None,None,'SOURCE', '{}'))
        self.db.commit();self.assertEqual(len(get_affiliations([1],self.path)[1]['historical_evidence']),2);self.assertIsNone(get_affiliations([1],self.path,as_of='2026-08-14')[1]['best_supported'])

    def test_confirm_affiliation_is_queryable_with_audit(self):
        self.cycle();r=self.db.execute('SELECT * FROM dynamic_reviews WHERE match_id=0').fetchone();dec=dict(review_id=r['review_id'],action='confirm_affiliation',scope=dict(player_id=1,match_id=0),value=dict(national_team_id=20,national_team_name='Verified national team',team_level='SENIOR',evidence_ids=['a']),decided_at='2026-08-17T18:00:00+02:00',actor='reviewer',rationale='Direct source confirmation');did=record_decision(self.path,dec);a=get_affiliations([1],self.path)[1];self.assertEqual(a['status'],'MANUAL_CONFIRMED');self.assertEqual(a['best_supported']['evidence_id'],did);self.assertIsNone(a['best_supported']['valid_from']);self.assertIsNone(get_affiliations([1],self.path,as_of='2026-08-16')[1]['best_supported'])
    def test_request_evidence_does_not_discard_candidates(self):
        self.conflict();record_decision(self.path,self.make_decision('request_evidence'));self.assertEqual(self.issue('MINUTES_CONFLICT')['review_status'],'EVIDENCE_REQUESTED');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM player_match_discoveries').fetchone()[0],2)
    def test_arbitrary_manual_number_rejected(self):
        self.conflict();self.assertRaises(ValueError,record_decision,self.path,self.make_decision('accept_value',dict(discovery_id='fabricated',minutes=99),dict(player_id=1,match_id=1,source_hashes=['a','b'])))
    def test_unknown_return_from_adapter_still_three_results(self):
        self.engine.cycle(1,'2026-08-16','EVENT_DATE',self.paths()[:1],[self.obs()],self.context);self.assertEqual(self.db.execute("SELECT COUNT(*) FROM dynamic_paths WHERE status='FAILED'").fetchone()[0],2)
    def test_engine_rejects_future_observation(self):
        o=self.obs(day='2026-08-18');self.assertRaises(ValueError,self.engine.cycle,1,'2026-08-16','EVENT_DATE',self.paths(o=o),[o],self.context)
    def test_engine_rejects_untrusted_asof_timestamp(self):self.assertRaises(ValueError,self.engine.cycle,1,'2026-08-16','TRUE_AS_OF',self.paths(),[self.obs()],self.context)
    def test_valid_no_new_records_logged_without_candidates(self):
        r=self.paths();r[0].update(status='NO_NEW_RECORDS',candidates=[]);self.engine.cycle(1,'2026-08-16','EVENT_DATE',r,[self.obs()],self.context);self.assertEqual(self.db.execute("SELECT COUNT(*) FROM dynamic_paths WHERE status='NO_NEW_RECORDS'").fetchone()[0],1)

    def test_explicit_no_representation_needs_evidence_not_empty_history(self):
        self.cycle();r=self.db.execute('SELECT * FROM dynamic_reviews WHERE match_id=0').fetchone();dec=dict(review_id=r['review_id'],action='confirm_affiliation',scope=dict(player_id=1,match_id=0),value=dict(national_team_id=None,national_team_name=None,team_level='UNKNOWN',affiliation_status='NO_REPRESENTATION_CONFIRMED',evidence_ids=['a']),decided_at='2026-08-17T18:00:00+02:00',actor='controlled test reviewer',rationale='Explicit source in controlled fixture');record_decision(self.path,dec);a=get_affiliations([1],self.path)[1]['best_supported'];self.assertEqual(a['affiliation_status'],'NO_REPRESENTATION_CONFIRMED');self.assertIsNone(a['national_team_id'])

    def test_source_body_and_metadata_versions_survive_same_path_correction(self):
        p=self.root/'source.json';p.write_text('{"minutes":90}');first=dict(artifact_id='source-a',path=str(p.relative_to(ROOT)),sha256=digest(p.read_bytes()),kind='MATCH_DETAIL',source_url=None,acquired_at=None,json_path='$');artifact(self.db,first);old=dict(self.db.execute('SELECT * FROM dynamic_source_versions WHERE artifact_id=?',('source-a',)).fetchone());p.write_text('{"minutes":85}');second=dict(first,artifact_id='source-b',sha256=digest(p.read_bytes()));artifact(self.db,second);self.db.commit();self.assertEqual((ROOT/old['immutable_path']).read_text(),'{"minutes":90}');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_source_versions').fetchone()[0],2)
    def test_equivalent_kickoff_offsets_do_not_conflict(self):
        o=self.obs();r=self.paths(o=o);r[2]['candidates'][0]=copy.deepcopy(r[2]['candidates'][0]);r[2]['candidates'][0]['kickoff']='2026-08-16T14:00:00+02:00';self.engine.cycle(1,'2026-08-16','EVENT_DATE',r,[o],self.context);self.assertEqual(self.state()['state'],'VERIFIED_MINUTES')

    def test_same_day_path_result_versions_are_preserved(self):
        self.cycle(routes='A');self.cycle(routes='ABC');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_paths').fetchone()[0],3);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_path_versions').fetchone()[0],6);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM dynamic_events').fetchone()[0],2)

    def test_national_rank_context_never_reads_nationality(self):
        ctx=Context.__new__(Context);ctx.teams=defaultdict(list);detail=dict(general=dict(countryCode='INT'),header=dict(teams=[dict(id=20,name='Norway',fifaRank=19),dict(id=10,name='Club',fifaRank=None)]));national_rank_context(ctx,detail,self.obs()['evidence'],{20,10});self.assertEqual(ctx.team_type(20),'NATIONAL');self.assertEqual(ctx.team_type(10),'UNKNOWN');self.assertEqual(ctx.teams[20][0]['json_path'],'$.header.teams[0].fifaRank')
