import unittest,tempfile,json
from datetime import timedelta
from pathlib import Path
from futbol.common import ROOT,UTC,instant,day_start,day_end,stable_id,DataError
from futbol.analytics import LoadIndex,daily_table,load_columns,percentile,ucl_rotation
from futbol.aggregation import longest_run,summary_table
from futbol.availability import resolve
from futbol.collection import FotMob,fixtures
from futbol.normalization import category,player_key,roster_registrations
from futbol.pipeline import configuration

class Contracts(unittest.TestCase):
    def setUp(self):
        self.c=configuration(as_of='2026-10-22');self.pid=player_key(1);self.team=stable_id('teams',self.c['club']['fotmob_team_id'])
    def index(self,events,complete=True):
        mm=[];aa=[]
        for i,(date,minutes,kind) in enumerate(events):
            mm.append(dict(match_id=str(i),datetime=date,competition_type=kind,is_national=kind=='International'))
            aa.append(dict(player_id=self.pid,match_id=str(i),minutes_played=minutes))
        return LoadIndex(aa,mm,{self.pid:dict(source_history_reached_start=complete,errors=[],requested_start='2020-01-01T00:00:00Z')},self.c)
    def test_left_inclusive_current_excluded(self):
        idx=self.index([('2026-10-01T12:00:00Z',80,'Domestic'),('2026-10-08T12:00:00Z',90,'UCL')])
        b=idx.window(self.pid,instant('2026-10-01T12:00:00Z'),instant('2026-10-08T12:00:00Z'));self.assertEqual(b['values']['total'],80)
    def test_total_club_nt_and_other_club(self):
        idx=self.index([('2026-10-01T12:00:00Z',30,'Domestic'),('2026-10-02T12:00:00Z',60,'International'),('2026-10-03T12:00:00Z',90,'Other')]);b=idx.window(self.pid,instant('2026-10-01'),instant('2026-10-04'));self.assertEqual(b['values']['total'],180);self.assertEqual(b['values']['club'],120)
    def test_missing_minutes_not_zero(self):
        b=self.index([('2026-10-01',None,'Domestic')]).window(self.pid,instant('2026-10-01'),instant('2026-10-02'));self.assertIsNone(b['values']['total']);self.assertFalse(b['complete'])
    def test_incomplete_history_not_zero(self):
        b=self.index([],False).window(self.pid,instant('2026-10-01'),instant('2026-10-02'));self.assertIsNone(b['values']['total'])
    def test_left_censoring(self):
        i=self.index([]);i.histories[self.pid]['requested_start']='2026-10-01';self.assertFalse(i.window(self.pid,instant('2026-09-20'),instant('2026-10-02'))['complete'])
    def test_zero_nt_share_is_null(self):
        r=load_columns(self.index([]),self.pid,instant('2026-10-22'),self.c,True);self.assertIsNone(r['nt_share_7d']);self.assertEqual(r['pre_match_7d'],0)
    def test_recovery_uses_last_appearance_not_last_fixture(self):
        idx=self.index([('2026-10-01',90,'Domestic'),('2026-10-03',20,'International')]);self.assertEqual(idx.recovery(self.pid,instant('2026-10-04')),1);self.assertEqual(idx.recovery(self.pid,instant('2026-10-04'),True),3)
    def test_calendar_includes_quiet_days_and_current_day(self):
        c=json.loads(json.dumps(self.c));c['research'].update(start='2026-10-01',as_of='2026-10-03');r=daily_table({'players':[{'player_id':self.pid}]},self.index([('2026-10-01T21:30:00Z',90,'Domestic')]),c);self.assertEqual(len(r),3);self.assertEqual([d['load_7d'] for d in r],[90,90,90])
    def test_madrid_dst_calendar_not_fixed_24_hours(self):
        self.assertEqual((day_end('2026-03-29','Europe/Madrid')-day_start('2026-03-29','Europe/Madrid')).total_seconds(),23*3600)
        self.assertEqual((day_end('2026-10-25','Europe/Madrid')-day_start('2026-10-25','Europe/Madrid')).total_seconds(),25*3600)
    def test_stable_id_across_transfers(self):self.assertEqual(player_key(900433),player_key('900433'));self.assertNotEqual(player_key(900433),player_key(900434))
    def test_unknown_absence_not_injured(self):self.assertEqual(resolve(False,None,None,None,True)[:2],('unknown','not_in_squad'))
    def test_named_bench_available(self):self.assertEqual(resolve(False,'subs',None,None,True)[:2],('available','unused_substitute'))
    def test_injury_and_suspension_explicit(self):
        self.assertEqual(resolve(False,'unavailable',None,{'lineup':{'unavailability':{'type':'injury'}}},True)[0],'injured')
        self.assertEqual(resolve(False,'unavailable',None,{'lineup':{'unavailability':{'type':'suspended'}}},True)[0],'suspended')
    def test_international_club_not_domestic(self):
        self.assertEqual(category(78,1,{1:'club'},'INT-1',self.c)[0],'Other');self.assertEqual(category(42,1,{1:'club'},'INT-2',self.c)[0],'UCL')
    def test_run_missed_fixture_breaks(self):self.assertEqual(longest_run([1,1,0,1,1,1],bool),3)
    def test_percentile_linear(self):self.assertEqual(percentile([0,90],.5),45);self.assertEqual(percentile([0,90],.9),81)
    def test_fixture_identity_quarantined_not_whole_tournament(self):
        f=lambda mid,away:dict(id=mid,home={'id':1},away={'id':away},status={})
        d={'fixtures':[f(1,2),f(2,3)],'playoff':[f(1,4)]};bad=[];self.assertEqual([r['id'] for r in fixtures(d,bad)],[2]);self.assertEqual(len(bad),1)
        with self.assertRaises(DataError):fixtures(d)
    def test_cursor_loop_rejected(self):
        previous='https://pub.fotmob.com/prod/db/api/player/1/matches?before=x'
        class Fake(FotMob):
            def get(self,*a,**kw):return {'matches':[{'id':1,'teamId':1,'matchDate':{'utcTime':'2026-10-01'}}],'previous':previous}
        profile={'initialMatchesPage':Fake(self.c).get()}
        with self.assertRaisesRegex(DataError,'Repeated'):Fake(self.c).history(1,profile,instant('2024-01-01'),instant('2027-01-01'))
    def test_absent_history_is_unknown_not_zero(self):
        from unittest.mock import patch
        with patch('futbol.collection.write_json'):
            h=FotMob(self.c).history(1,{},instant('2024-01-01'),instant('2027-01-01'))
        self.assertFalse(h['source_history_reached_start']);self.assertTrue(h['errors'])
        b=LoadIndex([],[],{self.pid:h},self.c).window(self.pid,instant('2026-10-01'),instant('2026-10-02'))
        self.assertIsNone(b['values']['total'])
    def test_incomplete_http_read_retried(self):
        from unittest.mock import patch,MagicMock
        import http.client
        reply=MagicMock();reply.__enter__.return_value.read.return_value=b'{"ok":true}'
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as temp:
            with patch('futbol.collection.urllib.request.urlopen',side_effect=[http.client.IncompleteRead(b'abc'),reply]) as request,patch('futbol.collection.time.sleep'):
                result=FotMob(self.c).get('example',{},Path(temp)/'response.json')
            self.assertTrue(result['ok']);self.assertEqual(request.call_count,2)
    def test_epoch_ms_date(self):self.assertEqual(instant(1783296000000),instant('2026-07-06'))
    def test_no_negative_team_identity(self):
        profile={'id':1,'careerHistory':{'careerItems':{'senior':{'teamEntries':[{'teamId':-1,'team':'Unknown','startDate':'2024-01-01'}]}}}}
        rows,names,types=roster_registrations({1:profile},self.c);self.assertEqual(rows,[]);self.assertNotIn(-1,names)
    def test_usage_extra_time_and_strict_threshold_and_nt_no_break(self):
        mm=[];aa=[]
        for mid,typ,started,minutes in [('1','Domestic',True,120),('2','International',True,90),('3','Domestic',True,90),('4','Domestic',False,0),('5','Domestic',True,90)]:
            mm.append(dict(match_id=mid,datetime=f'2026-10-0{mid}T12:00:00Z',date_madrid=f'2026-10-0{mid}',is_national=typ=='International',competition_type=typ))
            aa.append(dict(player_id=self.pid,team_id=self.team if typ!='International' else stable_id('teams',2),match_id=mid,started=started,minutes_played=minutes,status='available',squad_status='appeared' if minutes else 'unused_substitute',sequence_fixture_verified=True,match_detail_available=True,healthy_rotation_confirmed=False))
        daily=[]
        for day,value in [('2026-10-01',180),('2026-10-02',181)]:
            r=dict(player_id=self.pid,date=day)
            for w in self.c['windows_days']:
                for prefix in ['load','club_load']:r[f'{prefix}_{w}d']=value
            daily.append(r)
        data=dict(players=[dict(player_id=self.pid,player_name='Test')],matches=mm,player_availability=aa)
        # First two days: the national fixture does not break club continuity.
        c=json.loads(json.dumps(self.c));c['seasons']=[]
        result=summary_table(data,daily,c)[0];self.assertEqual(result['load_7d_fraction_above_threshold'],.5);self.assertEqual(result['Domestic_available_usage'],120/90)
        daily[1]['date']='2026-10-05';result=summary_table(data,daily,c)[0];self.assertEqual(result['longest_observed_club_starts'],2)
    def test_previous_domestic_B_negative_preserved(self):
        d=dict(matches=[dict(match_id='d',datetime='2026-10-01',competition_type='Domestic',home_team_id=self.team,away_team_id='x'),dict(match_id='u',datetime='2026-10-05',competition_type='UCL',ucl_stage='league_phase',home_team_id=self.team,away_team_id='x')],player_availability=[dict(player_id=self.pid,team_id=self.team,match_id='d',minutes_played=120,status='available',started=True,squad_status='appeared',healthy_rotation_confirmed=False),dict(player_id=self.pid,team_id=self.team,match_id='u',minutes_played=90,started=True,status='available',squad_status='appeared',registration_confirmed=False,membership_basis='career_affiliation')])
        pm=[dict(player_id=self.pid,team_id=self.team,match_id='u',pre_match_7d=120)];r=ucl_rotation(d,pm,self.c)[0];self.assertEqual(r['B_90_minus_previous_minutes'],-30);self.assertEqual(r['gap_days'],4)
if __name__=='__main__':unittest.main()
