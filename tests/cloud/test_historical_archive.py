import hashlib
from pathlib import Path
import shutil
import tarfile
from tempfile import TemporaryDirectory
import unittest

from ops.cloud.historical_delivery import EvidenceArchiveWriter
from ops.cloud.verify_historical_handoff import EvidenceArchiveReader, verify_archive


class HistoricalArchiveTests(unittest.TestCase):
    def test_split_stream_round_trip_and_digest(self):
        with TemporaryDirectory() as temp:
            work = Path(temp)
            data = bytes(range(256)) * 20
            source = work / 'source.json'
            source.write_bytes(data)
            writer = EvidenceArchiveWriter(work, limit=97)
            with tarfile.open(fileobj=writer, mode='w|gz', compresslevel=4) as tar:
                tar.add(source, arcname='.cloud/source.json')
            writer.close()
            self.assertGreater(len(writer.parts), 1)

            class Store:
                def download(self, asset_id, destination):
                    shutil.copyfile(writer.parts[asset_id], destination)

            records = [dict(id=i, name=path.name, size=path.stat().st_size,
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                       for i, path in enumerate(writer.parts)]
            restore = work / 'restore'
            restore.mkdir()
            reader = EvidenceArchiveReader(Store(), records, restore)
            manifest = dict(files=[dict(path='.cloud/source.json', size=len(data),
                                        sha256=hashlib.sha256(data).hexdigest())])
            verify_archive(reader, restore, manifest)
            while reader.read(64):
                pass
            self.assertEqual((restore / '.cloud/source.json').read_bytes(), data)
            self.assertEqual(reader.size, writer.size)
            self.assertEqual(reader.digest.hexdigest(), writer.digest.hexdigest())
            self.assertEqual(list(restore.glob('historical-evidence-part-*')), [])

    def test_release_part_is_retired_only_after_retention(self):
        with TemporaryDirectory() as temp:
            work = Path(temp)
            remote = {}

            def retain(path):
                self.assertEqual(list(work.iterdir()), [path])
                data = path.read_bytes()
                remote[path.name] = data
                return dict(name=path.name, size=len(data),
                            sha256=hashlib.sha256(data).hexdigest())

            writer = EvidenceArchiveWriter(work, limit=5, retain=retain)
            writer.write(b'hello archive')
            writer.close()
            self.assertEqual(list(work.iterdir()), [])
            self.assertEqual(b''.join(remote.values()), b'hello archive')
            self.assertEqual(sum(part['size'] for part in writer.records), 13)
