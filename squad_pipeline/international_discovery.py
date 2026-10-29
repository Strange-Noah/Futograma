"""Player-first discovery from public SSR pages. Never infers teams from nationality."""
import json,re
from urllib.parse import urljoin
from datetime import datetime

def payload(raw):
    m=re.findall(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',raw,re.S)
    if len(m)!=1:raise ValueError('SSR_NEXT_PAYLOAD_REQUIRED')
    return json.loads(m[0])['props']['pageProps']['data']

def level(name):
    m=re.search(r'\bU[- ]?(\d{2})\b|Under[- ]?(\d{2})',name,re.I)
    return 'U'+next(x for x in m.groups() if x) if m else 'SENIOR'

def profile_hints(raw,source,window_start,window_end):
    d=payload(raw);career=d.get('careerHistory',{}).get('careerItems',{});national=career.get('national team',{}).get('teamEntries',[]);affiliations=[]
    for i,t in enumerate(national):
        if t.get('participantId')!=d['id'] or not t.get('teamId') or not t.get('team'):continue
        if t.get('endDate') and t['endDate'][:10]<window_start or t.get('startDate') and t['startDate'][:10]>window_end:continue
        affiliations.append(dict(provider='FotMob',provider_player_id=str(d['id']),provider_team_id=str(t['teamId']),name=t['team'],level=level(t['team']),start_date=t.get('startDate'),end_date=t.get('endDate'),scope='EXPLICIT_NATIONAL_CAREER_SECTION;NOT_CITIZENSHIP',verification_status='PROVIDER_ASSERTED_CAREER_RANGE;NOT_CURRENT_CALLUP',source=source,source_path=f'$.props.pageProps.data.careerHistory.careerItems["national team"].teamEntries[{i}]'))
    teams={a['provider_team_id']:a for a in affiliations};matches={};allrows=list(d.get('recentMatches') or [])+list((d.get('initialMatchesPage') or {}).get('matches') or [])
    for i,m in enumerate(allrows):
        tid=str(m.get('teamId'));date=(m.get('matchDate') or {}).get('utcTime','')[:10]
        if tid not in teams or not window_start<=date<=window_end or not m.get('matchPageUrl') or not m.get('id'):continue
        own=dict(id=tid,name=m['teamName']);opponent=dict(id=str(m['opponentTeamId']),name=m['opponentTeamName']);home,away=(own,opponent) if m.get('isHomeTeam') else (opponent,own)
        match=dict(provider='FotMob',provider_match_id=str(m['id']),url=urljoin('https://www.fotmob.com',m['matchPageUrl']),home=home,away=away,kickoff=m['matchDate']['utcTime'],competition_key=str(m['leagueId']),competition_label=m['leagueName'],level=teams[tid]['level'],player_provider_id=str(d['id']),represented_team_provider_id=tid,source=source,source_path=f'personal_match_history[{i}]',discovery_claim=m)
        old=matches.get(match['provider_match_id'])
        if old and any(old[k]!=match[k] for k in ['home','away','kickoff','competition_key']):raise ValueError('PERSONAL_HISTORY_FIXTURE_CONFLICT')
        matches[match['provider_match_id']]=match
    dates=[(m.get('matchDate') or {}).get('utcTime','')[:10] for m in allrows if (m.get('matchDate') or {}).get('utcTime')]
    return dict(provider_player_id=str(d['id']),affiliations=affiliations,matches=list(matches.values()),history_coverage='PUBLIC_SSR_RECENT_HISTORY_ONLY;PAGINATION_OR_TEAM_FIXTURE_FALLBACK_REQUIRED' if not dates or min(dates)>window_start else 'RECENT_WINDOW_REACHED;SELECTION_ABSENCES_NOT_INFERRED',previous_cursor=(d.get('initialMatchesPage') or {}).get('previous'),source=source)

def team_fixture_hints(raw,source,affiliation,window_start,window_end):
    d=payload(raw);details=d.get('details') or {}
    if str(details.get('id'))!=affiliation['provider_team_id']:raise ValueError('NATIONAL_TEAM_FIXTURE_ID_MISMATCH')
    f=d.get('fixtures') or {};rows=f.get('allFixtures') or f.get('allMatches')
    if not isinstance(rows,list):raise ValueError('TEAM_FIXTURE_SCHEMA_UNSUPPORTED')
    out=[]
    for i,m in enumerate(rows):
        status=m.get('status') or {};kick=status.get('utcTime');date=(kick or '')[:10]
        if not kick or not window_start<=date<=window_end or not status.get('finished'):continue
        home=m.get('home') or {};away=m.get('away') or {}
        if affiliation['provider_team_id'] not in (str(home.get('id')),str(away.get('id'))):raise ValueError('NATIONAL_FIXTURE_SIDE_MISMATCH')
        url=m.get('pageUrl') or m.get('matchPageUrl')
        if not url:continue
        out.append(dict(provider='FotMob',provider_match_id=str(m['id']),url=urljoin('https://www.fotmob.com',url),home=dict(id=str(home['id']),name=home['name']),away=dict(id=str(away['id']),name=away['name']),kickoff=kick,competition_key=str(m.get('leagueId') or ''),competition_label=m.get('leagueName') or '',level=affiliation['level'],represented_team_provider_id=affiliation['provider_team_id'],source=source,source_path=f'$.props.pageProps.data.fixtures.allFixtures[{i}]',discovery_claim=m))
    return out
