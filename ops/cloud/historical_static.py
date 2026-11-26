"""Verified, read-only static projections of independently accepted units."""
import gzip
import json
import os
import sqlite3
from contextlib import closing

from ops.cloud.historical_batch import local_path, unit_window, write
from squad_pipeline.cloud.quality import sha
from squad_pipeline.core import ROOT
from squad_pipeline.international_store import digest


def project(database, unit, players):
    club_id = unit['club_id']
    window = (unit['start'], unit['end_exclusive'])
    marks = ','.join('?' for _ in players)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row

        def records(query, args):
            return [dict(row) for row in db.execute(query, args)]

        club = records("""
            SELECT p.* FROM player_match_minutes p JOIN matches m USING(match_id)
            WHERE p.club_id=? AND m.local_date>=? AND m.local_date<?
            ORDER BY p.match_id,p.player_id
        """, (club_id, *window))
        fixtures = records("""
            SELECT m.* FROM matches m WHERE EXISTS (
                SELECT 1 FROM player_match_minutes p WHERE p.match_id=m.match_id AND p.club_id=?
            ) AND m.local_date>=? AND m.local_date<? ORDER BY m.match_id
        """, (club_id, *window))
        national = records(f"""
            SELECT a.* FROM international_appearances a JOIN international_matches m USING(match_id)
            WHERE m.madrid_date>=? AND m.madrid_date<? AND a.player_id IN ({marks})
            ORDER BY a.match_id,a.player_id
        """, (*window, *players))
        national_fixtures = records(f"""
            SELECT m.* FROM international_matches m WHERE m.madrid_date>=? AND m.madrid_date<?
            AND EXISTS (SELECT 1 FROM international_appearances a
                        WHERE a.match_id=m.match_id AND a.player_id IN ({marks}))
            ORDER BY m.match_id
        """, (*window, *players))
        national_teams = sorted({row[field] for row in national_fixtures
                                 for field in ('home_team_key', 'away_team_key')})
        team_marks = ','.join('?' for _ in national_teams) or 'NULL'
        identities = sorted(set(players) | {row['player_id'] for row in club})
        player_marks = ','.join('?' for _ in identities)
        return dict(
            club_appearances=club, club_fixtures=fixtures,
            national_appearances=national, national_fixtures=national_fixtures,
            national_teams=records(f'SELECT * FROM football_teams WHERE team_key IN ({team_marks}) ORDER BY team_key', national_teams),
            players=records(f'SELECT player_id,display_name FROM players WHERE player_id IN ({player_marks}) ORDER BY player_id', identities),
            management=records(f'SELECT * FROM validation_roster_membership WHERE club_id=? AND player_id IN ({marks}) ORDER BY player_id, rowid', (club_id, *players)),
            eligibility=records(f'SELECT * FROM player_eligibility WHERE club_id=? AND player_id IN ({marks}) ORDER BY player_id, rowid', (club_id, *players)))


def frozen_receipts(fingerprints):
    """Index only original bundles referenced by independently accepted receipts."""
    indexed = {}
    for path in sorted((ROOT / '.cloud').rglob('*.json')):
        if not any(word in path.name.lower() for word in ('freeze', 'union', 'cohort', 'population', 'roster', 'acceptance', 'plan')):
            continue
        text = path.read_text()
        if not any(fingerprint in text for fingerprint in fingerprints):
            continue
        root = json.loads(text)
        if not isinstance(root, dict):
            continue
        for item in [root, *(value for value in root.values() if isinstance(value, dict))]:
            fingerprint = item.get('freeze_sha256')
            if (fingerprint in fingerprints and item.get('observed_population_accepted') is True
                    and digest({k: v for k, v in item.items() if k != 'freeze_sha256'}) == fingerprint):
                indexed[fingerprint] = item
    return indexed


