"""Generic strict FotMob club discovery and match-page cold-start adapters.

No manual roster or result insertion. Directory facts are provider assertions
with retained field evidence. Detail lineups must independently agree before
participation can be accepted. Club and national validation defaults stay apart.
"""
import hashlib
from .canonical_fixture import score_fields
from .international_fotmob import parse_public_club_match
from .quality_fixture import evaluate


def _proof(fixture,source):
    if not source.get('sha256') or not source.get('url'):raise ValueError('RETAINED_SOURCE_REQUIRED')
    return dict(field_evidence={k:dict(value=v,sha256=source['sha256'],url=source['url'],state='VERIFIED',source_path='FotMob.team.fixtures.allFixtures') for k,v in fixture.items() if v is not None})


def directory_fixture(row,source,season,team_provider_id,match_id=None,team_key=None):
    """Resolve a discovered directory row, never invent a missing score."""
    key=team_key or (lambda tid:'club:'+str(tid));status=row.get('status') or {};home=row.get('home') or {};away=row.get('away') or {};tournament=row.get('tournament') or {}
    if str(team_provider_id) not in {str(home.get('id')),str(away.get('id'))}:raise ValueError('DIRECTORY_TRACKED_TEAM_NOT_PRESENT')
    state='CANCELLED' if status.get('cancelled') else 'AWARDED' if status.get('awarded') else 'POSTPONED' if status.get('postponed') else 'ABANDONED' if status.get('abandoned') else 'FINISHED' if status.get('finished') else 'IN_PROGRESS' if status.get('started') else 'SCHEDULED'
    hs,aws=home.get('score'),away.get('score');scores=score_fields(status.get('scoreStr'))
    if scores and ('home_score' not in scores or hs is not None and scores['home_score']!=hs or aws is not None and scores['away_score']!=aws):raise ValueError('DIRECTORY_SCORE_SIDES_CONFLICT')
    if state in ('SCHEDULED','POSTPONED','CANCELLED') and not status.get('started') and not status.get('finished'):
        # Provider directory zero placeholders are observations, not a result.
        observed=[v for v in (hs,aws,scores.get('home_score'),scores.get('away_score')) if v is not None]
        if any(v!=0 for v in observed):raise ValueError('UNSTARTED_FIXTURE_NONZERO_SCORE_CONFLICT')
        hs=aws=None
    fixture=dict(match_id=match_id or 'club_fotmob_'+str(row['id']),provider='FotMob',provider_match_id=str(row['id']),actual_provider_match_id=str(row['id']),competition_key=str(tournament.get('leagueId') or ''),competition_label=tournament.get('name'),season=season,kickoff_utc=status.get('utcTime'),home=dict(id=key(home.get('id')),name=home.get('name')),away=dict(id=key(away.get('id')),name=away.get('name')),status=state,home_score=hs,away_score=aws)
    if state=='SCHEDULED':fixture['kickoff_uncertainty']='PROVIDER_SCHEDULED_TIME_SUBJECT_TO_CONFIRMATION'
    if not status.get('utcTime'):fixture['kickoff_uncertainty']='PROVIDER_DIRECTORY_KICKOFF_UNAVAILABLE'
    if state in ('CANCELLED','AWARDED','POSTPONED','ABANDONED') and hs is None and aws is None:fixture['score_unavailable_reason']='PROVIDER_STATUS_'+state
    evidence=_proof(fixture,source)
    return dict(fixture=fixture,evidence=evidence,quality=evaluate(fixture,evidence),provider_fixture=dict(provider_match_id=str(row['id']),home=dict(id=str(home.get('id')),name=home.get('name')),away=dict(id=str(away.get('id')),name=away.get('name')),kickoff=status.get('utcTime'),competition_key=str(tournament.get('leagueId') or ''),level='SENIOR'),discovery_row=row)


