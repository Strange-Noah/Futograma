import unittest
from unittest.mock import patch
from futbol.team_cohorts import resolve_affiliation,eligible_days,period_for,squad_records,half_statistics,streaks_for_player,verify_unchanged
from futbol.common import ROOT,read_json,read_csv,stable_id

class TeamCohorts(unittest.TestCase):
    def setUp(self):
        self.c=read_json(ROOT/'config/team_comparison.json');self.c['as_of']='2026-10-22';self.pid=stable_id('players',1);self.club=stable_id('teams',self.c['team_id']);self.player=dict(player_id=self.pid,fotmob_player_id=1,player_name='Test')
    def relation(self,team,start='2024-07-01',end=None,priority=3,kind='senior_playing'):
        return dict(team_id=team,team_name=str(team),valid_from=start,valid_to=end,priority=priority,kind=kind,raw_source='source',source='source',evidence_confidence='reported')
    def test_half_boundaries_disjoint_cwc_kept_together(self):
        self.assertEqual(period_for('2025-07-13',self.c['periods'])['id'],'2024-25_H2');self.assertEqual(period_for('2025-07-14',self.c['periods'])['id'],'2025-26_H1');self.assertEqual(period_for('2025-01-01',self.c['periods'])['half'],'H2')
    def test_external_loan_overrides_contract_ownership(self):
        rows=[self.relation(self.c['team_id']),self.relation(99,'2024-08-21','2025-06-10',4,'loan')]
        self.assertEqual(resolve_affiliation(rows,'2025-02-16')[0]['team_id'],99)
        self.assertEqual(resolve_affiliation(rows,'2025-06-10')[0]['team_id'],self.c['team_id'])
    def test_active_external_loan_beats_official_parent_transfer(self):
        rows=[self.relation(self.c['team_id'],priority=10,kind='transfer'),self.relation(99,'2025-01-17','2025-07-01',4,'loan')]
        self.assertEqual(resolve_affiliation(rows,'2025-03-01')[0]['team_id'],99)
    def test_incoming_loan_same_as_psg_transfer(self):
        rows=[self.relation(99),self.relation(self.c['team_id'],'2025-01-17','2025-07-01',4,'loan')]
        selected,_=eligible_days([{'date':'2025-01-17','load_28d':420}],rows,self.c['team_id'],True);self.assertEqual(len(selected),1)
    def test_end_exclusive_and_day_before_join_excluded(self):
        rows=[self.relation(self.c['team_id'],'2025-01-17','2025-01-30')]
        self.assertIsNone(resolve_affiliation(rows,'2025-01-16')[0]);self.assertIsNotNone(resolve_affiliation(rows,'2025-01-17')[0]);self.assertIsNone(resolve_affiliation(rows,'2025-01-30')[0])
    def test_previous_club_minutes_in_rolling_value_retained(self):
        dd=[{'date':'2025-01-16','load_28d':999},{'date':'2025-01-17','load_28d':420}];rows=[self.relation(self.c['team_id'],'2025-01-17')]
        selected,_=eligible_days(dd,rows,self.c['team_id'],True);self.assertEqual([d['load_28d'] for d,r in selected],[420]);self.assertEqual(dd[0]['load_28d'],999)
    def test_no_first_squad_date_invented_for_unknown_affiliation(self):
        selected,missing=eligible_days([{'date':'2025-01-17','load_28d':90}],[],self.c['team_id'],True);self.assertEqual(selected,[]);self.assertEqual(missing,1)
    def test_academy_or_reserve_needs_this_half_squad(self):
        for kind in ['academy_parent','reserve_parent']:
            rows=[self.relation(self.c['team_id'],kind=kind,priority=1)];dd=[{'date':'2025-01-17'}]
            self.assertEqual(eligible_days(dd,rows,self.c['team_id'],False)[0],[]);self.assertEqual(len(eligible_days(dd,rows,self.c['team_id'],True)[0]),1)
    def test_conflicting_clubs_remain_unknown(self):
        self.assertEqual(resolve_affiliation([self.relation(1),self.relation(2)],'2025-01-17')[1],'conflicting_playing_clubs')
    def test_squad_includes_unused_sub_excludes_unavailable(self):
        m=dict(match_id='test',fotmob_match_id=123,date_madrid='2025-01-17',home_team_id=self.club,away_team_id='other',competition='Test',home_team_name='PSG',away_team_name='Opponent',source='source')
        players=[dict(id=i,name=str(i)) for i in range(1,12)]
        d={'content':{'lineup':{'homeTeam':{'id':self.c['team_id'],'starters':players,'subs':[dict(id=12,name='Bench')],'unavailable':[dict(id=13,name='Injured')]}}}}
        with patch('futbol.normalization.load_detail',return_value=d):ss,cov=squad_records([m],self.c)
        self.assertEqual({r['fotmob_player_id'] for r in ss},set(range(1,13)));self.assertTrue(cov[0]['source_squad_complete'])
    def test_missing_sub_array_not_claimed_complete(self):
        m=dict(match_id='test',fotmob_match_id=123,date_madrid='2025-01-17',home_team_id=self.club,away_team_id='other',competition='Test',home_team_name='PSG',away_team_name='Opponent',source='source')
        d={'content':{'lineup':{'homeTeam':{'id':self.c['team_id'],'starters':[dict(id=i,name=str(i)) for i in range(1,12)]}}}}
        with patch('futbol.normalization.load_detail',return_value=d):ss,cov=squad_records([m],self.c)
        self.assertFalse(cov[0]['source_squad_complete'])
    def test_psg_minutes_exclude_other_club_and_cross_half_run_distinguished(self):
        p=self.c['periods'][1];dd=[]
        for day in ['2025-01-01','2025-01-02']:
            r=dict(date=day)
            for prefix in ['load','club_load']:
                for w in [7,14,28]:r[f'{prefix}_{w}d']=420
            dd.append((r,self.relation(self.c['team_id'])))
        mm=[dict(match_id='x',date_madrid='2025-01-01')];apps=[dict(player_id=self.pid,team_id=self.club,started=1,minutes_played=90,minute_conflict=False,_match=mm[0]),dict(player_id=self.pid,team_id='other',started=1,minutes_played=120,minute_conflict=False,_match=mm[0])]
        runs=[dict(measure='starts',match_ids=['previous_half','x'],full_observed_count=2,left_censored=False)]
        result=half_statistics(self.player,p,dd,mm,apps,{},runs,self.c)[0]
        self.assertEqual(result['load_28d_max'],420);self.assertEqual(result['target_team_minutes'],90);self.assertEqual(result['longest_starts_within_half'],1);self.assertEqual(result['full_observed_starts_run_overlapping_half'],2)
    def test_actual_dataset_and_core_code_bytes_unchanged(self):
        rows=verify_unchanged(read_json(ROOT/'reports/refinement_baseline.json'));self.assertTrue(all(r['identical'] for r in rows))
    def test_each_actual_psg_match_has_one_half(self):
        rows=read_csv(ROOT/'outputs/team_comparison/squad_completeness.csv');self.assertEqual(len(rows),133);self.assertEqual(len({r['match_id'] for r in rows}),133)
    def test_two_old_peaks_are_not_psg_observations(self):
        rows=read_csv(ROOT/'outputs/team_comparison/audit_peak_windows.csv');old=[r for r in rows if r['audit_origin']=='OLD_PERSONAL_ALL'];self.assertEqual(len(old),2);self.assertTrue(all(r['peak_during_target_affiliation']=='False' for r in old))
if __name__=='__main__':unittest.main()
