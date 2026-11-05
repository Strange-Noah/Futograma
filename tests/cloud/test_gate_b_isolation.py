"""Gate B uses established synthetic fixtures and original workflow entrypoints."""
import builtins,contextlib,io,json,sqlite3,tempfile,types,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from fixtures import database
from squad_pipeline.core import ROOT
from squad_pipeline.live_sources import Client
from squad_pipeline import live_ingest
from squad_pipeline.identity_pipeline import Candidate,Record,resolve,persist_verified_alias,DDL
from squad_pipeline.identity_sources import fotmob
from squad_pipeline.international_store import migrate
from squad_pipeline.managed_roster import migrate as management_migrate
from squad_pipeline.cloud.all_match_export import export_all_matches
from squad_pipeline.cloud.quality import inspect


def no_audits(name,*args,**kwargs):
    if 'reconstruction' in name:raise ImportError('CONTROLLED_AUDIT_UNAVAILABLE')
    return ORIGINAL_IMPORT(name,*args,**kwargs)
ORIGINAL_IMPORT=builtins.__import__


class GateBIsolationTests(unittest.TestCase):
    def setUp(self):
        folder=ROOT/'squad_pipeline/.cloud/gate-b-tests';folder.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=folder);self.root=Path(self.tmp.name);self.path=self.root/'fixture.sqlite'
        database(self.path);db=live_ingest.connect(self.path);management_migrate(db);migrate(db);db.executescript(DDL);db.close()
    def tearDown(self):self.tmp.cleanup()
    def export(self,name,summary=None):
        pointer=dict(current=dict(database=inspect(self.path),version='CONTROLLED',code_commit='a'*40),dataset_ref='acceptance/controlled')
        export_all_matches(self.path,self.root/name,'b'*40,pointer,'2026-11-01',True,audit_summary=summary)
        return json.loads((self.root/name/'data/browser.json').read_text())
    def test_ordinary_export_has_no_audit_import_or_execution_dependency(self):
        with patch('builtins.__import__',side_effect=no_audits):data=self.export('unavailable')
        self.assertNotIn('completeness_audit',data)
        enabled=self.export('enabled',dict(scope='AUDIT_ONLY',states={'SOURCE_WORK_REQUIRED':1}))
        self.assertEqual(data['records'],enabled['records']);self.assertEqual(data['clubs'],enabled['clubs'])
        self.assertEqual(enabled['completeness_audit']['scope'],'AUDIT_ONLY')
    def test_r3_r4_r6_failure_preserves_facts_and_normal_export(self):
        from squad_pipeline.reconstruction.engine import run
        from test_reconstruction import Adapter
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        before=list(db.execute('SELECT * FROM player_match_minutes ORDER BY club_id,match_id,player_id'))
        for stage,target in [('R3','gaps'),('R4','opportunities'),('R6','active_resolution')]:
            with self.subTest(stage=stage),patch('squad_pipeline.reconstruction.engine.'+target,side_effect=RuntimeError('CONTROLLED_'+stage)):
                with self.assertRaisesRegex(RuntimeError,'CONTROLLED_'):run(db,Adapter(),'2026-10-01','2026-11-04')
            db.rollback();self.assertEqual(before,list(db.execute('SELECT * FROM player_match_minutes ORDER BY club_id,match_id,player_id')))
            with patch('builtins.__import__',side_effect=no_audits):self.export(stage)
        db.close()
    def detail(self,provider):
        if provider=='Torlytics':
            event=dict(**{'@type':'SportsEvent'},url='https://torlytics.de/spiel/controlled-2026-10-24',organizer=dict(name='Premier League'),startDate='2026-10-24T12:00:00Z',homeTeam=dict(name='Manchester City'),awayTeam=dict(name='Arsenal'))
            body='<script type="application/ld+json">'+json.dumps(event)+'</script><span class="match-score__state">Ende</span><span class="match-score__value">0-0</span>'
            for side,index,club in [('home',0,'Manchester City'),('away',1,'Arsenal')]:
                body+='<div class="lineup-col lineup-col--'+side+'"><h3 class="lineup-col__title">'+club+'<span></span></h3>'
                for role,nums in [('xi',range(1,12)),('bench',range(12,14))]:
                    body+='<ol class="lineup-list '+('lineup-section--xi' if role=='xi' else 'lineup-list--bench')+'">'
                    for n in nums:body+=f'<li class="lineup-entry"><span class="lineup-entry__number">{n}</span><a href="https://torlytics.de/spieler/controlled-{index}-{n}" class="lineup-entry__name">Controlled Test {index} Player {n}</a></li>'
                    body+='</ol>'
                body+='</div>'
            return body.encode()
        body='<div class="sdc-site-match-header" data-status="4"><span class="sdc-site-match-header__team-name-block-target">Manchester City</span><span class="sdc-site-match-header__team-name-block-target">Arsenal</span><span class="sdc-site-match-header__team-score-block">0</span><span class="sdc-site-match-header__team-score-block">0</span><span class="sdc-site-match-header__detail-time">1:00pm, Saturday 24th October 2026</span><span class="sdc-site-match-header__detail-fixture">Manchester City vs Arsenal. Premier League</span></div>'
        for index,club in [(0,'Manchester City'),(1,'Arsenal')]:
            body+='<div class="sdc-site-team-lineup__col"><span class="sdc-site-team-lineup__team-name">'+club+'</span>'
            for bench,nums in [(False,range(1,12)),(True,range(12,14))]:
                body+='<div class="sdc-site-team-lineup__players'+(' sdc-site-team-lineup__players--subs' if bench else '')+'">'
                for n in nums:body+=f'<span class="sdc-site-team-lineup__player-number">{n}</span><span class="sdc-site-team-lineup__player-name"><span class="sdc-site-team-lineup__player-initial" title="Controlled Test {index} Player"></span><span class="sdc-site-team-lineup__player-surname">{n}</span></span>'
                body+='</div>'
            body+='</div>'
        return body.encode()
    def test_original_acquisition_identity_roster_update_and_export_without_audits(self):
        clock=patch('squad_pipeline.live_sources.now',return_value='2026-11-01T12:00:00+00:00');clock.start();self.addCleanup(clock.stop)
        from squad_pipeline.roster_refresh import refresh
        from squad_pipeline.source_interface import sha
        bodies={};clients=[]
        for pid in range(1,27):
            index,n=divmod(pid-1,13);n+=1;name=f'Controlled Test {index} Player {n}'
            d=dict(id=pid,name=name,gender='male',isCoach=False,primaryTeam=dict(teamId=8456 if index==0 else 9825),birthDate=dict(utcTime=f'2000-01-{pid:02d}T00:00:00Z'))
            bodies[f'https://www.fotmob.com/players/{pid}']=('<script id="__NEXT_DATA__">'+json.dumps(dict(props=dict(pageProps=dict(data=d))))+'</script>').encode()
        official=dict(playersListData=dict(cityTeamInfo=dict(category='mens'),body=[dict(name='defender',players=[dict(id=n,name=f'Controlled Test 0 Player {n}',isMensTeam=True,dateOfBirth=f'2000-01-{n:02d}',urlName=f'/controlled-{n}') for n in range(1,14)])]))
        bodies['https://www.mancity.com/players']=('<script id="__NEXT_DATA__">'+json.dumps(dict(props=dict(pageProps=official)))+'</script>').encode()
        bodies['https://torlytics.de/verein/manchester-city']=b'<a href="https://torlytics.de/spiel/controlled-2026-10-24" class="upcoming-fixture"><span class="upcoming-fixture__score">0:0</span></a>'
        cat=dict(start=dict(date='Saturday 24th October'),matchURL='/football/controlled/44',id=44,isResult=True,status='FT',competition=dict(name=dict(full='Premier League')),teams={s:dict(id=i,name=dict(full=n,short=n)) for s,i,n in [('home',8456,'Manchester City'),('away',9825,'Arsenal')]})
        import html
        bodies['https://www.skysports.com/manchester-city-scores-fixtures']=('<div data-state="'+html.escape(json.dumps(cat),quote=True)+'"></div>').encode()
        bodies['https://www.skysports.com/manchester-city-scores-fixtures']+=b'<a href="/manchester-city-scores-fixtures/2026-10-01">Previous</a>'
        bodies['https://www.skysports.com/manchester-city-scores-fixtures/2026-10-01']=bodies['https://www.skysports.com/manchester-city-scores-fixtures']
        bodies['https://torlytics.de/spiel/controlled-2026-10-24']=self.detail('Torlytics');bodies['https://www.skysports.com/football/controlled/teams/44']=self.detail('Sky')
        def transport(url,headers):return (404,{},b'') if url.endswith('/robots.txt') else (200,{},bodies[url])
        def factory(run_id,budget=100):
            client=Client(run_id,budget=budget,storage_root=self.root/'captures',transport=transport,sleeper=lambda n:None,allowed_hosts={'torlytics.de','www.skysports.com','www.fotmob.com','www.mancity.com'});clients.append(client);return client
        client=factory('identity');db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        # Reuse accepted synthetic canonical IDs, then verify acquired stable profiles.
        for pid in range(1,27):
            r,raw=client.fetch(f'https://www.fotmob.com/players/{pid}','identity');record=fotmob(raw.decode(),r)[0]
            anchor=Record('registry://controlled',sha(str(pid).encode()),'controlled','CanonicalRegistry',record.names,provider_ids={'FotMob':str(pid)},retrieved=False)
            result=resolve(record,[Candidate(pid,record.names,[anchor])]);persist_verified_alias(db,record,result,'2026-11-01')
            index,n=divmod(pid-1,13);db.execute('INSERT INTO validation_player_aliases VALUES(?,?,?)',(f'https://torlytics.de/spieler/controlled-{index}-{n+1}',pid,json.dumps(dict(names=record.names))))
        db.commit();config=self.root/'ops/cloud';config.mkdir(parents=True);(config/'roster-sources.json').write_text(json.dumps({'8456':'https://www.mancity.com/players'}))
        def fetch(url,purpose):r,b=client.fetch(url,purpose);return r,b.decode()
        with patch('builtins.__import__',side_effect=no_audits),patch('squad_pipeline.roster_refresh.ROOT',self.root):roster=refresh(db,fetch,'2026-11-01','CONTROLLED')
        self.assertGreater(roster['verified_positive_updates'],0)
        # A controlled omitted fact, with its retained prior row, tests real rediscovery.
        prior=dict(db.execute("SELECT * FROM player_match_minutes WHERE club_id=8456 AND player_id=1").fetchone());db.execute('DELETE FROM player_match_minutes WHERE club_id=8456 AND player_id=1')
        for provider in ['Torlytics','Sky']:db.execute('INSERT INTO live_scan_windows VALUES(?,?,?)',(8456,provider,'2026-10-23T00:00:00+00:00'))
        db.commit();db.close()
        class Clock(datetime):
            tick=0
            @classmethod
            def now(cls,tz=None):
                cls.tick+=1
                return datetime(2026,11,1,12,microsecond=cls.tick,tzinfo=timezone.utc).astimezone(tz)
        args=types.SimpleNamespace(preflight=False,recheck=True,budget=30,max_details=8)
        env=dict(FUTBOL_DATABASE_PATH=str(self.path),FUTBOL_SETTINGS_PATH=str(ROOT/'ops/cloud/runtime.json'),FUTBOL_RUN_ORIGIN='controlled-isolated-test')
        capture=io.StringIO()
        with patch.dict('os.environ',env),patch.object(live_ingest,'Client',side_effect=factory),patch.object(live_ingest,'ROOT',self.root),patch.object(live_ingest,'OUT',self.root/'output'),patch.object(live_ingest,'CLUB_FEEDS',{8456:('manchester-city','manchester-city')}),patch.object(live_ingest,'CLUBS',{8456:('Manchester City','manchestercity')}),patch.object(live_ingest,'datetime',Clock),patch('builtins.__import__',side_effect=no_audits),contextlib.redirect_stdout(capture):
            self.assertEqual(live_ingest.run(args),0)
            first=inspect(self.path)['tables'];self.assertEqual(live_ingest.run(args),0);self.assertEqual(first,inspect(self.path)['tables'])
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row;recovered=dict(db.execute('SELECT * FROM player_match_minutes WHERE club_id=8456 AND player_id=1').fetchone())
        self.assertEqual((recovered['minutes'],recovered['participation_status']),(prior['minutes'],prior['participation_status']))
        self.assertEqual(db.execute('SELECT count(*) FROM matches').fetchone()[0],1);self.assertEqual(db.execute('SELECT count(*) FROM player_match_minutes').fetchone()[0],26)
        self.assertTrue(db.execute('SELECT count(*) FROM raw_artifacts').fetchone()[0]>1);db.close()
        with patch('builtins.__import__',side_effect=no_audits):data=self.export('ordinary-workflow')
        self.assertNotIn('completeness_audit',data);self.assertTrue(clients[0].records)
