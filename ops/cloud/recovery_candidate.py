"""Run the existing isolated production acceptance over a verified catch-up window."""
import base64
import hashlib
import json
import re
import tarfile
import math
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone

from ops.cloud import release_acceptance
from ops.cloud.production_entry import with_audits
from squad_pipeline.cloud.driver import validate_monotonic
from squad_pipeline.core import ROOT


def coverage_start(path, fallback):
    """Find the earliest established scan boundary across coverage lanes."""
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        teams = {row[0] for row in db.execute('SELECT club_id FROM clubs')}
        latest = dict(db.execute("""
            SELECT p.club_id,max(m.kickoff) FROM player_match_minutes p
            JOIN matches m USING(match_id) WHERE m.kickoff IS NOT NULL GROUP BY p.club_id
        """))
        scans = {(club, provider): completed for club, provider, completed in
                 db.execute('SELECT club_id,provider,completed_at FROM live_scan_windows')} if 'live_scan_windows' in tables else {}
        national = db.execute(
            "SELECT completed_at FROM pipeline_checkpoints WHERE scope='international'"
        ).fetchone() if 'pipeline_checkpoints' in tables else None
    boundaries = []
    for club in teams:
        for provider in ('Sky', 'Torlytics'):
            value = scans.get((club, provider)) or latest.get(club)
            boundaries.append(min(datetime.fromisoformat(value), fallback) if value else fallback)
    boundaries.append(min(datetime.fromisoformat(national[0]), fallback) if national else fallback)
    return min(boundaries)

def retained_profiles(store, head, root, now):
    """Read only same-day, hash-verified public profile captures for an exact retry."""
    request = store.api('GET', store.base + '/contents/production-recovery-request.json?ref=' + head)
    material = json.loads(base64.b64decode(request['content'])).get('retained_archive')
    if not material:
        return
    archive = root / 'earlier-daily-evidence.tar.gz'
    store.download(material['id'], archive)
    if archive.stat().st_size != material['size'] or hashlib.sha256(archive.read_bytes()).hexdigest() != material['sha256']:
        raise ValueError('RETAINED_DAILY_EVIDENCE_HASH_MISMATCH')
    profiles = {}
    with tarfile.open(archive) as package:
        for entry in package.getmembers():
            if not entry.isfile() or not entry.name.startswith('metadata/') or not entry.name.endswith('.metadata.json'):
                continue
            record = json.load(package.extractfile(entry))
            url = record.get('requested_url') or ''
            if not re.fullmatch(r'https://www\.fotmob\.com/players/[0-9]+', url) or record.get('http_status') != 200:
                continue
            stamp = datetime.fromisoformat(record['response_timestamp'])
            if stamp.date() != now.date() or stamp > now:
                continue
            raw_entry = package.getmember(record['path'])
            if not raw_entry.isfile() or raw_entry.size > 6_000_000:
                raise ValueError('RETAINED_PROFILE_BODY_INVALID')
            raw = package.extractfile(raw_entry).read()
            checksum = hashlib.sha256(raw).hexdigest()
            if checksum != record['sha256']:
                raise ValueError('RETAINED_PROFILE_HASH_MISMATCH')
            path = root / (checksum + '.response')
            if not path.exists():
                path.write_bytes(raw)
            profiles[url] = dict(record, path=str(path.relative_to(ROOT)),
                                  retained_origin='VERIFIED_ISOLATED_RUN_ARCHIVE')
    if profiles:
        index = root / 'profile-index.json'
        index.write_text(json.dumps(profiles))
        os.environ['FUTOGRAMA_ISOLATED_RETAINED_PROFILES'] = str(index)
    print(json.dumps(dict(retained_profile_count=len(profiles))), flush=True)

def main():
    release_acceptance.guard()
    production = release_acceptance.store('main')
    isolated = release_acceptance.store(release_acceptance.REF)
    production_head, production_pointer = production.head()
    candidate_head, candidate_pointer = isolated.head()
    if (production_head != os.environ['EXPECTED_PRODUCTION_HEAD']
            or candidate_head != os.environ['GITHUB_SHA']
            or candidate_pointer['current']['database']['sha256'] != os.environ['EXPECTED_CANDIDATE_SHA']
            or production_pointer['current']['database']['sha256'] != os.environ['EXPECTED_DATABASE_SHA']):
        raise ValueError('RECOVERY_REQUIRES_EXACT_FRESH_PRODUCTION_BASE')

    runtime = ROOT / 'ops/cloud/runtime.json'
    settings = json.loads(runtime.read_text())
    published = datetime.fromisoformat(production_pointer['current']['created_at'])
    with TemporaryDirectory(dir=ROOT) as temp:
        baseline = Path(temp) / 'production.sqlite'
        restored_head, restored_pointer = production.restore(baseline)
        if (restored_head, restored_pointer) != (production_head, production_pointer):
            raise ValueError('PRODUCTION_BASE_MOVED_DURING_COVERAGE_READBACK')
        detached = Path(temp) / 'candidate.sqlite'
        restored_candidate = isolated.restore(detached)
        if restored_candidate != (candidate_head, candidate_pointer):
            raise ValueError('ISOLATED_CANDIDATE_MOVED_DURING_READBACK')
        validate_monotonic(baseline, detached)
        start = coverage_start(baseline, published)
    outage_hours = math.ceil((datetime.now(timezone.utc) - start).total_seconds() / 3600)
    settings['lookback_hours'] += max(0, outage_hours)
    runtime.write_text(json.dumps(settings) + '\n')
    print(json.dumps(dict(baseline_published_at=published.isoformat(),
                          verified_coverage_start=start.isoformat(),
                          configured_catchup_hours=settings['lookback_hours'])), flush=True)

    release_acceptance.run = with_audits(release_acceptance.run)
    with TemporaryDirectory(prefix='retained-daily-', dir=ROOT) as staging:
        retained_profiles(isolated, candidate_head, Path(staging), datetime.now(timezone.utc))
        release_acceptance.producer()


if __name__ == '__main__':
    main()
