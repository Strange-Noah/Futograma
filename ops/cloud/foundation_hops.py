"""Plan alternative public source routes from verified historical entities."""

from collections import defaultdict
from contextlib import closing
import re
import sqlite3
from urllib.parse import urlsplit

from ops.cloud.historical_batch import unit_window
from squad_pipeline.source_interface import SourceRequest


def plan(candidate, rows):
    """Group coverage obligations by verified provider entity, across units."""
    windows = [(f"{r['club_id']}:{r['unit']}", unit_window(r))
               for r in rows if not r['source_ready'] and not r['data_accepted']]
    anchors = defaultdict(set)
    direct = defaultdict(set)
    profiles = defaultdict(set)
    with closing(sqlite3.connect(candidate.as_uri() + '?mode=ro', uri=True)) as db:
        fixtures = db.execute("""
            SELECT f.match_id, f.home_key, f.away_key, m.local_date
            FROM canonical_fixtures f JOIN matches m USING(match_id)
            WHERE f.domain='CLUB'
            ORDER BY m.local_date, f.match_id
        """).fetchall()
        teams = defaultdict(set)
        for key, native in db.execute("""
            SELECT team_key,provider_team_id FROM football_team_provider_ids
            WHERE provider='FotMob' AND evidence_id IS NOT NULL
            UNION
            SELECT entity_id,provider_id FROM canonical_provider_entities
            WHERE provider='FotMob' AND lower(kind) IN ('team','club')
        """):
            if re.fullmatch(r'[0-9]+', str(native)):
                teams[key].add(str(native))
        coverage = {(club, match): (starts, unknown) for club, match, starts, unknown in db.execute("""
            SELECT club_id,match_id,sum(participation_status='STARTER'),
                sum(minutes IS NULL AND participation_status IN ('STARTER','SUBSTITUTE_USED'))
            FROM player_match_minutes GROUP BY club_id,match_id
        """)}
        complete = set(db.execute("""
            SELECT DISTINCT o.club_id,o.match_id FROM squad_observations o
            JOIN canonical_fixture_sources s ON s.match_id=o.match_id AND s.domain='CLUB'
            WHERE json_extract(s.evidence_json,'$.complete_squad')=1
        """))
        bound_matches = defaultdict(list)
        for mid, native, url in db.execute("""
            SELECT match_id,provider_match_id,json_extract(evidence_json,'$.url')
            FROM canonical_fixture_sources WHERE domain='CLUB' AND provider='FotMob'
        """):
            if url and urlsplit(url).hostname == 'www.fotmob.com':
                bound_matches[mid].append((str(native), url))
        players = defaultdict(set)
        for club, match, native in db.execute("""
            SELECT p.club_id,p.match_id,l.provider_player_id
            FROM player_match_minutes p
            JOIN verified_identity_links l USING(player_id)
            WHERE p.minutes IS NULL AND p.participation_status IN ('STARTER','SUBSTITUTE_USED')
              AND l.provider='FotMob'
              AND (SELECT count(*) FROM verified_identity_links x
                   WHERE x.provider=l.provider AND x.player_id=l.player_id)=1
        """):
            if re.fullmatch(r'[0-9]+', str(native)):
                players[club, match].add(str(native))
        for key, unit in windows:
            club = unit['club_id']
            start, end = unit['start'], unit['end_exclusive']
            for mid, home, away, on in fixtures:
                if not on or not start <= on < end or f'club:{club}' not in (home, away):
                    continue
                starts, unknown = coverage.get((club, mid), (0, 0))
                if starts == 11 and not unknown and (club, mid) in complete:
                    continue
                for native in players[club, mid]:
                    profiles[native].add((key, mid, on))
                for native, url in bound_matches[mid]:
                    direct[native, url].add((key, mid, on))
                for side in (home, away):
                    if len(teams[side]) == 1:
                        anchors[next(iter(teams[side]))].add((key, mid, on))

    def targets(values):
        return [dict(unit=key, match=mid, date=on) for key, mid, on in sorted(values)]

    tasks = []
    for (native, url), values in sorted(direct.items()):
        tasks.append((SourceRequest('FotMob', 'match', url, 'Exact bound alternate fixture',
                                    expected_ids={'match': native}, context={'domain': 'CLUB'},
                                    policy='LIVE_ALLOWED'), targets(values)))
    for native, values in sorted(anchors.items()):
        dates = [value[2] for value in values]
        tasks.append((SourceRequest(
            'FotMob', 'team_history', f'https://www.fotmob.com/teams/{native}/fixtures',
            'Verified team or opponent fixture discovery',
            expected_ids={'team': native},
            context={'level': 'CLUB', 'start': min(dates), 'cutoff': max(dates)},
            policy='LIVE_ALLOWED'), targets(values)))
    for native, values in sorted(profiles.items()):
        dates = [value[2] for value in values]
        tasks.append((SourceRequest(
            'FotMob', 'player_history', f'https://www.fotmob.com/players/{native}',
            'Verified profile entry for unknown minutes',
            expected_ids={'player': native},
            context={'include_club': True, 'start': min(dates), 'cutoff': max(dates)},
            policy='LIVE_ALLOWED'), targets(values)))
    return tasks
