import sqlite3
import unittest
from dataclasses import replace
from squad_pipeline.reconstruction.engine import (
    Evidence, Membership, Fixture, SquadStatus, Window, Resolution,
    run, windows, validate_inputs, digest,
)

E = Evidence('Independent', 'https://source.example/report', 'a'*64,
             'match/squad', '2026-11-04T00:00:00+00:00', 'original-publisher')


def member(player='p', team='club', domain='CLUB', **kw):
    return Membership(player, team, domain, 'SENIOR', '2026-10-01',
                      '2026-11-05', 'FIRST_TEAM', (E,), **kw)


def fixture(on='2026-10-07', canonical='match', domain='CLUB', team='club'):
    return Fixture(canonical, canonical, canonical, team, 'opponent', domain,
                   'SENIOR', 'competition', on, (E,))


def status(player='peer', canonical='match', team='club', state='STARTER', minutes=90):
    return SquadStatus(player, canonical, team, state, minutes, (E,))


class Adapter:
    version = '1'
    def __init__(self, members=None, fixtures=None, statuses=None, calendars=(), answers=None):
        self.members = members if members is not None else [member(), member('peer')]
        self.fixtures = fixtures if fixtures is not None else [fixture()]
        self.statuses = statuses if statuses is not None else [status()]
        self.calendars = calendars
        self.answers = answers or {}
        self.calls = []

    def discover(self, start, cutoff, mode):
        return self.members, self.fixtures, self.statuses, self.calendars

    def resolve(self, path, finding):
        self.calls.append(path)
        if finding.get('canonical') is None and self.answers.get(path) and self.answers[path].status:
            return Resolution(path, 'NO_MATCH', 'Single fixture status cannot resolve a whole gap', (E,))
        return self.answers.get(path, Resolution(path, 'UNAVAILABLE', 'No retained source for exact claim'))


