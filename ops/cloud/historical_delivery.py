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
from collections import Counter
from contextlib import closing
from pathlib import Path

from ops.cloud.historical_batch import continue_batches, discover_gaps, unit_window, write
from ops.cloud.verify_historical_handoff import relative_path, relocate_json, verify
from squad_pipeline.cloud.export import export
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import inspect, pack, sha
from squad_pipeline.cloud.storage import StaleWriter
from squad_pipeline.core import ROOT
from squad_pipeline.reconstruction.calendar import fifa_windows
from squad_pipeline.reconstruction.engine import run
from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
from squad_pipeline.source_interface import EvidenceStore, SourceService


def read(path):
    return json.loads(path.read_text())


def download(store, record, target):
    store.download(record['id'], target)
    if target.stat().st_size != record['size'] or sha(target) != record['sha256']:
        raise ValueError('HISTORICAL_ASSET_DIGEST_MISMATCH')
    return target


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
    workers = ROOT / 'ops/cloud/historical_continuation_workers'
    for source in workers.rglob('*.py'):
        dest = target / source.relative_to(workers)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    return verified, applied


def runtime_report(candidate, matrix, key, handoff, config, destination):
    row = next(row for row in matrix['units'] if f'{row["club_id"]}:{row["unit"]}' == key)
    unit = unit_window(row)
    work = destination.parent
    candidate = candidate.resolve()
    begin, end = unit['start'], unit['end_exclusive']
    cutoff = (datetime.date.fromisoformat(end) - datetime.timedelta(days=1)).isoformat()
    _, obligations = discover_gaps(candidate, dict(matrix, units=[row]), config.get('catalogues', ()))
    calendar = None
    if config.get('calendar'):
        calendar = ROOT / relative_path(config['calendar'])
    calendars = []
    if calendar is not None:
        observed = datetime.datetime.fromtimestamp(calendar.stat().st_mtime, datetime.timezone.utc).isoformat()
        calendars = fifa_windows(calendar.read_bytes(),
                    'https://inside.fifa.com/tournament-organisation/international-match-calendars',
                    observed, begin, cutoff)
    # Audit operates on a disposable SQLite copy. Its disposition and evidence
    # remain separate from the existing whole-unit acceptance gate.
    audit_copy = work / 'audit-input.sqlite'
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as src, closing(
            sqlite3.connect(audit_copy)) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(audit_copy)) as db:
        db.row_factory = sqlite3.Row
        service = SourceService(EvidenceStore(work / 'audit-sources', archives=[ROOT / '.cloud']),
                                'historical-delivery-audit', set(), max_calls=0)
        adapter = RetainedAdapter(db, ROOT / '.cloud', unit['club_id'], calendars, source_service=service)
        try:
            audit = run(db, adapter, begin, cutoff, max_attempts=200)
            write(work / 'audit-private.json', audit)
            audit_summary = dict(stages=audit['stages'], metrics=audit['metrics'],
                                 retained_calendar_supplied=bool(calendar),
                                 resolution_states=dict(Counter(item.get('state') for item in audit['resolutions'])))
        except (ValueError, OSError, sqlite3.Error) as exc:
            audit_summary = dict(status='AUDIT_INPUT_BLOCKER', detail=str(exc))
        club = db.execute("""
            SELECT m.competition_id,count(DISTINCT m.match_id),count(*),
                   count(p.minutes),sum(nullif(m.kickoff, '') IS NULL)
            FROM player_match_minutes p JOIN matches m USING(match_id)
            WHERE p.club_id=? AND m.local_date>=? AND m.local_date<?
            GROUP BY m.competition_id ORDER BY m.competition_id
        """, (unit['club_id'], begin, end)).fetchall()
    audit_copy.unlink()
    checks = config.get('unit_plans', {})
    result = dict(unit=key, whole_unit_plan_present=key in checks, data_accepted=row['data_accepted'],
                  source_ready=row['source_ready'], audit=audit_summary,
                  club_coverage=[list(item) for item in club],
                  club_coverage_columns=['competition_id','matches','appearances','numeric_minutes','missing_kickoff_rows'],
                  pending=obligations[key])
    closed = handoff.get('latest_evidence_checkpoint', {}).get('official_selection', {})
    if handoff.get('latest_evidence_checkpoint', {}).get('unit') == key:
        result['reused_verified_appearances'] = sum(
            len(records) for fixture in closed.get('verified_existing_appearances', ())
            for records in fixture['players'].values())
    write(destination, result)
    return result


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
    verified, adopted = stage(store, handoff, base_checkout, temp)
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
    state = continue_batches(config_path, checkpoint.parent, key, sources,
                             national_only=request.get('scope') == 'national',
                             duration_certificates=request.get('duration_certificates', ()))
    candidate = Path(state['candidate']).resolve()
    matrix = read(matrix_path)
    # The exporter reads the historical snapshot; the pointer is attached only
    # after the new bundle passes verified readback and compare-and-swap.
    quality = inspect(candidate)
    export_pointer = dict(pointer, current=dict(pointer['current'], database=quality))
    export(candidate, temp / 'export', parent, export_pointer, ROOT / 'web')
    work = checkpoint.parent / ('delivery-' + os.environ['GITHUB_RUN_ID'])
    work.mkdir()
    report = runtime_report(candidate, matrix, key, handoff, read(config_path), work / 'scope.json')
    duration_result = checkpoint.parent / 'duration-obligations.json'
    if request.get('duration_certificates') and duration_result.exists():
        report['duration_exceptions'] = read(duration_result)
    batches = state['batches'][before:]
    fields = ('new_matches','new_player_match_rows','new_national_matches','new_national_player_rows','minutes_recovered')
    report.update(status='CHECKPOINT_VERIFIED', batches=len(batches),
                  counts={field:sum(batch.get(field, 0) or 0 for batch in batches) for field in fields},
                  new_national_numeric_minutes=national_numeric(candidate)-numeric_before,
                  state=state['state'], ordinary_export='PASS', reused_legacy_delta=adopted,
                  accepted=sum(row['data_accepted'] for row in matrix['units']),
                  source_ready=sum(row['source_ready'] for row in matrix['units']),
                  run=f'https://github.com/{repository}/actions/runs/{os.environ["GITHUB_RUN_ID"]}')
    write(work / 'scope.json', report)
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
    manifest, archive = build_bundle(candidate, report, matrix, temp)
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
