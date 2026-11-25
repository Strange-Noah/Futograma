"""Run and atomically publish an isolated historical continuation."""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import resource
import time
from collections import Counter
from dataclasses import asdict, replace
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from squad_pipeline.coldstart_national import offlineapply
from squad_pipeline.roster_policy import roster_decision
from ops.cloud.historical_batch import continue_batches, discover_gaps, refresh_matrix, unit_window, write
from ops.cloud.obligation_execution import adjudicate_packets
from ops.cloud.verify_historical_handoff import relative_path, relocate_json, verify
from squad_pipeline.cloud.export import export
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import inspect, pack, sha
from squad_pipeline.cloud.storage import StaleWriter
from squad_pipeline.core import ROOT
from squad_pipeline.international_store import digest
from squad_pipeline.roster_initialization import PeriodUnit
from squad_pipeline.reconstruction.calendar import fifa_windows
from squad_pipeline.reconstruction.engine import run
from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
from squad_pipeline.source_interface import EvidenceStore, SourceRequest, SourceService


def read(path):
    return json.loads(path.read_text())


def download(store, record, target):
    store.download(record['id'], target)
    if target.stat().st_size != record['size'] or sha(target) != record['sha256']:
        raise ValueError('HISTORICAL_ASSET_DIGEST_MISMATCH')
    return target


class DeliveryReceipt:
    """Append small phase records to the base historical release, outside the CAS pointer."""

    def __init__(self, store, release_id, root, request, archive_bytes):
        self.store = store
        self.release_id = release_id
        self.root = root
        self.run_id = int(os.environ['GITHUB_RUN_ID'])
        self.start = time.monotonic()
        self.number = 0
        self.unit = request['unit']
        self.archive_bytes = archive_bytes

    def can_start_batch(self, snapshot):
        # Two compressed evidence copies coexist during publish/readback.
        # One latest candidate and one readback can also coexist during verification.
        snapshot_size = snapshot.stat().st_size
        publish_reserve = 2 * self.archive_bytes + 2 * snapshot_size + 384 * 1024**2
        worker_reserve = 4 * snapshot_size + 256 * 1024**2
        return shutil.disk_usage(self.root).free >= max(publish_reserve, worker_reserve)

    def __call__(self, stage, **counts):
        self.number += 1
        result = dict(
            run=self.run_id, stage=stage, unit=self.unit, sequence=self.number,
            elapsed_seconds=round(time.monotonic()-self.start, 2),
            process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            child_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            free_bytes=shutil.disk_usage(self.root).free, **counts)
        name = f'historical-stage-{self.run_id}-{self.number:04d}-{stage}.json'
        path = self.root / name
        write(path, result)
        asset = self.store.upload(self.release_id, path)
        label = f"{result['elapsed_seconds']}s freeMiB={result['free_bytes']//1048576} rssKiB={result['process_peak_rss_kib']} childKiB={result['child_peak_rss_kib']}"
        for key in ('round', 'batch', 'worker', 'tasks', 'attempts', 'replays', 'outcome',
                    'official_windows', 'parser', 'mapped', 'unmapped', 'source_work_required', 'frozen', 'plan'):
            if key in counts:
                label += f' {key}={counts[key]}'
        self.store.api('PATCH', self.store.base + '/releases/assets/' + str(asset['id']),
                       dict(label=label[:250]))
        print('HISTORICAL_STAGE=' + json.dumps(result, sort_keys=True), flush=True)


def overlay_retained_batch(store, handoff, staging, temp):
    """Adopt a verified legacy delta exactly once, before running the current code."""
    delta = handoff.get('latest_evidence_checkpoint', {})
    if (delta.get('original_database_sha256') != handoff['database_sha256']
            or handoff.get('incorporated_evidence_checkpoint') == delta.get('release')):
        return False
    assets = delta['assets']
    receipt = read(download(store, assets['receipt.json'], temp / 'delta-receipt.json'))
    archive = download(store, assets['historical-batch-delta.tar.gz'], temp / 'delta.tar.gz')
    prefix = relative_path(receipt['batch'])
    if (staging / prefix).exists():
        raise ValueError('HISTORICAL_DELTA_BATCH_EXISTS')
    seen = set()
    with tarfile.open(archive, 'r|gz') as stream:
        for member in stream:
            path = relative_path(member.name)
            if not path.is_relative_to(prefix) or path in seen or not (member.isfile() or member.isdir()):
                raise ValueError('UNSAFE_HISTORICAL_DELTA_MEMBER')
            seen.add(path)
            stream.extract(member, staging, filter='data')
    checkpoint = staging / relative_path(handoff['checkpoint'])
    matrix = staging / relative_path(handoff['matrix'])
    for key, target in [('continuation-private.json', checkpoint), ('matrix-private.json', matrix)]:
        verified = download(store, assets[key], temp / key)
        shutil.copyfile(verified, target)
    old = receipt['original_workspace']
    for path in [checkpoint, matrix, *(staging / prefix).rglob('*.json')]:
        relocate_json(path, old, staging)
    candidate = Path(read(checkpoint)['candidate'])
    if not candidate.resolve().is_relative_to((staging / prefix).resolve()) or sha(candidate) != delta['working_candidate_sha256']:
        raise ValueError('HISTORICAL_DELTA_CANDIDATE_MISMATCH')
    return True


def stage(store, handoff, source, temp):
    directory = temp / 'restore'
    verified = verify(store, handoff, directory, source)
    applied = overlay_retained_batch(store, handoff, directory, temp)
    target = ROOT / '.cloud'
    if target.exists():
        raise ValueError('EXPECTED_DISPOSABLE_EXECUTION_ROOT')
    for path in (directory / '.cloud').rglob('*.json'):
        relocate_json(path, str(directory), ROOT)
    shutil.move(directory / '.cloud', target)
    # The original verified assets remain in the private Release. The downloaded
    # copies and verification export have no downstream consumers in this runner.
    shutil.rmtree(directory)
    workers = ROOT / 'ops/cloud/historical_continuation_workers'
    for source in workers.rglob('*.py'):
        dest = target / source.relative_to(workers)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    return verified, applied


def retained_store(work):
    root = ROOT / '.cloud'
    index, records = {}, []
    for path in sorted(root.rglob('*.response')):
        if re.fullmatch(r'[a-f0-9]{64}', path.stem):
            index.setdefault(path.stem, []).append(str(path))
    for path in sorted(root.rglob('results-private.json')):
        rows = read(path)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get('outcome') != 'FOUND' or not row.get('artifact'):
                continue
            record = row['artifact']
            if not {'locator', 'request', 'metadata', 'content_sha256'} <= record.keys():
                continue
            locator = Path(record['locator'])
            if not locator.is_absolute():
                locator = path.parent / locator
            records.append(dict(record['metadata'], url=row['url'], sha256=record['content_sha256'],
                                path=str(locator), provider=record['request']['provider']))
    for path in sorted(root.glob('**/national-match-sources/artifacts/*.json')):
        entry = read(path)
        detail = entry['request']
        locator = path.parent.parent / entry['locator']
        records.append(dict(entry['metadata'], url=detail['url'], sha256=entry['content_sha256'],
                            path=str(locator), provider=detail['provider']))
    return EvidenceStore(work / 'audit-sources', archives=[root], index=index, records=records)


