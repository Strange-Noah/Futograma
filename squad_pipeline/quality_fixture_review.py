"""Independent raw-to-directory-to-export audit of completed fixture fields.

It does not call the production fixture parser and does not review player
participation, minutes, club membership or canonical provider bindings.
"""
import hashlib
import json
import re
from datetime import datetime, timezone


def _require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _require(result.tzinfo is not None, 'REVIEW_KICKOFF_TIMEZONE_REQUIRED')
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError):
        raise ValueError('REVIEW_KICKOFF_REQUIRED')


def _page(raw):
    body = raw if isinstance(raw, bytes) else raw.encode()
    scripts = re.findall(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', body.decode('utf8'), re.S)
    _require(len(scripts) == 1, 'REVIEW_EXACT_SSR_REQUIRED')
    p = json.loads(scripts[0])['props']['pageProps']
    return body, p.get('data', p)


def _score(status):
    m = re.fullmatch(r'\s*(\d+)\s*[-–]\s*(\d+)\s*', str(status.get('scoreStr', '')))
    _require(m is not None, 'REVIEW_ORDINARY_SCORE_REQUIRED')
    return [int(m[1]), int(m[2])]


def _proof(source, body=None):
    _require(isinstance(source, dict) and source.get('url') and re.fullmatch('[0-9a-fA-F]{64}', str(source.get('sha256', ''))), 'REVIEW_RETAINED_SOURCE_REQUIRED')
    if body is not None:
        _require(hashlib.sha256(body).hexdigest() == source['sha256'].lower(), 'REVIEW_SOURCE_BODY_HASH_MISMATCH')
    return dict(provider=source.get('provider', 'FotMob'), source_url=source['url'], sha256=source['sha256'])


def review_completed(raw, source, directory_row, exported_fixture, fingerprint,
                     directory_source=None, expected_home_key=None, expected_away_key=None):
    body, d = _page(raw)
    proofs = [_proof(source, body)]
    row = directory_row.get('discovery_row', directory_row)
    g, h = d.get('general', {}), d.get('header', {})
    lineup = (d.get('content') or {}).get('lineup') or {}
    expected = str(row.get('id'))
    _require(row.get('id') is not None and str(g.get('matchId')) == expected and str(lineup.get('matchId')) == expected, 'REVIEW_MATCH_OR_LINEUP_ID_CONFLICT')
    _require(str(exported_fixture.get('provider_match_id')) == expected and str(exported_fixture.get('actual_provider_match_id')) == expected, 'REVIEW_EXPORTED_PROVIDER_ID_CONFLICT')
    wanted = [str((row.get(side) or {}).get('id')) for side in ('home', 'away')]
    actual = [str((g.get(side + 'Team') or {}).get('id')) for side in ('home', 'away')]
    header_teams = h.get('teams') or []
    _require(None not in [row.get('home', {}).get('id'), row.get('away', {}).get('id')] and len(set(wanted)) == 2 and actual == wanted, 'REVIEW_ORDERED_TEAM_ID_CONFLICT')
    _require([str(t.get('id')) for t in header_teams] == wanted and [str((lineup.get(side + 'Team') or {}).get('id')) for side in ('home', 'away')] == wanted, 'REVIEW_HEADER_OR_LINEUP_TEAM_ID_CONFLICT')
    status, directory_status = h.get('status') or {}, row.get('status') or {}
    _require(g.get('finished') is True and status.get('finished') is True and directory_status.get('finished') is True and exported_fixture.get('status') == 'FINISHED', 'REVIEW_COMPLETED_ONLY')
    _require(not any(s.get(flag) for s in (status, directory_status) for flag in ('cancelled', 'awarded', 'abandoned', 'postponed')), 'REVIEW_EXCEPTIONAL_RESULT_NOT_SUPPORTED')
    kickoff = _instant(g.get('matchTimeUTCDate'))
    _require(all(_instant(x) == kickoff for x in (status.get('utcTime'), directory_status.get('utcTime'), exported_fixture.get('kickoff_utc'))), 'REVIEW_KICKOFF_CONFLICT')
    _require(kickoff <= datetime.now(timezone.utc), 'REVIEW_FUTURE_COMPLETED_REJECTED')
    competition = str(g.get('parentLeagueId') or g.get('leagueId') or '')
    _require(competition and competition == str((row.get('tournament') or {}).get('leagueId')) == str(exported_fixture.get('competition_key')), 'REVIEW_COMPETITION_CONFLICT')
    score = [t.get('score') for t in header_teams]
    _require(all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in score), 'REVIEW_NUMERIC_SCORE_REQUIRED')
    _require(all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in [row['home'].get('score'), row['away'].get('score'), exported_fixture.get('home_score'), exported_fixture.get('away_score')]), 'REVIEW_DIRECTORY_EXPORT_NUMERIC_SCORE_REQUIRED')
    _require(score == _score(status) == _score(directory_status) == [row['home'].get('score'), row['away'].get('score')] == [exported_fixture.get('home_score'), exported_fixture.get('away_score')], 'REVIEW_SCORE_CONFLICT')
    field_evidence = (exported_fixture.get('source_provenance') or {}).get('field_evidence') or {}
    for side, expected_key in (('home', expected_home_key), ('away', expected_away_key)):
        side_proof = field_evidence.get(side) or {}
        asserted = side_proof.get('value') or {}
        retained_hashes = {source['sha256']}
        if directory_source is not None:retained_hashes.add(directory_source.get('sha256'))
        _require(side_proof.get('sha256') in retained_hashes and side_proof.get('state') == 'VERIFIED', 'REVIEW_SIDE_PROVENANCE_NOT_BOUND_TO_SOURCE')
        _require(asserted.get('id') is not None and str(asserted['id']) == str((exported_fixture.get(side) or {}).get('id')), 'REVIEW_EXPORTED_CANONICAL_SIDE_CONFLICT')
        if expected_key is not None:
            _require(str(expected_key) == str(asserted['id']), 'REVIEW_EXPECTED_CANONICAL_SIDE_CONFLICT')
    if directory_source is not None:
        directory_raw = directory_source.get('raw', directory_source.get('body'))
        directory_body = None
        if directory_raw is not None:
            directory_body, directory_data = _page(directory_raw)
            def contains(value):
                if isinstance(value, dict):
                    if value == row:return True
                    return any(contains(v) for v in value.values())
                return isinstance(value, list) and any(contains(v) for v in value)
            _require(contains(directory_data), 'REVIEW_DIRECTORY_ROW_NOT_IN_RETAINED_BODY')
        proofs.append(_proof(directory_source, directory_body))
    _require(bool(fingerprint), 'REVIEW_FINGERPRINT_REQUIRED')
    return dict(fingerprint=fingerprint, independent=True,
                reviewer_id='completed-fixture-raw-export-review-v1', method='INDEPENDENT_REPLAY',
                evidence=proofs, scope='FIXTURE_IDENTITY_STATUS_SCORE_ONLY',
                excludes=['player_identity', 'participation', 'minutes', 'membership'],
                directory_body_rehashed=directory_source is not None and directory_source.get('raw', directory_source.get('body')) is not None)
