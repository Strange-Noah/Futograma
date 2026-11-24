"""Replay retained Senegal sheets using dated official selection evidence."""
import argparse
import json
import runpy
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from ops.cloud.historical_batch import local_path
from squad_pipeline.source_interface import RawSourceArtifact
from squad_pipeline.cloud.quality import sha
from squad_pipeline.international_parse import namekey
from squad_pipeline.source_interface import EvidenceStore, SourceRequest, SourceService

FIXTURES = {
    '549807': ('2026-06-23', 'PMSR-M41-NOR-V-SEN.pdf'),
    '549830': ('2026-06-26', 'PMSR-M62-SEN-V-IRQ.pdf'),
}
FIFA = 'https://www.fifatrainingcentre.com/media/native/tournaments/fifa-world-cup/2026/'
PLAYERS = ('Nicolas Jackson', 'Bara Sapoko Ndiaye')
BARA_PROFILE = 'https://fcbayern.com/en/teams/first-team/bara-sapoko-ndiaye'
BARA_PROVIDER = 'https://www.fotmob.com/players/1798782/bara-ndiaye'


def read(path):
    return json.loads(path.read_text())


def readonly(path):
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)


def replay(candidate, reference, acquisition, worker, output, cached_selection=None):
    output.mkdir(exist_ok=False)
    staged = output / 'reference'
    staged.mkdir()
    ref_copy = staged / 'reference.sqlite'
    with closing(readonly(reference)) as connection, closing(sqlite3.connect(ref_copy)) as copied:
        connection.backup(copied)
    source_receipt = acquisition / 'results-private.json'
    sheets = {row['url'].rsplit('/', 1)[-1]: row for row in read(source_receipt)
              if row.get('outcome') == 'FOUND' and row.get('artifact')}
    store = EvidenceStore(output / 'selection-sources')
    service = SourceService(store, 'historical-bayern-senegal-selection',
                            {'www.fifatrainingcentre.com'}, max_calls=4, max_bytes=32_000_000)
    claims, aliases, captures, selected, identity_blockers = {}, {}, [], [], []
    prior = {}
    if cached_selection:
        prior = {item['url']: item for item in read(cached_selection / 'selection-results-private.json')
                 if item['outcome'] == 'FOUND' and item.get('artifact')}
    prior_store = EvidenceStore(cached_selection / 'selection-sources') if cached_selection else None
    with closing(readonly(candidate)) as db, closing(readonly(reference)) as ref:
        ids = {}
        for player in PLAYERS:
            rows = db.execute("""
                SELECT DISTINCT p.player_id FROM players p
                JOIN player_match_minutes a USING(player_id) JOIN matches m USING(match_id)
                WHERE lower(p.display_name)=lower(?) AND a.club_id=9823
                  AND m.local_date>='2026-01-01' AND m.local_date<'2026-07-01'
                """, (player,)).fetchall()
            if len(rows) != 1 and player == 'Bara Sapoko Ndiaye':
                # Both profiles give the same DOB and Bayern affiliation.
                # The historical DB must already certify the stable provider ID.
                rows = db.execute("""
                    SELECT DISTINCT i.player_id FROM verified_identity_links i
                    JOIN player_match_minutes a USING(player_id) JOIN matches m USING(match_id)
                    WHERE i.provider='FotMob' AND i.provider_player_id='1798782'
                      AND json_extract(i.evidence_json, '$.identity_status')='VERIFIED'
                      AND a.club_id=9823 AND m.local_date>='2026-01-01' AND m.local_date<'2026-07-01'
                    """).fetchall()
            if len(rows) != 1:
                identity_blockers.append(dict(player=player, exact_observed_id_count=len(rows)))
                continue
            ids[player] = rows[0][0]
        if not ids:
            raise ValueError('NO_UNIQUE_BAYERN_OBSERVED_PLAYER_ID')
        for native, (day, document) in FIXTURES.items():
            if native not in sheets:
                raise ValueError('RETAINED_SKY_SHEET_MISSING')
            rows = ref.execute("""
                SELECT m.match_id, h.display_name, a.display_name
                FROM international_matches m
                JOIN international_match_provider_ids v USING(match_id)
                JOIN football_teams h ON h.team_key=m.home_team_key
                JOIN football_teams a ON a.team_key=m.away_team_key
                WHERE v.provider='Sky' AND v.provider_match_id=? AND m.madrid_date=?
                """, (native, day)).fetchall()
            if len(rows) != 1 or 'Senegal' not in rows[0][1:]:
                raise ValueError('SENEGAL_FIXTURE_ID_NOT_UNIQUE')
            request = SourceRequest('FIFA', 'match', FIFA + document,
                                    'Official FIFA World Cup match sheet', policy='LIVE_ALLOWED')
            if request.url in prior:
                raw = dict(prior[request.url]['artifact'])
                old_request = raw.pop('request')
                if old_request['url'] != request.url or old_request['provider'] != 'FIFA':
                    raise ValueError('CACHED_FIFA_SOURCE_MISMATCH')
                retained = RawSourceArtifact(request=request, **raw)
                verified = prior_store.read(retained)
                local = store.import_body(request, verified, retained.metadata, retained.authority)
                result = service.acquire(request, local)
            else:
                result = service.acquire(request)
            captures.append(dict(url=FIFA + document, outcome=result.outcome, reason=result.reason,
                                 artifact=asdict(result.artifact) if result.artifact else None))
            (output / 'selection-results-private.json').write_text(json.dumps(captures, indent=2) + '\n')
            if result.outcome != 'FOUND':
                continue
            pdf = store.read(result.artifact)
            if not pdf.startswith(b'%PDF-'):
                raise ValueError('OFFICIAL_SELECTION_SOURCE_IS_NOT_PDF')
            # Only the first summary pages are relevant to the official selected squad.
            page = subprocess.check_output(['pdftotext', '-f', '1', '-l', '3', '-', '-'],
                                           input=pdf).decode().casefold()
            flattened = ' '.join(page.split())
            if not all(team.casefold() in flattened for team in rows[0][1:]) or not all(
                    player.casefold() in flattened for player in ids):
                raise ValueError('OFFICIAL_MATCHDAY_SELECTION_NEEDS_REVIEW')
            for player in ids:
                source_names, shirt = (
                    (('Nicolas Jackson', 'N Jackson'), '11') if player == 'Nicolas Jackson'
                    else (('Bara Sapoko Ndiaye', 'Bara Ndiaye', 'N Ndiaye'), '22')
                )
                certificate = dict(player_id=ids[player], shirt=shirt, source_url=FIFA + document,
                                   source_sha256=result.artifact.content_sha256,
                                   scope='EXACT_TEAM_MATCH_AND_SHIRT')
                for source_name in source_names:
                    aliases[(rows[0][0], namekey('Senegal'), namekey(source_name))] = certificate
                claims[(rows[0][0], ids[player], namekey('Senegal'))] = dict(
                    source_url=FIFA + document, source_sha256=result.artifact.content_sha256,
                    national_team='Senegal', matchday=day, source_scope='FIFA_MATCHDAY_SELECTION',
                    player_display_name=player)
                if player == 'Bara Sapoko Ndiaye':
                    claims[(rows[0][0], ids[player], namekey('Senegal'))]['identity_sources'] = [BARA_PROFILE, BARA_PROVIDER]
            selected.append(native)
    if not selected:
        raise ValueError('NO_OFFICIAL_MATCHDAY_SELECTION_SOURCE_AVAILABLE')
    (staged / 'NEW_PROVIDER_IDS_PRIVATE.json').write_text(json.dumps(selected) + '\n')
    runpy.run_path(str(worker), init_globals=dict(source=candidate, reference=ref_copy,
                   root=output / 'ingestion', source_receipts=[source_receipt], selection_claims=claims,
                   selection_aliases=aliases))
    result = read(output / 'ingestion' / 'SUMMARY.json')
    result['selection_source_count'] = len(selected)
    result['selection_identity_blockers'] = identity_blockers
    result['new_numeric_minutes'] = sum(row.get('minutes') is not None for row in
                                        read(output / 'ingestion' / 'appearances-private.json'))
    result['candidate_sha256'] = sha(output / 'ingestion' / 'candidate.sqlite')
    (output / 'result-private.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('candidate', 'reference', 'acquisition', 'worker', 'output'):
        parser.add_argument('--' + arg, required=True, type=Path)
    args = parser.parse_args()
    replay(*(local_path(getattr(args, arg)) for arg in
             ('candidate', 'reference', 'acquisition', 'worker', 'output')))


if __name__ == '__main__':
    main()