def coverage_projection(db, unit, queries, aliases=None):
    aliases = aliases or {}
    results = []
    for query in queries:
        on, (home, away) = query['date'], query['teams']
        if not unit['start'] <= datetime.date.fromisoformat(on).isoformat() < unit['end_exclusive']:
            raise ValueError('COVERAGE_QUERY_OUTSIDE_SELECTED_UNIT')
        fixtures = db.execute("""
            SELECT m.match_id,m.duration_minutes,m.kickoff_utc FROM international_matches m
            JOIN football_teams h ON h.team_key=m.home_team_key
            JOIN football_teams a ON a.team_key=m.away_team_key
            WHERE lower(h.display_name) IN (lower(?), lower(?))
              AND lower(a.display_name) IN (lower(?), lower(?)) AND m.madrid_date=?
            """, (home, aliases.get(home, home), away, aliases.get(away, away), on)).fetchall()
        coverage = dict(date=on,teams=[home,away], fixture_count=len(fixtures),players={})
        for player in query['players']:
            params = [unit['club_id'], unit['start'], unit['end_exclusive']]
            if isinstance(player, str):
                match = 'lower(p.display_name)=lower(?)'
                params.append(player)
                label = player
            else:
                label = player['label']
                match = """EXISTS (SELECT 1 FROM verified_identity_links i WHERE i.player_id=p.player_id
                                   AND i.provider=? AND i.provider_player_id=?
                                   AND json_extract(i.evidence_json,'$.identity_status')='VERIFIED')"""
                params.extend([player['provider'], player['provider_id']])
            ids = db.execute('''SELECT DISTINCT p.player_id FROM players p JOIN player_match_minutes a USING(player_id)
                                JOIN matches m USING(match_id) WHERE a.club_id=? AND m.local_date>=?
                                AND m.local_date<? AND ''' + match, params).fetchall()
            appearance = None
            status = ('FIXTURE_MISSING' if not fixtures else 'FIXTURE_CONFLICT' if len(fixtures)>1 else
                      'ROSTER_IDENTITY_NOT_UNIQUE' if len(ids)!=1 else 'NO_APPEARANCE_ROW')
            if len(ids)==1 and len(fixtures)==1:
                appearance = db.execute('SELECT participation_status,minutes FROM international_appearances '
                    'WHERE match_id=? AND player_id=?',(fixtures[0][0],ids[0][0])).fetchone()
                if appearance:
                    status = 'EXISTING_NUMERIC_FACT' if appearance[1] is not None else 'MINUTES_UNKNOWN'
            coverage['players'][label] = dict(status=status, fact=list(appearance) if appearance else None)
        if len(fixtures)==1:
            coverage.update(duration=fixtures[0][1],kickoff=fixtures[0][2])
        results.append(coverage)
    return results


def audit_followup(db, audit):
    """Share exact fixture acquisition across independent R3/R4 obligations."""
    opportunities = {(item['domain'], item['canonical']): item['on']
                     for item in audit['final_opportunities']}
    groups = {}
    for claim in audit['resolutions']:
        if claim['state'] == 'RESOLVED':
            continue
        for match in claim.get('opportunities', ()) if claim['trigger'] == 'R3' else (claim['canonical'],):
            domain = claim['domain']
            group = groups.setdefault((domain, match), set())
            group.add(claim['claim_key'])

    tasks = {}
    unmapped = []
    for (domain, match), claims in sorted(groups.items()):
        if domain == 'CLUB':
            sources = db.execute("""
                SELECT provider_match_id, json_extract(evidence_json, '$.url')
                FROM canonical_fixture_sources
                WHERE domain='CLUB' AND match_id=? AND provider='Sky'
            """, (match,))
        else:
            sources = db.execute("""
                SELECT ids.provider_match_id, sources.source_url
                FROM international_match_provider_ids ids
                JOIN persistent_source_evidence sources USING(evidence_id)
                WHERE ids.match_id=? AND ids.provider='Sky'
            """, (match,))
        on = opportunities.get((domain, match))
        found = False
        for native, url in sources:
            if (not on or not isinstance(url, str)
                    or not re.fullmatch(r'https://www\.skysports\.com/football/[a-z0-9-]+/teams/[0-9]+', url)
                    or urlsplit(url).path.rsplit('/', 1)[-1] != str(native)):
                continue
            found = True
            record = tasks.setdefault(str(native), dict(url=url, date=on,
                worker='club' if domain == 'CLUB' else 'national',
                claims=set(), fixtures=set()))
            record['claims'].update(claims)
            record['fixtures'].add((domain, match))
        if not found:
            unmapped.append(dict(domain=domain, match=match, on=on, claims=sorted(claims)))
    requests = [
        dict(item, claims=sorted(item['claims']), fixtures=sorted(item['fixtures']))
        for _, item in sorted(tasks.items())
    ]
    return dict(requests=requests, unmapped=unmapped, exact_fixture_groups=len(groups))


def collect_club_audit_evidence(work, tasks):
    """Capture exact existing club fixtures without rebuilding the candidate per request."""
    directory = work / 'club-audit-sources'
    prior = retained_store(work)
    unavailable = {item['url'] for path in (ROOT / '.cloud').glob('**/club-audit-sources/results-private.json')
                   for item in read(path) if item['outcome'] in {'UNAVAILABLE', 'REDIRECT_REQUIRES_REVIEW'}}
    capture = EvidenceStore(directory)
    service = SourceService(capture, 'historical-club-audit', {'www.skysports.com'},
                            max_calls=2 * len(tasks), max_bytes=12000000 * len(tasks))
    results = []
    requested = set()
    for task in tasks:
        url = task['url']
        request = SourceRequest('Sky', 'match', url, 'Known canonical club audit fixture',
                                expected_ids={'match': url.rsplit('/', 1)[-1]}, policy='LIVE_ALLOWED')
        if url in unavailable or prior.lookup(request).outcome == 'FOUND':
            continue
        requested.add(url)
        response = service.acquire(request)
        results.append(dict(url=url, outcome=response.outcome, reason=response.reason,
                            claims=task['claims'], artifact=asdict(response.artifact) if response.artifact else None))
        directory.mkdir(parents=True, exist_ok=True)
        write(directory / 'results-private.json', results)
        if response.outcome in {'ACCESS_RESTRICTED', 'ROBOTS_DISALLOWED', 'RATE_LIMITED', 'BUDGET_EXHAUSTED'}:
            break
    return requested, Counter(item['outcome'] for item in results)