def publish_static(store, handoff, config, matrix, work, receipt, selected=(), recovered_frozen_inputs=None):
    if selected and not set(selected) <= set(handoff['accepted_snapshots']):
        raise ValueError('STATIC_PROJECTION_REQUIRES_ACCEPTED_TARGETS')
    eligible = {key: item for key, item in handoff['accepted_snapshots'].items()
                if not selected or key in selected}
    verdicts = {key: json.loads(local_path(item['receipt']).read_text()) for key, item in eligible.items()}
    known = frozen_receipts({item['frozen_union_sha256'] for item in verdicts.values()
                             if item.get('frozen_union_sha256')})

    for key, freeze in (recovered_frozen_inputs or {}).items():
        if key not in eligible:
            raise ValueError('RECOVERED_FREEZE_REQUIRES_SELECTED_ACCEPTED_TARGET')
        fingerprint = verdicts[key].get('frozen_union_sha256')
        if (not fingerprint or freeze.get('freeze_sha256') != fingerprint
                or digest({k: v for k, v in freeze.items() if k != 'freeze_sha256'}) != fingerprint):
            raise ValueError('RECOVERED_FREEZE_FINGERPRINT_MISMATCH')
        known[fingerprint] = freeze

    results, assets = {}, []
    rows = {f'{r["club_id"]}:{r["unit"]}': r for r in matrix['units']}
    for key, metadata in sorted(eligible.items()):
        verdict = verdicts[key]
        freeze = known.get(verdict.get('frozen_union_sha256'))
        path = config.get('unit_plans', {}).get(key)
        if freeze is None and path and local_path(path).is_file():
            plan = json.loads(local_path(path).read_text())
            original = local_path(plan['freeze'])
            if original.is_file():
                freeze = json.loads(original.read_text())
        if freeze is None:
            results[key] = dict(status='ORIGINAL_FREEZE_NOT_RESTORED', plan_reference=bool(path))
            continue
        database = local_path(metadata['database'])
        fingerprint = freeze.get('freeze_sha256')
        players = freeze.get('player_ids')
        unit = unit_window(rows[key])
        if (freeze.get('observed_population_accepted') is not True
                or not players or not isinstance(freeze.get('unit'), dict)
                or any(freeze['unit'].get(k) != v for k, v in unit.items())
                or fingerprint != digest({k: v for k, v in freeze.items() if k != 'freeze_sha256'})
                or fingerprint != verdict.get('frozen_union_sha256')):
            results[key] = dict(status='ORIGINAL_FREEZE_BINDING_NOT_VERIFIED')
            continue
        values = project(database, unit, players)
        counts = {}
        for domain in ('club', 'national'):
            counts[domain + '_rows'] = len(values[domain + '_appearances'])
            counts[domain + '_fixtures'] = len(values[domain + '_fixtures'])
            counts[domain + '_unknown_minutes'] = sum(row['minutes'] is None for row in values[domain + '_appearances'])
        if any(counts[field] != verdict[field] for field in counts if field in verdict):
            results[key] = dict(status='SCOPED_ACCEPTANCE_COUNTS_DIFFER', counts=counts)
            continue
        payload = dict(unit=unit, unit_key=key, original=metadata, original_acceptance=verdict,
                       frozen_roster=freeze, facts=values, facts_sha256=digest(values), counts=counts,
                       retained_evidence_release=handoff['release'])
        bundle = gzip.compress(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode(), mtime=0)
        if json.loads(gzip.decompress(bundle)) != payload:
            raise ValueError('STATIC_PROJECTION_LOCAL_READBACK_MISMATCH')
        dest = work / (key.replace(':', '-').replace('/', '_') + '.json.gz')
        dest.write_bytes(bundle)
        results[key] = dict(status='STATIC_EQUIVALENT', database_bytes=database.stat().st_size,
                            static_bytes=dest.stat().st_size, counts=counts, facts_sha256=payload['facts_sha256'])
        assets.append((key, dest))
    if assets:
        tag = 'historical-static-' + os.environ['GITHUB_RUN_ID']
        release = store.api('POST', store.base + '/releases', dict(
            tag_name=tag, target_commitish=store.ref, name='Verified historical static units',
            body='Scoped exports; original independently accepted recovery assets remain authoritative.', draft=True))
        for key, asset in assets:
            remote = store.upload(release['id'], asset)
            size, checksum = store.download(remote['id'], None)
            if (size, checksum) != (asset.stat().st_size, sha(asset)):
                raise ValueError('STATIC_PROJECTION_REMOTE_READBACK_MISMATCH')
            results[key].update(asset_id=remote['id'], asset_sha256=checksum)
        manifest = work / 'static-acceptance.json'
        write(manifest, results)
        remote = store.upload(release['id'], manifest)
        if store.download(remote['id'], None) != (manifest.stat().st_size, sha(manifest)):
            raise ValueError('STATIC_ACCEPTANCE_REMOTE_READBACK_MISMATCH')
        store.api('PATCH', store.base + '/releases/' + str(release['id']), dict(draft=False))
        receipt('static-equivalence', release=tag, count=len(assets), results=results)
        return dict(release=tag, results=results)
    else:
        receipt('static-equivalence', count=0, results=results)
        return dict(release=None, results=results)
