import copy,json,sqlite3,tempfile,unittest
from pathlib import Path
from collections import defaultdict
from squad_pipeline.core import BASE
from squad_pipeline.personal_workload import connect,resolve,artifact,discovery_id,upsert,get_workloads
from squad_pipeline.player_history import Context
from squad_pipeline.roster_query import get_players,roster_workloads

class WorkloadArchitectureTests(unittest.TestCase):
    def setUp(self):
        (BASE/'tests/runtime').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=BASE/'tests/runtime');self.root=Path(self.tmp.name);self.path=self.root/'workload.sqlite';self.db=connect(self.path)
        self.context=Context.__new__(Context);self.context.teams=defaultdict(list,{10:[dict(type='CLUB')],20:[dict(type='NATIONAL')],30:[dict(type='YOUTH_OR_RESERVE')]});self.context.competitions=defaultdict(list,{55:[dict(is_friendly=False)],114:[dict(is_friendly=True)],489:[dict(is_friendly=True)],264:[dict(is_friendly=False)]});self.db.execute('INSERT INTO workload_players VALUES(1,?)',('Test player',));self.db.commit()
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def observation(self,mid=1,day='2026-01-01T12:00:00Z',team=10,competition=55,minutes=90,played=True,kind='MATCH_DETAIL',sha='a'):
        return dict(player_id=1,match_id=mid,kickoff=day,team_id=team,team_name='Source team',opponent_team_id=99,competition_id=competition,source_competition_id=competition,competition_name='Provider competition',match_status='FINISHED',played=played,minutes=minutes,role='starters' if played else 'subs',authoritative_minutes=True,rejected=[],evidence=dict(artifact_id=sha,path='synthetic',sha256=sha,kind=kind,source_url=None,acquired_at=None,json_path='$.player'))
    def save(self,obs):
        for o in obs:artifact(self.db,o['evidence'])
        idx,app=resolve(obs,self.context);upsert(self.db,'player_match_index',idx,['player_id','match_id']);upsert(self.db,'player_match_appearances',app,['player_id','match_id']);self.db.commit();return idx,app
    def query(self,ref='2026-01-02',w=7):return get_workloads([1],ref,[w],self.path)[0]
    def test_window_crosses_january_and_summer(self):
        self.save([self.observation(mid=1,day='2025-12-29T12:00:00Z')]);self.save([self.observation(mid=2,day='2026-01-01T12:00:00Z',minutes=60)]);self.assertEqual(self.query()['known_minutes'],150)
        self.save([self.observation(mid=3,day='2026-06-29T12:00:00Z')]);self.save([self.observation(mid=4,day='2026-07-02T12:00:00Z',minutes=30)]);self.assertEqual(self.query('2026-07-03')['known_minutes'],120)
    def test_transfer_does_not_reset_window(self):
        self.save([self.observation(mid=1,day='2025-12-29T12:00:00Z',team=10)]);self.context.teams[40]=[dict(type='CLUB')];self.save([self.observation(mid=2,team=40,minutes=45)]);self.assertEqual(self.query()['known_minutes'],135)
    def test_national_competitive_and_friendly_count(self):
        self.save([self.observation(mid=1,team=20,competition=55,minutes=80)]);self.save([self.observation(mid=2,team=20,competition=114,minutes=90)]);r=self.query();self.assertEqual(r['known_minutes'],170);self.assertEqual(r['known_category_minutes']['NATIONAL_TEAM_FRIENDLY'],90)
    def test_club_preseason_is_excluded(self):
        idx,app=self.save([self.observation(competition=489)]);self.assertEqual(idx['eligibility_status'],'EXCLUDE');self.assertEqual(self.query()['known_minutes'],0)
    def test_unknown_friendly_context_is_review(self):
        idx,_=self.save([self.observation(team=999,competition=114)]);self.assertEqual(idx['eligibility_status'],'REVIEW');self.assertIsNone(self.query()['complete_minutes'])
    def test_official_youth_competition_included(self):
        idx,_=self.save([self.observation(team=30,competition=264)]);self.assertEqual(idx['competition_category'],'OFFICIAL_YOUTH_RESERVE');self.assertEqual(self.query()['known_minutes'],90)
    def test_duplicate_discovery_never_adds_minutes(self):
        one=self.observation();two=self.observation(kind='PLAYER_HISTORY_PAGE',sha='b');self.save([one,two]);self.save([one,two]);self.assertEqual(self.query()['known_minutes'],90);self.assertEqual(self.db.execute('select count(*) from player_match_appearances').fetchone()[0],1)
    def test_conflicting_source_minutes_are_null(self):
        self.save([self.observation(),self.observation(minutes=85,kind='PLAYER_HISTORY_PAGE',sha='b')]);r=self.query();self.assertEqual(r['known_minutes'],0);self.assertEqual(r['missing_or_uncertain_minute_records'],1);self.assertIsNone(r['complete_minutes'])
    def test_unused_bench_missing_stats_never_inferred_zero(self):
        _,app=self.save([self.observation(minutes=None,played=None)]);self.assertEqual(app['played_status'],'UNKNOWN_APPEARANCE');self.assertIsNone(app['minutes']);self.assertEqual(self.query()['missing_or_uncertain_minute_records'],1)
    def test_explicit_dnp_and_appearance_zero_differ(self):
        _,a=self.save([self.observation(minutes=0,played=False)]);_,b=self.save([self.observation(mid=2,minutes=0,played=True)]);self.assertEqual(a['played_status'],'CONFIRMED_NONAPPEARANCE');self.assertEqual(b['minutes_status'],'SOURCE_REPORTED_ZERO')
    def test_unknown_player_query_does_not_need_roster(self):
        r=get_workloads([999999],'2026-01-02',[7],self.path)[0];self.assertEqual(r['known_minutes'],0);self.assertIsNone(r['complete_minutes']);self.assertEqual(r['history_status'],'NOT_BUILT_NO_HISTORY_EVIDENCE')
    def test_workload_api_rejects_membership_filter(self):
        with self.assertRaises(TypeError):get_workloads([1],'2026-01-02',club='barcelona',path=self.path)
    def test_strict_lower_inclusive_upper_and_madrid_midnight(self):
        self.save([self.observation(mid=1,day='2025-12-26T12:00:00Z',minutes=100)]);self.save([self.observation(mid=2,day='2026-01-02T22:30:00Z',minutes=20)]);self.save([self.observation(mid=3,day='2026-01-02T23:30:00Z',minutes=80)]);self.assertEqual(self.query()['known_minutes'],20)
    def test_dst_spring_and_fall(self):
        self.save([self.observation(mid=1,day='2026-03-28T23:30:00Z')]);self.assertEqual(self.query('2026-03-29')['known_minutes'],90)
        self.save([self.observation(mid=2,day='2026-10-25T00:30:00Z',minutes=45)]);self.save([self.observation(mid=3,day='2026-10-25T01:30:00Z',minutes=30)]);self.assertEqual(self.query('2026-10-25')['known_minutes'],75)
    def test_complete_requires_explicit_window_coverage(self):
        self.save([self.observation()]);self.assertIsNone(self.query()['complete_minutes'])
        self.db.execute("INSERT INTO player_history_coverage(player_id,coverage_certified,certified_start_date,certified_end_date,coverage_status) VALUES(1,1,'2025-12-01','2026-02-01','CONTROLLED_FIXTURE')");self.db.commit();self.assertEqual(self.query()['complete_minutes'],90)
    def test_roster_add_remove_and_relabel_cannot_touch_facts(self):
        self.save([self.observation()]);roster=self.root/'roster.sqlite';r=sqlite3.connect(roster);r.executescript('CREATE TABLE clubs(club_id,club_key); INSERT INTO clubs VALUES(10,"test"); CREATE TABLE combined_membership(club_id,period_id,player_id); CREATE TABLE standardized_official_roster(club_id,season,half_season,snapshot_id,official_member_id,player_name_official,identity_status,fotmob_player_id);');r.commit()
        before=self.query();r.execute('INSERT INTO combined_membership VALUES(10,"2026-27_H1",1)');r.commit();selected=get_players('test','2026-27','H1',roster);self.assertEqual(selected['player_ids'],[1]);via=roster_workloads('test','2026-27','H1','2026-01-02',[7],roster,self.path)['workloads'][0];self.assertEqual(via,before)
        r.execute('UPDATE combined_membership SET period_id="2026-27_H2"');r.commit();self.assertEqual(get_players('test','2026-27','H2',roster)['player_ids'],[1]);self.assertEqual(self.query(),before);r.execute('DELETE FROM combined_membership');r.commit();self.assertEqual(self.query(),before);r.close()
