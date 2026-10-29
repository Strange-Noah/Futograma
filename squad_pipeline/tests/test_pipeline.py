import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from squad_pipeline import core,manual,acquisition
from squad_pipeline.core import BASE,ROOT,connect,config,seed,digest
from squad_pipeline.fotmob import extract,period_for,competition_category
from squad_pipeline.identity import propose_mapping,parse_official_snapshot
from futbol.collection import fixtures

def match():
    def pl(pid):return dict(id=pid,name='Player '+str(pid),shirtNumber=str(pid))
    def st(pid,tid,val=90):return dict(id=pid,name='Player '+str(pid),teamId=tid,stats=[{'stats':{'Minutes':{'key':'minutes_played','stat':{'value':val}}}}])
    return dict(general=dict(matchId='100',homeTeam=dict(id=1),awayTeam=dict(id=2),finished=True),header=dict(teams=[dict(id=1),dict(id=2)]),content=dict(lineup=dict(homeTeam=dict(id=1,starters=[pl(i) for i in range(1,12)],subs=[pl(12)],unavailable=[pl(13)]),awayTeam=dict(id=2,starters=[pl(i) for i in range(21,32)],subs=[pl(32)])),playerStats={str(i):st(i,1) for i in range(1,12)}))

class ExtractTests(unittest.TestCase):
    def test_own_side_and_unused_bench(self):
        rows=extract(match(),1);self.assertEqual(len(rows),12);self.assertEqual(rows[-1]['minutes'],0);self.assertFalse(rows[-1]['played']);self.assertNotIn(13,[x['player_id'] for x in rows]);self.assertNotIn(21,[x['player_id'] for x in rows])
    def test_away_side(self):self.assertTrue(all(x['player_id']>=21 for x in extract(match(),2)))
    def test_duplicate_player(self):
        d=match();d['content']['lineup']['homeTeam']['subs'][0]['id']=1
        with self.assertRaisesRegex(ValueError,'Duplicate'):extract(d,1)
    def test_both_sides(self):
        d=match();d['content']['lineup']['awayTeam']['subs'][0]['id']=12
        with self.assertRaisesRegex(ValueError,'both sides'):extract(d,1)
    def test_side_mismatch(self):
        d=match();d['content']['lineup']['homeTeam']['id']=2
        with self.assertRaisesRegex(ValueError,'side'):extract(d,1)
    def test_fixture_mismatch(self):
        with self.assertRaisesRegex(ValueError,'Fixture'):extract(match(),1,dict(id=100,home=dict(id=2),away=dict(id=1)))
    def test_missing_bench_not_empty(self):
        d=match();d['content']['lineup']['homeTeam']['subs']=[]
        with self.assertRaisesRegex(ValueError,'Incomplete'):extract(d,1)
    def test_missing_minutes_stay_unknown(self):
        d=match();d['content']['playerStats']={};rows=extract(d,1)
        self.assertTrue(all(x['minutes'] is None for x in rows));self.assertIsNone(rows[-1]['played'])
    def test_stats_team_mismatch(self):
        d=match();d['content']['playerStats']['1']['teamId']=2
        with self.assertRaisesRegex(ValueError,'identity'):extract(d,1)
    def test_stats_name_mismatch(self):
        d=match();d['content']['playerStats']['1']['name']='Someone else'
        with self.assertRaisesRegex(ValueError,'name'):extract(d,1)
    def test_late_sub_zero_minutes_still_played(self):
        d=match();d['content']['lineup']['homeTeam']['subs'][0]['performance']={'substitutionEvents':[{'type':'subIn','time':90}]};r=extract(d,1)[-1]
        self.assertIsNone(r['minutes']);self.assertTrue(r['played'])
    def test_negative_minutes(self):
        d=match();d['content']['playerStats']['1']['stats'][0]['stats']['Minutes']['stat']['value']=-1
        with self.assertRaisesRegex(ValueError,'minutes'):extract(d,1)
    def test_midnight_madrid_boundary(self):
        p,date=period_for('2024-12-31T23:00:00Z',config());self.assertEqual(p['id'],'2024-25_H2');self.assertEqual(date,'2025-01-01')
    def test_club_world_cup_boundary(self):
        self.assertEqual(period_for('2025-07-13T21:00:00Z',config())[0]['id'],'2024-25_H2');self.assertEqual(period_for('2025-07-14T12:00:00Z',config())[0]['id'],'2025-26_H1')
    def test_all_competitions(self):
        self.assertEqual(competition_category('FIFA Club World Cup',78,config()['clubs']),'GlobalClubCup');self.assertEqual(competition_category('UEFA Super Cup',74,config()['clubs']),'SuperCup');self.assertEqual(competition_category('Club Friendlies',489,[]),'EXCLUDED')
    def test_catalog_conflict_is_not_merged(self):
        a=dict(id=100,home=dict(id=1),away=dict(id=2),status={});b=copy.deepcopy(a);b['home']['id']=3;errors=[]
        self.assertEqual(fixtures(dict(fixtures=[a],playoff=[b]),errors),[]);self.assertEqual(len(errors),1)