def collect_national_directories(candidate, unit, work, reviewed_urls):
    """Resolve available team-history inputs from verified national affiliations."""
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as db:
        teams = db.execute("""
            SELECT DISTINCT ids.provider_team_id, teams.level
            FROM national_affiliation_events affiliations
            JOIN football_teams teams USING(team_key)
            JOIN football_team_provider_ids ids USING(team_key)
            JOIN player_match_minutes players ON players.player_id=affiliations.player_id
            JOIN matches fixtures ON fixtures.match_id=players.match_id
            WHERE ids.provider='FotMob' AND players.club_id=?
              AND fixtures.local_date>=? AND fixtures.local_date<?
            ORDER BY ids.provider_team_id, teams.level
        """, (unit['club_id'], unit['start'], unit['end_exclusive'])).fetchall()
    retained = retained_store(work)
    directory = work / 'national-directory-sources'
    # Cache restricted outcomes by exact URL as well as successful bodies.
    blocked = set()
    for path in (ROOT / '.cloud').glob('**/national-directory-sources/results-private.json'):
        blocked.update(item['url'] for item in read(path)
                       if item['outcome'] in {'ACCESS_RESTRICTED', 'ROBOTS_DISALLOWED', 'REDIRECT_REQUIRES_REVIEW'})
    service = SourceService(EvidenceStore(directory), 'historical-national-directories',
                            {'www.fotmob.com'}, max_calls=2 * len(teams),
                            max_bytes=max(1, len(teams)) * 8000000)
    cutoff = (datetime.date.fromisoformat(unit['end_exclusive'])-datetime.timedelta(days=1)).isoformat()
    responses = []
    for identity, level in teams:
        if not str(identity).isdigit():
            continue
        base_url = f'https://www.fotmob.com/teams/{identity}/fixtures'
        url = reviewed_urls.get(str(identity), base_url)
        if url != base_url and not re.fullmatch(re.escape(base_url) + r'/[a-z0-9-]+', url):
            raise ValueError('EXACT_REVIEWED_NATIONAL_TEAM_URL_REQUIRED')
        request = SourceRequest('FotMob', 'team_history', url, 'Known senior team fixture history',
                                expected_ids=dict(team=str(identity)),
                                context=dict(start=unit['start'], cutoff=cutoff, level=level),
                                policy='LIVE_ALLOWED')
        if url in blocked or retained.lookup(request).outcome == 'FOUND':
            continue
        acquired, parsed = service.observe(request)
        responses.append(dict(url=url, outcome=acquired.outcome, reason=acquired.reason,
                              parser=parsed.outcome if parsed else None,
                              fixtures=len(parsed.native.get('matches', ())) if parsed else 0,
                              artifact=asdict(acquired.artifact) if acquired.artifact else None))
        directory.mkdir(parents=True, exist_ok=True)
        write(directory / 'results-private.json', responses)
        if acquired.outcome in {'ACCESS_RESTRICTED', 'RATE_LIMITED', 'ROBOTS_DISALLOWED', 'BUDGET_EXHAUSTED'}:
            break
    return {item['url'] for item in responses}, Counter(item['outcome'] for item in responses)


def apply_scoped_national_fixtures(candidate, work, scratch, unit, request, receipt):
    """Use typed directory claims and the preserved national writer for explicit open dates."""
    targets = [item for item in request.get('coverage_queries', ())
               if item['date'] in request.get('national_apply_dates', ())]
    if not targets:
        return dict(matches=0, rows=0, requests=0)
    source = retained_store(work)
    archive = SourceService(source, 'historical-national-match-index', set(), max_calls=0)
    cutoff = (datetime.date.fromisoformat(unit['end_exclusive'])-datetime.timedelta(days=1)).isoformat()
    fixtures = {}
    reviewed = set(request.get('national_directory_urls', {}).values())
    for url in sorted(reviewed):
        match = re.fullmatch(r'https://www\.fotmob\.com/teams/([0-9]+)/fixtures/[a-z0-9-]+', url)
        if not match:
            raise ValueError('EXACT_REVIEWED_NATIONAL_TEAM_URL_REQUIRED')
        observed = SourceRequest('FotMob','team_history',url,'reviewed-team-fixtures',
                                 expected_ids=dict(team=match[1]),
                                 context=dict(start=unit['start'],cutoff=cutoff,level='SENIOR'))
        _, observation = archive.observe(observed)
        if not observation or observation.outcome != 'PARSED':
            continue
        for fixture in observation.native['matches']:
            fixtures[fixture['provider_match_id']] = fixture
    transport = SourceService(EvidenceStore(work / 'national-match-sources'), 'historical-exact-national',
                              {'www.fotmob.com'}, max_calls=2 * len(targets), max_bytes=10000000 * len(targets))
    aliases = request.get('national_team_aliases', {})
    def name(label):
        return aliases.get(label, label).casefold()
    metrics = dict(matches=0, rows=0, requests=0, outcomes=[])
    for query in targets:
        on = query['date']
        if not unit['start'] <= on < unit['end_exclusive']:
            raise ValueError('NATIONAL_APPLY_DATE_OUTSIDE_UNIT')
        candidates = [item for item in fixtures.values()
                      if datetime.datetime.fromisoformat(item['kickoff']).astimezone(ZoneInfo('Europe/Madrid')).date().isoformat() == on
                      and [name(item[side]['name']) for side in ('home','away')] == [name(label) for label in query['teams']]]
        if len(candidates) != 1:
            metrics['outcomes'].append(dict(date=on, status='UNIQUE_TYPED_FIXTURE_REQUIRED', candidates=len(candidates)))
            continue
        fixture = candidates[0]
        native = str(fixture['provider_match_id'])
        with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            previous = coverage_projection(db, unit, [query], aliases)[0]
            exact = db.execute("""
                SELECT match_id FROM international_match_provider_ids
                WHERE provider='FotMob' AND provider_match_id=?
            """, (native,)).fetchone()
            if previous['fixture_count'] and not exact:
                metrics['outcomes'].append(dict(date=on, status='EXISTING_FIXTURE_REQUIRES_PROVIDER_BRIDGE'))
                continue
            if all(value['status']=='EXISTING_NUMERIC_FACT' for value in previous['players'].values()):
                continue
            if db.execute("""
                    SELECT 1 FROM international_acquisition_queue
                    WHERE entity_type='MATCH' AND provider='FotMob' AND provider_entity_id=?
                      AND state IN ('QUARANTINED','HUMAN_REVIEW')""", (native,)).fetchone():
                metrics['outcomes'].append(dict(date=on, status='EXISTING_PROVIDER_QUARANTINE'))
                continue
            ids = {}
            for selected in query['players']:
                details = selected if isinstance(selected, dict) else dict(label=selected)
                links = db.execute("""
                    SELECT DISTINCT links.provider_player_id, players.player_id
                    FROM verified_identity_links links JOIN players USING(player_id)
                    WHERE provider='FotMob' AND json_extract(links.evidence_json,'$.identity_status')='VERIFIED'
                      AND (lower(players.display_name)=lower(?) OR links.provider_player_id=?)
                """, (details['label'], str(details.get('provider_id', '')))).fetchall()
                if details.get('provider_id'):
                    links = [row for row in links if str(row[0]) == str(details['provider_id'])]
                if len(links) == 1 and roster_decision(db, unit['club_id'], links[0][1], on)['included']:
                    ids[str(links[0][0])] = links[0][1]
        if not ids:
            metrics['outcomes'].append(dict(date=on, status='NO_VERIFIED_APPLICABLE_PLAYER'))
            continue
        detail = SourceRequest('FotMob','match',fixture['url'],'exact-reviewed-national-match',
                               expected_ids=dict(match=native), policy='LIVE_ALLOWED')
        response = archive.acquire(replace(detail, policy='ARCHIVE_ONLY'))
        if response.outcome in {'NOT_REQUESTED', 'ARCHIVE_LOCATOR_UNRESOLVED'}:
            response = transport.acquire(detail)
            metrics['requests'] += 1
            detail_store = transport.store
        else:
            detail_store = archive.store
        if response.outcome != 'FOUND':
            metrics['outcomes'].append(dict(date=on, status=response.outcome))
            continue
        artifact = response.artifact
        raw = detail_store.read(artifact)
        capture = dict(url=fixture['url'], status='SUCCESS', provider_match_id=native,
                       sha256=artifact.content_sha256, body=raw,
                       path=str(detail_store.root / artifact.locator),
                       fetched_at=artifact.metadata.get('response_timestamp'))
        trial = scratch / 'national-trial.sqlite'
        with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as db, closing(sqlite3.connect(trial)) as copy:
            db.backup(copy)
            copy.row_factory = sqlite3.Row
            result = offlineapply(copy, dict(fixtures={native: fixture}, managing_club_id=unit['club_id']),
                                  [capture], ids, on)
            copy.commit()
        if result['new_player_facts']:
            inspect(trial)
            os.replace(trial, candidate)
            metrics['matches'] += result['new_matches']
            metrics['rows'] += result['new_player_facts']
        else:
            trial.unlink()
        metrics['outcomes'].append(dict(date=on, status='WRITER_CHECKED', result=result))
    write(work / 'national-match-application.json', metrics)
    receipt('national-match-done', attempts=metrics['requests'],
            new_matches=metrics['matches'], new_rows=metrics['rows'])
    return metrics


