"""Run trusted, project-local historical replay and audit scripts in sequence."""
import argparse
import datetime
import fcntl
import json
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from ops.cloud.historical_unit import within
from ops.cloud.historical_duration import bind
from ops.cloud.historical_selection_certificate import prepare as selection_proofs
from squad_pipeline.cloud.quality import inspect, sha
from squad_pipeline.core import ROOT

KINDS = {'BLOCKING', 'NONBLOCKING_UNKNOWN', 'OPTIONAL_YOUTH',
         'SOURCE_UNAVAILABLE', 'TECHNICAL_FAILURE'}


def write(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def local_path(path):
    path = within(path)
    if not path.is_relative_to(within(ROOT / '.cloud')):
        raise ValueError('HISTORICAL_BATCH_REQUIRES_ISOLATED_WORKSPACE')
    return path


def counts(database, unit, player_ids=None):
    args = [unit['start'], unit['end_exclusive']]
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        club = db.execute(
            'SELECT count(DISTINCT p.match_id), count(*), count(DISTINCT p.player_id), '
            'count(*)-count(p.minutes) FROM player_match_minutes p '
            'JOIN matches m USING(match_id) WHERE p.club_id=? AND m.local_date>=? AND m.local_date<?'
            + (' AND m.competition_id=?' if 'competition_id' in unit else ''),
            [unit['club_id'], *args] + ([unit['competition_id']] if 'competition_id' in unit else [])).fetchone()
        result = dict(club_fixtures=club[0], club_rows=club[1], players=club[2], club_unknown_minutes=club[3])
        if player_ids is None:
            return result
        ids = sorted(set(player_ids))
        if not ids:
            raise ValueError('EMPTY_OBSERVED_UNION')
        national = db.execute(
            'SELECT count(DISTINCT a.match_id), count(*), count(*)-count(a.minutes) '
            'FROM international_appearances a JOIN international_matches m USING(match_id) '
            'WHERE m.madrid_date>=? AND m.madrid_date<? AND a.player_id IN ('
            + ','.join('?' for _ in ids) + ')', [*args, *ids]).fetchone()
    return dict(result, players=len(ids), national_fixtures=national[0], national_rows=national[1],
                national_unknown_minutes=national[2])


def source_counts(paths):
    bodies, unavailable = set(), set()
    for path in paths:
        rows = json.loads(path.read_text())
        if not isinstance(rows, list):
            continue  # Audit and verification receipts are not acquisition logs.
        for row in rows:
            if not isinstance(row, dict) or 'url' not in row or 'outcome' not in row:
                continue
            artifact = row.get('artifact') or {}
            if row['outcome'] == 'FOUND' and artifact.get('content_sha256'):
                bodies.add(artifact['content_sha256'])
            elif row['outcome'] != 'FOUND':
                unavailable.add(row['url'])
    return dict(retained_unique_bodies=len(bodies), unavailable_requests=len(unavailable))


def run(plan_path, work):
    plan_path, work = local_path(plan_path), local_path(work)
    work.mkdir(exist_ok=True)
    with (work / "batch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return execute(plan_path, work)


def execute(plan_path, work):
    plan = json.loads(plan_path.read_text())
    source = local_path(plan['source'])
    freeze_path = local_path(plan['freeze']) if 'freeze' in plan else None
    freeze = json.loads(freeze_path.read_text()) if freeze_path else None
    unit = freeze['unit'] if freeze else plan['scope']
    initial_hash = sha(source)
    if initial_hash != plan['source_sha256']:
        raise ValueError('BATCH_SOURCE_HASH_MISMATCH')
    steps = plan['steps']
    sources = [local_path(p) for p in plan.get('source_receipts', [])]
    binding = dict(plan=sha(plan_path), source=initial_hash, freeze=sha(freeze_path) if freeze_path else None,
                   scripts=[sha(local_path(s['script'])) for s in steps],
                   source_receipts={str(p): sha(p) for p in sources})
    state = work / 'checkpoint.json'
    if state.exists() and json.loads(state.read_text()) != binding:
        raise ValueError('BATCH_INPUTS_CHANGED_USE_NEW_WORK_DIRECTORY')
    write(state, binding)
    exceptions = list({json.dumps(e, sort_keys=True): e for e in plan.get('exceptions', [])}.values())
    if any(e['kind'] not in KINDS for e in exceptions):
        raise ValueError('UNKNOWN_EXCEPTION_KIND')
    report = dict(batch=work.name, unit=freeze['unit_key'] if freeze else unit['unit_key'],
                  scope='FROZEN_UNIT' if freeze else 'COMPETITION_SUBSET', input=binding, steps=[],
                  frozen_union_sha256=freeze['freeze_sha256'] if freeze else None, exceptions=exceptions)
    candidate = source
    reused = 0
    for index, step in enumerate(steps, 1):
        # A failed or interrupted attempt is retained. A retry always starts from
        # the last completed candidate, using a fresh attempt directory.
        parent = work / f'{index:02d}'
        parent.mkdir(exist_ok=True)
        receipt = parent / 'complete.json'
        if receipt.exists():
            result = json.loads(receipt.read_text())
            if result['input_sha256'] != sha(candidate):
                raise ValueError('BATCH_CHECKPOINT_INPUT_CHANGED')
            if sha(local_path(result['database'])) != result['database_sha256']:
                raise ValueError('BATCH_CHECKPOINT_DATABASE_CHANGED')
            if sha(local_path(result['details'])) != result['details_sha256']:
                raise ValueError('BATCH_CHECKPOINT_DETAILS_CHANGED')
            if 'exceptions' in result and sha(local_path(result['exceptions'])) != result['exceptions_sha256']:
                raise ValueError('BATCH_CHECKPOINT_EXCEPTIONS_CHANGED')
            reused += 1
        else:
            attempt = parent / f'attempt-{len(list(parent.glob("attempt-*")))+1:03d}'
            attempt.mkdir()
            out = attempt / 'output'
            before = sha(candidate)
            command = [sys.executable, '-B', str(local_path(step['script'])), str(candidate), str(out)]
            if 'code' in step:
                code = (ROOT / step['code']).resolve()
                if code.parent != ROOT.parent:
                    raise ValueError('EXPECTED_EXISTING_SIBLING_WORKTREE')
                command.append(str(code))
            log = attempt / 'run.log'
            try:
                with log.open('wb') as stream:
                    subprocess.run(command, cwd=ROOT, stdout=stream,
                                   stderr=subprocess.STDOUT, check=True)
                database = local_path(out / step.get('database', 'candidate.sqlite'))
                details = local_path(out / step.get('details', 'SUMMARY_PRIVATE.json'))
                if not database.is_relative_to(out) or not details.is_relative_to(out):
                    raise ValueError('STAGE_OUTPUT_MUST_BE_INSIDE_ATTEMPT')
                output = json.loads(details.read_text())
                for key, expected in step.get('expect', {}).items():
                    if output.get(key) != expected:
                        raise ValueError(f'BATCH_CHECK_FAILED:{key}')
                if sha(candidate) != before:
                    raise ValueError('BATCH_INPUT_MODIFIED')
                result = dict(input_sha256=before, database=str(database), database_sha256=sha(database),
                              details=str(details), details_sha256=sha(details), log=str(log),
                              validation=step.get('expect', {}))
                if 'exceptions' in step:
                    sidecar = local_path(out / step['exceptions'])
                    if not sidecar.is_relative_to(out):
                        raise ValueError('STAGE_OUTPUT_MUST_BE_INSIDE_ATTEMPT')
                    result.update(exceptions=str(sidecar), exceptions_sha256=sha(sidecar))
                write(receipt, result)
            except (ValueError, OSError, subprocess.CalledProcessError) as error:
                exceptions.append(dict(kind='TECHNICAL_FAILURE', step=index, evidence=str(log),
                                       next_action=f'Inspect stage output and resume ({error})'))
                break
        report['steps'].append(result)
        if step.get('promote', True):
            candidate = local_path(result['database'])
    try:
        report['quality'] = inspect(candidate)
        roster = freeze['player_ids'] if freeze else None
        report['counts'] = counts(candidate, unit, roster)
        prior = counts(source, unit, roster)
        report['changes'] = {k: value - prior[k] for k, value in report['counts'].items()}
        report['accepted_receipt_comparison'] = {
            k: report['counts'][k] == expected for k, expected in plan.get('expected_counts', {}).items()}
        if not all(report['accepted_receipt_comparison'].values()) or sha(source) != initial_hash:
            raise ValueError('ACCEPTED_FACT_OR_INPUT_MISMATCH')
    except (ValueError, OSError, sqlite3.Error) as error:
        exceptions.append(dict(kind='TECHNICAL_FAILURE', evidence=str(candidate),
                               next_action=str(error)))
    for stage in report['steps']:
        if 'exceptions' in stage:
            for issue in json.loads(local_path(stage['exceptions']).read_text()):
                if issue.get('kind') not in KINDS:
                    raise ValueError('UNKNOWN_EXCEPTION_KIND')
                exceptions.append(dict(issue, evidence=issue.get('evidence', issue.get('source', stage['exceptions'])),
                                       next_action=issue.get('next_action', 'Resolve the flagged fact using its retained evidence')))
        detail = json.loads(local_path(stage['details']).read_text())
        metrics = detail.get('metrics', {})
        open_work = {k: metrics[k] for k in ('final_gap_count', 'source_work_required',
                     'accepted_challenges', 'human_pending', 'conflicts', 'unperformed_paths')
                     if metrics.get(k)}
        if open_work:
            exceptions.append(dict(kind='BLOCKING', metrics=open_work,
                                   evidence=stage['details'],
                                   next_action='Review existing audit findings before acceptance'))
    exceptions[:] = list({json.dumps(e,sort_keys=True): e for e in exceptions}.values())
    report['state'] = 'FAIL' if any(e['kind'] == 'TECHNICAL_FAILURE' for e in exceptions) else 'PASS'
    review = any(e['kind'] in {'BLOCKING', 'TECHNICAL_FAILURE', 'SOURCE_UNAVAILABLE'} for e in exceptions)
    report['acceptance'] = 'NEEDS_SCOPE_REVIEW' if review or not freeze else 'SEE_EXISTING_UNIT_ACCEPTANCE'
    report['candidate'] = str(candidate)
    report['source_receipts'] = plan.get('source_receipts', [])
    report['stages_reused'] = reused
    write(work / 'audit-private.json', report)
    compact = {k: report[k] for k in ('batch', 'unit', 'state', 'acceptance', 'exceptions', 'stages_reused')}
    compact.update(scope=report['scope'], counts=report.get('counts'), changes=report.get('changes'), stages_completed=len(report['steps']),
                   exception_counts=dict(Counter(e['kind'] for e in exceptions)),
                   source_receipts=len(report['source_receipts']),
                   sources=source_counts(sources),
                   validation=[{'state': 'PASS' if s['validation'] else 'NOT_TESTED',
                                'checks': list(s['validation'])} for s in report['steps']],
                   detailed_receipt=str(work / 'audit-private.json'))
    write(work / 'exceptions.json', compact)
    return compact


def unit_window(row):
    season, half = row['unit'].split('-')
    year = int(season[:4])
    start = f'{year}-07-01' if half == 'H1' else f'{year + 1}-01-01'
    end = f'{year + 1}-01-01' if half == 'H1' else f'{year + 1}-07-01'
    return dict(club_id=row['club_id'], start=start, end_exclusive=end)


def refresh_matrix(matrix, candidate):
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as db:
        for row in matrix['units']:
            if row['data_accepted']:
                continue
            unit = unit_window(row)
            args = tuple(unit.values())
            ids = [r[0] for r in db.execute(
                'SELECT DISTINCT p.player_id FROM player_match_minutes p JOIN matches m USING(match_id) '
                'WHERE club_id=? AND local_date>=? AND local_date<?', args)]
            if not ids:
                continue  # A separate isolated candidate may own this unit.
            result = counts(candidate, unit, ids)
            row.update(canonical_matches=result['club_fixtures'],
                       canonical_player_rows=result['club_rows'],
                       unknown_minutes=result['club_unknown_minutes'],
                       numeric_minutes=result['club_rows'] - result['club_unknown_minutes'],
                       canonical_national_matches=result['national_fixtures'],
                       canonical_national_player_rows=result['national_rows'],
                       national_numeric_minutes=result['national_rows'] - result['national_unknown_minutes'])
    matrix['candidate'] = str(candidate.relative_to(ROOT))
    return matrix


def continue_batches(config_path, work, national_unit=None, national_sources=(), *, national_only=True, duration_certificates=(), selection_certificates=()):
    config = json.loads(local_path(config_path).read_text())
    work = local_path(work)
    work.mkdir(exist_ok=True)
    with (work / 'batch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return consume_sources(config, work, national_unit, national_sources, national_only=national_only,
                               duration_certificates=duration_certificates, selection_certificates=selection_certificates)


def discover_gaps(candidate, matrix, catalogues):
    """Join retained fixture mappings to the current candidate before scheduling."""
    windows = [(row, unit_window(row)) for row in matrix['units'] if not row['data_accepted']]
    tasks = {}
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as db:
        fixtures = db.execute("""
            SELECT f.home_key, f.away_key, substr(coalesce(m.local_date, json_extract(k.value_json,'$')),1,10),
                   s.url, f.match_id, coalesce(s.complete,0)
            FROM canonical_fixtures f
            LEFT JOIN matches m USING(match_id)
            LEFT JOIN canonical_field_values k ON k.match_id=f.match_id AND k.field='kickoff'
            LEFT JOIN (
                SELECT match_id, json_extract(evidence_json,'$.url') url,
                       json_extract(evidence_json,'$.complete_squad') complete
                FROM canonical_fixture_sources WHERE provider='Sky' AND domain='CLUB'
            ) s USING(match_id)
            WHERE f.domain='CLUB'
        """).fetchall()
        coverage = {(club, mid): (starts, unknown) for club, mid, starts, unknown in db.execute("""
            SELECT club_id,match_id,sum(participation_status='STARTER'),
                   sum(minutes IS NULL AND participation_status IN ('STARTER','SUBSTITUTE_USED'))
            FROM player_match_minutes GROUP BY club_id,match_id
        """)}
        complete = {(club, mid) for club, mid in db.execute("""
            SELECT DISTINCT o.club_id,o.match_id FROM squad_observations o
            JOIN canonical_fixture_sources s ON s.match_id=o.match_id AND s.domain='CLUB'
            WHERE json_extract(s.evidence_json,'$.complete_squad')=1
        """)}
        national_work = db.execute("""
            SELECT q.url,m.madrid_date,q.player_id FROM international_acquisition_queue q
            JOIN international_matches m ON q.match_id=m.match_id
            WHERE q.provider='Sky' AND q.state IN ('PENDING','RETRY')
              AND q.entity_type IN ('MATCH','ALTERNATIVE_MATCH','PLAYER_MATCH','PLAYER_MINUTES')
        """).fetchall()
        days, urls_by_match = {}, {}
        for home, away, day, url, mid, _ in fixtures:
            for side in (home, away):
                if isinstance(side, str) and side.startswith('club:') and day:
                    days.setdefault((int(side.split(':')[1]), day), set()).add(mid)
            if url:
                urls_by_match.setdefault(mid, set()).add(url)

        def offer(day, url, kind):
            if url and urlsplit(url).hostname == 'www.skysports.com':
                tasks.setdefault(source_id(url), (url, set(), kind))[1].add(day)

        gaps = {f'{u["club_id"]}:{row["unit"]}': Counter() for row, u in windows}
        for row, unit in windows:
            cid = unit['club_id']
            gap = gaps[f'{cid}:{row["unit"]}']
            for (club, day), mids in days.items():
                if club != cid or not unit['start'] <= day < unit['end_exclusive']:
                    continue
                for mid in mids:
                    starts, unknown = coverage.get((cid, mid), (0, 0))
                    needs_squad = starts != 11 or (cid, mid) not in complete
                    if not (needs_squad or unknown):
                        continue
                    gap['squads_without_complete_canonical_coverage'] += needs_squad
                    gap['observed_played_rows_without_numeric_minutes'] += unknown or 0
                    for url in sorted(urls_by_match.get(mid, ())):
                        offer(day, url, 'club')
            if not row['source_ready']:
                gap['whole_unit_source_scope_requires_evidence'] = 1
            if row.get('national_coverage', '').startswith(('PARTIAL', 'NOT CHECKED')):
                gap['applicable_national_scope_requires_evidence'] = 1
            for url, day, pid in national_work:
                if unit['start'] <= day < unit['end_exclusive'] and pid and db.execute("""
                    SELECT 1 FROM player_match_minutes p JOIN matches m USING(match_id)
                    WHERE p.club_id=? AND p.player_id=? AND m.local_date>=? AND m.local_date<? LIMIT 1
                """, (cid, pid, unit['start'], unit['end_exclusive'])).fetchone():
                    offer(day, url, 'national')

        unmapped = {}
        official_gaps = set()
        for path in catalogues:
            for entry in json.loads(local_path(path).read_text()):
                if not isinstance(entry, dict):
                    continue
                for fixture in (entry.get('native') or {}).get('fixtures', [entry]):
                    if fixture.get('provider') in {'Mancity', 'DFB'}:
                        cid = fixture['club_id']
                        day = fixture.get('source_start', '')[:10]
                        if not day:
                            date = fixture.get('visible_datetime', '').split()
                            day = datetime.datetime.strptime(date[1], '%d.%m.%Y').date().isoformat() if len(date) > 1 else ''
                        for row, unit in windows:
                            if cid != unit['club_id'] or not unit['start'] <= day < unit['end_exclusive']:
                                continue
                            if days.get((cid, day)) or not fixture.get('score'):
                                continue
                            key = f'{cid}:{row["unit"]}'
                            teams = (fixture.get('home_name', fixture.get('home_label')),
                                     fixture.get('away_name', fixture.get('away_label')))
                            signature = (key, day, teams)
                            if signature in official_gaps:
                                continue
                            official_gaps.add(signature)
                            gaps[key]['unmatched_official_catalogue_entries'] += 1
                            examples = unmapped.setdefault(key, [])
                            if len(examples) < 8:
                                catalogue = fixture.get('source_url') or entry.get('url') or str(path)
                                examples.append(dict(date=day, teams=teams, catalogue=catalogue,
                                    next_action='Verify competition scope and supply an exact supported squad source'))
                        continue
                    if not fixture.get('catalog_completed'):
                        continue
                    cid, day, url = fixture.get('club_id', entry.get('club_id')), fixture.get('date', ''), fixture.get('url')
                    if not url or not day:
                        continue
                    for row, unit in windows:
                        if cid != unit['club_id'] or not unit['start'] <= day < unit['end_exclusive']:
                            continue
                        mids = days.get((cid, day), set())
                        if not mids or any(coverage.get((cid, mid), (0, 0))[0] != 11
                                           or (cid, mid) not in complete
                                           or coverage.get((cid, mid), (0, 0))[1] for mid in mids):
                            offer(day, url, 'club')
        for row, unit in windows:
            key = f'{unit["club_id"]}:{row["unit"]}'
            row['acceptance_assessment'] = ('COMPLETE_FIXTURE_AND_SQUAD_SCOPE_REQUIRED'
                if not row['source_ready'] else 'APPLICABLE_NATIONAL_SCOPE_AND_UNIT_VERDICT_REQUIRED')
    return tasks, {key: dict(gap, source_mapping_examples=unmapped.get(key, []))
                   for key, gap in gaps.items()}


def consume_sources(config, work, national_unit=None, national_sources=(), *, national_only=True, duration_certificates=(), selection_certificates=()):
    matrix_path = work / 'matrix-private.json'
    matrix = json.loads((matrix_path if matrix_path.exists() else local_path(config['matrix'])).read_text())
    state_path = work / 'continuation-private.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        'candidate': config['candidate'], 'reference': config['reference'], 'batches': []}
    unit_checks = state.setdefault('unit_checks', {})
    active_rows = [row for row in matrix['units']
                   if national_unit is None or f'{row["club_id"]}:{row["unit"]}' == national_unit]
    if not active_rows:
        raise ValueError('UNKNOWN_HISTORICAL_UNIT')
    if national_unit and national_only and not active_rows[0]['source_ready']:
        raise ValueError('NATIONAL_ONLY_BATCH_REQUIRES_SOURCE_READY_UNIT')
    active_matrix = dict(matrix, units=active_rows)
    attempted, archived = set(), {}
    paths = [*(ROOT / '.cloud').glob('*/results-private.json'),
             *work.glob('batch-*/acquisition/results-private.json')]
    for path in paths:
        try:
            rows = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue  # An independent acquisition may be updating its receipt.
        if isinstance(rows, list):
            attempted.update(source_id(row['url']) for row in rows
                             if isinstance(row, dict) and row.get('url') and 'outcome' in row)
            for row in rows:
                if (isinstance(row, dict) and row.get('outcome') == 'FOUND'
                        and row.get('url') and row.get('artifact')):
                    archived[row['url']] = (path.parent, row)
    processed = {source_id(url) for batch in state['batches'] for url in batch['attempted_urls']}
    attempted.update(processed)
    replayed = {url for batch in state['batches'] for url in batch.get('replayed_urls', [])}
    # These were collected but excluded by the old opponent rule before processing.
    excluded_opponents = {url for url in archived
                          if any(name in urlsplit(url).path.lower() for name in ('real-madrid', 'realmadrid'))}
    queued = {}
    retained_gap_keys = set()
    for path in config['source_ledgers'] if not national_unit else ():
        for group in json.loads(local_path(path).read_text()):
            date = datetime.datetime.strptime(group['month'], '%B %Y').date().isoformat()
            for url in group['urls']:
                parsed = urlsplit(url)
                if parsed.hostname != 'www.skysports.com':
                    continue
                kind = group.get('kind', 'national')
                key = source_id(url)
                queued.setdefault(key, (url, set(), kind))[1].add(date)
                if group.get('replay_unapplied_archives') and url in archived:
                    retained_gap_keys.add(key)
    if national_sources:
        window = unit_window(active_rows[0])
        for item in national_sources:
            url, day, kind = item['url'], item['date'], item['worker']
            if (not re.fullmatch(r'https://www\.skysports\.com/football/[a-z0-9-]+/teams/[0-9]+', url)
                    or not window['start'] <= datetime.date.fromisoformat(day).isoformat() < window['end_exclusive']
                    or kind not in ('national', 'afcon') or kind not in config['workers']):
                raise ValueError('INVALID_SCOPED_NATIONAL_SOURCE')
            key = source_id(url)
            queued[key] = (url, {day}, kind)
            if url in archived:
                retained_gap_keys.add(key)
    durations, duration_errors = bind(duration_certificates, archived, work, unit_window(active_rows[0]),
                                      set(state.get('duration_certificates', ()))) if duration_certificates else ({}, [])
    if duration_certificates:
        write(work / 'duration-obligations.json', duration_errors)
    if selection_certificates:
        selected_scope = unit_window(active_rows[0])
        supplied = [dict(item,unit=selected_scope) for item in selection_certificates]
        selections, selection_errors = selection_proofs(supplied, archived, work, set(state.get('selection_certificates', ())))
        write(work / 'selection-obligations.json', selection_errors)
    else:
        selections = {}
    if selections:
        for old_batch in state['batches']:
            proof_path = Path(old_batch['queue']).parent / 'verified-durations.json'
            if proof_path.is_file():
                for native, proof in json.loads(proof_path.read_text()).items():
                    matched = next((url for url in selections if source_id(url)[1] == native), None)
                    if matched and proof['match_sha256'] == selections[matched][0]['raw_sha256']:
                        durations.setdefault(matched, proof)
    for url in set(durations) | set(selections):
        key = source_id(url)
        if key not in queued:
            raise ValueError('DURATION_CERTIFICATE_REQUIRES_SCOPED_SOURCE_TASK')
        retained_gap_keys.add(key)
        processed.discard(key)
    for url in sorted(excluded_opponents):
        if source_id(url) in queued and url not in replayed:
            attempted.discard(source_id(url))

    def pending():
        windows = [(unit_window(row), row['source_ready']) for row in active_rows
                   if not row['data_accepted']]
        ranked = []
        for key, (url, dates, kind) in queued.items():
            if key in attempted and not (key in retained_gap_keys and key not in processed):
                continue
            affected = [ready for unit, ready in windows
                        if any(unit['start'] <= date < unit['end_exclusive'] for date in dates)]
            if affected:
                ranked.append((-sum(affected), -len(affected), url, kind))
        return [(url, kind) for _, _, url, kind in sorted(ranked)]

    def check_ready_units(before=None):
        fields = ('canonical_matches', 'canonical_player_rows', 'unknown_minutes',
                  'canonical_national_matches', 'canonical_national_player_rows',
                  'national_numeric_minutes')
        for row in active_rows:
            key = f'{row["club_id"]}:{row["unit"]}'
            plan_path = config.get('unit_plans', {}).get(key)
            if row['data_accepted']:
                continue
            metrics = tuple(row.get(field) for field in fields)
            if before is not None and before.get(key) == metrics:
                continue
            if not row['source_ready'] or not plan_path:
                row['acceptance_assessment'] = ('COMPLETE_FIXTURE_AND_SQUAD_SCOPE_REQUIRED'
                    if not row['source_ready'] else 'APPLICABLE_NATIONAL_SCOPE_AND_UNIT_VERDICT_REQUIRED')
                continue
            plan_path = local_path(plan_path)
            plan_hash = sha(plan_path)
            previous = unit_checks.get(key, {})
            if previous.get('plan_sha256') == plan_hash:
                continue
            plan = json.loads(plan_path.read_text())
            unit = json.loads(local_path(plan['freeze']).read_text())['unit']
            if any(unit[field] != value for field, value in unit_window(row).items()):
                raise ValueError('CONTINUATION_UNIT_PLAN_MISMATCH')
            unit_work = config.get('unit_work', {}).get(key)
            result = run(plan_path, Path(unit_work) if unit_work else work / f'unit-{row["club_id"]}-{plan_hash[:16]}')
            detail = json.loads(local_path(result['detailed_receipt']).read_text())
            last = detail['steps'][-1] if detail['steps'] else None
            verdict = json.loads(local_path(last['details']).read_text()) if last else {}
            accepted = (result['acceptance'] == 'SEE_EXISTING_UNIT_ACCEPTANCE'
                        and result['stages_completed'] == len(plan['steps'])
                        and (verdict.get('data_accepted') is True or verdict.get('DATA_ACCEPTED') is True)
                        and verdict.get('sqlite_sha256') == sha(local_path(detail['candidate'])))
            unit_checks[key] = dict(plan_sha256=plan_hash, receipt=result['detailed_receipt'],
                                    accepted=accepted, exceptions=result['exceptions'])
            if accepted:
                row.update(data_accepted=True, acceptance_receipt=last['details'],
                           accepted_candidate=detail['candidate'],
                           canonical_matches=result['counts']['club_fixtures'],
                           canonical_player_rows=result['counts']['club_rows'],
                           numeric_minutes=result['counts']['club_rows'] - result['counts']['club_unknown_minutes'],
                           unknown_minutes=result['counts']['club_unknown_minutes'],
                           canonical_national_matches=result['counts']['national_fixtures'],
                           canonical_national_player_rows=result['counts']['national_rows'],
                           national_numeric_minutes=result['counts']['national_rows'] - result['counts']['national_unknown_minutes'],
                           national_coverage='PASS / VERIFIED: applicable scope in acceptance receipt',
                           national_match_coverage='PASS / VERIFIED: applicable scope in acceptance receipt',
                           R1_R6='PASS / VERIFIED: scoped disposition in acceptance receipt')
                for field in ('acceptance_assessment', 'next_action', 'next_action_priority'):
                    row.pop(field, None)
                state['accepted'] = sum(item['data_accepted'] for item in matrix['units'])
            write(matrix_path, matrix)
            write(state_path, state)

    candidate = local_path(state['candidate'])
    check_ready_units()
    state['state'] = 'RUNNING'
    write(state_path, state)
    source_stop = None
    while any(not row['data_accepted'] for row in active_rows):
        remaining = pending()
        if not remaining:
            discovered, obligations = discover_gaps(candidate, active_matrix, () if national_unit and national_only else config.get('catalogues', []))
            if national_unit and national_only:
                discovered = {key: value for key, value in discovered.items() if value[2] == 'national'}
            write(work / 'obligations-private.json', obligations)
            remaining = [(url, kind) for key, (url, _, kind) in discovered.items()
                         if key not in attempted or (url in archived and key not in processed)]
            queued.update(discovered)
            if not remaining:
                break
        kind = remaining[0][1]
        urls = [url for url, source_kind in remaining if source_kind == kind][:config.get('chunk_size', 8)]
        # Only this independent subset must be available before it can be ingested.
        batch = work / f'batch-{len(state["batches"]) + 1:04d}'
        batch.mkdir(exist_ok=True)
        queue = batch / 'queue-private.json'
        if queue.exists():
            urls = json.loads(queue.read_text())
            if national_unit and any(source_id(url) not in queued or (national_only and queued[source_id(url)][2] not in ('national', 'afcon'))
                                     for url in urls):
                raise ValueError('EXISTING_QUEUE_OUTSIDE_NATIONAL_UNIT')
            kind = queued[source_id(urls[0])][2]
        else:
            write(queue, urls)
        acquisition = batch / 'acquisition'
        if not (acquisition / 'results-private.json').exists():
            retained = []
            for url in urls:
                if url not in archived:
                    continue
                parent, row = archived[url]
                artifact = dict(row['artifact'])
                loc = parent / artifact['locator']
                if loc.is_file():
                    artifact['locator'] = str(loc.resolve())
                    retained.append(dict(row, artifact=artifact, continuation_replay=True))
            if retained:
                acquisition.mkdir(exist_ok=True)
                write(acquisition / 'results-private.json', retained)
        duration_proofs = {url.rsplit('/', 1)[-1]:durations[url] for url in urls if url in durations}
        if duration_proofs:
            write(batch / 'verified-durations.json', duration_proofs)
        selection = {url:selections[url] for url in urls if url in selections}
        if selection:
            write(batch / 'verified-selections.json', selection)
        receipt = batch / 'result-private.json'
        if not receipt.exists():
            with (batch / 'run.log').open('a') as log:
                worker = config.get('workers', {}).get(kind, config['worker'])
                subprocess.run([sys.executable, '-B', str(local_path(worker)),
                                str(queue), str(candidate), str(local_path(state['reference'])),
                                str(batch)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        result = json.loads(receipt.read_text())
        state.update(candidate=result['candidate'], reference=result['reference'])
        candidate = local_path(state['candidate'])
        progress = result['ordinary_export'] == 'PASS' and any(result.get(field, 0) > 0 for field in (
            'new_national_matches', 'new_national_player_rows', 'new_matches',
            'new_player_match_rows', 'minutes_recovered'))
        state['batches'].append(dict(result, productive=progress, queue=str(queue), kind=kind))
        applied = state.setdefault('duration_certificates', [])
        applied.extend(proof['certificate_key'] for proof in duration_proofs.values()
                       if proof['certificate_key'] not in applied)
        selection_done = state.setdefault('selection_certificates', [])
        held = batch / 'selection-reconciliation.json'
        unresolved = {(item['source'],item['player']) for item in json.loads(held.read_text())} if held.exists() else set()
        for cert in (cert for packet in selection.values() for cert in packet):
            if (cert['source_url'],cert['player']['label']) not in unresolved and cert['certificate_key'] not in selection_done:
                selection_done.append(cert['certificate_key'])
        attempted.update(map(source_id, result['attempted_urls']))
        processed.update(map(source_id, result['attempted_urls']))
        replayed.update(result.get('replayed_urls', []))
        write(state_path, state)
        before = {f'{row["club_id"]}:{row["unit"]}': tuple(row.get(field) for field in (
            'canonical_matches', 'canonical_player_rows', 'unknown_minutes', 'canonical_national_matches',
            'canonical_national_player_rows', 'national_numeric_minutes')) for row in matrix['units']}
        refresh_matrix(matrix, candidate)
        if progress:
            check_ready_units(before)
        write(matrix_path, matrix)
        if result['source_stop'] in {'ACCESS_RESTRICTED', 'RATE_LIMITED', 'ROBOTS_DISALLOWED'}:
            source_stop = result['source_stop']
            break

    write(matrix_path, refresh_matrix(matrix, candidate))
    state.update(accepted=sum(row['data_accepted'] for row in matrix['units']),
                 remaining_units=[dict(club_id=row['club_id'], unit=row['unit'],
                                       next_action=row.get('next_action'))
                                  for row in matrix['units'] if not row['data_accepted']])
    state['state'] = (source_stop or 'SOURCE_MAPPING_OR_SCOPE_EVIDENCE_REQUIRED') if state['remaining_units'] else 'DATA_ACCEPTED'
    write(state_path, state)
    return state


def source_id(url):
    parsed = urlsplit(url)
    return parsed.hostname, parsed.path.rstrip('/').rsplit('/', 1)[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('work', type=Path)
    parser.add_argument('--continuous', action='store_true')
    parser.add_argument('--national-unit', help='Limit continuous replay to national evidence for a source-ready unit')
    parser.add_argument('--national-sources', type=Path, help='Evidence-backed Sky match sheets for the selected unit')
    args = parser.parse_args()
    if args.national_sources and not args.national_unit:
        parser.error('--national-sources requires --national-unit')
    if args.national_unit and not args.continuous:
        parser.error('--national-unit requires --continuous')
    try:
        sources = json.loads(local_path(args.national_sources).read_text()) if args.national_sources else ()
        result = (continue_batches(args.plan, args.work, args.national_unit, sources) if args.continuous
                  else run(args.plan, args.work))
    except (ValueError, OSError, sqlite3.Error, subprocess.CalledProcessError) as error:
        result = dict(batch=args.work.name, state='FAIL', exceptions=[
            dict(kind='TECHNICAL_FAILURE', evidence=str(args.plan),
                 next_action=f'Inspect batch inputs and checkpoint ({error})')])
    print(json.dumps(result, ensure_ascii=False))
    return result['state'] == 'FAIL'


if __name__ == '__main__':
    raise SystemExit(main())