def parse_completed_club(raw,source,directory,team_provider_id,history_claims=None):
    """A detail response cannot silently correct mismatched directory identities."""
    body=raw if isinstance(raw,bytes) else raw.encode()
    if hashlib.sha256(body).hexdigest()!=source.get('sha256'):raise ValueError('SOURCE_BODY_HASH_MISMATCH')
    for claim in history_claims or []:
        league=(claim.get('discovery_claim') or {}).get('leagueId')
        if league is not None and str(league)!=str(directory['provider_fixture']['competition_key']):raise ValueError('PERSONAL_HISTORY_COMPETITION_MISMATCH')
    text=body.decode('utf8');parsed=parse_public_club_match(text,source,directory['provider_fixture'],history_claims);fixture=dict(directory['fixture']);score=score_fields(parsed.get('score'))
    if set(score)!={'home_score','away_score'}:raise ValueError('COMPLETED_DETAIL_SCORE_REQUIRED')
    for k,v in score.items():
        if fixture.get(k) is not None and fixture[k]!=v:raise ValueError('DETAIL_DIRECTORY_SCORE_CONFLICT')
    fixture.update(score,status='FINISHED',kickoff_utc=parsed['kickoff_utc']);evidence=_proof(fixture,source)
    own=[s for s in parsed['sheets'] if str(s['provider_team_id'])==str(team_provider_id)]
    if len(own)!=1:raise ValueError('OWN_LINEUP_IDENTITY_REQUIRED')
    return dict(fixture=fixture,evidence=evidence,quality=evaluate(fixture,evidence),appearances=own[0]['players'],parsed=parsed,discovery_source=directory['evidence'])


def extract_club_history(raw,source,club_provider_id):
    """Return explicit target-club history claims from a verified player page.

    URL and body player ID must agree. Absence of a row is never a zero-minute
    observation. A row without participation/minute assertions is not a claim.
    Callers must select only the requested match before passing claims to the
    strict detail adapter, which checks ordered opponents, kickoff and lineup.
    """
    from urllib.parse import urlsplit
    import re
    from datetime import datetime
    from .international_discovery import payload
    body=raw if isinstance(raw,bytes) else raw.encode()
    if hashlib.sha256(body).hexdigest()!=source.get('sha256'):raise ValueError('SOURCE_BODY_HASH_MISMATCH')
    d=payload(body.decode('utf8'));pid=str(d.get('id') or '');parts=urlsplit(source.get('url',''));path=re.match(r'^/players/([0-9]+)(?:/|$)',parts.path)
    if parts.scheme!='https' or parts.hostname not in {'www.fotmob.com','fotmob.com'} or not path or path[1]!=pid:raise ValueError('PROFILE_URL_BODY_PLAYER_ID_MISMATCH')
    rows=list(d.get('recentMatches') or [])+list((d.get('initialMatchesPage') or {}).get('matches') or [])+list(d.get('personal_match_history') or []);claims={}
    for i,row in enumerate(rows):
        if str(row.get('teamId'))!=str(club_provider_id):continue
        if not any(k in row and row[k] is not None for k in ('minutesPlayed','playedInMatch','onBench')):continue
        if not row.get('id') or not row.get('opponentTeamId') or not isinstance(row.get('isHomeTeam'),bool):raise ValueError('CLUB_HISTORY_FIXTURE_CONTEXT_INCOMPLETE')
        kick=(row.get('matchDate') or {}).get('utcTime')
        try:aware=datetime.fromisoformat(kick.replace('Z','+00:00')).tzinfo is not None
        except (AttributeError,ValueError,TypeError):aware=False
        if not aware:raise ValueError('CLUB_HISTORY_KICKOFF_TIMEZONE_REQUIRED')
        if row.get('minutesPlayed') is not None:
            from .international_fotmob import numeric
            numeric(row['minutesPlayed'])
        for flag in ('playedInMatch','onBench'):
            if row.get(flag) is not None and not isinstance(row[flag],bool):raise ValueError('CLUB_HISTORY_PARTICIPATION_BOOLEAN_REQUIRED')
        claim=dict(provider_match_id=str(row['id']),player_provider_id=pid,source={k:source[k] for k in ('provider','url','sha256','path','run_id','acquired_at','response_timestamp','transport_mode') if k in source},discovery_claim=row,source_path='personal_match_history['+str(i)+']')
        old=claims.get(claim['provider_match_id'])
        semantic=lambda c:{k:c.get(k) for k in ('id','teamId','opponentTeamId','isHomeTeam','matchDate','minutesPlayed','playedInMatch','onBench','leagueId')}
        if old and semantic(old['discovery_claim'])!=semantic(row):raise ValueError('CLUB_PERSONAL_HISTORY_CONFLICT')
        claims[claim['provider_match_id']]=claim
    return list(claims.values())