def runtime_report(candidate, matrix, key, handoff, config, destination, request, receipt, round_number):
    row = next(row for row in matrix['units'] if f'{row["club_id"]}:{row["unit"]}' == key)
    unit = unit_window(row)
    work = destination.parent
    adjudicated = Counter()
    audit_adopted = False
    followup = dict(requests=[], unmapped=[], exact_fixture_groups=0)
    candidate = candidate.resolve()
    begin, end = unit['start'], unit['end_exclusive']
    cutoff = (datetime.date.fromisoformat(end) - datetime.timedelta(days=1)).isoformat()
    _, obligations = discover_gaps(candidate, dict(matrix, units=[row]), config.get('catalogues', ()))
    calendar = None
    if config.get('calendar'):
        calendar = ROOT / relative_path(config['calendar'])
    calendars = []
    calendar_evidence = 'NONE'
    calendar_parse = None
    calendar_issue = None
    if calendar is None and request.get('use_fifa_calendar'):
        url = 'https://inside.fifa.com/tournament-organisation/international-match-calendars'
        local = retained_store(work)
        svc = SourceService(local, 'historical-calendar', {'inside.fifa.com'}, max_calls=2)
        entry = SourceRequest('FIFA','calendar',url,'international-match-calendar',policy='CACHE_FIRST')
        acquired, parsed = svc.observe(replace(entry, policy='ARCHIVE_ONLY'))
        if acquired.outcome in {'NOT_REQUESTED', 'ARCHIVE_LOCATOR_UNRESOLVED'}:
            acquired, parsed = svc.observe(entry)
            if acquired.artifact and acquired.outcome == 'FOUND':
                local.root.mkdir(parents=True, exist_ok=True)
                write(local.root / 'results-private.json', [dict(
                    url=url, outcome='FOUND', artifact=asdict(acquired.artifact))])
        calendar_evidence = acquired.outcome
        calendar_parse = parsed.outcome if parsed else None
        if acquired.artifact and acquired.outcome == 'FOUND':
            stamp = acquired.artifact.metadata.get('response_timestamp') or acquired.artifact.metadata.get('observed_on')
            if stamp:
                try:
                    precision = 'DAY' if len(stamp) == 10 else 'TIMESTAMP'
                    calendars = fifa_windows(local.read(acquired.artifact), url, stamp, begin, cutoff,
                                             time_precision=precision, observation=parsed)
                except ValueError as error:
                    calendar_issue = str(error)
            else:
                calendar_issue = 'SOURCE_OBSERVATION_TIMESTAMP_MISSING'
    if calendar is not None:
        observed = datetime.datetime.fromtimestamp(calendar.stat().st_mtime, datetime.timezone.utc).isoformat()
        calendars = fifa_windows(calendar.read_bytes(),
                    'https://inside.fifa.com/tournament-organisation/international-match-calendars',
                    observed, begin, cutoff)
    receipt('calendar', round=round_number, acquisition=calendar_evidence,
            parser=calendar_parse, official_windows=len(calendars), issue=calendar_issue)
    # Audit operates on a disposable SQLite copy. Its disposition and evidence
    # remain separate from the existing whole-unit acceptance gate.
    audit_copy = work / 'audit-input.sqlite'
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as src, closing(
            sqlite3.connect(audit_copy)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(audit_copy)) as db:
        db.row_factory = sqlite3.Row
        service = SourceService(retained_store(work),
                                'historical-delivery-audit', set(), max_calls=0)
        adapter = RetainedAdapter(db, ROOT / '.cloud', unit['club_id'], calendars, source_service=service)
        try:
            receipt('audit-start', round=round_number, candidate_bytes=candidate.stat().st_size)
            audit = run(db, adapter, begin, cutoff, max_attempts=None)
            receipt('audit-done', round=round_number, **audit['metrics'])
            required = {(finding['domain'],mid,finding['player']) for finding in audit['gaps']
                        for mid in finding.get('opportunities', ())}
            required.update((finding['domain'],finding['canonical'],finding['player'])
                            for finding in audit['opportunities']
                            if finding.get('challenge') or finding['state'] in ('CONFLICT','INSUFFICIENT_COVERAGE'))
            packets = [item for item in adapter.candidate_packets.values()
                       if (item['domain'],item['match_id'],str(item['player_id'])) in required]
            decisions = adjudicate_packets(db, packets, service, cutoff)
            adjudicated.update(item.get('result',item)['state'] for item in decisions)
            write(work / 'adjudication-private.json', decisions)
            audit_adopted = bool(adjudicated['RECORDED_NEW'] or adjudicated['SUPPLEMENTED_UNKNOWN'])
            if audit_adopted:
                audit = run(db, adapter, begin, cutoff, max_attempts=None)
                db.commit()
            write(work / 'audit-private.json', audit)
            followup = audit_followup(db, audit)
            receipt('audit-mapped', round=round_number, mapped=len(followup['requests']),
                    unmapped=len(followup['unmapped']), coverage=adapter.coverage)
            write(work / 'audit-followup-private.json', followup)
            audit_summary = dict(stages=audit['stages'], metrics=audit['metrics'],
                                 retained_calendar_supplied=bool(calendars), retained_source_calls=service.parse_calls,
                                 adapter_coverage=adapter.coverage, official_window_count=len(calendars),
                                 calendar_coverage=audit['calendar_coverage'],
                                 resolution_states=dict(Counter(item.get('state') for item in audit['resolutions'])))
        except (ValueError, OSError, sqlite3.Error) as exc:
            if audit_adopted:
                raise
            audit_summary = dict(status='AUDIT_INPUT_BLOCKER', detail=str(exc))
        projection = coverage_projection(db, unit, request.get('coverage_queries', ()),
                                       request.get('national_team_aliases', {}))
        club = db.execute("""
            SELECT m.competition_id,count(DISTINCT m.match_id),count(*),
                   count(p.minutes),sum(nullif(m.kickoff, '') IS NULL)
            FROM player_match_minutes p JOIN matches m USING(match_id)
            WHERE p.club_id=? AND m.local_date>=? AND m.local_date<?
            GROUP BY m.competition_id ORDER BY m.competition_id
        """, (unit['club_id'], begin, end)).fetchall()
    if audit_adopted:
        inspect(audit_copy)
        candidate = audit_copy
    else:
        audit_copy.unlink()
    checks = config.get('unit_plans', {})
    result = dict(unit=key, whole_unit_plan_present=key in checks, data_accepted=row['data_accepted'],
                  source_ready=row['source_ready'], audit=audit_summary, adjudication=dict(adjudicated),
                  club_coverage=[list(item) for item in club],
                  club_coverage_columns=['competition_id','matches','appearances','numeric_minutes','missing_kickoff_rows'],
                  pending=obligations[key])
    result['coverage_queries'] = projection
    result['calendar_evidence'] = calendar_evidence
    result['calendar_parser'] = calendar_parse
    result['calendar_issue'] = calendar_issue
    closed = handoff.get('latest_evidence_checkpoint', {}).get('official_selection', {})
    if handoff.get('latest_evidence_checkpoint', {}).get('unit') == key:
        result['reused_verified_appearances'] = sum(
            len(records) for fixture in closed.get('verified_existing_appearances', ())
            for records in fixture['players'].values())
    write(destination, result)
    result['audit_followup'] = dict(exact_fixture_groups=followup['exact_fixture_groups'],
                                    mapped_source_requests=len(followup['requests']),
                                    unmapped_fixture_groups=len(followup['unmapped']))
    return result, candidate, followup['requests']


