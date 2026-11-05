"""Independent Gate S contract tests; does not import/run reconstruction R1–R6."""
import unittest,json,tempfile,shutil
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
from squad_pipeline.source_interface import SourceRequest,RawSourceArtifact,SourceAcquisitionResult,EvidenceStore,SourceService,sha,encode
from squad_pipeline.source_parsing import parse,PARSER_VERSION
from squad_pipeline.live_sources import Client
from squad_pipeline.international_discovery import payload
from test_international_fotmob import data,html


class SourceContractTests(unittest.TestCase):
    def test_stale_locator_reuses_hash_index_but_existing_corruption_fails(self):
        body=b'retained';p=self.root/'indexed';p.write_bytes(body)
        store=EvidenceStore(self.root,index={sha(body):[p.name]})
        record=dict(path='stale',sha256=sha(body),url=self.request().url)
        self.assertEqual(store.normalize(record).outcome,'FOUND')
        (self.root/'stale').write_bytes(b'corrupt')
        self.assertEqual(store.normalize(record).outcome,'INTEGRITY_CONFLICT')

    def test_hash_metadata_identity_requires_unique_url(self):
        body=b'retained';p=self.root/'body';p.write_bytes(body)
        record=dict(path=p.name,sha256=sha(body))
        captures=[dict(sha256=sha(body),url=self.request().url)]
        store=EvidenceStore(self.root,records=captures)
        self.assertEqual(store.normalize(record).artifact.request.url,self.request().url)
        store=EvidenceStore(self.root,records=captures+[dict(sha256=sha(body),url='https://source.example/other')])
        self.assertEqual(store.normalize(record).outcome,'EVIDENCE_CONTRACT_INCOMPLETE')

    def test_replay_cache_is_versioned_isolated_and_checks_body_integrity(self):
        a=self.artifact();service=SourceService(self.store,'offline',set(),max_calls=0)
        _,first=service.observe(a.request,a);first.native['modified_by_caller']=True
        _,second=service.observe(a.request,a)
        self.assertNotIn('modified_by_caller',second.native);self.assertEqual(service.parse_calls,1)
        self.assertEqual(service.parse_cache_hits,1)
        with patch('squad_pipeline.source_parsing.PARSER_VERSION','next-test-parser'):
            service.observe(a.request,a)
        self.assertEqual(service.parse_calls,2)
        (self.root/a.locator).write_bytes(b'tampered')
        acquired,observation=service.observe(a.request,a)
        self.assertEqual(acquired.outcome,'INTEGRITY_CONFLICT');self.assertIsNone(observation)

    def test_sky_native_national_tuple_and_request_binding_both_required(self):
        header='<div class="sdc-site-match-header" data-status="4"><span class="sdc-site-match-header__team-name-block-target">Country A</span><span class="sdc-site-match-header__team-name-block-target">Country B</span><span class="sdc-site-match-header__detail-time">7:45pm, Friday 2nd October 2026</span><span class="sdc-site-match-header__detail-fixture">International Match</span></div>'
        columns=[]
        for side in ['A','B']:
            groups=[]
            for bench,indices in [(False,range(11)),(True,[11])]:
                players=''.join('<span class="sdc-site-team-lineup__player-number">'+str(i)+'</span><span class="sdc-site-team-lineup__player-name"><span class="sdc-site-team-lineup__player-initial" title="Given'+side+str(i)+'"></span><span class="sdc-site-team-lineup__player-surname">Family'+side+str(i)+'</span></span>' for i in indices)
                groups.append('<div class="sdc-site-team-lineup__players'+(' sdc-site-team-lineup__players--subs' if bench else '')+'">'+players+'</div>')
            columns.append('<div class="sdc-site-team-lineup__col"><span class="sdc-site-team-lineup__team-name">Country '+side+'</span>'+''.join(groups)+'</div>')
        body=('<!doctype html><html>'+header+''.join(columns)+'</html>').encode()
        fixture=dict(home='Country A',away='Country B',home_team_key='a',away_team_key='b',kickoff='2026-10-02T18:45:00Z',competition_label='International Match',competition_key='114',level='SENIOR',evidence=dict(url='https://source.example/schedule',sha256='a'*64))
        request=self.request(provider='Sky',url='https://www.skysports.com/lineups/44',expected_ids=dict(match='44'),context=dict(domain='NATIONAL',fixture=fixture))
        o=parse(self.artifact(body,request),body);self.assertEqual(o.outcome,'PARSED');self.assertFalse(o.completeness['returned_provider_id']);self.assertIsNone(o.canonical_identity)
        wrong=replace(request,expected_ids=dict(match='45'))
        self.assertEqual(parse(self.artifact(body,wrong),body).outcome,'IDENTITY_MISMATCH')

    def test_archive_capture_metadata_excludes_headers_and_retains_source_identity(self):
        import tarfile,io
        from squad_pipeline.source_interface import AssetArchiveReader
        body=b'captured';metadata=json.dumps(dict(url='https://source.example/teams/88/fixtures',sha256=sha(body),headers={'Authorization':'private'},response_timestamp='2026-11-04T00:00:00Z',http_status=200)).encode()
        path=self.root/'asset.tar'
        with tarfile.open(path,'w') as tar:
            for name,value in [('arbitrary.response',body),('arbitrary.metadata.json',metadata)]:
                entry=tarfile.TarInfo(name);entry.size=len(value);tar.addfile(entry,io.BytesIO(value))
        reader=AssetArchiveReader({'retained':dict(path=str(path),sha256=sha(path.read_bytes()))},wanted_hashes={sha(body)})
        self.assertEqual(reader.captures[0]['sha256'],sha(body));self.assertNotIn('headers',reader.captures[0])
        self.assertEqual(reader('retained',sha(body)),body)

    def test_exact_capture_index_replays_via_service_without_network(self):
        body=b'archived';p=self.root/'capture';p.write_bytes(body)
        store=EvidenceStore(self.root,records=[dict(url=self.request().target_url,path=p.name,sha256=sha(body),http_status=200)])
        service=SourceService(store,'offline',set(),max_calls=0)
        self.assertEqual(service.acquire(self.request()).outcome,'FOUND');self.assertEqual(service.client.calls,0)

    def test_archived_failure_is_transport_failure_not_parser_malformed(self):
        a=self.store.import_body(self.request(),b'error',dict(http_status=500))
        service=SourceService(self.store,'offline',set(),max_calls=0)
        acquired,observation=service.observe(a.request,a)
        self.assertEqual(acquired.outcome,'TEMPORARILY_FAILED');self.assertIsNone(observation)
        self.assertEqual(service.client.calls,0)

    def test_capture_index_preserves_later_restriction_instead_of_stale_success(self):
        p=self.root/'body';p.write_bytes(b'captured')
        base=dict(url=self.request().target_url,path=p.name,sha256=sha(b'captured'))
        records=[dict(base,http_status=200,response_timestamp='2026-11-04T01:00:00+02:00'),dict(base,http_status=403,response_timestamp='2026-11-04T00:00:00Z')]
        service=SourceService(EvidenceStore(self.root,records=records),'offline',set(),max_calls=0)
        self.assertEqual(service.acquire(self.request()).outcome,'ACCESS_RESTRICTED')

    def test_joint_history_detail_request_rejects_different_fixture_claim(self):
        claim=dict(player_provider_id='111',provider_match_id='different',source=dict(url='https://source.example/players/111'),discovery_claim=dict(id='different'))
        r=self.request(context=dict(history_claims=[claim]));a=self.artifact(request=r)
        self.assertEqual(parse(a,self.store.read(a)).outcome,'IDENTITY_MISMATCH')

    def test_national_source_keeps_explicit_team_level(self):
        body=json.dumps(data()).encode();r=self.request(context=dict(domain='NATIONAL',level='U21'))
        o=parse(self.artifact(body,r),body);self.assertEqual(o.native['level'],'U21')
        self.assertIn('international_fotmob.parse_public_match@',o.parser_version)

    def test_national_level_mismatch_is_not_accepted_as_club_fixture(self):
        body=json.dumps(data()).encode();r=self.request(context=dict(domain='NATIONAL',level='SENIOR'))
        self.assertEqual(parse(self.artifact(body,r),body).outcome,'IDENTITY_MISMATCH')

    def test_player_history_reuses_native_discovery_not_nationality(self):
        d=dict(id=77,gender='male',isCoach=False,nationality='Example',recentMatches=[],initialMatchesPage={})
        body=('<script id="__NEXT_DATA__">'+json.dumps(dict(props=dict(pageProps=dict(data=d))))+'</script>').encode()
        request=self.request(category='player_history',url='https://source.example/players/77',context=dict(start='2026-08-01',cutoff='2026-11-04'))
        o=parse(self.artifact(body,request),body)
        self.assertEqual(o.outcome,'PARSED');self.assertEqual(o.native['affiliations'],[])
        self.assertFalse(o.completeness['history_complete']);self.assertIsNone(o.canonical_identity)

    def test_player_history_wrong_requested_player_is_conflict(self):
        body=b'<script id="__NEXT_DATA__">{"props":{"pageProps":{"data":{"id":78}}}}</script>'
        request=self.request(category='player_history',url='https://source.example/players/77',context=dict(start='2026-08-01',cutoff='2026-11-04'))
        self.assertEqual(parse(self.artifact(body,request),body).outcome,'IDENTITY_MISMATCH')

    def test_team_history_requires_explicit_team_and_level(self):
        d=dict(details=dict(id=88),fixtures=dict(allFixtures=[]))
        body=('<script id="__NEXT_DATA__">'+json.dumps(dict(props=dict(pageProps=dict(data=d))))+'</script>').encode()
        request=self.request(category='team_history',context=dict(start='2026-08-01',cutoff='2026-11-04'))
        self.assertEqual(parse(self.artifact(body,request),body).outcome,'INCOMPLETE')
        request=replace(request,expected_ids=dict(team='88'),context=dict(start='2026-08-01',cutoff='2026-11-04',level='SENIOR'))
        self.assertEqual(parse(self.artifact(body,request),body).outcome,'PARSED')

    def test_hash_bound_sidecar_recovers_legacy_request_url(self):
        body=b'archive';p=self.root/'arbitrary.response';p.write_bytes(body)
        p.with_suffix('.meta.json').write_text(json.dumps(dict(url='https://source.example/detail?matchId=10',sha256=sha(body))))
        r=self.store.normalize(dict(path=p.name,sha256=sha(body)))
        self.assertEqual(r.outcome,'FOUND');self.assertEqual(r.artifact.request.url,'https://source.example/detail?matchId=10')
        self.assertIsNone(r.artifact.metadata['response_timestamp'])

    def test_unbound_sidecar_cannot_grant_request_identity(self):
        body=b'archive';p=self.root/'arbitrary.response';p.write_bytes(body)
        p.with_suffix('.meta.json').write_text(json.dumps(dict(url='https://source.example/detail?matchId=10',sha256='f'*64)))
        self.assertEqual(self.store.normalize(dict(path=p.name,sha256=sha(body))).outcome,'EVIDENCE_CONTRACT_INCOMPLETE')

    def test_body_store_symlink_cannot_escape(self):
        outside=self.root/'outside';outside.mkdir();owned=self.root/'owned';owned.mkdir()
        (owned/'bodies').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'STORAGE_BOUNDARY'):
            EvidenceStore(owned).import_body(self.request(),b'body',{})
        self.assertEqual(list(outside.iterdir()),[])

    def test_metadata_store_symlink_cannot_escape(self):
        outside=self.root/'outside';outside.mkdir();owned=self.root/'owned';owned.mkdir()
        (owned/'artifacts').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'STORAGE_BOUNDARY'):
            EvidenceStore(owned).import_body(self.request(),b'body',{})
        self.assertEqual(list(outside.iterdir()),[])

    def test_failed_response_bytes_count_toward_budget(self):
        s=self.service([(404,{},b''),(503,{},b'failure'),(503,{},b'failure')],max_bytes=10)
        self.assertEqual(s.acquire(self.request(policy='LIVE_ALLOWED')).outcome,'BUDGET_EXHAUSTED')
        self.assertEqual(s.bytes,14)

    def setUp(self):
        root=Path.cwd()/'.cloud';root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.root=Path(self.temp.name)
        self.store=EvidenceStore(self.root)

    def tearDown(self):self.temp.cleanup()

    def request(self,**kw):
        d=dict(provider='FotMob',category='match',url='https://source.example/detail?matchId=10',resource='match-detail')
        d.update(kw);return SourceRequest(**d)

    def artifact(self,body=None,request=None):
        body=body if body is not None else json.dumps(data()).encode()
        return self.store.import_body(request or self.request(),body,dict(request_timestamp='2026-11-04T00:00:00Z',response_timestamp='2026-11-04T00:00:01Z'))

    def service(self,sequence,**kw):
        self.calls=[];seq=iter(sequence)
        def transport(url,headers):
            self.calls.append((url,headers));item=next(seq)
            if isinstance(item,Exception):raise item
            return item
        return SourceService(self.store,'source-tests',{'source.example'},transport=transport,sleeper=lambda n:None,**kw)

    def test_request_preserves_query_and_parameters(self):
        r=self.request(parameters=(('date','2026-11-04'),('page','2')))
        self.assertEqual(r.target_url,'https://source.example/detail?matchId=10&date=2026-11-04&page=2')

    def test_archive_identity_does_not_depend_on_storage_filename(self):
        body=json.dumps(data()).encode();a=self.artifact(body)
        arbitrary=self.root/'not-a-provider-id.json';arbitrary.write_bytes(body)
        b=replace(a,locator=arbitrary.name)
        self.assertEqual(a.key,b.key)
        self.assertEqual(parse(a,body),parse(b,body))

    def test_relocation_preserves_logical_identity_and_replay(self):
        a=self.artifact();other=self.root/'relocated';shutil.copytree(self.root/'bodies',other/'bodies')
        store=EvidenceStore(other)
        self.assertEqual(store.read(a),self.store.read(a))
        self.assertEqual(parse(a,store.read(a)),parse(a,self.store.read(a)))

    def test_path_escape_not_read(self):
        a=replace(self.artifact(),locator='../outside')
        with self.assertRaises(FileNotFoundError):self.store.read(a)

    def test_hash_tampering_detected(self):
        a=self.artifact();(self.root/a.locator).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):self.store.read(a)

    def test_content_immutable_store_collision_detected(self):
        a=self.artifact();(self.root/a.locator).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'COLLISION'):self.artifact()

    def test_same_body_new_capture_retains_distinct_provenance(self):
        a=self.artifact();b=self.store.import_body(a.request,self.store.read(a),dict(response_timestamp='2026-11-05T00:00:00Z'))
        self.assertEqual(a.content_sha256,b.content_sha256);self.assertNotEqual(a.key,b.key)
        self.assertEqual(len(list((self.root/'artifacts').glob('*.json'))),2)

    def test_archival_metadata_excludes_secret_headers(self):
        a=self.store.import_body(self.request(),b'body',dict(headers={'Set-Cookie':'secret'},authorization='secret',cookie='secret'))
        self.assertNotIn('headers',a.metadata);self.assertNotIn('secret',str(a.metadata))

    def test_not_requested_is_not_unavailable(self):
        s=self.service([]);r=s.acquire(self.request());self.assertEqual(r.outcome,'NOT_REQUESTED');self.assertEqual(s.client.calls,0)

    def test_offline_replay_has_no_network(self):
        a=self.artifact();s=self.service([]);r=s.acquire(a.request,a)
        self.assertEqual(r.outcome,'FOUND');self.assertEqual(s.client.calls,0)

    def test_replay_request_mismatch_not_unavailable(self):
        a=self.artifact();s=self.service([])
        self.assertEqual(s.acquire(self.request(resource='other'),a).outcome,'IDENTITY_MISMATCH')

    def test_response_timestamp_normalization(self):
        p=self.root/'legacy';p.write_bytes(b'legacy')
        r=self.store.normalize(dict(url='https://source.example/source',path='legacy',sha256=sha(b'legacy'),response_timestamp='2026-11-04T00:00:00Z'))
        self.assertEqual(r.outcome,'FOUND');self.assertEqual(r.artifact.metadata['timestamp_precision'],'TIMESTAMP')

    def test_standard_source_record_hash_lookup_and_day_precision(self):
        p=self.root/'legacy';p.write_bytes(b'legacy');store=EvidenceStore(self.root,index={sha(b'legacy'):['legacy']})
        r=store.normalize(dict(source_url='https://source.example/source',source_sha256=sha(b'legacy'),locator='field',observed_on='2026-11-04'))
        self.assertEqual(r.outcome,'FOUND');self.assertEqual(r.artifact.authority,'SOURCE_RECORD');self.assertEqual(r.artifact.metadata['timestamp_precision'],'DAY_OR_UNKNOWN')
        self.assertIsNone(r.artifact.metadata['response_timestamp'])

    def test_unresolved_asset_is_not_website_unavailable(self):
        r=self.store.normalize(dict(source_url='https://source.example/source',source_sha256='a'*64,raw_fields=dict(retained_raw_asset='asset-ref')))
        self.assertEqual(r.outcome,'REMOTE_ASSET_REQUIRED')

    def test_empty_index_is_not_website_unavailable(self):
        r=self.store.normalize(dict(source_url='https://source.example/source',source_sha256='a'*64))
        self.assertEqual(r.outcome,'ARCHIVE_LOCATOR_UNRESOLVED')

    def test_manual_policy_remains_separate_authority(self):
        r=self.store.normalize(dict(authorization_date='2026-10-01',instruction='Include authorized case',source_type='USER_POLICY'))
        self.assertEqual(r.outcome,'MANUAL_POLICY');self.assertIsNone(r.artifact)
        self.assertNotIn('http_status',r.metadata)

    def test_canonical_identity_never_assigned_by_parse(self):
        a=self.artifact();o=parse(a,self.store.read(a));self.assertIsNone(o.canonical_identity)
        self.assertEqual(o.native['provider_match_id'],'10')

    def test_native_json_uses_existing_strict_parser(self):
        a=self.artifact();o=parse(a,self.store.read(a))
        self.assertIn('international_fotmob.parse_public_club_match',o.parser_version)
        self.assertEqual(o.native['requested_identity'],'MATCHED')

    def test_unknown_bench_not_zero(self):
        a=self.artifact();o=parse(a,self.store.read(a));last=o.native['sheets'][0]['players'][-1]
        self.assertIsNone(last['minutes']);self.assertEqual(last['participation_status'],'BENCH_PARTICIPATION_UNKNOWN')

    def test_returned_source_id_mismatch(self):
        a=self.artifact(request=self.request(expected_ids={'match':'999'}))
        self.assertEqual(parse(a,self.store.read(a)).outcome,'IDENTITY_MISMATCH')

    def test_missing_requested_id_remains_unverified(self):
        a=self.artifact(request=self.request(url=None))
        o=parse(a,self.store.read(a));self.assertEqual(o.native['requested_identity'],'UNVERIFIED');self.assertEqual(o.outcome,'INCOMPLETE')

    def test_ordered_team_conflict_preserved(self):
        a=self.artifact(request=self.request(context={'home_id':'2','away_id':'1'}))
        self.assertEqual(parse(a,self.store.read(a)).outcome,'IDENTITY_MISMATCH')

    def test_competition_parent_relation_retains_both_identifiers(self):
        d=data();d['general'].update(leagueId=999,parentLeagueId=8)
        a=self.artifact(json.dumps(d).encode(),self.request(context={'competition_id':8}))
        o=parse(a,self.store.read(a));self.assertEqual(o.native['provider_competition_id'],'999');self.assertEqual(o.native['provider_parent_competition_id'],'8')

    def test_unrelated_parent_competition_rejected(self):
        d=data();d['general'].update(leagueId=999,parentLeagueId=9)
        a=self.artifact(json.dumps(d).encode(),self.request(context={'competition_id':8}))
        self.assertEqual(parse(a,self.store.read(a)).outcome,'IDENTITY_MISMATCH')

    def test_wrong_season_rejected(self):
        a=self.artifact(request=self.request(expected_ids={'season':'not-source-season'}))
        self.assertEqual(parse(a,self.store.read(a)).outcome,'IDENTITY_MISMATCH')

    def test_malformed_json_explicit(self):
        a=self.artifact(b'{bad-json')
        self.assertEqual(parse(a,self.store.read(a)).outcome,'MALFORMED')

    def test_unsupported_parser_not_source_unavailable(self):
        a=self.artifact(b'unknown',self.request(provider='Unknown'))
        self.assertEqual(parse(a,b'unknown').outcome,'PARSER_NOT_IMPLEMENTED')

    def test_web_excerpt_not_full_response_or_invented_minutes(self):
        text='Retained provider report excerpt';body=json.dumps([dict(source_url=self.request().url,source_lines=text,sha256_of_extract=sha(text.encode()))]).encode()
        a=self.artifact(body,self.request(provider='VI'));o=parse(a,body)
        self.assertEqual(o.outcome,'INCOMPLETE');self.assertFalse(o.completeness['reported_minutes']);self.assertFalse(o.completeness['full_http_response'])

    def test_extract_source_conflict_is_ambiguous(self):
        body=b'[]';a=self.artifact(body,self.request(provider='VI'));self.assertEqual(parse(a,body).outcome,'AMBIGUOUS')

    def test_extract_hash_contradiction_not_ignored(self):
        body=json.dumps([dict(source_url=self.request().url,source_lines='text',sha256_of_extract='b'*64)]).encode()
        a=self.artifact(body,self.request(provider='VI'));self.assertEqual(parse(a,body).outcome,'INTEGRITY_CONFLICT')

    def test_parser_version_explicit_and_repeat_stable(self):
        a=self.artifact();one=parse(a,self.store.read(a));two=parse(a,self.store.read(a))
        self.assertEqual(one,two);self.assertIn(PARSER_VERSION,one.parser_version)

    def test_direct_json_envelope_requires_match_structure(self):
        with self.assertRaisesRegex(ValueError,'STRUCTURE'):payload('{"unrelated":true}')
        self.assertEqual(str(payload(json.dumps(data()))['general']['matchId']),'10')

    def test_http_found_archives_response_and_metadata(self):
        s=self.service([(404,{},b''),(200,{'ETag':'version'},b'<html>source</html>')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'FOUND');self.assertTrue(r.artifact.metadata['response_timestamp']);self.assertEqual(self.store.read(r.artifact),b'<html>source</html>')

    def test_cache_first_has_no_new_request(self):
        s=self.service([(404,{},b''),(200,{'ETag':'version'},b'<html>source</html>')]);s.acquire(self.request(policy='LIVE_ALLOWED'));calls=s.client.calls
        r=s.acquire(self.request(policy='CACHE_FIRST'));self.assertEqual(r.outcome,'FOUND');self.assertEqual(s.client.calls,calls);self.assertTrue(r.artifact.metadata['response_timestamp'])

    def test_transient_retry_then_success(self):
        s=self.service([(404,{},b''),(503,{},b'retry'),(200,{},b'<html>source</html>')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'FOUND');self.assertEqual(s.client.calls,3)

    def test_timeout_classified_without_football_claim(self):
        s=self.service([(404,{},b''),TimeoutError('timed out'),TimeoutError('timed out')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'TEMPORARILY_FAILED');self.assertIsNone(r.artifact)

    def test_rate_limit_has_distinct_outcome(self):
        s=self.service([(404,{},b''),(429,{'Retry-After':'30'},b'limit')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'RATE_LIMITED')

    def test_access_restriction_not_bypassed(self):
        s=self.service([(404,{},b''),(403,{},b'denied')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'ACCESS_RESTRICTED');calls=s.client.calls;s.acquire(self.request(policy='LIVE_ALLOWED'));self.assertEqual(s.client.calls,calls)

    def test_robots_disallow_target_not_requested(self):
        s=self.service([(200,{},b'User-agent: *\nDisallow: /detail')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'ACCESS_RESTRICTED');self.assertEqual(s.client.calls,1)

    def test_private_api_transport_remains_restricted(self):
        s=self.service([]);r=s.acquire(self.request(url='https://source.example/api/private',policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'ACCESS_RESTRICTED');self.assertEqual(s.client.calls,0)

    def test_host_not_authorized_no_network(self):
        s=self.service([]);r=s.acquire(self.request(url='https://other.example/page',policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'ACCESS_RESTRICTED');self.assertEqual(s.client.calls,0)

    def test_redirect_requires_review_not_auto_follow(self):
        s=self.service([(404,{},b''),(302,{'Location':'https://other.example'},b'')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'REDIRECT_REQUIRES_REVIEW');self.assertEqual(s.client.calls,2)

    def test_network_budget_is_explicit(self):
        s=self.service([(404,{},b'')],max_calls=1);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'BUDGET_EXHAUSTED');self.assertEqual(s.client.calls,1)

    def test_byte_budget_is_explicit(self):
        s=self.service([(404,{},b''),(200,{},b'<html>source</html>')],max_bytes=2);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'BUDGET_EXHAUSTED')

    def test_pagination_bounded_before_network(self):
        s=self.service([]);r=s.pages(self.request(policy='LIVE_ALLOWED'),[(('page',str(n)),) for n in range(4)],max_pages=3)
        self.assertEqual(r[0].outcome,'BUDGET_EXHAUSTED');self.assertEqual(s.client.calls,0)

    def test_conditional_304_reuses_verified_body(self):
        s=self.service([(404,{},b''),(200,{'ETag':'v1'},b'<html>body</html>'),(304,{},b'')]);a=s.acquire(self.request(policy='LIVE_ALLOWED'));b=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(a.artifact.content_sha256,b.artifact.content_sha256);self.assertEqual(self.calls[-1][1].get('If-None-Match'),'v1')

    def test_http_not_found_is_real_unavailable(self):
        s=self.service([(404,{},b''),(404,{},b'not here')]);self.assertEqual(s.acquire(self.request(policy='LIVE_ALLOWED')).outcome,'UNAVAILABLE')

    def test_credentials_in_url_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'ANONYMOUS_HTTPS'):self.request(url='https://user:secret@source.example/').target_url

    def test_legacy_client_default_storage_unchanged(self):
        from squad_pipeline.core import ROOT
        from squad_pipeline.live_sources import RAW,OUT,HOSTS
        c=Client('no-calls',transport=lambda u,h:(200,{},b''),sleeper=lambda n:None)
        self.assertEqual(c.root,ROOT);self.assertEqual(c.raw_dir,RAW);self.assertEqual(c.output_dir,OUT);self.assertIs(c.hosts,HOSTS)

    def test_authorized_asset_backend_verifies_hash_and_replays(self):
        body=b'asset-body';called=[]
        def reader(ref,expected):called.append(ref);return body
        store=EvidenceStore(self.root,asset_reader=reader)
        r=store.normalize(dict(source_url='https://source.example/source',source_sha256=sha(body),raw_fields=dict(retained_raw_asset='existing-asset')))
        self.assertEqual(r.outcome,'FOUND');self.assertEqual(store.read(r.artifact),body);self.assertEqual(called,['existing-asset'])

    def test_asset_integrity_conflict_not_website_unavailable(self):
        store=EvidenceStore(self.root,asset_reader=lambda r,h:b'wrong')
        r=store.normalize(dict(source_url='https://source.example/source',source_sha256='a'*64,retained_asset_ref='asset'))
        self.assertEqual(r.outcome,'INTEGRITY_CONFLICT')

    def test_asset_access_denial_explicit(self):
        def deny(*args):raise PermissionError()
        store=EvidenceStore(self.root,asset_reader=deny)
        r=store.normalize(dict(source_url='https://source.example/source',source_sha256='a'*64,retained_asset_ref='asset'))
        self.assertEqual(r.outcome,'ASSET_ACCESS_RESTRICTED')

    def test_corrupt_cache_not_silently_replaced(self):
        s=self.service([(404,{},b''),(200,{},b'<html>body</html>')]);s.acquire(self.request(policy='LIVE_ALLOWED'))
        cached=s.client.cache[self.request().target_url];(self.root/cached['path']).write_bytes(b'changed');calls=s.client.calls
        r=s.acquire(self.request(policy='CACHE_FIRST'));self.assertEqual(r.outcome,'INTEGRITY_CONFLICT');self.assertEqual(s.client.calls,calls)

    def test_empty_200_not_access_restriction(self):
        s=self.service([(404,{},b''),(200,{},b'')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'MALFORMED')

    def test_304_without_cache_not_redirect(self):
        s=self.service([(404,{},b''),(304,{},b'')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'CACHE_INVALID')

    def test_downstream_observe_uses_contract_without_filesystem_assumptions(self):
        a=self.artifact();s=self.service([]);acquired,observation=s.observe(a.request,a)
        self.assertEqual(acquired.outcome,'FOUND');self.assertEqual(observation.native['provider_match_id'],'10');self.assertEqual(s.client.calls,0)

    def test_pagination_preserves_source_query_and_stops_at_budget(self):
        s=self.service([(404,{},b''),(200,{},b'<html>one</html>'),(200,{},b'<html>two</html>')])
        r=s.pages(self.request(policy='LIVE_ALLOWED'),[(('page','1'),),(('page','2'),)],max_pages=2)
        self.assertTrue(all(x.outcome=='FOUND' for x in r));self.assertIn('matchId=10&page=1',self.calls[1][0]);self.assertIn('matchId=10&page=2',self.calls[2][0])

    def test_parser_version_change_keeps_artifact_identity(self):
        a=self.artifact();body=self.store.read(a);one=parse(a,body)
        with patch('squad_pipeline.source_parsing.PARSER_VERSION','source-parser/test-next'):
            two=parse(a,body)
        self.assertEqual(one.artifact_key,two.artifact_key);self.assertNotEqual(one.parser_version,two.parser_version)

    def test_relocated_legacy_absolute_path_requires_explicit_mapping(self):
        p=self.root/'body';p.write_bytes(b'archived')
        store=EvidenceStore(self.root,relocations={'/historic-project':self.root})
        r=store.normalize(dict(url='https://source.example/source',path='/historic-project/body',sha256=sha(b'archived')))
        self.assertEqual(r.outcome,'FOUND')

    def test_new_storage_boundary_does_not_relax_legacy_guard(self):
        s=self.service([])
        with self.assertRaisesRegex(ValueError,'STORAGE_BOUNDARY'):s.client._write(self.root.parent/'outside.json',{})

    def test_existing_tar_asset_lookup_without_path_extraction(self):
        import tarfile,io
        from squad_pipeline.source_interface import AssetArchiveReader
        body=b'retained-proof';path=self.root/'asset.tar.gz'
        with tarfile.open(path,'w:gz') as tar:
            m=tarfile.TarInfo('../../unsafe-looking-name');m.size=len(body);tar.addfile(m,io.BytesIO(body))
        reader=AssetArchiveReader({'existing':dict(path=path,sha256=sha(path.read_bytes()))},wanted_hashes={sha(body)})
        store=EvidenceStore(self.root,asset_reader=reader)
        r=store.normalize(dict(source_url='https://source.example/source',source_sha256=sha(body)))
        self.assertEqual(r.outcome,'FOUND');self.assertEqual(store.read(r.artifact),body)
        self.assertFalse((self.root.parent/'unsafe-looking-name').exists())

    def test_asset_archive_hash_mismatch_rejected(self):
        from squad_pipeline.source_interface import AssetArchiveReader
        path=self.root/'bad.tar.gz';path.write_bytes(b'not-the-asset')
        with self.assertRaisesRegex(ValueError,'ARCHIVE_HASH_MISMATCH'):
            AssetArchiveReader({'old':dict(path=path,sha256='a'*64)})

    def test_asset_declared_reference_cannot_silently_switch(self):
        import tarfile,io
        from squad_pipeline.source_interface import AssetArchiveReader
        body=b'proof';p=self.root/'asset.tar.gz'
        with tarfile.open(p,'w:gz') as tar:
            m=tarfile.TarInfo('body');m.size=len(body);tar.addfile(m,io.BytesIO(body))
        reader=AssetArchiveReader({'actual':dict(path=p,sha256=sha(p.read_bytes()))})
        with self.assertRaises(FileNotFoundError):reader('different-asset',sha(body))

    def test_asset_archive_expansion_budget(self):
        import tarfile,io
        from squad_pipeline.source_interface import AssetArchiveReader
        p=self.root/'asset.tar.gz'
        with tarfile.open(p,'w:gz') as tar:
            m=tarfile.TarInfo('body');m.size=10;tar.addfile(m,io.BytesIO(b'0123456789'))
        with self.assertRaisesRegex(ValueError,'MEMBER_BUDGET'):
            AssetArchiveReader({'old':dict(path=p,sha256=sha(p.read_bytes()))},max_member=5)

    def test_contract_version_not_silently_accepted(self):
        a=self.artifact(request=self.request(version='source-contract/future'))
        self.assertEqual(parse(a,self.store.read(a)).outcome,'CONTRACT_VERSION_UNSUPPORTED')
        self.assertEqual(self.service([]).acquire(a.request).outcome,'CONTRACT_VERSION_UNSUPPORTED')

    def test_invalid_hash_shape_is_contract_failure(self):
        r=self.store.normalize(dict(url='https://source.example/source',sha256=123))
        self.assertEqual(r.outcome,'EVIDENCE_CONTRACT_INCOMPLETE')

    def test_manual_record_does_not_create_new_authorization(self):
        r=self.store.normalize(dict(authorization_date='2026-10-01',instruction='Existing policy'))
        self.assertEqual(r.metadata['authorization'],'PRESERVED_PRIOR_ASSERTION;NOT_A_NEW_GRANT')

    def test_official_profile_empty_extraction_not_successful_profile(self):
        r=self.request(provider='OfficialClub',category='profile',url='https://www.arsenal.com/players/example',context={'club_id':9825})
        a=self.artifact(b'<html>no person metadata</html>',r);o=parse(a,self.store.read(a))
        self.assertEqual(o.outcome,'INCOMPLETE');self.assertFalse(o.completeness['profile'])

    def test_roster_requires_explicit_provider_context(self):
        a=self.artifact(b'<html>roster</html>',self.request(provider='OfficialClub',category='roster'))
        self.assertEqual(parse(a,self.store.read(a)).outcome,'INCOMPLETE')

    def test_official_calendar_native_rows_and_scope(self):
        fields=dict(DateStart='2026-09-21T00:00:00Z',DateEnd='2026-10-06T00:00:00Z',TypeName='window')
        view=dict(activeTab="Men's",description='Fixed dates for international “A” matches',content=[dict(contentProps=dict(tableType='International Match Calendar',years={'2026':dict(groups=[dict(rows=[dict(fields=fields)])])}))])
        raw=('<script id="__NEXT_DATA__">'+json.dumps(dict(props=dict(pageProps=dict(pageData=dict(dashboardView=view)))))+'</script>').encode()
        a=self.artifact(raw,self.request(provider='FIFA',category='calendar'));o=parse(a,raw)
        self.assertEqual(o.outcome,'PARSED');self.assertEqual(o.native['rows'][0]['fields'],fields)
        view['activeTab']="Women's";wrong=('<script id="__NEXT_DATA__">'+json.dumps(dict(props=dict(pageProps=dict(pageData=dict(dashboardView=view)))))+'</script>').encode()
        b=self.artifact(wrong,a.request);self.assertEqual(parse(b,wrong).outcome,'MALFORMED')

    def test_explicit_store_run_id_cannot_escape(self):
        with self.assertRaisesRegex(ValueError,'INVALID_SOURCE_RUN_ID'):
            SourceService(self.store,'../outside',{'source.example'})

    def test_raw_archive_symlink_cannot_escape_store(self):
        external=self.root/'external';external.mkdir();store_root=self.root/'owned';store_root.mkdir();(store_root/'raw').symlink_to(external)
        s=SourceService(EvidenceStore(store_root),'test',{'source.example'},transport=lambda u,h:(200,{},b'<html>body</html>'),sleeper=lambda n:None)
        with self.assertRaisesRegex(ValueError,'STORAGE_BOUNDARY'):s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(list(external.iterdir()),[])

    def test_http_date_retry_after_is_deferred(self):
        from datetime import datetime,timezone,timedelta
        from email.utils import format_datetime
        stamp=format_datetime(datetime.now(timezone.utc)+timedelta(seconds=120))
        s=self.service([(404,{},b''),(429,{'Retry-After':stamp},b'wait')]);r=s.acquire(self.request(policy='LIVE_ALLOWED'))
        self.assertEqual(r.outcome,'RATE_LIMITED');self.assertEqual(s.client.calls,2)

    def test_article_metadata_not_identity_adjudication(self):
        raw=b'<html><meta property="og:title" content="Official announcement"></html>'
        a=self.artifact(raw,self.request(provider='OfficialArticle',category='article'));o=parse(a,raw)
        self.assertEqual(o.outcome,'PARSED');self.assertFalse(o.completeness['identity_adjudication']);self.assertIsNone(o.canonical_identity)

    def test_registration_missing_context_is_incomplete(self):
        a=self.artifact(b'<html>registration</html>',self.request(provider='PremierLeague',category='registration'))
        self.assertEqual(parse(a,self.store.read(a)).outcome,'INCOMPLETE')

    def test_uefa_legacy_season_default_unchanged_and_new_context_supported(self):
        from squad_pipeline.identity_sources import uefa_squad
        from squad_pipeline.source_interface import sha
        raw='<html><meta property="og:title" content="2027/28"><pk-table-row><a href="/uefachampionsleague/clubs/players/123--example/" title="Example Player">Example</a></pk-table-row></html>'
        capture=dict(url='https://www.uefa.com/uefachampionsleague/clubs/52919--example/squad/',sha256=sha(raw.encode()),status='SUCCESS')
        with self.assertRaisesRegex(ValueError,'SCOPE_REQUIRED'):uefa_squad(raw,capture)
        self.assertEqual(uefa_squad(raw,capture,'2027/28')[0].provider_ids['UEFA'],'123')
