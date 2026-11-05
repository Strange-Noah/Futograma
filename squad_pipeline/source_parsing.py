"""Adapters to existing pure parsers; native observations never grant canonical IDs."""
import json
import re
from urllib.parse import urlsplit,parse_qs
from .source_interface import ParsedSourceObservation,sha,VERSION
from .acquisition import detect_type

PARSER_VERSION='source-parser/4'


def _result(artifact,outcome,native,complete,uncertainties=(),parser=PARSER_VERSION):
    return ParsedSourceObservation(outcome,artifact.request.provider,artifact.request.category,
        native,artifact.key,parser,complete,tuple(uncertainties))


def returned_match_id(url):
    if not url:return None
    u=urlsplit(url);q=parse_qs(u.query)
    if q.get('matchId'):return q['matchId'][0]
    return None


def parse(artifact, body):
    if artifact.version!=VERSION or artifact.request.version!=VERSION:
        return _result(artifact,'CONTRACT_VERSION_UNSUPPORTED',{}, {},['Unsupported contract version'])
    if sha(body)!=artifact.content_sha256:
        return _result(artifact,'INTEGRITY_CONFLICT',{}, {},['Body hash differs'])
    request=artifact.request
    mime,_=detect_type(body)
    source=dict(url=request.target_url,sha256=artifact.content_sha256,artifact_key=artifact.key,
        path='artifact:'+artifact.key,response_timestamp=artifact.metadata.get('response_timestamp') or '',
        http_status=artifact.metadata.get('http_status'),status='SUCCESS')
    if artifact.metadata.get('catalog_candidate'):source['catalog_candidate']=artifact.metadata['catalog_candidate']
    try:
        if request.provider=='FotMob' and request.category in {'player_history','team_history'}:
            from .international_discovery import profile_hints,team_fixture_hints,payload
            context=request.context;left=context.get('start');right=context.get('cutoff')
            if not left or not right:return _result(artifact,'INCOMPLETE',{}, {},['Explicit discovery interval required'])
            d=payload(body.decode('utf8'))
            if request.category=='player_history':
                match=re.search(r'/players/(\d+)',request.target_url or '')
                asked=request.expected_ids.get('player') or (match[1] if match else None)
                if asked is None:return _result(artifact,'INCOMPLETE',{}, {},['Independent requested player ID required'])
                if str(d.get('id'))!=str(asked):return _result(artifact,'IDENTITY_MISMATCH',{}, {},['Requested/returned player ID mismatch'])
                if d.get('isCoach') or d.get('gender')!='male':return _result(artifact,'IDENTITY_MISMATCH',{}, {},['Player scope mismatch'])
                native=profile_hints(body.decode('utf8'),source,left,right,include_club=context.get('include_club',False))
                return _result(artifact,'PARSED',native,dict(source_identity=True,history_complete=False),
                    ['Recent history and career ranges are discovery hints; not current call-up membership'],
                    'international_discovery.profile_hints@'+PARSER_VERSION)
            asked=request.expected_ids.get('team');level=context.get('level')
            if asked is None or not level:return _result(artifact,'INCOMPLETE',{}, {},['Explicit team ID and level required'])
            matches=team_fixture_hints(body.decode('utf8'),source,dict(provider_team_id=str(asked),level=level),left,right)
            return _result(artifact,'PARSED',dict(matches=matches),dict(source_identity=True,history_complete=False),
                ['Fixture hints require independent detail/canonical verification'],
                'international_discovery.team_fixture_hints@'+PARSER_VERSION)
        if request.category=='article':
            from .identity_sources import meta,jsonld
            return _result(artifact,'PARSED',dict(metadata=meta(body.decode('utf8')),structured=jsonld(body.decode('utf8'))),
                dict(article_metadata=True,identity_adjudication=False),['Article metadata is not an accepted identity or membership decision'],
                'identity_sources.meta+jsonld@'+PARSER_VERSION)
        if request.category=='registration' and request.provider=='PremierLeague':
            from .identity_sources import registration
            context=request.context
            if not context.get('club_name') or context.get('club_id') is None:
                return _result(artifact,'INCOMPLETE',{}, {},['Registration club context required'])
            records=registration(body.decode('utf8'),source,context['club_name'],context['club_id'])
            return _result(artifact,'PARSED' if records else 'INCOMPLETE',dict(records=[r.evidence() for r in records]),dict(registration=bool(records)),
                parser='identity_sources.registration@'+PARSER_VERSION)
        if request.category=='roster' and request.provider=='UEFA':
            from .identity_sources import uefa_squad
            records=uefa_squad(body.decode('utf8'),source,request.context.get('season'))
            return _result(artifact,'PARSED',dict(records=[r.evidence() for r in records]),dict(roster_extraction=True),
                parser='identity_sources.uefa_squad@'+PARSER_VERSION)
        if request.category=='calendar' and request.provider=='FIFA':
            from .identity_sources import fifa_calendar
            rows=fifa_calendar(body.decode('utf8'),source)
            return _result(artifact,'PARSED',dict(rows=rows),dict(provider_calendar=True),
                parser='identity_sources.fifa_calendar@'+PARSER_VERSION)
        if request.category=='roster' and request.provider=='OfficialClub':
            from .identity_sources import official_roster
            cid=request.context.get('club_id')
            if cid is None:return _result(artifact,'INCOMPLETE',{}, {},['Provider club context required'])
            records=official_roster(body.decode('utf8'),source,int(cid))
            return _result(artifact,'PARSED' if records else 'INCOMPLETE',dict(records=[r.evidence() for r in records]),dict(roster_extraction=bool(records)),
                parser='identity_sources.official_roster@'+PARSER_VERSION)
        if request.category=='profile':
            from .identity_sources import fotmob,uefa_profile,person_profile
            fn={'FotMob':fotmob,'UEFA':uefa_profile,'OfficialClub':person_profile}.get(request.provider)
            if fn is None:return _result(artifact,'PARSER_NOT_IMPLEMENTED',{}, {},['Profile provider unsupported'])
            if request.provider=='OfficialClub':
                if request.context.get('scope')=='FIRST_TEAM_CARD':
                    from .identity_sources import current_official_profile
                    fn=current_official_profile
                records=fn(body.decode('utf8'),source,request.context.get('club_id'))
            else:records=fn(body.decode('utf8'),source)
            return _result(artifact,'PARSED' if records else 'INCOMPLETE',dict(records=[r.evidence() for r in records]),dict(profile=bool(records)),parser=fn.__module__+'.'+fn.__name__+'@'+PARSER_VERSION)
        if request.category!='match':
            return _result(artifact,'PARSER_NOT_IMPLEMENTED',{}, {},['No registered parser for category'])
        if request.provider=='Sky' and request.context.get('domain')=='NATIONAL' and mime=='text/html':
            from .international_parse import parse_sky
            fixture=request.context.get('fixture') or {}
            asked=request.expected_ids.get('match');url_id=urlsplit(request.target_url).path.rstrip('/').rsplit('/',1)[-1]
            if asked is None:return _result(artifact,'INCOMPLETE',{}, {},['Independent Sky request ID required'])
            if str(asked)!=url_id:return _result(artifact,'IDENTITY_MISMATCH',{}, {},['Sky request URL differs from provider binding'])
            from .live_parse import ParseError
            labels=fixture.get('competition_labels') or [fixture.get('competition_label')]
            for i,label in enumerate(labels):
                try:parsed=parse_sky(body.decode('utf8'),source,dict(fixture,competition_label=label));break
                except ParseError as exc:
                    if str(exc)!='NATIONAL_COMPETITION_UNVERIFIED' or i==len(labels)-1:raise
            parsed['provider_fixture_id']=url_id;parsed['identity_basis']='REQUEST_URL_AND_VERIFIED_ORDERED_FIXTURE_TUPLE;NO_RETURNED_NATIVE_ID'
            return _result(artifact,'PARSED',parsed,dict(source_identity=True,returned_provider_id=False,returned_fixture_tuple=True),
                ['Canonical identity remains downstream; no returned native fixture ID claimed'],
                'international_parse.parse_sky@'+PARSER_VERSION)
        payload=None
        if mime=='application/json':payload=json.loads(body)
        # Saved web-tool extractions remain a different evidence class from HTTP HTML.
        if isinstance(payload,list):
            entries=[x for x in payload if isinstance(x,dict) and x.get('source_url')==request.url]
            if len(entries)!=1:return _result(artifact,'AMBIGUOUS',{}, {},['Exact extract source URL not unique'])
            item=entries[0];text=item.get('source_lines')
            if not isinstance(text,str):return _result(artifact,'MALFORMED',{}, {},['Extract text missing'])
            declared=item.get('sha256_of_extract')
            if declared and declared!=sha(text.encode()):
                return _result(artifact,'INTEGRITY_CONFLICT',{}, {},['Extract hash differs from declared value'])
            native=dict(evidence_class='WEB_TOOL_EXTRACT',source_url=item['source_url'],
                excerpt_sha256=sha(text.encode()),provider_fixture_id=None,
                request_timestamp=item.get('request_timestamp'),timestamp_note=item.get('timestamp_note'))
            complete=dict(full_http_response=False,typed_events=False,reported_minutes=False,source_identity=False)
            if request.provider=='Sky':
                from .multi_hop_reviewed import sky_rosters
                rosters=sky_rosters(text);native['rosters']=rosters
                complete['roster_structure']=bool(len(rosters)==2 and all(len(r['starters'])==11 for r in rosters))
            else:
                # Preserve source text as a typed excerpt observation. Historical
                # one-off hand transcriptions are not a reusable native parser.
                native['excerpt']=text;complete['roster_structure']=False
            return _result(artifact,'INCOMPLETE',native,complete,
                ['Retained excerpt is not full native response; canonical/typed minute adjudication still required'],
                'multi_hop_reviewed.sky_rosters@'+PARSER_VERSION if request.provider=='Sky' else 'archived-excerpt@'+PARSER_VERSION)
        if request.provider=='FotMob':
            from .international_discovery import payload as extract_payload
            from .international_fotmob import parse_public_club_match,parse_public_match
            d=extract_payload(body.decode('utf8'));g=d.get('general') or {}
            actual=str(g.get('matchId') or '')
            asked=request.expected_ids.get('match') or returned_match_id(request.target_url)
            if asked is not None and str(asked)!=actual:
                return _result(artifact,'IDENTITY_MISMATCH',dict(requested_id=str(asked),returned_id=actual),{},['Provider requested/returned ID mismatch'])
            context=request.context
            # The returned tuple can be parsed as native data even when no
            # independently retained request identity/canonical candidate exists.
            fixture=dict(provider_match_id=actual,
                home=dict(id=context.get('home_id', (g.get('homeTeam') or {}).get('id'))),
                away=dict(id=context.get('away_id', (g.get('awayTeam') or {}).get('id'))),
                kickoff=context.get('kickoff') or g.get('matchTimeUTCDate'),
                competition_key=str(context.get('competition_id') or g.get('parentLeagueId') or g.get('leagueId')))
            domain=context.get('domain','CLUB')
            if domain=='NATIONAL':
                if not context.get('level'):return _result(artifact,'INCOMPLETE',{}, {},['National team level context required'])
                parsed=parse_public_match(body.decode('utf8'),source,dict(fixture,level=context['level']),context.get('history_claims'))
            elif domain=='CLUB':parsed=parse_public_club_match(body.decode('utf8'),source,fixture,context.get('history_claims'))
            else:return _result(artifact,'INCOMPLETE',{}, {},['Unsupported match domain'])
            parsed.pop('source',None)
            parsed.update(provider_competition_id=str(g.get('leagueId')),
                provider_parent_competition_id=str(g['parentLeagueId']) if g.get('parentLeagueId') is not None else None,
                season_id=g.get('seasonId'),season=g.get('seasonName'),requested_id=asked,
                requested_identity='MATCHED' if asked is not None else 'UNVERIFIED',
                identity_basis='REQUEST_AND_RETURNED_PROVIDER_ID' if asked is not None else 'RETURNED_SOURCE_ONLY')
            expected_season=request.expected_ids.get('season')
            if expected_season is not None and str(expected_season)!=str(g.get('seasonId')):
                return _result(artifact,'IDENTITY_MISMATCH',parsed,{},['Provider season differs'])
            uncertainty=[] if asked is not None else ['Independent request/provider ID unavailable; no canonical acceptance']
            unknown=sum(p['minutes'] is None or p['participation_status']=='BENCH_PARTICIPATION_UNKNOWN' for s in parsed['sheets'] for p in s['players'])
            if unknown:uncertainty.append('Unknown minutes or bench participation retained')
            return _result(artifact,'PARSED' if not uncertainty else 'INCOMPLETE',parsed,
                dict(source_identity=asked is not None,squad=not parsed['identity_issues'],applicable_minutes=unknown==0),uncertainty,
                ('international_fotmob.parse_public_match@' if domain=='NATIONAL' else 'international_fotmob.parse_public_club_match@')+PARSER_VERSION)
        if request.provider in {'Torlytics','Sky'} and mime=='text/html':
            from .live_parse import torly_detail,sky_detail
            from .sky_german import parse as german_sky
            parser=torly_detail if request.provider=='Torlytics' else german_sky if urlsplit(request.url).hostname=='sport.sky.de' else sky_detail
            returned_url=None
            if request.provider=='Torlytics':
                from .identity_sources import jsonld
                events=[x for x in jsonld(body.decode('utf8')) if x.get('@type')=='SportsEvent']
                if len(events)!=1:return _result(artifact,'AMBIGUOUS',{}, {},['One SportsEvent required'])
                returned_url=events[0].get('url')
                if returned_url and returned_url.rstrip('/')!=request.target_url.rstrip('/'):
                    return _result(artifact,'IDENTITY_MISMATCH',dict(requested_url=request.target_url,returned_url=returned_url),{},['SportsEvent resource URL mismatch'])
            fixture,sheets=parser(body.decode('utf8'),source)
            directory_identity=False
            if request.provider=='Sky':
                candidate=artifact.metadata.get('catalog_candidate') or {};directory=candidate.get('catalog_source') or {}
                asked=request.expected_ids.get('match');url_id=urlsplit(request.target_url).path.rstrip('/').rsplit('/',1)[-1]
                if asked is not None and str(asked)!=url_id:return _result(artifact,'IDENTITY_MISMATCH',{}, {},['Sky request/provider binding differs'])
                directory_identity=bool(asked is not None and str(candidate.get('catalog_provider_id'))==str(asked) and directory.get('sha256') and directory.get('url'))
                directory_identity=directory_identity or bool(asked is not None and request.context.get('accepted_source_url')==request.target_url and str(asked)==url_id)
            fixture.pop('match_id',None)
            mapping=dict(home_club_id=fixture.pop('home_id',None),away_club_id=fixture.pop('away_id',None),
                competition_id=fixture.pop('competition_id',None))
            for sheet in sheets:
                sheet.pop('match_id',None)
                if 'club_id' in sheet:sheet['configured_club_id']=sheet.pop('club_id')
            # Legacy label/config mapping is parser context, not canonical identity.
            fixture['native_source_team_labels']=fixture.get('source_team_names')
            issues=[i for s in sheets for i in s.get('issues',[])]
            if not returned_url and not directory_identity:issues.append('Returned resource identity unavailable from legacy parser')
            native=dict(fixture=fixture,sheets=sheets,provider_fixture_id=returned_url or request.expected_ids.get('match'),requested_resource=request.target_url,returned_resource=returned_url,
                canonical_mapping='NOT_ADJUDICATED;LEGACY_TEAM_LABEL_ADAPTER',legacy_mapping_context=mapping)
            if request.provider=='Sky':native['identity_basis']='REQUEST_URL_AND_ACCEPTED_FIXTURE_TUPLE;NO_RETURNED_NATIVE_ID'
            return _result(artifact,'INCOMPLETE' if issues else 'PARSED',native,
                dict(source_identity=bool(returned_url) or directory_identity,returned_provider_id=False if request.provider=='Sky' else bool(returned_url),squad=all(s['complete_squad'] for s in sheets),events=all(s['complete_events'] for s in sheets),
                     reported_minutes=all(p.get('reported_minutes') is not None for s in sheets for p in s['players'])),
                issues,parser.__module__+'.'+parser.__name__+'@'+PARSER_VERSION)
        return _result(artifact,'PARSER_NOT_IMPLEMENTED',dict(content_type=mime),{},['No matching provider/format adapter'])
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        from .live_parse import Pending
        outcome='INCOMPLETE' if isinstance(exc,Pending) else 'IDENTITY_MISMATCH' if any(k in str(exc).upper() for k in ['IDENTITY','MATCH_ID','TEAM_ID','TEAM_LEVEL','KICKOFF','COMPETITION_ID']) else 'MALFORMED'
        return _result(artifact,outcome,{}, {},[type(exc).__name__+':'+str(exc)])