def write_whole_unit_inputs(work, key, candidate, matrix, config, report):
    """Bind known acceptance inputs without replacing the approved acceptance gate."""
    row = next(row for row in matrix['units'] if f'{row["club_id"]}:{row["unit"]}' == key)
    unit = unit_window(row)
    season, half = row['unit'].split('-')
    freeze_key = PeriodUnit(season=season, half=half, **unit).key
    root = ROOT / '.cloud'
    frozen = {}
    candidates = []
    for path in sorted(root.rglob('*.json')):
        if not any(word in path.name.lower() for word in ('freeze', 'frozen', 'union', 'cohort', 'roster', 'population', 'plan', 'acceptance')):
            continue
        doc = read(path)
        if not isinstance(doc, dict):
            continue
        for item in [doc, *(value for value in doc.values() if isinstance(value, dict))]:
            if item.get('unit_key') not in (None, freeze_key, key):
                continue
            bound = item.get('unit')
            if not isinstance(bound, dict) or any(bound.get(field) != value for field, value in unit.items()):
                continue
            fingerprint = item.get('freeze_sha256')
            if (item.get('observed_population_accepted') is True and isinstance(item.get('player_ids'), list)
                    and fingerprint and fingerprint == digest({k: v for k, v in item.items() if k != 'freeze_sha256'})):
                output = path
                if item is not doc:
                    output = work / ('verified-freeze-' + fingerprint + '.json')
                    write(output, item)
                frozen.setdefault(fingerprint, (output, len(item['player_ids'])))
            else:
                candidates.append(dict(path=str(path.relative_to(ROOT)), accepted=item.get('observed_population_accepted'),
                                       has_fingerprint=bool(fingerprint), has_player_ids=isinstance(item.get('player_ids'), list)))
    audit = report['audit']
    open_work = {state: count for state, count in audit.get('resolution_states', {}).items()
                 if state != 'RESOLVED' and count}
    approved_plan = config.get('unit_plans', {}).get(key)
    missing = []
    if len(frozen) != 1:
        missing.append('UNIQUE_VERIFIED_FROZEN_POPULATION')
    if not str(row.get('national_coverage', '')).startswith('PASS / VERIFIED'):
        missing.append('VERIFIED_APPLICABLE_NATIONAL_COVERAGE')
    if not audit.get('stages') or open_work or audit.get('metrics', {}).get('unperformed_paths'):
        missing.append('COMPLETE_R1_R6_DISPOSITION')
    if not approved_plan:
        missing.append('APPROVED_WHOLE_UNIT_ACCEPTANCE_PLAN')
    path = work / 'whole-unit-input-plan.json'
    write(path, dict(
        unit=unit, unit_key=key, source=str(candidate.relative_to(ROOT)),
        source_sha256=sha(candidate),
        frozen_populations=[dict(path=str(path.relative_to(ROOT)), sha256=fingerprint, players=count)
                            for fingerprint, (path, count) in frozen.items()],
        candidate_freeze_records=candidates, national_scope=report['pending'], open_audit_work=open_work,
        approved_plan=approved_plan, missing=missing))
    return dict(path=str(path.relative_to(ROOT)), frozen_population_count=len(frozen),
                frozen_player_counts=sorted(n for _, n in frozen.values()),
                candidate_freeze_record_count=len(candidates),
                approved_plan_present=bool(approved_plan), missing=missing)


def sync_delivery_matrix(matrix, report):
    """Project verified runtime observations without promoting a unit."""
    row = next(row for row in matrix['units']
               if f'{row["club_id"]}:{row["unit"]}' == report['unit'])
    if row['data_accepted']:
        return matrix
    audit = report['audit']
    if set(audit.get('stages', ())) == {'R1', 'R2', 'R5', 'R3_R4', 'R6'}:
        open_count = sum(count for state, count in audit['resolution_states'].items()
                         if state != 'RESOLVED')
        row['R1_R6'] = (
            f'EXECUTED / OPEN: {open_count} unresolved audit obligations'
            if open_count else 'EXECUTED / WHOLE-UNIT ACCEPTANCE PENDING'
        )
        row['audit_execution'] = dict(
            status='EXECUTED', metrics=audit['metrics'],
            resolution_states=audit['resolution_states'], action=report['run'])
    elif audit.get('status') == 'AUDIT_INPUT_BLOCKER':
        row['audit_execution'] = dict(status='INPUT_BLOCKER', action=report['run'])
        if row.get('R1_R6') == 'NOT TESTED / UNKNOWN':
            row['R1_R6'] = 'NOT EXECUTED / INPUT BLOCKER'

    columns = report['club_coverage_columns']
    coverage = [dict(zip(columns, values, strict=True)) for values in report['club_coverage']]
    row['club_competition_coverage'] = coverage
    fixture_count = sum(item['matches'] for item in coverage)
    player_rows = sum(item['appearances'] for item in coverage)
    numeric_minutes = sum(item['numeric_minutes'] for item in coverage)
    missing_kickoff_rows = sum(item['missing_kickoff_rows'] for item in coverage)
    if (fixture_count and fixture_count == row['canonical_matches']
            and player_rows == row['canonical_player_rows']
            and numeric_minutes == player_rows and not missing_kickoff_rows
            and row.get('fixture_catalogue_completion', '').startswith('PASS')):
        row['canonical_scope'] = 'VERIFIED_COMPETITIVE_CATALOGUE;CANONICAL_FIXTURES_HAVE_KICKOFF'
        row['remaining_identity_minutes'] = (
            'All canonical club player-match rows have numeric minutes. '
            'National scope and whole-unit audit disposition remain subject to acceptance.'
        )
    return matrix

