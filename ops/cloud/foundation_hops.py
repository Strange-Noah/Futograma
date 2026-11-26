"""Plan alternative public source routes from verified historical entities."""

from collections import defaultdict
from contextlib import closing
import re
import sqlite3
from urllib.parse import urlsplit

from ops.cloud.historical_batch import unit_window
from squad_pipeline.source_interface import SourceRequest


def plan(candidate, rows):
    """Group coverage obligations by verified provider entity, across units."""
    from squad_pipeline.six_club_validation import CLUBS

    windows = [(f"{r['club_id']}:{r['unit']}", unit_window(r))
               for r in rows if not r['source_ready'] and not r['data_accepted']]
    anchors = defaultdict(set)
    direct = defaultdict(set)
    profiles = defaultdict(set)
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as db:
        fixtures = db.execute("""
            SELECT f.match_id, f.home_key, f.away_key, m.local_date
            FROM canonical_fixtures f JOIN matches m USING(match_id)
            WHERE f.domain='CLUB'
            ORDER BY m.local_date, f.match_id
        """).fetchall()
        teams = defaultdict(set)
        for key, native in db.execute("""
            SELECT team_key,provider_team_id FROM football_team_provider_ids
            WHERE provider='FotMob' AND evidence_id IS NOT NULL
            UNION
            SELECT entity_id,provider_id FROM canonical_provider_entities
            WHERE provider='FotMob' AND lower(kind) IN ('team','club')
        """):
            if re.fullmatch(r'[0-9]+', str(native)):
                teams[key].add(str(native))
        # The established window planner has FotMob identities for the tracked
        # clubs even when the historical team registry only has national rows.
        for club_id in CLUBS:
            teams[f'club:{club_id}'].add(str(club_id))
        coverage = {(club, match): (starts, unknown) for club, match, starts, unknown in db.execute("""
            SELECT club_id,match_id,sum(participation_status='STARTER'),
                sum(minutes IS NULL AND participation_status IN ('STARTER','SUBSTITUTE_USED'))
            FROM player_match_minutes GROUP BY club_id,match_id
        """)}
        complete = set(db.execute("""
            SELECT DISTINCT o.club_id,o.match_id FROM squad_observations o
            JOIN canonical_fixture_sources s ON s.match_id=o.match_id AND s.domain='CLUB'
            WHERE json_extract(s.evidence_json,'$.complete_squad')=1
        """))
        bound_matches = defaultdict(list)
        for mid, native, url in db.execute("""
            SELECT match_id,provider_match_id,json_extract(evidence_json,'$.url')
            FROM canonical_fixture_sources WHERE domain='CLUB' AND provider='FotMob'
        """):
            if url and urlsplit(url).hostname == 'www.fotmob.com':
                bound_matches[mid].append((str(native), url))
        players = defaultdict(set)
        for club, match, native in db.execute("""
            SELECT p.club_id,p.match_id,l.provider_player_id
            FROM player_match_minutes p
            JOIN verified_identity_links l USING(player_id)
            WHERE p.minutes IS NULL AND p.participation_status IN ('STARTER','SUBSTITUTE_USED')
              AND l.provider='FotMob'
              AND (SELECT count(*) FROM verified_identity_links x
                   WHERE x.provider=l.provider AND x.player_id=l.player_id)=1
        """):
            if re.fullmatch(r'[0-9]+', str(native)):
                players[club, match].add(str(native))
        for key, unit in windows:
            club = unit['club_id']
            start, end = unit['start'], unit['end_exclusive']
            for mid, home, away, on in fixtures:
                if not on or not start <= on < end or f'club:{club}' not in (home, away):
                    continue
                starts, unknown = coverage.get((club, mid), (0, 0))
                if starts == 11 and not unknown and (club, mid) in complete:
                    continue
                for native in players[club, mid]:
                    profiles[native].add((key, mid, on))
                for native, url in bound_matches[mid]:
                    direct[native, url].add((key, mid, on))
                for side in (home, away):
                    if len(teams[side]) == 1:
                        anchors[next(iter(teams[side]))].add((key, mid, on))

    def targets(values):
        return [dict(unit=key, match=mid, date=on) for key, mid, on in sorted(values)]

    tasks = []
    for (native, url), values in sorted(direct.items()):
        tasks.append((SourceRequest('FotMob', 'match', url, 'Exact bound alternate fixture',
                                    expected_ids={'match': native}, context={'domain': 'CLUB'},
                                    policy='LIVE_ALLOWED'), targets(values)))
    for native, values in sorted(anchors.items()):
        dates = [value[2] for value in values]
        tasks.append((SourceRequest(
            'FotMob', 'team_history', f'https://www.fotmob.com/teams/{native}/fixtures',
            'Verified team or opponent fixture discovery',
            expected_ids={'team': native},
            context={'level': 'CLUB', 'start': min(dates), 'cutoff': max(dates)},
            policy='LIVE_ALLOWED'), targets(values)))
    for native, values in sorted(profiles.items()):
        dates = [value[2] for value in values]
        tasks.append((SourceRequest(
            'FotMob', 'player_history', f'https://www.fotmob.com/players/{native}',
            'Verified profile entry for unknown minutes',
            expected_ids={'player': native},
            context={'include_club': True, 'start': min(dates), 'cutoff': max(dates)},
            policy='LIVE_ALLOWED'), targets(values)))
    return tasks


