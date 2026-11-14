"""Run trusted, project-local historical replay and audit scripts in sequence."""
import argparse
import fcntl
import json
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

from ops.cloud.historical_unit import within
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


def counts(database, freeze):
    ids = sorted(set(freeze['player_ids']))
    if not ids:
        raise ValueError('EMPTY_OBSERVED_UNION')
    unit = freeze['unit']
    args = [unit['start'], unit['end_exclusive']]
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        club = db.execute(
            'SELECT count(DISTINCT p.match_id), count(*) FROM player_match_minutes p '
            'JOIN matches m USING(match_id) WHERE p.club_id=? AND m.local_date>=? AND m.local_date<?',
            [unit['club_id'], *args]).fetchone()
        national = db.execute(
            'SELECT count(DISTINCT a.match_id), count(*), count(*)-count(a.minutes) '
            'FROM international_appearances a JOIN international_matches m USING(match_id) '
            'WHERE m.madrid_date>=? AND m.madrid_date<? AND a.player_id IN ('
            + ','.join('?' for _ in ids) + ')', [*args, *ids]).fetchone()
    return dict(club_fixtures=club[0], club_rows=club[1], players=len(ids),
                national_fixtures=national[0], national_rows=national[1],
                national_unknown_minutes=national[2])


def run(plan_path, work):
    plan_path, work = local_path(plan_path), local_path(work)
    work.mkdir(exist_ok=True)
    with (work / "batch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return execute(plan_path, work)


def execute(plan_path, work):
    plan = json.loads(plan_path.read_text())
    source = local_path(plan['source'])
    freeze_path = local_path(plan['freeze'])
    freeze = json.loads(freeze_path.read_text())
    initial_hash = sha(source)
    if initial_hash != plan['source_sha256']:
        raise ValueError('BATCH_SOURCE_HASH_MISMATCH')
    steps = plan['steps']
    sources = [local_path(p) for p in plan.get('source_receipts', [])]
    binding = dict(plan=sha(plan_path), source=initial_hash, freeze=sha(freeze_path),
                   scripts=[sha(local_path(s['script'])) for s in steps],
                   source_receipts={str(p): sha(p) for p in sources})
    state = work / 'checkpoint.json'
    if state.exists() and json.loads(state.read_text()) != binding:
        raise ValueError('BATCH_INPUTS_CHANGED_USE_NEW_WORK_DIRECTORY')
    write(state, binding)
    exceptions = list({json.dumps(e, sort_keys=True): e for e in plan.get('exceptions', [])}.values())
    if any(e['kind'] not in KINDS for e in exceptions):
        raise ValueError('UNKNOWN_EXCEPTION_KIND')
    report = dict(batch=work.name, unit=freeze['unit_key'], input=binding, steps=[],
                  frozen_union_sha256=freeze['freeze_sha256'], exceptions=exceptions)
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
        report['counts'] = counts(candidate, freeze)
        prior = counts(source, freeze)
        report['changes'] = {k: value - prior[k] for k, value in report['counts'].items()}
        report['accepted_receipt_comparison'] = {
            k: report['counts'][k] == expected for k, expected in plan.get('expected_counts', {}).items()}
        if not all(report['accepted_receipt_comparison'].values()) or sha(source) != initial_hash:
            raise ValueError('ACCEPTED_FACT_OR_INPUT_MISMATCH')
    except (ValueError, OSError, sqlite3.Error) as error:
        exceptions.append(dict(kind='TECHNICAL_FAILURE', evidence=str(candidate),
                               next_action=str(error)))
    for stage in report['steps']:
        detail = json.loads(local_path(stage['details']).read_text())
        metrics = detail.get('metrics', {})
        open_work = {k: metrics[k] for k in ('final_gap_count', 'source_work_required',
                     'accepted_challenges', 'human_pending', 'conflicts', 'unperformed_paths')
                     if metrics.get(k)}
        if open_work:
            exceptions.append(dict(kind='BLOCKING', metrics=open_work,
                                   evidence=stage['details'],
                                   next_action='Review existing audit findings before acceptance'))
    report['state'] = 'FAIL' if any(e['kind'] == 'TECHNICAL_FAILURE' for e in exceptions) else 'PASS'
    report['acceptance'] = 'NEEDS_SCOPE_REVIEW' if exceptions else 'SEE_EXISTING_UNIT_ACCEPTANCE'
    report['candidate'] = str(candidate)
    report['source_receipts'] = plan.get('source_receipts', [])
    report['stages_reused'] = reused
    write(work / 'audit-private.json', report)
    compact = {k: report[k] for k in ('batch', 'unit', 'state', 'acceptance', 'exceptions', 'stages_reused')}
    compact.update(counts=report.get('counts'), changes=report.get('changes'), stages_completed=len(report['steps']),
                   exception_counts=dict(Counter(e['kind'] for e in exceptions)),
                   source_receipts=len(report['source_receipts']),
                   validation=[{'state': 'PASS', 'checks': list(s['validation'])} for s in report['steps']],
                   detailed_receipt=str(work / 'audit-private.json'))
    write(work / 'exceptions.json', compact)
    return compact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('work', type=Path)
    args = parser.parse_args()
    result = run(args.plan, args.work)
    print(json.dumps(result, ensure_ascii=False))
    return result['state'] == 'FAIL'


if __name__ == '__main__':
    raise SystemExit(main())
