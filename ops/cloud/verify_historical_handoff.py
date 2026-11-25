"""Read-only verification of a historical handoff from private Release assets."""
import argparse
import hashlib
from contextlib import closing
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

from squad_pipeline.cloud.export import export
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import sha
from squad_pipeline.source_interface import EvidenceStore
from ops.cloud.historical_unit import artifact


def relative_path(value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != '.cloud':
        raise ValueError('UNSAFE_HANDOFF_ASSET_PATH')
    return path


def verify_archive(archive_path, root, manifest):
    expected = {entry['path']: entry for entry in manifest['files']}
    options = dict(fileobj=archive_path) if hasattr(archive_path, 'read') else dict(name=archive_path)
    with tarfile.open(mode='r|gz', **options) as archive:
        for entry in archive:
            item = expected.pop(entry.name)
            target = root / relative_path(entry.name)
            if not entry.isfile() or entry.size != item['size']:
                raise ValueError('INVALID_EVIDENCE_MEMBER')
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as output:
                shutil.copyfileobj(archive.extractfile(entry), output)
            if sha(target) != item['sha256']:
                raise ValueError('EVIDENCE_MEMBER_HASH_MISMATCH')
    if expected:
        raise ValueError('EVIDENCE_MEMBERS_MISSING')


def relocate_json(path, old, root):
    def relocate(value):
        if isinstance(value, dict):
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        if isinstance(value, str) and value.startswith(old + '/.cloud/'):
            return str(root / relative_path(value.removeprefix(old + '/')))
        return value

    before = json.loads(path.read_text())
    after = relocate(before)
    if after != before:
        path.write_text(json.dumps(after, ensure_ascii=False, indent=2) + '\n')
    return after


class EvidenceArchiveReader:
    """Verify and consume one immutable Release asset at a time."""

    def __init__(self, store, records, root):
        self.store = store
        self.records = iter(records)
        self.root = root
        self.digest = hashlib.sha256()
        self.size = 0
        self.stream = None
        self.path = None

    def read(self, size):
        output = bytearray()
        while len(output) < size:
            if self.stream is None:
                record = next(self.records, None)
                if record is None:
                    break
                if not re.fullmatch(r'historical-evidence-part-[0-9]{4}\.gz', record['name']):
                    raise ValueError('INVALID_EVIDENCE_PART_NAME')
                self.path = self.root / record['name']
                self.store.download(record['id'], self.path)
                if self.path.stat().st_size != record['size'] or sha(self.path) != record['sha256']:
                    raise ValueError('EVIDENCE_PART_HASH_OR_SIZE_MISMATCH')
                self.stream = self.path.open('rb')
            data = self.stream.read(size - len(output))
            if data:
                output += data
                self.digest.update(data)
                self.size += len(data)
            else:
                self.close()
        return bytes(output)

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.path is not None:
            self.path.unlink(missing_ok=True)
            self.path = None


def verify(store, handoff, root, code):
    root.mkdir(exist_ok=False)
    executed = subprocess.check_output(['git', '-C', str(code), 'rev-parse', 'HEAD'], text=True).strip()
    if executed != handoff['code_commit']:
        raise ValueError('EXECUTED_CODE_COMMIT_MISMATCH')
    manifest_file = root / 'manifest.json'
    store.download(handoff['manifest_asset']['id'], manifest_file)
    if sha(manifest_file) != handoff['manifest_asset']['sha256']:
        raise ValueError('HANDOFF_MANIFEST_HASH_MISMATCH')
    manifest = json.loads(manifest_file.read_text())
    archive = manifest['evidence_archive']
    if 'evidence_assets' in handoff:
        parts = handoff['evidence_assets']
        description = [{key: r[key] for key in ('name', 'size', 'sha256')} for r in parts]
        if not description or description != archive.get('parts') or sum(p['size'] for p in parts) != archive['size']:
            raise ValueError('EVIDENCE_PART_MANIFEST_MISMATCH')
        with closing(EvidenceArchiveReader(store, parts, root)) as stream:
            verify_archive(stream, root, manifest)
            while stream.read(1024**2):
                pass
            evidence_sha, evidence_size = stream.digest.hexdigest(), stream.size
    else:
        evidence = root / 'evidence.tar.gz'
        store.download(handoff['evidence_asset']['id'], evidence)
        evidence_sha, evidence_size = sha(evidence), evidence.stat().st_size
        if evidence_sha != archive['sha256'] or evidence_size != archive['size']:
            raise ValueError('EVIDENCE_ARCHIVE_HASH_OR_SIZE_MISMATCH')
        verify_archive(evidence, root, manifest)
    if evidence_sha != archive['sha256'] or evidence_size != archive['size']:
        raise ValueError('EVIDENCE_ARCHIVE_HASH_OR_SIZE_MISMATCH')

    candidate = root / relative_path(manifest['candidate'])
    head, pointer = store.restore(candidate)
    if pointer['current']['code_commit'] != executed or pointer['current']['database']['sha256'] != manifest['candidate_sha256']:
        raise ValueError('RESTORED_POINTER_MISMATCH')
    checkpoint = root / relative_path(handoff['checkpoint'])
    matrix_path = root / relative_path(handoff['matrix'])
    old = manifest['original_workspace']
    for entry in manifest['files']:
        path = root / relative_path(entry['path'])
        if path.suffix == '.json':
            relocate_json(path, old, root)
    config = json.loads((root / relative_path(handoff['runner_config'])).read_text())
    matrix = json.loads(matrix_path.read_text())
    state = json.loads(checkpoint.read_text())
    accepted = sum(unit['data_accepted'] is True for unit in matrix['units'])
    source_ready = sum(unit['source_ready'] is True for unit in matrix['units'])
    if (len(matrix['units']), accepted, source_ready) != (manifest['units'], manifest['accepted'], manifest['source_ready']):
        raise ValueError('MATRIX_COUNTS_MISMATCH')
    if candidate != Path(state['candidate']) or candidate != root / relative_path(matrix['candidate']) or state['accepted'] != accepted:
        raise ValueError('CHECKPOINT_MATRIX_MISMATCH')
    if not Path(state['reference']).is_file():
        raise ValueError('REFERENCE_DATABASE_MISSING')
    worker_source = code / 'ops/cloud/historical_continuation_workers'
    for source in worker_source.rglob('*.py'):
        target = root / '.cloud' / source.relative_to(worker_source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for value in [config['worker'], *config['workers'].values(), *config['source_ledgers'], *config['catalogues']]:
        if not (root / relative_path(value)).is_file():
            raise ValueError('RUNNER_INPUT_MISSING')
    reused = 0
    for unit in handoff['accepted_snapshots'].values():
        receipt = json.loads((root / relative_path(unit['receipt'])).read_text())
        database = root / relative_path(unit['database'])
        if receipt['data_accepted'] is not True or receipt['sqlite_sha256'] != unit['sha256'] or sha(database) != unit['sha256']:
            raise ValueError('ACCEPTED_SNAPSHOT_MISMATCH')
        reused += 1
    if reused != accepted:
        raise ValueError('ACCEPTED_SNAPSHOT_COUNT_MISMATCH')
    scope = root / relative_path(handoff['next_action']['cohorts'])
    if not scope.is_file() or not handoff['next_action']['obligation']:
        raise ValueError('NEXT_SCOPE_MISSING')
    source_receipt = root / relative_path(handoff['next_action']['retained_source_receipt'])
    stored = EvidenceStore(source_receipt.parent, archives=[root])
    captures = json.loads(source_receipt.read_text())
    proof = next(row['artifact'] for row in captures if row.get('outcome') == 'FOUND' and row.get('artifact'))
    proof['request']['policy'] = 'ARCHIVE_ONLY'
    stored.read(artifact(proof))
    export(candidate, root / 'isolated-export', head, pointer, code / 'web')
    return dict(status='PASS', code_commit=executed, data_commit=head, database_sha256=sha(candidate),
                evidence_sha256=evidence_sha, evidence_files=len(manifest['files']), accepted=accepted,
                source_ready=source_ready, total=manifest['units'], accepted_snapshots_reused=reused,
                ordinary_export='PASS', evidence_replay='PASS', next_scope=handoff['next_action']['unit'],
                mac_files_required=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entry', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    handoff = json.loads(args.entry.read_text())
    ref = handoff['data_ref']
    if (os.environ.get('GITHUB_REPOSITORY') != 'Strange-Noah/Futograma-data'
            or not ref.startswith('codex/historical-')
            or os.environ.get('GITHUB_REF') != 'refs/heads/' + ref):
        raise ValueError('ISOLATED_HANDOFF_ACTION_REQUIRED')
    if handoff['data_ref'] != ref or not re.fullmatch('[a-f0-9]{40}', handoff['code_commit']):
        raise ValueError('INVALID_HANDOFF_ENTRY')
    store = GitHubStore('Strange-Noah/Futograma-data', os.environ['GH_TOKEN'], ref)
    result = verify(store, handoff, args.output.resolve(), Path.cwd())
    print('HISTORICAL_CLOUD_RESTORE_RECEIPT=' + json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