def bind_retained_hints(candidate, observations):
    """Accept a hop only when the retained itinerary matches an exact fixture tuple."""
    from collections import Counter
    from datetime import datetime, timezone

    def instant(value):
        try:
            stamp = datetime.fromisoformat(value)
            return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
        except (TypeError, ValueError):
            return None

    rejected = Counter()
    checked = {}
    routes = {}
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as db:
        for record in observations:
            observation = record.get('observation') or {}
            if observation.get('outcome') != 'PARSED' or not record.get('artifact'):
                continue
            by_day = defaultdict(list)
            for obligation in record['obligations']:
                by_day[obligation['date']].append(obligation)
            for hint in observation['native'].get('matches', []):
                if hint.get('domain', 'CLUB') != 'CLUB':
                    continue
                for obligation in by_day[hint['kickoff'][:10]]:
                    mid = obligation['match']
                    match = checked.get(mid)
                    if match is None:
                        match = db.execute("""
                            SELECT m.home_id,m.away_id,m.kickoff,f.competition_key,m.competition_id
                            FROM matches m JOIN canonical_fixtures f USING(match_id)
                            WHERE m.match_id=? AND f.domain='CLUB'
                        """, (mid,)).fetchone()
                        checked[mid] = match
                    if not match or not instant(match[2]) or instant(hint['kickoff']) != instant(match[2]):
                        rejected['KICKOFF_NOT_BOUND'] += 1
                        continue
                    if tuple(map(str, match[:2])) != (str(hint['home']['id']), str(hint['away']['id'])):
                        rejected['ORDERED_TEAMS_NOT_BOUND'] += 1
                        continue
                    competition = db.execute("""
                        SELECT 1 FROM canonical_provider_entities
                        WHERE lower(kind)='competition' AND provider='FotMob'
                        AND provider_id=? AND entity_id=?
                    """, (hint['competition_key'], match[3])).fetchone()
                    from squad_pipeline.competition_identity import COMP
                    agreed_label = COMP.get(hint.get('competition_label'))
                    known_league = (agreed_label is not None
                                    and str(agreed_label) == str(match[4]) == str(hint['competition_key']))
                    if not competition and not known_league:
                        rejected['COMPETITION_NOT_BOUND'] += 1
                        continue
                    url = urlsplit(hint['url'])
                    native = str(hint['provider_match_id'])
                    if (url.scheme != 'https' or url.netloc != 'www.fotmob.com'
                            or not re.fullmatch(r'[0-9]+', native) or not url.path.startswith('/matches/')):
                        rejected['PUBLIC_MATCH_ROUTE_NOT_BOUND'] += 1
                        continue
                    key = mid, native, hint['url']
                    result = routes.setdefault(key, dict(match=mid, provider_match_id=native,
                        url=hint['url'], context=dict(home_id=hint['home']['id'],
                        away_id=hint['away']['id'], kickoff=hint['kickoff'],
                        competition_id=hint['competition_key'], domain='CLUB'), units=set()))
                    result['units'].add(obligation['unit'])
    output = [dict(result, units=sorted(result['units'])) for _, result in sorted(routes.items(), key=lambda row: str(row[0]))]
    candidates = defaultdict(set)
    for row in output:
        candidates[row['match']].add(row['provider_match_id'])
    rejected['AMBIGUOUS_MATCH_BINDINGS'] += sum(len(v) > 1 for v in candidates.values())
    return [row for row in output if len(candidates[row['match']]) == 1], dict(rejected)