class EvidenceArchiveWriter:
    """Split a single compressed tar stream before the GitHub asset size limit."""

    def __init__(self, output, limit=512 * 1024**2):
        self.output = output
        self.limit = limit
        self.parts = []
        self.stream = None
        self.part_size = 0
        self.size = 0
        self.digest = hashlib.sha256()

    def write(self, data):
        self.digest.update(data)
        self.size += len(data)
        data = memoryview(data)
        total = len(data)
        while data:
            if self.stream is None or self.part_size == self.limit:
                self.close()
                path = self.output / f'historical-evidence-part-{len(self.parts)+1:04d}.gz'
                self.parts.append(path)
                self.stream = path.open('xb')
                self.part_size = 0
            take = min(len(data), self.limit - self.part_size)
            self.stream.write(data[:take])
            self.part_size += take
            data = data[take:]
        return total

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None


def build_bundle(candidate, progress, matrix, output):
    files = []
    archive = EvidenceArchiveWriter(output)
    with closing(archive), tarfile.open(fileobj=archive, mode='w|gz', compresslevel=4) as package:
        for path in sorted((ROOT / '.cloud').rglob('*')):
            if path.is_symlink():
                raise ValueError('HISTORICAL_SYMLINK_FORBIDDEN')
            if not path.is_file() or path == candidate or path.name.endswith(('.lock', '.download.gz')):
                continue
            rel = str(path.relative_to(ROOT))
            files.append(dict(path=rel, size=path.stat().st_size, sha256=sha(path)))
            package.add(path, arcname=rel, recursive=False)
    manifest = output / 'historical-manifest.json'
    write(manifest, dict(files=files, original_workspace=str(ROOT),
             candidate=str(candidate.relative_to(ROOT)), candidate_sha256=sha(candidate),
             evidence_archive=dict(size=archive.size, sha256=archive.digest.hexdigest(),
                                   parts=[dict(name=p.name, size=p.stat().st_size, sha256=sha(p))
                                          for p in archive.parts]),
             units=len(matrix['units']), accepted=progress['accepted'], source_ready=progress['source_ready']))
    return manifest, archive.parts


