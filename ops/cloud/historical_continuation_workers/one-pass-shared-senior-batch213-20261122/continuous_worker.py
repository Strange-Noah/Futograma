"""Wire the historical SourceService, header preparation and ingestion for one subset."""
import json
import runpy
import sys
import subprocess
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path.cwd()))
from ops.cloud.historical_batch import write
from squad_pipeline.source_interface import EvidenceStore, SourceRequest, SourceService

queue, candidate, reference, batch = map(Path, sys.argv[1:5])
stages = Path(__file__).resolve().parent
club = globals().get('club', False)
archive = Path(sys.argv[5]) if len(sys.argv) > 5 else batch / 'acquisition'
receipt = archive / 'results-private.json'
archive.mkdir(exist_ok=True)
rows = json.loads(receipt.read_text()) if receipt.exists() else []
urls = [url for url in json.loads(queue.read_text()) if url not in {r['url'] for r in rows}]
service = SourceService(EvidenceStore(archive), 'historical-continuation',
                        {urlsplit(url).hostname for url in urls},
                        max_calls=500, max_bytes=200_000_000)
for url in urls:
    request = SourceRequest('Sky', 'match', url, 'Historical match for observed six-club players',
                            policy='LIVE_ALLOWED')
    result = service.acquire(request)
    rows.append(dict(url=url, provider='Sky', outcome=result.outcome, reason=result.reason,
                     artifact=asdict(result.artifact) if result.artifact else None))
    write(receipt, rows)
    if result.outcome in {'ACCESS_RESTRICTED', 'RATE_LIMITED', 'ROBOTS_DISALLOWED', 'BUDGET_EXHAUSTED'}:
        break

def stage_directory(name):
    """Reuse a complete stage and keep interrupted outputs available for review."""
    attempts = sorted(batch.glob(name + '-*'))
    if attempts and (attempts[-1] / ('results-private.json' if name == 'parsed' else 'SUMMARY.json')).exists():
        return attempts[-1]
    return batch / f'{name}-{len(attempts) + 1:03d}'


if club:
    private = stages.parent
    parsed = stage_directory('parsed')
    native_receipt = parsed / 'results-private.json'
    if not native_receipt.exists():
        runpy.run_path(str(private / 'parse_arsenal_last_h1_20261121.py'),
                       init_globals=dict(root=parsed, acquisition_roots=[archive]))
    # Keep previously evidenced fixture IDs when publisher labels differ.
    attempts = sorted(batch.glob('fixture-labels-*'))
    labelled = attempts[-1] if attempts else None
    if labelled is None or not (labelled / 'DECISIONS_PRIVATE.json').exists():
        labelled = batch / f'fixture-labels-{len(attempts) + 1:03d}'
        runpy.run_path(str(private / 'adjudicate_shared_fixture_team_labels_20261121.py'),
                       init_globals=dict(source=candidate, root=labelled,
                                         folders=[str(parsed.relative_to(private))]))
    ingestion_source = labelled/'candidate.sqlite' if json.loads(
        (labelled/'DECISIONS_PRIVATE.json').read_text()) else candidate
    identity_file = batch / 'identities-private.json'
    if not identity_file.exists():
        subprocess.run([sys.executable, '-B', str(private/'extend_observed_identity_dates_20261121.py'),
                        str(private/'one-pass-scoped-identities-r19-20261121.json'),
                        str(native_receipt), str(identity_file)], check=True)
    output = stage_directory('ingestion')
    if not (output / 'SUMMARY.json').exists():
        runpy.run_path(str(private / 'ingest_corroborated_source_union_20261121.py'),
                       init_globals=dict(source=ingestion_source, root=output, source_receipts=[native_receipt],
                                         identity_file=identity_file))
    result = json.loads((output / 'SUMMARY.json').read_text())
    final = output
    if result['ordinary_export'] == 'PASS':
        minutes = stage_directory('minutes')
        if not (minutes / 'SUMMARY.json').exists():
            runpy.run_path(str(private / 'recover_corroborated_source_union_minutes_20261121.py'),
                           init_globals=dict(source=output/'candidate.sqlite', root=minutes,
                                             source_receipts=[native_receipt], identity_file=identity_file))
        minute_result = json.loads((minutes / 'SUMMARY.json').read_text())
        if minute_result['ordinary_export'] == 'PASS':
            final = minutes
            result['sha256'] = minute_result['sha256']
            result['minutes_recovered'] = minute_result['counts'].get('SUPPLEMENTED_UNKNOWN', 0)
    write(batch / 'result-private.json', dict(result,
        candidate=str(final / 'candidate.sqlite') if result['ordinary_export'] == 'PASS' else str(candidate),
        reference=str(reference), attempted_urls=[row['url'] for row in rows], source_receipt=str(receipt),
        replayed_urls=[row['url'] for row in rows if row.get('continuation_replay')],
        fixture_exceptions=str(output/'results-private.json'),
        identity_exceptions_file=str(output/'unresolved-private.json'),
        source_stop=rows[-1]['outcome'] if rows else None))
    sys.exit(0)

prepared = stage_directory('reference')
if not (prepared / 'SUMMARY.json').exists():
    runpy.run_path(str(stages / 'prepare.py'), init_globals=dict(
        source=reference, parent=archive, root=prepared, **globals().get("prepare_inputs", {})))
output = stage_directory('ingestion')
if not (output / 'SUMMARY.json').exists():
    runpy.run_path(str(stages / 'ingest.py'), init_globals=dict(
        source=candidate, reference=prepared / 'reference.sqlite', root=output,
        source_receipts=[receipt]))
result = json.loads((output / 'SUMMARY.json').read_text())
write(batch / 'result-private.json', dict(result,
    candidate=str(output / 'candidate.sqlite') if result['ordinary_export'] == 'PASS' else str(candidate),
    reference=str(prepared / 'reference.sqlite'),
    attempted_urls=[row['url'] for row in rows],
    source_receipt=str(receipt),
        replayed_urls=[row['url'] for row in rows if row.get('continuation_replay')],
    fixture_exceptions=str(prepared / 'results-private.json'),
    identity_exceptions_file=str(output / 'identity-exceptions-private.json'),
    source_stop=rows[-1]['outcome'] if rows else None))