def recover_bound_minutes(candidate, observations, work, retained_work=None, *, in_place=False):
    """Fetch only exact hops and pass existing unknown minutes through the canonical writer."""
    from collections import Counter
    from dataclasses import asdict
    import shutil

    from ops.cloud.obligation_execution import adjudicate_packets
    from ops.cloud.historical_unit import artifact as load_artifact
    import json
    from squad_pipeline.minute_policy import select
    from squad_pipeline.source_interface import EvidenceStore, SourceService

    routes, held = bind_retained_hints(candidate, observations)
    work.mkdir(parents=True, exist_ok=False)
    outcomes = Counter()
    if not routes:
        return dict(routes=0, held=held, outcomes={})
    evidence = EvidenceStore(work / 'sources')
    saved = {}
    if retained_work:
        prior = EvidenceStore(retained_work / 'sources')
        saved = {item['route']['url']: item['artifact'] for item in
                 json.loads((retained_work / 'bound-replay.json').read_text())['captures']
                 if item['outcome'] == 'FOUND' and item.get('artifact')}

    service = SourceService(evidence, 'historical-bound-fixture-hops', {'www.fotmob.com'},
                            max_calls=min(24, len(routes) * 2), max_bytes=32_000_000)
    if in_place and retained_work is None:
        raise ValueError('IN_PLACE_REPLAY_REQUIRES_VERIFIED_RETAINED_INPUT')
    target = candidate if in_place else work / 'candidate.sqlite'
    if not in_place:
        shutil.copyfile(candidate, target)
    captures = []
    with closing(sqlite3.connect(target)) as db:
        db.row_factory = sqlite3.Row
        packets = []
        for route in routes:
            source_request = SourceRequest('FotMob', 'match', route['url'],
                                           'Exact historical fixture from retained player history',
                                           expected_ids={'match': route['provider_match_id']},
                                           context=route['context'], policy='LIVE_ALLOWED')
            if route['url'] in saved:
                raw = saved[route['url']]
                earlier = load_artifact(dict(raw, request=dict(raw['request'], policy='ARCHIVE_ONLY')))
                if earlier.request.key != source_request.key:
                    raise ValueError('RETAINED_MATCH_REQUEST_BINDING_MISMATCH')
                retained = evidence.import_body(source_request, prior.read(earlier),
                                                earlier.metadata, earlier.authority)
                acquired, observation = service.observe(source_request, retained)
            elif in_place:
                raise ValueError('IN_PLACE_REPLAY_CANNOT_ACQUIRE_NEW_EVIDENCE')
            else:
                acquired, observation = service.observe(source_request)
            outcomes[acquired.outcome] += 1
            captures.append(dict(route=route, requested=asdict(source_request),
                                 outcome=acquired.outcome, reason=acquired.reason,
                                 artifact=asdict(acquired.artifact) if acquired.artifact else None,
                                 observation=asdict(observation) if observation else None))
            if acquired.outcome in {'ACCESS_RESTRICTED', 'ROBOTS_DISALLOWED', 'RATE_LIMITED', 'BUDGET_EXHAUSTED'}:
                break
            if (not observation or observation.outcome not in {'PARSED', 'INCOMPLETE'}
                    or not observation.completeness.get('source_identity')
                    or not observation.completeness.get('squad')):
                outcomes['DETAIL_IDENTITY_NOT_VERIFIED'] += 1
                continue
            match = route['match']
            raw = acquired.artifact
            match_date = db.execute('SELECT local_date FROM matches WHERE match_id=?', (match,)).fetchone()[0]
            for sheet in observation.native.get('sheets', []):
                team = str(sheet.get('provider_team_id'))
                if team not in {k.split(':', 1)[0] for k in route['units']}:
                    continue
                for player in sheet['players']:
                    native = str(player.get('provider_player_id'))
                    existing = db.execute("""
                        SELECT p.player_id,p.participation_status FROM player_match_minutes p
                        JOIN verified_identity_links l USING(player_id)
                        WHERE p.match_id=? AND p.club_id=? AND p.minutes IS NULL
                          AND l.provider='FotMob' AND l.provider_player_id=?
                    """, (match, team, native)).fetchall()
                    status = player.get('participation_status')
                    if len(existing) != 1 or status not in {'STARTER', 'SUBSTITUTE_USED'}:
                        continue
                    if existing[0]['participation_status'] != status:
                        outcomes['MATERIAL_ROLE_CONFLICT'] += 1
                        continue
                    selected = select([
                        dict(status=status, minutes=player.get('reported_minutes'), provider='FotMob'),
                        dict(status=status, minutes=player.get('derived_minutes'), provider='DERIVED_CLOCK'),
                    ])
                    if not selected['accepted'] or type(selected['minutes']) is not int:
                        continue
                    typed = dict(player, provider_key='fotmob:' + native, minutes=selected['minutes'])
                    metadata = dict(provider='FotMob', url=source_request.target_url,
                                    sha256=raw.content_sha256, path='artifact:' + raw.key,
                                    artifact_key=raw.key, storage_locator=raw.locator,
                                    response_timestamp=raw.metadata.get('response_timestamp'))
                    packets.append(dict(domain='CLUB', match_id=match, team=team,
                                        managing_club_id=int(team), player_id=existing[0]['player_id'],
                                        player=typed, source=metadata, artifact=asdict(raw), on=match_date))
        decisions = []
        from itertools import groupby
        for on, group in groupby(sorted(packets, key=lambda item: item['on']), key=lambda item: item['on']):
            decisions.extend(adjudicate_packets(db, list(group), service, on))
        outcomes.update(item.get('result', item)['state'] for item in decisions)
    from ops.cloud.historical_batch import write
    write(work / 'bound-replay.json', dict(captures=captures, decisions=decisions))
    return dict(routes=len(routes), held=held, outcomes=dict(outcomes))