def national_numeric(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return db.execute('SELECT count(minutes) FROM international_appearances').fetchone()[0]


def refresh_retained_unit_inputs(work, matrix, config, candidate, continuation):
    """Rebind signed inputs against the exact audit snapshot without reacquiring evidence."""
    current = sha(candidate)
    rebound = {}
    for row in matrix['units']:
        if row['data_accepted']:
            continue
        key = f'{row["club_id"]}:{row["unit"]}'
        action = row.get('audit_execution', {}).get('action', '')
        run_id = action.rsplit('/', 1)[-1]
        if not run_id.isdigit():
            continue
        tag = key.replace(':', '-').replace('/', '_')
        delivery = continuation / ('delivery-' + run_id)
        candidates = (delivery / tag / 'scope.json', delivery / 'scope.json')
        for path in candidates:
            if not path.is_file():
                continue
            latest = read(path)
            if latest.get('unit') != key:
                continue
            previous = latest.get('whole_unit_input_plan', {}).get('path')
            if not previous:
                continue
            old = ROOT / relative_path(previous)
            if not old.is_file() or read(old).get('source_sha256') != current:
                continue
            output = work / tag
            output.mkdir(parents=True, exist_ok=True)
            status = write_whole_unit_inputs(output, key, candidate, matrix, config, latest)
            row['whole_unit_input_plan'] = status
            rebound[key] = status
            break
    return rebound


def matrix_targets(matrix, config, preferred):
    plans = config.get('unit_plans', {})
    ranked = []
    for row in matrix['units']:
        key = f'{row["club_id"]}:{row["unit"]}'
        if row['data_accepted']:
            continue
        known = row.get('audit_execution', {}).get('resolution_states', {})
        remaining = sum(count for state, count in known.items() if state != 'RESOLVED')
        ranked.append(((not row['source_ready'], key != preferred, key not in plans,
                        not bool(known), remaining, key), key))
    return [key for _, key in sorted(ranked)]


def run_unit(request, key, handoff, repository, config_path, config, checkpoint,
             matrix_path, state_before, rebuild_sha, adopted, work, temp, receipt):
    before = len(state_before['batches'])
    numeric_before = national_numeric(Path(state_before['candidate']))
    sources = request.get('sources', ())
    probe = request.get('probe') == 'retained-audit'
    if probe and sources:
        raise ValueError('RETAINED_AUDIT_PROBE_REQUIRES_NO_SOURCES')
    state = state_before if probe else continue_batches(
        config_path, checkpoint.parent, key, sources,
        national_only=request.get('scope') == 'national',
        duration_certificates=request.get('duration_certificates', ()),
        selection_certificates=request.get('selection_certificates', ()), receipt=receipt)
    receipt('initial-batches-done', batches=len(state['batches'])-before)
    # The exporter reads the historical snapshot; publish only after the last
    # round has passed verification. A blocked source is preserved in its receipt.
    selected_matrix = read(matrix_path)
    selected_row = next(row for row in selected_matrix['units']
                        if f'{row["club_id"]}:{row["unit"]}' == key)
    active_candidate = Path(state['candidate']).resolve()
    if request.get('national_apply_dates') and any(
            active_candidate == ROOT / relative_path(snap['database'])
            for snap in handoff['accepted_snapshots'].values()):
        raise ValueError('ACCEPTED_SNAPSHOT_IMMUTABLE')
    national_apply = apply_scoped_national_fixtures(
        active_candidate, work, temp, unit_window(selected_row), request, receipt)
    if national_apply['rows']:
        write(matrix_path, refresh_matrix(selected_matrix, active_candidate))
    offered = {item['url'].rsplit('/', 1)[-1] for item in sources}
    followup_batches = 0
    direct_requests = set()
    direct_outcomes = Counter()
    directory_outcomes = Counter()
    directory_checked = False
    rounds = 0
    while True:
        rounds += 1
        round_work = work / f'round-{rounds:03d}'
        round_work.mkdir()
        candidate = Path(state['candidate']).resolve()
        matrix = read(matrix_path)
        report, candidate, mapped = runtime_report(
            candidate, matrix, key, handoff, config, round_work / 'scope.json', request, receipt, rounds)
        if probe:
            receipt('probe-done', audit=report['audit'], followup=report['audit_followup'])
            return None, candidate, matrix
        if str(candidate) != state['candidate']:
            state['candidate'] = str(candidate)
            write(checkpoint, state)
            matrix = refresh_matrix(matrix, candidate)
            write(matrix_path, matrix)
        if report['data_accepted']:
            break
        next_sources = [
            item for item in mapped if item['url'].rsplit('/', 1)[-1] not in offered
            and (request.get('scope') != 'national' or item['worker'] != 'club')
        ]
        directories_found = 0
        if not directory_checked and report['pending'].get('applicable_national_scope_requires_evidence'):
            directory_checked = True
            selected = next(row for row in matrix['units'] if f'{row["club_id"]}:{row["unit"]}' == key)
            requests, outcomes = collect_national_directories(
                candidate, unit_window(selected), round_work, request.get('national_directory_urls', {}))
            direct_requests.update(requests)
            directory_outcomes.update(outcomes)
            directories_found = outcomes['FOUND']
            receipt('national-discovery-done', round=rounds, attempts=len(requests), outcome=dict(outcomes))
        if not next_sources and not directories_found:
            break
        offered.update(item['url'].rsplit('/', 1)[-1] for item in next_sources)
        club = [item for item in next_sources if item['worker'] == 'club']
        national = [item for item in next_sources if item['worker'] != 'club']
        captured = 0
        if club and not any(direct_outcomes[state] for state in ('ACCESS_RESTRICTED', 'RATE_LIMITED', 'ROBOTS_DISALLOWED')):
            receipt('direct-audit-start', round=rounds, tasks=len(club))
            requests, outcomes = collect_club_audit_evidence(round_work, club)
            direct_requests.update(requests)
            direct_outcomes.update(outcomes)
            captured = outcomes['FOUND']
            receipt('direct-audit-done', round=rounds, attempts=len(requests), outcome=dict(outcomes))
        batch_count = len(state['batches'])
        if national and state['state'] not in {
                'ACCESS_RESTRICTED', 'RATE_LIMITED', 'ROBOTS_DISALLOWED', 'RESOURCE_CHECKPOINT'}:
            state = continue_batches(config_path, checkpoint.parent, key, national,
                                     national_only=request.get('scope') == 'national', receipt=receipt)
        delta = len(state['batches']) - batch_count
        followup_batches += delta
        if not captured and not directories_found and not delta and read(matrix_path) == matrix:
            break
    report['audit_followup'].update(rounds=rounds, executed_batches=followup_batches,
                                    direct_outcomes=dict(direct_outcomes),
                                    national_directory_outcomes=dict(directory_outcomes))
    duration_result = checkpoint.parent / 'duration-obligations.json'
    if request.get('duration_certificates') and duration_result.exists():
        report['duration_exceptions'] = read(duration_result)
    selection_failures = checkpoint.parent / 'selection-obligations.json'
    if request.get('selection_certificates') and selection_failures.exists():
        report['selection_exceptions'] = read(selection_failures)
    batches = state['batches'][before:]
    report['selection_reconciliation'] = [item for batch in batches
        if (check := Path(batch['queue']).parent / 'selection-reconciliation.json').exists() for item in read(check)]
    proofs = []
    for batch in state['batches']:
        folder = Path(batch['queue']).parent
        binding = folder / 'verified-durations.json'
        if not binding.exists():
            continue
        results = {item['url']:item for item in read(Path(batch['fixture_exceptions']))}
        for native, proof in read(binding).items():
            actual = next((result for url,result in results.items() if url.rsplit('/',1)[-1] == native), {})
            proofs.append(dict(native_id=native, teams=proof['teams'], status=actual.get('state'),
                               detail=actual.get('reason'), authorized_minutes=120,
                               raw_sha256=proof['match_sha256'], official_sha256=proof['official_sha256']))
    if proofs:
        report['duration_receipts'] = proofs
    fields = ('new_matches','new_player_match_rows','new_national_matches','new_national_player_rows','minutes_recovered')
    report.update(status='CHECKPOINT_VERIFIED', batches=len(batches),
                  counts={field:sum(batch.get(field, 0) or 0 for batch in batches) for field in fields},
                  source_requests=len(direct_requests | {url for batch in batches for url in batch['attempted_urls']}),
                  replayed_source_requests=len({url for batch in batches for url in batch.get('replayed_urls', ())}),
                  new_national_numeric_minutes=national_numeric(candidate)-numeric_before,
                  state=state['state'], ordinary_export='PASS', reused_legacy_delta=adopted,
                  accepted=sum(row['data_accepted'] for row in matrix['units']),
                  source_ready=sum(row['source_ready'] for row in matrix['units']),
                  run=f'https://github.com/{repository}/actions/runs/{os.environ["GITHUB_RUN_ID"]}')
    if request.get('national_apply_dates') and any(
            query['fixture_count'] > 1 for query in report.get('coverage_queries', ())):
        raise ValueError('SCOPED_NATIONAL_DUPLICATE_NO_PUBLICATION')
    report['rebuild_base_sha256'] = rebuild_sha
    report['national_match_application'] = national_apply
    report['counts']['new_national_matches'] += national_apply['matches']
    report['counts']['new_national_player_rows'] += national_apply['rows']
    report['source_requests'] += national_apply['requests']
    report['whole_unit_input_plan'] = write_whole_unit_inputs(work, key, candidate, matrix, config, report)
    write(work / 'scope.json', report)
    sync_delivery_matrix(matrix, report)
    write(matrix_path, matrix)
    return report, candidate, matrix


def run_delivery(request_path, entry_path, base_checkout):
    handoff, request = read(entry_path), read(request_path)
    ref = handoff['data_ref']
    repository = os.environ['GITHUB_REPOSITORY']
    parent = os.environ['GITHUB_SHA']
    if (repository != 'Strange-Noah/Futograma-data' or not ref.startswith('codex/historical-')
            or os.environ['GITHUB_REF'] != 'refs/heads/' + ref):
        raise ValueError('ISOLATED_HISTORICAL_RUNNER_REQUIRED')
    store = GitHubStore(repository, os.environ['GH_TOKEN'], ref)
    head, pointer = store.head()
    if head != parent or request['database_sha256'] != pointer['current']['database']['sha256'] or (
            request['database_sha256'] != handoff['database_sha256']):
        raise StaleWriter('HISTORICAL_REQUEST_PARENT_MISMATCH')
    code = subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()
    if request['code_commit'] != code:
        raise ValueError('HISTORICAL_REQUEST_CODE_MISMATCH')
    temp = Path(os.environ['RUNNER_TEMP']) / 'historical-delivery'
    temp.mkdir(exist_ok=False)
    receipt = DeliveryReceipt(store, pointer['current']['release_id'], temp, request,
                              handoff['evidence_asset']['size'])
    receipt('restore-start', evidence_bytes=handoff['evidence_asset']['size'])
    verified, adopted = stage(store, handoff, base_checkout, temp)
    receipt('restore-done', evidence_files=verified['evidence_files'])
    if verified['data_commit'] != parent:
        raise StaleWriter('HISTORICAL_RESTORE_PARENT_MISMATCH')
    config_path = ROOT / relative_path(handoff['runner_config'])
    checkpoint = ROOT / relative_path(handoff['checkpoint'])
    matrix_path = ROOT / relative_path(handoff['matrix'])
    key = request['unit']
    if not re.fullmatch(r'\d+:\d{4}/\d{2}-H[12]', key):
        raise ValueError('INVALID_HISTORICAL_UNIT')
    state_before = read(checkpoint)
    rebuild_sha = request.get('rebuild_from_previous_sha256')
    if rebuild_sha:
        previous = pointer.get('previous')
        if not previous or previous['database']['sha256'] != rebuild_sha:
            raise ValueError('REBUILD_REQUIRES_EXACT_VERIFIED_PREVIOUS_DATABASE')
        candidate = Path(state_before['candidate']).resolve()
        if any(candidate == ROOT / relative_path(snap['database']) for snap in handoff['accepted_snapshots'].values()):
            raise ValueError('ACCEPTED_SNAPSHOT_IMMUTABLE')
        restored = temp / 'historical-rebuild.sqlite'
        previous_head, _ = store.restore(restored, previous=True)
        if previous_head != parent or sha(restored) != rebuild_sha:
            raise ValueError('REBUILD_PREVIOUS_DATABASE_HASH_MISMATCH')
        os.replace(restored, candidate)
        Path(str(restored) + '.download.gz').unlink()
        write(matrix_path, refresh_matrix(read(matrix_path), candidate))
        receipt('rebuild-restored', database_sha256=rebuild_sha)
    config = read(config_path)
    work = checkpoint.parent / ('delivery-' + os.environ['GITHUB_RUN_ID'])
    work.mkdir()
    matrix = read(matrix_path)
    sweep = request.get('matrix_mode') == 'OPEN_UNITS'
    if sweep and (rebuild_sha or request.get('probe')):
        raise ValueError('MATRIX_RUN_REQUIRES_STANDARD_CONTINUATION')
    cycle_path = checkpoint.parent / 'matrix-cycle.json'
    prior = read(cycle_path) if sweep and cycle_path.exists() else {}
    pending = set(prior.get('pending', ()))
    targets = matrix_targets(matrix, config, key) if sweep else [key]
    if pending:
        targets = [target for target in targets if target in pending] or targets
    if not targets:
        raise ValueError('NO_OPEN_HISTORICAL_UNIT')
    reports = []
    for index, target in enumerate(targets):
        # Deadline reserve for packaging and readback in the existing 35 minute job.
        if reports and time.monotonic() - receipt.start >= 23 * 60:
            receipt('matrix-checkpoint', completed=len(reports), pending=len(targets)-index)
            break
        scoped = dict(request)
        if target != key:
            for option in ('coverage_queries', 'sources', 'duration_certificates', 'selection_certificates',
                           'national_apply_dates', 'national_team_aliases', 'national_directory_urls'):
                scoped.pop(option, None)
        unit_work = work / target.replace(':', '-').replace('/', '_') if sweep else work
        unit_work.mkdir(exist_ok=True)
        receipt.unit = target
        report, candidate, matrix = run_unit(scoped, target, handoff, repository, config_path, config,
            checkpoint, matrix_path, read(checkpoint), rebuild_sha, adopted, unit_work, temp, receipt)
        if report is None:
            return
        reports.append(report)
        receipt('unit-finished', outcome=report['state'],
                source_work_required=report['audit'].get('metrics', {}).get('source_work_required'),
                frozen=report['whole_unit_input_plan']['frozen_population_count'],
                plan=report['whole_unit_input_plan']['approved_plan_present'])
        if sweep:
            write(cycle_path, dict(selected=targets, completed=[r['unit'] for r in reports],
                                   pending=targets[len(reports):]))
    report = reports[-1]
    if sweep:
        totals = Counter()
        for entry in reports:
            totals.update(entry['counts'])
        report = dict(report, matrix_cycle=dict(
            **read(cycle_path), counts=dict(totals),
            numeric_minutes=sum(r['new_national_numeric_minutes'] for r in reports),
            source_requests=sum(r['source_requests'] for r in reports),
            units={r['unit']: dict(metrics=r['audit'].get('metrics', {}),
                                   outcomes=r['audit'].get('resolution_states', {}),
                                   state=r['state'], counts=r['counts'],
                                   sources=r['source_requests'], plan=r['whole_unit_input_plan'])
                   for r in reports}))
        write(work / 'scope.json', report)
    batches = read(checkpoint)['batches'][len(state_before['batches']):]
    rounds = sum(r['audit_followup']['rounds'] for r in reports)
    if request.get('verify_frozen_inputs'):
        rebound = refresh_retained_unit_inputs(work / 'frozen-plan-refresh', matrix, config,
                                                candidate, checkpoint.parent)
        report['retained_plan_refresh'] = rebound
        write(matrix_path, matrix)
        write(work / 'scope.json', report)
        receipt('matrix-frozen-inputs', represented=len(rebound),
                frozen=sum(item['frozen_population_count'] for item in rebound.values()),
                approved_plans=sum(item['approved_plan_present'] for item in rebound.values()))
    # Keep every previously accepted independent reference and validate any new receipt.
    snapshots = dict(handoff['accepted_snapshots'])
    for row in matrix['units']:
        row_key = f'{row["club_id"]}:{row["unit"]}'
        if row['data_accepted'] and row_key not in snapshots:
            acceptance_receipt = Path(row['acceptance_receipt'])
            accepted = Path(row['accepted_candidate'])
            verdict = read(acceptance_receipt)
            if verdict.get('data_accepted') is not True or verdict.get('sqlite_sha256') != sha(accepted):
                raise ValueError('INVALID_NEW_UNIT_ACCEPTANCE')
            snapshots[row_key] = dict(receipt=str(acceptance_receipt.relative_to(ROOT)),
                                      database=str(accepted.relative_to(ROOT)),sha256=sha(accepted))
    if len(snapshots) != report['accepted']:
        raise ValueError('ACCUMULATED_ACCEPTANCE_COUNT_MISMATCH')
    quality = inspect(candidate)
    export_pointer = dict(pointer, current=dict(pointer['current'], database=quality))
    export(candidate, temp / 'verified-output-export', parent, export_pointer, ROOT / 'web')
    receipt('bundle-start', batches=len(batches), rounds=rounds)
    manifest, archives = build_bundle(candidate, report, matrix, temp)
    archive_record = read(manifest)['evidence_archive']
    receipt('bundle-done', archive_bytes=archive_record['size'], parts=len(archives))
    compressed = temp / 'candidate.sqlite.gz'
    pack(candidate, compressed)

    def companion(new, assets):
        info = new['current']
        entry = dict(handoff, code_commit=code, database_version=info['version'],
                     database_sha256=info['database']['sha256'], release=info['tag'],
                     database_asset=assets[compressed.name], manifest_asset=assets[manifest.name],
                     evidence_asset=dict(name='historical-evidence.tar.gz', size=archive_record['size'],
                                         sha256=archive_record['sha256']),
                     evidence_assets=[assets[path.name] for path in archives], accepted=report['accepted'],
                     source_ready=report['source_ready'], accepted_snapshots=snapshots,
                     latest_delivery=report,
                     cloud_acceptance=dict(status='ASSETS_AND_DATABASE_READBACK_PASS',
                                           path='historical-cloud-acceptance.json',action=report['run'],
                                           expected_parent=parent))
        if adopted:
            entry['incorporated_evidence_checkpoint'] = handoff['latest_evidence_checkpoint']['release']
        matrix_bytes = (json.dumps(matrix,ensure_ascii=False,indent=2)+'\n').encode()
        entry['matrix_file'] = dict(path='HISTORICAL_UNIT_MATRIX.json',sha256=hashlib.sha256(matrix_bytes).hexdigest())
        marker = dict(status='HANDOFF_READY',entry='historical-handoff.json',
                      database_version=info['version'], database_sha256=info['database']['sha256'],
                      code_commit=code, expected_parent=parent, actions_url=report['run'],
                      accepted=report['accepted'],source_ready=report['source_ready'],total=len(matrix['units']))
        return {'historical-handoff.json':entry,
                'historical-cloud-acceptance.json':report,
                'HISTORICAL_UNIT_MATRIX.json':matrix,
                'HANDOFF_READY':marker}

    receipt('publish-start')
    head, published = store.publish(candidate, compressed, parent, code,
                                    evidence=(manifest, *archives), companion=companion)
    print('HISTORICAL_DELIVERY=' + json.dumps(dict(report, data_commit=head,
          version=published['current']['version']), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--entry', type=Path, required=True)
    parser.add_argument('--base-checkout', type=Path, required=True)
    args = parser.parse_args()
    run_delivery(args.request, args.entry, args.base_checkout)


if __name__ == '__main__':
    main()