class ControllerTests(unittest.TestCase):
    def setUp(self):
        (BASE/'tests/runtime').mkdir(exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=BASE/'tests/runtime');self.path=Path(self.tmp.name);self.db=connect(self.path/'test.sqlite');seed(self.db,config())
        self.s=dict(source_id='test_source',club_id=8456,club_key='manchester_city',organization='Synthetic test only',url='https://example.test/roster',policy='ONE_OFF_LOCAL_CAPTURE',policy_note='Synthetic fixture',period_id='2026-27_H1',season='2026/27',stage='H1',roster_evidence_tokens=['Example Player'],preferred=True)
        acquisition.seed_sources(self.db,[self.s]);self.patcher=patch.object(acquisition,'BASE',self.path);self.patcher.start()
    def tearDown(self):self.patcher.stop();self.db.close();self.tmp.cleanup()
    def controller(self,responses):
        calls=[]
        def get(url):calls.append(url);return responses.pop(0)
        return acquisition.Controller(self.db,transport=get,sleeper=lambda n:None,clock=lambda:1000),calls
    def test_terms_restricted_never_calls(self):
        c,calls=self.controller([]);s=dict(self.s,policy='RESTRICTED');self.assertEqual(c.acquire(s)['status'],'TERMS_RESTRICTED');self.assertEqual(calls,[])
    def test_robots_wildcard_and_specific_allow(self):
        text='User-agent: *\nAllow: /\nDisallow: /api/*\nAllow: /api/public$\nCrawl-delay: 30'
        self.assertFalse(acquisition.robots_rules(text,'https://a.test/api/data')[0]);self.assertTrue(acquisition.robots_rules(text,'https://a.test/api/public')[0]);self.assertFalse(acquisition.robots_rules(text,'https://a.test/api/public/data')[0]);self.assertEqual(acquisition.robots_rules(text,'https://a.test/')[1],30)
    def test_unknown_policy_fails_closed(self):
        c,calls=self.controller([]);self.assertEqual(c.acquire(dict(self.s,policy='TYPO'))['status'],'POLICY_REVIEW_REQUIRED');self.assertEqual(calls,[])
    def test_robots_no_target_request(self):
        c,calls=self.controller([(200,{},b'User-agent: *\nDisallow: /')]);self.assertEqual(c.acquire(self.s)['status'],'ROBOTS_RESTRICTED');self.assertEqual(len(calls),1)
    def test_401_not_terms(self):
        c,calls=self.controller([(404,{},b''),(401,{},b'login')]);self.assertEqual(c.acquire(self.s)['status'],'AUTHENTICATION_REQUIRED')
    def test_403_not_terms(self):
        c,calls=self.controller([(404,{},b''),(403,{},b'forbidden')]);self.assertEqual(c.acquire(self.s)['status'],'ACCESS_DENIED');self.assertEqual(len(calls),2)
    def test_200_shell_not_success(self):
        c,_=self.controller([(404,{},b''),(200,{'Content-Type':'text/html'},b'<html>loading...</html>')]);self.assertEqual(c.acquire(self.s)['status'],'CONTENT_INCOMPLETE');self.assertEqual(self.db.execute('SELECT COUNT(*) FROM official_snapshots').fetchone()[0],0)
    def test_acquired_raw_not_parsed_and_idempotent(self):
        c,calls=self.controller([(404,{},b''),(200,{'Content-Type':'text/html'},b'<html>Example Player</html>')]);self.assertEqual(c.acquire(self.s)['status'],'ACQUIRED');self.assertEqual(c.acquire(self.s)['status'],'ACQUIRED');self.assertEqual(len(calls),2);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM official_membership').fetchone()[0],0)
    def test_blocked_source_does_not_stop_other_source(self):
        other=dict(self.s,source_id='test_second')
        ctl,_=self.controller([(404,{},b''),(200,{'Content-Type':'text/html'},b'<html>Example Player</html>')])
        with patch.object(acquisition,'Controller',lambda db:ctl):
            result=acquisition.run_official(self.db,[dict(self.s,policy='RESTRICTED'),other],online=True)
        self.assertEqual([r['status'] for r in result],['TERMS_RESTRICTED','ACQUIRED'])
    def test_transient_retry(self):
        c,calls=self.controller([(404,{},b''),(503,{},b'later'),(200,{'Content-Type':'text/html'},b'<html>Example Player</html>')]);self.assertEqual(c.acquire(self.s)['status'],'ACQUIRED');self.assertEqual(len(calls),3)
    def test_retry_after_deferred(self):
        c,calls=self.controller([(404,{},b''),(429,{'Retry-After':'120'},b'later')]);self.assertEqual(c.acquire(self.s)['status'],'RETRYABLE_ERROR');self.assertEqual(len(calls),2)
    def test_redirect_stops(self):
        c,calls=self.controller([(404,{},b''),(302,{'Location':'https://other.test/'},b'')]);self.assertEqual(c.acquire(self.s)['status'],'BROWSER_INTERACTION_REQUIRED');self.assertEqual(len(calls),2)
    def test_policy_unknown_not_restricted(self):
        c,calls=self.controller([]);self.assertEqual(c.acquire(dict(self.s,policy='REVIEW_REQUIRED'))['status'],'POLICY_REVIEW_REQUIRED');self.assertEqual(calls,[])
    def test_manual_roundtrip_and_dedupe(self):
        with patch.object(manual,'BASE',self.path):
            manual.prepare([self.s]);folder=self.path/'manual_inputs/manchester_city/2026-27/H1';(folder/'synthetic.html').write_text('<html>Example Player</html>')
            first=manual.register(self.db,folder,[self.s]);second=manual.register(self.db,folder,[self.s]);self.assertEqual(first[0]['snapshot_id'],second[0]['snapshot_id']);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM official_snapshots').fetchone()[0],1);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM official_membership').fetchone()[0],0)
    def test_manual_source_mismatch(self):
        with patch.object(manual,'BASE',self.path):
            manual.prepare([self.s]);folder=self.path/'manual_inputs/manchester_city/2026-27/H1';m=json.loads((folder/'source.json').read_text());m['source_url']='https://wrong.test';(folder/'source.json').write_text(json.dumps(m))
            with self.assertRaisesRegex(ValueError,'mismatch'):manual.register(self.db,folder,[self.s])
    def test_manual_does_not_bypass_restrictions(self):
        with patch.object(manual,'BASE',self.path):
            manual.prepare([self.s]);folder=self.path/'manual_inputs/manchester_city/2026-27/H1';(folder/'test.pdf').write_bytes(b'%PDF-test');self.assertEqual(manual.register(self.db,folder,[dict(self.s,policy='RESTRICTED')])[0]['status'],'TERMS_RESTRICTED')
    def test_authorised_feed_stages_new_match_without_official_membership(self):
        from squad_pipeline import feeds
        proof=self.path/'permission.txt';proof.write_text('Synthetic test permission fixture, not a real permission')
        spec=dict(self.s,kind='match',match_id=100,policy='OWNER_AUTHORIZED',permission_evidence='permission.txt')
        c,calls=self.controller([(404,{},b''),(200,{'Content-Type':'application/json'},json.dumps(match()).encode())])
        with patch.object(feeds,'BASE',self.path):
            result=feeds.acquire_feed(self.db,config(),spec,c)
        self.assertEqual(result['status'],'ACQUIRED');self.assertTrue((self.path/'raw/fotmob/matches/100.json').exists());self.assertEqual(self.db.execute('SELECT COUNT(*) FROM official_snapshots').fetchone()[0],0)
    def test_incremental_new_match_and_correction_removes_stale_membership(self):
        from squad_pipeline import fotmob
        cfg=config();cfg['clubs']=[cfg['clubs'][0]];cfg['as_of']='2026-10-23'
        d=match();d['general'].update(homeTeam={'id':8456},awayTeam={'id':2},leagueId=47,parentLeagueId=47,leagueName='Premier League',matchTimeUTCDate='2026-10-20T19:00:00Z');d['header']['teams'][0]['id']=8456;d['content']['lineup']['homeTeam']['id']=8456
        for x in d['content']['playerStats'].values():x['teamId']=8456
        fixture=dict(id=100,home=dict(id=8456),away=dict(id=2),status=dict(utcTime='2026-10-20T19:00:00Z',finished=True))
        catalog=dict(details=dict(id=47,name='Premier League',country='ENG',selectedSeason='2026/2027'),fixtures=[fixture]);cp=self.path/'catalog.json';cp.write_text(json.dumps(catalog));mp=self.path/'raw/fotmob/matches/100.json';mp.parent.mkdir(parents=True);mp.write_text(json.dumps(d))
        with patch.object(fotmob,'BASE',self.path),patch.object(fotmob.LocalFotMob,'catalogs',lambda _:iter([(cp,catalog)])),patch.object(fotmob.LocalFotMob,'details',lambda _:iter([(100,mp)])):
            fotmob.reconstruct(self.db,cfg);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM squad_observations').fetchone()[0],12)
            first=self.db.execute('SELECT artifact_id FROM squad_observations LIMIT 1').fetchone()[0]
            fotmob.reconstruct(self.db,cfg);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM squad_observations').fetchone()[0],12)
            d['content']['lineup']['homeTeam']['subs'][0].update(id=14,name='Player 14');mp.write_text(json.dumps(d));fotmob.reconstruct(self.db,cfg)
            self.assertEqual(self.db.execute('SELECT COUNT(*) FROM historical_membership WHERE player_id=12').fetchone()[0],0);self.assertEqual(self.db.execute('SELECT COUNT(*) FROM historical_membership WHERE player_id=14').fetchone()[0],1)
            a=self.db.execute('SELECT path,sha256 FROM raw_artifacts WHERE artifact_id=?',(first,)).fetchone();self.assertEqual(core.digest((ROOT/a['path']).read_bytes()),a['sha256'])
    def test_authorised_feed_requires_permission_evidence(self):
        from squad_pipeline import feeds
        c,calls=self.controller([])
        self.assertEqual(feeds.acquire_feed(self.db,config(),dict(self.s,kind='match',match_id=100,policy='OWNER_AUTHORIZED'),c)['status'],'AUTHORIZATION_REQUIRED');self.assertEqual(calls,[])
    def test_permission_does_not_enable_public_prohibited_api(self):
        from squad_pipeline import feeds
        (self.path/'permission.txt').write_text('Synthetic fixture')
        c,calls=self.controller([])
        with patch.object(feeds,'BASE',self.path):r=feeds.acquire_feed(self.db,config(),dict(self.s,kind='match',match_id=100,policy='OWNER_AUTHORIZED',permission_evidence='permission.txt',url='https://www.fotmob.com/api/data/matchDetails?matchId=100'),c)
        self.assertEqual(r['status'],'TERMS_RESTRICTED');self.assertEqual(calls,[])
    def test_path_escape(self):
        with self.assertRaises(ValueError):core.within(ROOT/'data/player_load.sqlite')
    def test_period_changes_require_migration(self):
        cfg=config();cfg['periods'][0]['end_exclusive']='2025-02-01'
        with self.assertRaisesRegex(ValueError,'migration'):seed(self.db,cfg)
    def test_no_speculative_parser(self):
        with self.assertRaises(NotImplementedError):parse_official_snapshot()
    def test_unknown_identity_evidence_rejected(self):
        with self.assertRaisesRegex(ValueError,'Evidence'):propose_mapping(self.db,source_id='test_source',official_player_id='abc',fotmob_player_id=1,full_name='Test',date_of_birth=None,club_id=8456,season='2026/27',confidence='UNREVIEWED',evidence_artifact_id='missing')

if __name__=='__main__':unittest.main()