class ReconstructionTests(unittest.TestCase):
    def test_native_exclusion_checks_target_without_requiring_every_peer_mapping(self):
        from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE validation_player_aliases(player_id INTEGER,provider_key TEXT,identity_json TEXT)')
        import json
        db.execute('INSERT INTO validation_player_aliases VALUES(?,?,?)',(1,'fotmob:11',json.dumps(dict(names=['Target Fullname']))))
        adapter=RetainedAdapter.__new__(RetainedAdapter);adapter.db=db;adapter.club_id=9
        sheet=dict(native_complete_squad=True,provider='FotMob',native_players=[dict(provider_player_id='22',name='Other Fullname')])
        self.assertTrue(adapter.exact_native_exclusion(sheet,'1'))
        sheet['native_players'].append(dict(provider_player_id='11',name='Different Display'))
        self.assertFalse(adapter.exact_native_exclusion(sheet,'1'))
        sheet['native_players']=[dict(provider_player_id='22',name='Target Fullname')]
        self.assertFalse(adapter.exact_native_exclusion(sheet,'1'))
        sheet['native_players']=[];sheet['native_complete_squad']=False
        self.assertFalse(adapter.exact_native_exclusion(sheet,'1'));db.close()

    def test_accepted_nonselection_is_known_without_reinterpreting_as_bench(self):
        receipt=replace(E,url='baseline:'+'a'*64,authority='ACCEPTED_BASELINE',time_precision='RETAINED_RECORD')
        accepted=replace(status('p',state='NOT_IN_MATCHDAY_SQUAD',minutes=0),authority='ACCEPTED_BASELINE',evidence=(receipt,))
        a=Adapter(statuses=[status(),accepted]);r=self.execute(a)
        finding=next(f for f in r['opportunities'] if f['player']=='p')
        self.assertEqual(finding['state'],'NOT_IN_MATCHDAY_SQUAD')
        self.assertEqual(finding['confidence_status'],'CONFIRMED');self.assertFalse(a.calls)
        self.assertFalse(replace(accepted,minutes=1).reliable())
        self.assertFalse(status('p',state='BENCH_PARTICIPATION_UNKNOWN',minutes=None).reliable())

    def test_republished_same_body_does_not_become_independent_human_escalation(self):
        other=replace(E,origin='syndicated-outlet',provider='Other')
        a=Adapter(answers=dict(player_history=Resolution('player_history','FOUND','Exact controlled claim',(E,),status('p')),
            fixture_details=Resolution('fixture_details','FOUND','Contradictory controlled claim',(other,),replace(status('p',state='UNUSED_SUBSTITUTE',minutes=0),evidence=(other,)))))
        r=self.execute(a);finding=r['resolutions'][0]
        self.assertEqual(finding['state'],'CONFLICT');self.assertEqual(finding['independent_source_origins'],1)
        self.assertFalse(finding['human_pending']);self.assertFalse(finding['canonical_correction_authorized'])

    def test_existing_minute_policy_closes_small_variation_without_challenging_acceptance(self):
        receipt=replace(E,url='baseline:'+'a'*64,authority='ACCEPTED_BASELINE',time_precision='RETAINED_RECORD')
        accepted=replace(status('p',minutes=45),authority='ACCEPTED_BASELINE',evidence=(receipt,))
        r=self.execute(Adapter(statuses=[status(),accepted,status('p',minutes=46)]))
        finding=next(f for f in r['opportunities'] if f['player']=='p')
        self.assertEqual(finding['state'],'STARTER');self.assertIsNone(finding['challenge'])
        self.assertTrue(finding['compatible_source_variation']);self.assertEqual(finding['confidence_status'],'CONFIRMED')
        self.assertEqual(r['metrics']['r4_triggers'],0);self.assertEqual(r['metrics']['human_pending'],0)

    def test_material_minute_difference_preserves_accepted_fact_and_records_challenge(self):
        receipt=replace(E,url='baseline:'+'a'*64,authority='ACCEPTED_BASELINE',time_precision='RETAINED_RECORD')
        accepted=replace(status('p',minutes=45),authority='ACCEPTED_BASELINE',evidence=(receipt,))
        r=self.execute(Adapter(statuses=[status(),accepted,status('p',minutes=60)]))
        finding=next(f for f in r['opportunities'] if f['player']=='p')
        self.assertEqual(finding['accepted_state_retained'],'STARTER');self.assertTrue(finding['challenge'])
        self.assertEqual(finding['confidence_status'],'CONFLICTED')
        self.assertTrue(all(not x['canonical_correction_authorized'] for x in r['resolutions']))

    def test_accepted_status_is_retained_when_new_observation_challenges_it(self):
        receipt=replace(E,url='baseline:'+'a'*64,authority='ACCEPTED_BASELINE',time_precision='RETAINED_RECORD')
        accepted=replace(status('p'),authority='ACCEPTED_BASELINE',evidence=(receipt,))
        new=status('p',state='UNUSED_SUBSTITUTE',minutes=0)
        r=self.execute(Adapter(statuses=[status(),accepted,new]))
        finding=r['opportunities'][0]
        self.assertEqual(finding['state'],'STARTER');self.assertEqual(finding['accepted_state_retained'],'STARTER')
        self.assertEqual(finding['confidence_status'],'CONFLICTED')
        self.assertEqual(r['gaps'],[])
        self.assertFalse(r['resolutions'][0]['canonical_correction_authorized'])
        self.assertFalse(r['resolutions'][0]['human_pending'])

    def test_retained_receipt_does_not_masquerade_as_source_http(self):
        receipt=replace(E,url='baseline:'+'a'*64,authority='ACCEPTED_BASELINE',observed_at='NOT_A_NEW_SOURCE_OBSERVATION',time_precision='RETAINED_RECORD')
        self.assertTrue(receipt.valid());self.assertFalse(replace(receipt,authority='SOURCE_RESPONSE').valid())

    def test_retained_detail_is_not_relabelled_as_an_official_search(self):
        from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
        adapter=object.__new__(RetainedAdapter);adapter.parsed={}
        r=adapter.resolve('official_squad',dict(canonical='match',player='p',team='club'))
        self.assertEqual(r.outcome,'NOT_REQUESTED')

    def test_complete_exclusion_conflicting_with_listed_unknown_stays_conflicted(self):
        from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
        adapter=object.__new__(RetainedAdapter)
        adapter.parsed={'match':[(dict(players=[],complete_squad=True,issues=[]),E),
            (dict(players=[dict(player_id='p',status='BENCH_PARTICIPATION_UNKNOWN',reported_minutes=None)],complete_squad=True,issues=[]),replace(E,provider='Other'))]}
        r=adapter.resolve('fixture_details',dict(canonical='match',player='p',team='club'))
        self.assertEqual(r.outcome,'CONFLICT');self.assertIsNone(r.status)

    def test_retained_adapter_consumes_typed_source_and_never_zeros_unknown_bench(self):
        import json,tempfile
        from pathlib import Path
        from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
        from squad_pipeline.source_interface import EvidenceStore,SourceService,sha
        from test_international_fotmob import data
        folder=Path.cwd()/'.cloud';folder.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder) as temporary:
            root=Path(temporary);body=json.dumps(data()).encode();(root/'arbitrary-name.response').write_bytes(body)
            source=dict(path='arbitrary-name.response',sha256=sha(body),url='https://source.example/detail?matchId=10',acquired_at='2026-10-03T00:00:00Z')
            self.db.row_factory=sqlite3.Row
            self.db.executescript('''CREATE TABLE club_management_events(player_id INTEGER,club_id INTEGER,status TEXT,effective_from TEXT,end_exclusive TEXT,observed_on TEXT,verification_status TEXT,date_precision TEXT,source_json TEXT,reason TEXT);
            CREATE TABLE matches(match_id TEXT,home_id INTEGER,away_id INTEGER,kickoff TEXT,local_date TEXT,source_competition_id INTEGER,competition_id INTEGER,metadata_json TEXT);
            CREATE TABLE validation_match_sheets(match_id TEXT,club_id INTEGER,provider TEXT,payload_json TEXT);
            CREATE TABLE canonical_fixture_sources(domain TEXT,provider TEXT,provider_match_id TEXT,match_id TEXT);
            CREATE TABLE validation_player_aliases(player_id INTEGER,provider_key TEXT);''')
            self.db.execute('INSERT INTO matches VALUES(?,?,?,?,?,?,?,?)',('canonical',1,2,'2026-10-02T18:45:00Z','2026-10-02',8,8,'{}'))
            self.db.execute('INSERT INTO validation_match_sheets VALUES(?,?,?,?)',('canonical',1,'FotMob',json.dumps(dict(source=source))))
            for pid in [100,101]:
                self.db.execute('INSERT INTO club_management_events VALUES(?,?,?,?,?,?,?,?,?,?)',(pid,1,'FIRST_TEAM','2026-10-01','2026-10-03','2026-10-01','VERIFIED','RETAINED_ACCEPTED_INTERVAL',json.dumps([dict(reference='older-accepted-contract')]),'Retained acceptance'))
            for pid in range(100,112):self.db.execute('INSERT INTO validation_player_aliases VALUES(?,?)',(pid,'fotmob:'+str(pid)))
            service=SourceService(EvidenceStore(root),'offline',set(),max_calls=0)
            adapter=RetainedAdapter(self.db,root,1,source_service=service)
            members,fixtures,statuses,_=adapter.discover('2026-10-01','2026-10-04','INCREMENTAL')
            self.assertEqual(len(fixtures),1);self.assertEqual(fixtures[0].canonical,'canonical')
            self.assertEqual({m.player for m in members},{'100','101'})
            self.assertTrue(all(m.end<='2026-10-03' for m in members))
            self.assertTrue(all(m.evidence[0].authority=='ACCEPTED_BASELINE' for m in members))
            self.assertFalse(any(s.player=='111' for s in statuses));self.assertEqual(service.client.calls,0)
            self.db.executescript('''CREATE TABLE verified_identity_links(provider TEXT,provider_player_id TEXT,player_id INTEGER);
            CREATE TABLE persistent_source_evidence(evidence_id TEXT,provider TEXT,source_url TEXT,body_sha256 TEXT,fetched_at TEXT);''')
            self.db.execute('INSERT INTO verified_identity_links VALUES(?,?,?)',('FotMob','111',111))
            claim=dict(id=10,teamId=1,teamName='A U21',opponentTeamId=2,opponentTeamName='B U21',isHomeTeam=True,matchDate=dict(utcTime='2026-10-02T18:45:00Z'),matchPageUrl='/match?matchId=10',leagueId=8,leagueName='Synthetic',minutesPlayed=0,playedInMatch=False,onBench=True)
            profile=dict(id=111,gender='male',careerHistory=dict(careerItems=dict(senior=dict(teamEntries=[dict(participantId=111,teamId=1,team='A')]))),recentMatches=[claim])
            raw=('<script id="__NEXT_DATA__">'+json.dumps(dict(props=dict(pageProps=dict(data=profile)) ))+'</script>').encode()
            path=root/'profile.response';path.write_bytes(raw);service.store.index[sha(raw)]=[str(path)]
            self.db.execute('INSERT INTO persistent_source_evidence VALUES(?,?,?,?,?)',('profile','FotMob','https://source.example/players/111',sha(raw),'2026-10-03T00:00:00Z'))
            finding=dict(player='111',canonical='canonical',team='1',interval=['2026-10-02','2026-10-03'])
            resolved=adapter.player_history(finding)
            self.assertEqual(resolved.outcome,'FOUND');self.assertEqual(resolved.status.status,'UNUSED_SUBSTITUTE')
            self.assertEqual(resolved.status.minutes,0);self.assertEqual(service.client.calls,0)

    def test_two_provider_observations_merge_without_losing_native_bindings(self):
        from squad_pipeline.reconstruction.engine import coalesce_fixtures
        a=fixture();b=replace(a,provider_id='other-id',returned_id='other-id',evidence=(replace(E,provider='Other',url='https://other.example/report'),))
        merged=coalesce_fixtures([a,b]);self.assertEqual(len(merged),1)
        self.assertEqual(len(merged[0].provider_bindings),2);self.assertEqual(len(merged[0].evidence),2)
        with self.assertRaisesRegex(ValueError,'CONFLICTING_CANONICAL'):
            coalesce_fixtures([a,replace(b,opponent='different')])

    def test_secondary_provider_binding_must_be_requested_returned_equal(self):
        from squad_pipeline.reconstruction.engine import coalesce_fixtures
        with self.assertRaisesRegex(ValueError,'STRICT_FIXTURE'):
            coalesce_fixtures([replace(fixture(),provider_bindings=(('Other','request','different'),))])

    def test_day_precision_evidence_does_not_invent_retrieval_time(self):
        day_only=replace(E,observed_at='2026-11-04',time_precision='DAY')
        self.assertTrue(day_only.valid());self.assertFalse(replace(day_only,time_precision='TIMESTAMP').valid())

    def test_manual_policy_authority_is_not_an_http_capture(self):
        policy=replace(E,url='policy:'+'a'*64,authority='MANUAL_POLICY',time_precision='DAY',observed_at='2026-11-04')
        self.assertTrue(policy.valid());self.assertFalse(replace(policy,authority='SOURCE_RESPONSE').valid())

    def test_unperformed_request_cannot_be_pending_after_fictitious_search(self):
        class Unmapped(Adapter):
            def resolve(self,path,finding):
                return Resolution(path,'NOT_REQUESTED','No actual source request mapped')
        r=self.execute(Unmapped())
        self.assertTrue(r['resolutions']);self.assertTrue(all(x['state']=='SOURCE_WORK_REQUIRED' for x in r['resolutions']))
        self.assertEqual(r['metrics']['pending'],0)
        self.assertEqual(r['metrics']['attempts'],0)
        self.assertIn('player_history',r['resolutions'][0]['remaining_paths'])

    def setUp(self):
        self.db = sqlite3.connect(':memory:')

    def tearDown(self):
        self.db.close()

    def execute(self, adapter=None, **kw):
        return run(self.db, adapter or Adapter(), '2026-10-01', '2026-11-04', **kw)

    def test_mandatory_stage_order(self):
        self.assertEqual(self.execute()['stages'], ['R1','R2','R5','R3_R4','R6'])

    def test_five_day_gap_requires_actual_participating_peer(self):
        result = self.execute(Adapter(statuses=[]))
        self.assertEqual(result['gaps'], [])
        self.assertEqual(result['opportunities'], [])
        result = self.execute()
        self.assertEqual(result['metrics']['r3_triggers'], 1)
        self.assertEqual(result['metrics']['r4_triggers'], 1)

    def test_r4_applies_under_five_days(self):
        a = Adapter(fixtures=[fixture('2026-10-02')])
        result = run(self.db, a, '2026-10-01', '2026-10-03')
        self.assertEqual(result['metrics']['r3_triggers'], 0)
        self.assertEqual(result['metrics']['r4_triggers'], 1)

    def test_unused_substitute_zero_is_known(self):
        result = self.execute(Adapter(statuses=[status(), status('p', state='UNUSED_SUBSTITUTE', minutes=0)]))
        self.assertEqual(result['opportunities'][0]['state'], 'UNUSED_SUBSTITUTE')
        self.assertEqual(result['metrics']['r4_triggers'], 0)

    def test_unknown_minutes_never_becomes_zero(self):
        self.assertFalse(status('p', state='UNUSED_SUBSTITUTE', minutes=None).reliable())
        self.assertFalse(status('p', minutes=True).reliable())
        self.assertFalse(status('p', minutes=-1).reliable())

    def test_absence_requires_complete_matchday_squad(self):
        s = status('p', state='ABSENT_COMPLETE_SQUAD', minutes=None)
        self.assertFalse(s.reliable())
        result = self.execute(Adapter(statuses=[status(), replace(s, complete_squad=True)]))
        self.assertEqual(result['metrics']['supported_absences'], 1)
        self.assertEqual(result['metrics']['r4_triggers'], 0)

    def test_loan_in_injury_included_departure_youth_excluded(self):
        for value, expected in [('LOAN_IN',1),('FIRST_TEAM_INJURED',1),('LOAN_OUT',0),('TRANSFERRED_OUT',0),('YOUTH',0),('AUTHORIZED_EXCEPTION',1)]:
            with self.subTest(value=value):
                a = Adapter(members=[replace(member(), status=value),member('peer')])
                self.assertEqual(self.execute(a)['metrics']['opportunities'], expected)

    def test_membership_boundaries_no_invented_history(self):
        a = Adapter(members=[replace(member(), start='2026-10-08'),member('peer')])
        self.assertEqual(self.execute(a)['opportunities'], [])
        a.members[0] = replace(member(), end='2026-10-07')
        self.assertEqual(self.execute(a)['opportunities'], [])

    def test_national_requires_same_team_level_and_period(self):
        f = fixture(domain='NATIONAL', team='national')
        a = Adapter(members=[member('p','national','NATIONAL'),member('peer','other','NATIONAL')],
                    fixtures=[f], statuses=[status(team='national')])
        self.assertEqual(self.execute(a)['opportunities'], [])
        a.members[1] = replace(member('peer','national','NATIONAL'),level='U21')
        self.assertEqual(self.execute(a)['opportunities'], [])
        a.members[1] = member('peer','national','NATIONAL')
        self.assertEqual(self.execute(a)['metrics']['opportunities'], 1)

    def test_official_window_is_distinct_from_fixture_span(self):
        official = Window('OFFICIAL','2026-10-01','2026-10-10','calendar',None,'SENIOR','ANNOUNCED',(E,))
        result = windows([fixture(domain='NATIONAL')], [official])
        self.assertEqual([w.kind for w in result], ['OFFICIAL','OBSERVED'])
        self.assertEqual(result[0].start,'2026-10-01')
        self.assertEqual(result[1].start,'2026-10-07')

    def test_club_fixture_in_official_window_still_audited(self):
        w = Window('OFFICIAL','2026-10-01','2026-10-10','calendar',None,'SENIOR','ANNOUNCED',(E,))
        self.assertEqual(self.execute(Adapter(calendars=[w]))['metrics']['opportunities'],1)

    def test_source_identity_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'STRICT_FIXTURE'):
            self.execute(Adapter(fixtures=[replace(fixture(),returned_id='wrong')]))

    def test_malformed_calendar_cannot_claim_official_coverage(self):
        with self.assertRaisesRegex(ValueError,'CALENDAR_EVIDENCE'):
            windows([], [Window('OFFICIAL','2026-01-01','2026-01-02','x',None,'SENIOR','UNKNOWN',())])

    def test_unavailable_paths_recorded_before_pending_and_repeat_reuses(self):
        a = Adapter()
        first = self.execute(a)
        self.assertGreater(first['metrics']['attempts'], 1)
        self.assertTrue(all(r['resolution_basis']=='PATHS_UNAVAILABLE' for r in first['resolutions']))
        rows = self.db.execute('SELECT count(*) FROM reconstruction_findings').fetchone()[0]
        a.calls.clear()
        second = self.execute(a)
        self.assertEqual(second['metrics']['attempts'],0)
        self.assertEqual(a.calls,[])
        self.assertEqual(self.db.execute('SELECT count(*) FROM reconstruction_findings').fetchone()[0], rows)

    def test_dependency_change_reopens(self):
        a = Adapter()
        self.execute(a)
        a.version='2'
        self.assertGreater(self.execute(a)['metrics']['attempts'],0)

    def test_global_attempt_budget_and_explicit_remaining_paths(self):
        result = self.execute(max_attempts=2)
        self.assertEqual(result['metrics']['attempts'],2)
        self.assertTrue(all(r['remaining_paths'] for r in result['resolutions']))
        self.assertTrue(all(r['resolution_basis']=='RESOURCE_LIMIT' for r in result['resolutions']))

    def test_active_resolution_recovers_status_without_writing_appearance(self):
        a=Adapter(answers={'fixture_details':Resolution('fixture_details','FOUND','exact squad',(E,),status('p'))})
        r=self.execute(a)
        found=[x for x in r['resolutions'] if x['canonical']=='match'][0]
        self.assertEqual(found['state'],'RESOLVED')
        tables={x[0] for x in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('player_match_minutes',tables)

    def test_conflicting_resolution_does_not_replace_fact(self):
        a=Adapter(answers={
            'fixture_details':Resolution('fixture_details','FOUND','exact squad',(E,),status('p')),
            'official_squad':Resolution('official_squad','FOUND','exact squad',(E,),status('p',state='UNUSED_SUBSTITUTE',minutes=0))})
        r=self.execute(a)
        self.assertEqual(next(x for x in r['resolutions'] if x['canonical']=='match')['state'],'CONFLICT')

    def test_injury_one_day_cannot_explain_whole_gap(self):
        a=Adapter(answers={'injury_availability':Resolution('injury_availability','FOUND','one day',(E,),explanation='injury',explanation_start='2026-10-07',explanation_end='2026-10-08')})
        with self.assertRaisesRegex(ValueError,'EXPLANATION_DOES_NOT_COVER'):
            self.execute(a)

    def test_future_opportunities_not_audited(self):
        a=Adapter(fixtures=[fixture('2026-11-05')])
        self.assertEqual(self.execute(a)['opportunities'],[])

    def test_all_modes_share_core_contract(self):
        for mode in ['ONBOARD','BACKFILL','INCREMENTAL','DELAYED_RECONCILIATION']:
            self.assertEqual(self.execute(mode=mode)['stages'][2],'R5')

    def test_national_relaxation_requires_positive_coverage_evidence(self):
        f=fixture(domain='NATIONAL',team='national')
        a=Adapter(members=[member('p','national','NATIONAL',region='AFC'),member('peer','national','NATIONAL')],fixtures=[f],statuses=[status(team='national')])
        self.assertFalse(self.execute(a)['opportunities'][0]['relaxed'])
        a.members[0]=replace(a.members[0],coverage_evidence=(E,))
        self.assertTrue(self.execute(a)['opportunities'][0]['relaxed'])

    def test_existing_conflict_requires_adjudication_not_a_single_new_claim(self):
        a=Adapter(statuses=[status(),status('p'),status('p',state='UNUSED_SUBSTITUTE',minutes=0)],answers={'fixture_details':Resolution('fixture_details','FOUND','scoped',(E,),status('p'))})
        result=self.execute(a)
        self.assertEqual(next(r for r in result['resolutions'] if r['canonical']=='match')['state'],'CONFLICT')

    def test_archive_path_cannot_escape_boundary(self):
        from squad_pipeline.reconstruction.archive import Archive
        from pathlib import Path
        with self.assertRaisesRegex(ValueError,'OUTSIDE_AUTHORIZED'):
            Archive(Path.cwd()).body('../outside','a'*64)

    def test_recovered_squad_status_reaudits_gap_before_opening_obsolete_task(self):
        a=Adapter(answers={'fixture_details':Resolution('fixture_details','FOUND','scoped',(E,),status('p'))})
        result=self.execute(a)
        self.assertEqual(len(result['gaps']),1)
        self.assertEqual(result['final_gaps'],[])
        self.assertFalse(any(r['trigger']=='R3' for r in result['resolutions']))

    def test_calendar_parser_refuses_wrong_scope(self):
        import json
        from squad_pipeline.reconstruction.calendar import fifa_windows
        state={'props':{'pageProps':{'pageData':{'dashboardView':{'activeTab':"Women's",'description':'international “A” matches','content':[]}}}}}
        raw=('<script id="__NEXT_DATA__">'+json.dumps(state)+'</script>').encode()
        with self.assertRaisesRegex(ValueError,'MENS_SCOPE'):
            fifa_windows(raw,'https://inside.fifa.com/tournament-organisation/international-match-calendars','2026-11-04T00:00:00+00:00','2026-07-01','2026-11-04')

    def test_conflicted_squad_day_does_not_fill_gap(self):
        a=Adapter(statuses=[status(),status('p'),status('p',state='UNUSED_SUBSTITUTE',minutes=0)])
        r=self.execute(a)
        self.assertEqual(r['metrics']['r3_triggers'],1)

    def test_public_export_summary_excludes_private_claim_details(self):
        from squad_pipeline.reconstruction.export import summary
        self.assertIsNone(summary(self.db))
        self.execute()
        value=summary(self.db)
        self.assertFalse(value['complete'])
        self.assertNotIn('player',str(value))
        self.assertNotIn('https://',str(value))
