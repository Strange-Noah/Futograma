"""Extract the verified latest batch and its receipts from a private historical run."""
import argparse
import json
import tarfile
from collections import Counter
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from squad_pipeline.cloud.quality import sha


def read_json(path):
    return json.loads(path.read_text())


def extract(archive, output, run_id, parent, code):
    output.mkdir(exist_ok=False)
    with ZipFile(archive) as box:
        receipt = json.loads(box.read('receipt.json'))
        state = json.loads(box.read('continuation-private.json'))
        matrix = box.read('matrix-private.json')
        if (receipt['run_id'], receipt['parent_data_commit'], receipt['code_commit']) != (run_id, parent, code):
            raise ValueError('HISTORICAL_RUN_BINDING_MISMATCH')
        if receipt['added_batches'] != 1:
            raise ValueError('REVIEW_REQUIRES_ONE_NEW_BATCH')
        final_batch = state['batches'][-1]
        old_root, sep, queue = final_batch['queue'].partition('/.cloud/')
        if not sep or not old_root:
            raise ValueError('INVALID_ARCHIVED_QUEUE_PATH')
        prefix = PurePosixPath('.cloud', queue).parent
        if prefix.parent != PurePosixPath('.cloud/historical-continuation-20261122') or not prefix.name.startswith('batch-'):
            raise ValueError('INVALID_ARCHIVED_BATCH_PATH')
        expected = set()
        scratch = output / 'unpacked'
        scratch.mkdir()
        with box.open('working-checkpoint.tar.gz') as raw, tarfile.open(fileobj=raw, mode='r|gz') as tar:
            for entry in tar:
                path = PurePosixPath(entry.name)
                if not path.is_relative_to(prefix):
                    continue
                if (path.is_absolute() or '..' in path.parts or not (entry.isdir() or entry.isfile())
                        or path in expected):
                    raise ValueError('UNSAFE_ARCHIVED_BATCH_MEMBER')
                expected.add(path)
                tar.extract(entry, scratch, filter='data')
    candidate_path = PurePosixPath(state['candidate'].removeprefix(old_root + '/'))
    if not candidate_path.is_relative_to(prefix) or sha(scratch / candidate_path) != receipt['candidate_sha256']:
        raise ValueError('ARCHIVED_CANDIDATE_HASH_MISMATCH')
    batch = scratch / prefix
    acquisitions = read_json(batch / 'acquisition/results-private.json')
    prepared = read_json(scratch / final_batch['fixture_exceptions'].removeprefix(old_root + '/'))
    ingested = read_json(batch / candidate_path.relative_to(prefix).parent / 'results-private.json')
    identity = read_json(scratch / final_batch['identity_exceptions_file'].removeprefix(old_root + '/'))
    receipt.update(
        original_workspace=old_root,
        batch=str(prefix),
        acquisitions=[dict(url=row['url'], outcome=row['outcome'], reason=row.get('reason'),
                           sha256=(row.get('artifact') or {}).get('content_sha256')) for row in acquisitions],
        header_results=dict(Counter(row['state'] for row in prepared)),
        headers=prepared,
        ingestion_results=dict(Counter(row['state'] for row in ingested)),
        identity_exceptions=len(identity),
    )
    with tarfile.open(output / 'historical-batch-delta.tar.gz', 'w:gz', compresslevel=4) as tar:
        tar.add(batch, arcname=str(prefix))
    (output / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    (output / 'continuation-private.json').write_text(json.dumps(state, ensure_ascii=False) + '\n')
    (output / 'matrix-private.json').write_bytes(matrix)
    print(json.dumps({key: receipt[key] for key in ('run_id', 'header_results', 'ingestion_results',
                                                    'identity_exceptions', 'candidate_sha256')}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--parent', required=True)
    parser.add_argument('--code', required=True)
    args = parser.parse_args()
    extract(args.artifact, args.output, args.run_id, args.parent, args.code)


if __name__ == '__main__':
    main()
