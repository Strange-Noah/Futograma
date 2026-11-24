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
from squad_pipeline.cloud.quality import sha
from squad_pipeline.international_parse import namekey
from squad_pipeline.source_interface import EvidenceStore, SourceRequest, SourceService

FIXTURES = {
    '549807': ('2026-06-23', 'PMSR-M41-NOR-V-SEN.pdf'),
    '549830': ('2026-06-26', 'PMSR-M62-SEN-V-IRQ.pdf'),
}
FIFA = 'https://www.fifatrainingcentre.com/media/native/tournaments/fifa-world-cup/2026/'
PLAYERS = ('Nicolas Jackson', 'Bara Sapoko Ndiaye')


def read(path):
    return json.loads(path.read_text())


def readonly(path):
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)


def replay(candidate, reference, acquisition, worker, output):
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
    claims, captures, selected = {}, [], []
    with closing(readonly(candidate)) as db, closing(readonly(reference)) as ref:
        ids = {}
        for player in PLAYERS:
            rows = db.execute("""
                SELECT DISTINCT p.player_id FROM players p
                JOIN player_match_minutes a USING(player_id) JOIN matches m USING(match_id)
                WHERE lower(p.display_name)=lower(?) AND a.club_id=9823
                  AND m.local_date>='2026-01-01' AND m.local_date<'2026-07-01'
                """, (player,)).fetchall()
            if len(rows) != 1:
                raise ValueError('BAYERN_OBSERVED_PLAYER_ID_NOT_UNIQUE: ' + player)
            ids[player] = rows[0][0]
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
            result = service.acquire(SourceRequest('FIFA', 'match', FIFA + document,
                                     'Official FIFA World Cup match sheet', policy='LIVE_ALLOWED'))
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
            if 'senegal' not in flattened or not all(player.casefold() in flattened for player in PLAYERS):
                raise ValueError('OFFICIAL_MATCHDAY_SELECTION_NEEDS_REVIEW')
            for player in PLAYERS:
                claims[(rows[0][0], ids[player], namekey('Senegal'))] = dict(
                    source_url=FIFA + document, source_sha256=result.artifact.content_sha256,
                    national_team='Senegal', matchday=day, source_scope='FIFA_MATCHDAY_SELECTION',
                    player_display_name=player)
            selected.append(native)
    if not selected:
        raise ValueError('NO_OFFICIAL_MATCHDAY_SELECTION_SOURCE_AVAILABLE')
    (staged / 'NEW_PROVIDER_IDS_PRIVATE.json').write_text(json.dumps(selected) + '\n')
    runpy.run_path(str(worker), init_globals=dict(source=candidate, reference=ref_copy,
                   root=output / 'ingestion', source_receipts=[source_receipt], selection_claims=claims))
    result = read(output / 'ingestion' / 'SUMMARY.json')
    result['selection_source_count'] = len(selected)
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
