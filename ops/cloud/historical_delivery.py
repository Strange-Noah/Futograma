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
from dataclasses import asdict
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from ops.cloud.historical_batch import continue_batches, discover_gaps, refresh_matrix, unit_window, write
from ops.cloud.obligation_execution import adjudicate_packets
from ops.cloud.verify_historical_handoff import relative_path, relocate_json, verify
from squad_pipeline.cloud.export import export
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import inspect, pack, sha
from squad_pipeline.cloud.storage import StaleWriter
from squad_pipeline.core import ROOT
from squad_pipeline.canonical_fixture import digest
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
                    'official_windows', 'parser', 'mapped', 'unmapped', 'source_work_required'):
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
    return EvidenceStore(work / 'audit-sources', archives=[root], index=index, records=records)


def coverage_projection(db, unit, queries):
    results = []
    for query in queries:
        on, (home, away) = query['date'], query['teams']
        if not unit['start'] <= datetime.date.fromisoformat(on).isoformat() < unit['end_exclusive']:
            raise ValueError('COVERAGE_QUERY_OUTSIDE_SELECTED_UNIT')
        fixtures = db.execute("""
            SELECT m.match_id,m.duration_minutes,m.kickoff_utc FROM international_matches m
            JOIN football_teams h ON h.team_key=m.home_team_key
            JOIN football_teams a ON a.team_key=m.away_team_key
            WHERE lower(h.display_name)=lower(?) AND lower(a.display_name)=lower(?) AND m.madrid_date=?
            """, (home, away, on)).fetchall()
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
        acquired, parsed = svc.observe(entry)
        calendar_evidence = acquired.outcome
        if acquired.artifact and acquired.outcome == 'FOUND':
            write(local.root / 'results-private.json', [dict(
                url=url, outcome='FOUND', artifact=asdict(acquired.artifact))])
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
            audit = run(db, adapter, begin, cutoff, max_attempts=200)
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
                audit = run(db, adapter, begin, cutoff, max_attempts=200)
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
        projection = coverage_projection(db, unit, request.get('coverage_queries', ()))
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
    root = ROOT / '.cloud'
    frozen = {}
    for path in sorted(root.rglob('*.json')):
        if not any(word in path.name.lower() for word in ('freeze', 'frozen', 'union', 'cohort')):
            continue
        item = read(path)
        if not isinstance(item, dict) or item.get('unit') != unit:
            continue
        if item.get('observed_population_accepted') is not True:
            continue
        fingerprint = item.get('freeze_sha256')
        if fingerprint and fingerprint == digest({k: v for k, v in item.items() if k != 'freeze_sha256'}):
            frozen.setdefault(fingerprint, (path, len(item['player_ids'])))
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
        national_scope=report['pending'], open_audit_work=open_work,
        approved_plan=approved_plan, missing=missing))
    return dict(path=str(path.relative_to(ROOT)), frozen_population_count=len(frozen),
                frozen_player_counts=sorted(n for _, n in frozen.values()),
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

def build_bundle(candidate, progress, matrix, output):
    files = []
    archive = output / 'historical-evidence.tar.gz'
    with tarfile.open(archive, 'w:gz', compresslevel=4) as package:
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
             evidence_archive=dict(size=archive.stat().st_size,sha256=sha(archive)),
             units=len(matrix['units']), accepted=progress['accepted'], source_ready=progress['source_ready']))
    return manifest, archive


def national_numeric(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return db.execute('SELECT count(minutes) FROM international_appearances').fetchone()[0]


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
    work = checkpoint.parent / ('delivery-' + os.environ['GITHUB_RUN_ID'])
    work.mkdir()
    config = read(config_path)
    offered = {item['url'].rsplit('/', 1)[-1] for item in sources}
    followup_batches = 0
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
            return
        if str(candidate) != state['candidate']:
            state['candidate'] = str(candidate)
            write(checkpoint, state)
            matrix = refresh_matrix(matrix, candidate)
            write(matrix_path, matrix)
        if report['data_accepted'] or state['state'] in {
                'ACCESS_RESTRICTED', 'RATE_LIMITED', 'ROBOTS_DISALLOWED', 'RESOURCE_CHECKPOINT'}:
            break
        next_sources = [
            item for item in mapped if item['url'].rsplit('/', 1)[-1] not in offered
            and (request.get('scope') != 'national' or item['worker'] != 'club')
        ]
        if not next_sources:
            break
        offered.update(item['url'].rsplit('/', 1)[-1] for item in next_sources)
        batch_count = len(state['batches'])
        # Keep the latest audit evidence path even if the collector has already
        # attempted every mapped request in persisted continuation state.
        state = continue_batches(config_path, checkpoint.parent, key, next_sources,
                                 national_only=request.get('scope') == 'national', receipt=receipt)
        delta = len(state['batches']) - batch_count
        followup_batches += delta
        if not delta and read(matrix_path) == matrix:
            break
    report['audit_followup'].update(rounds=rounds, executed_batches=followup_batches)
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
                  source_requests=len({url for batch in batches for url in batch['attempted_urls']}),
                  replayed_source_requests=len({url for batch in batches for url in batch.get('replayed_urls', ())}),
                  new_national_numeric_minutes=national_numeric(candidate)-numeric_before,
                  state=state['state'], ordinary_export='PASS', reused_legacy_delta=adopted,
                  accepted=sum(row['data_accepted'] for row in matrix['units']),
                  source_ready=sum(row['source_ready'] for row in matrix['units']),
                  run=f'https://github.com/{repository}/actions/runs/{os.environ["GITHUB_RUN_ID"]}')
    report['whole_unit_input_plan'] = write_whole_unit_inputs(work, key, candidate, matrix, config, report)
    write(work / 'scope.json', report)
    sync_delivery_matrix(matrix, report)
    write(matrix_path, matrix)
    # Keep every previously accepted independent reference and validate any new receipt.
    snapshots = dict(handoff['accepted_snapshots'])
    for row in matrix['units']:
        row_key = f'{row["club_id"]}:{row["unit"]}'
        if row['data_accepted'] and row_key not in snapshots:
            receipt = Path(row['acceptance_receipt'])
            accepted = Path(row['accepted_candidate'])
            verdict = read(receipt)
            if verdict.get('data_accepted') is not True or verdict.get('sqlite_sha256') != sha(accepted):
                raise ValueError('INVALID_NEW_UNIT_ACCEPTANCE')
            snapshots[row_key] = dict(receipt=str(receipt.relative_to(ROOT)),
                                      database=str(accepted.relative_to(ROOT)),sha256=sha(accepted))
    if len(snapshots) != report['accepted']:
        raise ValueError('ACCUMULATED_ACCEPTANCE_COUNT_MISMATCH')
    quality = inspect(candidate)
    export_pointer = dict(pointer, current=dict(pointer['current'], database=quality))
    export(candidate, temp / 'verified-output-export', parent, export_pointer, ROOT / 'web')
    receipt('bundle-start', batches=len(batches), rounds=rounds)
    manifest, archive = build_bundle(candidate, report, matrix, temp)
    receipt('bundle-done', archive_bytes=archive.stat().st_size)
    compressed = temp / 'candidate.sqlite.gz'
    pack(candidate, compressed)

    def companion(new, assets):
        info = new['current']
        entry = dict(handoff, code_commit=code, database_version=info['version'],
                     database_sha256=info['database']['sha256'], release=info['tag'],
                     database_asset=assets[compressed.name], manifest_asset=assets[manifest.name],
                     evidence_asset=assets[archive.name], accepted=report['accepted'],
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
                                    evidence=(manifest, archive), companion=companion)
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
