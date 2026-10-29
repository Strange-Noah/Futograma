"""Strict public-page adapter. A successful HTTP response is not match identity proof."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .international_discovery import payload,level
from .identity_pipeline import similarity

def instant(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
def numeric(value,duration=None):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or int(value)!=value or not 0<=value<=150:raise ValueError('INVALID_REPORTED_MINUTES')
    if duration is not None and value>duration:raise ValueError('MINUTES_EXCEED_MATCH_DURATION')
    return int(value)

def parse_public_match(raw,source,fixture,history_claims=None):
    d=payload(raw);g=d.get('general') or {};header=d.get('header') or {};content=d.get('content') or {};expected=str(fixture['provider_match_id'])
    if str(g.get('matchId'))!=expected:raise ValueError('RETURNED_MATCH_ID_MISMATCH')
    if not g.get('finished') or not (header.get('status') or {}).get('finished'):raise ValueError('MATCH_NOT_FINISHED')
    if g.get('gender')!='male':raise ValueError('MATCH_GENDER_UNVERIFIED')
    actual=[str((g.get(s+'Team') or {}).get('id')) for s in ('home','away')];wanted=[str(fixture[s]['id']) for s in ('home','away')]
    if actual!=wanted or len(set(actual))!=2:raise ValueError('ORDERED_TEAM_ID_MISMATCH')
    if [str(x.get('id')) for x in header.get('teams',[])]!=wanted:raise ValueError('HEADER_TEAM_ID_MISMATCH')
    kick=instant(g['matchTimeUTCDate'])
    if kick!=instant(fixture['kickoff']) or kick!=instant(header['status']['utcTime']):raise ValueError('MATCH_KICKOFF_MISMATCH')
    comp=str(g.get('parentLeagueId') or g.get('leagueId') or '')
    if comp!=str(fixture['competition_key']):raise ValueError('MATCH_COMPETITION_ID_MISMATCH')
    levels={level(g[s+'Team']['name']) for s in ('home','away')}
    if levels!={fixture['level']}:raise ValueError('NATIONAL_TEAM_LEVEL_MISMATCH')
    lineup=content.get('lineup') or {}
    if str(lineup.get('matchId'))!=expected:raise ValueError('LINEUP_MATCH_ID_MISMATCH')
    status=header['status'];halves=status.get('halfs') or {};reason=(status.get('reason') or {}).get('short','').upper()
    duration=120 if halves.get('firstExtraHalfStarted') or halves.get('secondExtraHalfStarted') or reason=='AET' else 90 if reason=='FT' and halves.get('secondHalfEnded') else None
    stats=content.get('playerStats') or {};seen=set();sheets=[];identity_issues=[];claims=history_claims or []
    for side in ('home','away'):
        t=lineup.get(side+'Team') or {};tid=str(t.get('id'))
        if tid!=str(fixture[side]['id']):raise ValueError('LINEUP_TEAM_ID_MISMATCH')
        if not isinstance(t.get('starters'),list) or len(t['starters'])!=11 or not isinstance(t.get('subs'),list):raise ValueError('COMPLETE_XI_AND_BENCH_REQUIRED')
        players=[]
        for role,key in [('starters','starters'),('bench','subs')]:
            for i,p in enumerate(t[key]):
                pid=str(p.get('id') or '')
                if not p.get('name'):raise ValueError('LINEUP_PLAYER_NAME_MISSING')
                if not pid or pid=='0':
                    identity_issues.append(dict(side=side,array=key,index=i,name=p['name'],issue='SOURCE_PLAYER_ID_MISSING'));continue
                if pid in seen:raise ValueError('DUPLICATE_LINEUP_PLAYER_ID')
                seen.add(pid);stat=stats.get(pid);reported=None;scope='LINEUP_IDENTITY_ONLY';verification='LINEUP_ID_VERIFIED_MINUTES_UNKNOWN';claim_ref=None
                if stat is not None:
                    if str(stat.get('id'))!=pid or str(stat.get('teamId'))!=tid or similarity(p['name'],stat.get('name',''))<.6:raise ValueError('PLAYER_STAT_IDENTITY_MISMATCH')
                    values=[v['stat']['value'] for group in stat.get('stats',[]) for v in group.get('stats',{}).values() if v.get('key')=='minutes_played' and 'value' in (v.get('stat') or {})]
                    if len(set(values))>1:raise ValueError('CONFLICTING_MINUTE_STATS')
                    if values:reported=numeric(values[0],duration);scope='PROVIDER_REPORTED_MATCH_STAT;EXACT_LINEUP_AND_STAT_IDS';verification='PROVIDER_ID_AND_MATCH_VERIFIED'
                ownclaims=[h for h in claims if str(h.get('player_provider_id'))==pid]
                explicit=[]
                for h in ownclaims:
                    c=h['discovery_claim']
                    if str(h.get('provider_match_id'))!=expected or str(c.get('id'))!=expected or str(c.get('teamId'))!=tid or instant(c['matchDate']['utcTime'])!=kick:raise ValueError('PERSONAL_HISTORY_IDENTITY_MISMATCH')
                    if (str(c['teamId']),str(c['opponentTeamId']))!=(tid,str(fixture['away' if side=='home' else 'home']['id'])):raise ValueError('PERSONAL_HISTORY_OPPONENT_MISMATCH')
                    if bool(c.get('isHomeTeam'))!=(side=='home'):raise ValueError('PERSONAL_HISTORY_SIDE_MISMATCH')
                    if c.get('minutesPlayed') is not None:explicit.append((numeric(c['minutesPlayed'],duration),c,h))
                if len({x[0] for x in explicit})>1:raise ValueError('CONFLICTING_PERSONAL_HISTORY_MINUTES')
                if explicit and reported is not None and explicit[0][0]!=reported:raise ValueError('MATCH_STAT_HISTORY_MINUTES_CONFLICT')
                if explicit and reported is None:
                    reported,c,claim_ref=explicit[0];scope='SOURCE_REPORTED_TARGET;LINEUP_ID_VERIFIED;NOT_INDEPENDENT_MINUTE_CORROBORATION';verification='TARGET_HISTORY_AND_LINEUP_ID_VERIFIED'
                participation='STARTER' if role=='starters' else 'SUBSTITUTE_USED' if reported is not None and reported>0 else 'BENCH_PARTICIPATION_UNKNOWN'
                if role=='bench' and explicit:
                    c=explicit[0][1]
                    if c.get('playedInMatch') is True:participation='SUBSTITUTE_USED'
                    elif c.get('playedInMatch') is False and c.get('onBench') is True and reported==0:participation='UNUSED_SUBSTITUTE'
                # Missing event/stat coverage never turns a listed substitute into a confirmed zero.
                players.append(dict(provider_player_id=pid,name=p['name'],shirt=p.get('shirtNumber'),role=role,represented_team_provider_id=tid,participation_status=participation,minutes=reported,reported_minutes=reported,derived_minutes=None,minutes_precision='SOURCE_REPORTED_INTEGER' if reported is not None else 'UNKNOWN',verification_status=verification,evidence_scope=scope,source=source,history_source=claim_ref.get('source') if claim_ref else None,source_path=f'$.props.pageProps.content.lineup.{side}Team.{key}[{i}]'))
        sheets.append(dict(side=side,provider_team_id=tid,name=t['name'],players=players,complete_squad=not any(x['side']==side for x in identity_issues)))
    return dict(provider='FotMob',provider_match_id=expected,kickoff_utc=kick.isoformat(),madrid_date=kick.astimezone(ZoneInfo('Europe/Madrid')).date().isoformat(),competition_key=comp,competition_label=g.get('leagueName'),level=fixture['level'],duration_minutes=duration,extra_time_status='VERIFIED_120' if duration==120 else 'VERIFIED_90' if duration==90 else 'UNKNOWN',score=status.get('scoreStr'),identity_issues=identity_issues,full_squad_identity_status='INCOMPLETE' if identity_issues else 'UNIQUE_PROVIDER_IDS',shootout_minutes=0,sheets=sheets,source=source)
