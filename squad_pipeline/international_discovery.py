"""Player-first discovery from public SSR pages. Never infers teams from nationality."""
import json,re
from urllib.parse import urljoin,urlsplit
from datetime import datetime

def payload(raw):
    if raw.lstrip().startswith('{'):
        direct=json.loads(raw)
        if isinstance(direct,dict) and isinstance(direct.get('general'),dict) and isinstance(direct.get('content'),dict):return direct
        raise ValueError('DIRECT_MATCH_PAYLOAD_STRUCTURE_REQUIRED')
    m=re.findall(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',raw,re.S)
    if len(m)!=1:raise ValueError('SSR_NEXT_PAYLOAD_REQUIRED')
    page=json.loads(m[0])['props']['pageProps'];return page.get('data',page)

def level(name):
    m=re.search(r'\bU[- ]?(\d{2})\b|Under[- ]?(\d{2})',name,re.I)
    return 'U'+next(x for x in m.groups() if x) if m else 'SENIOR'

def profile_hints(raw,source,window_start,window_end,include_club=False):
    d=payload(raw);career=d.get('careerHistory',{}).get('careerItems',{});national=career.get('national team',{}).get('teamEntries',[]);affiliations=[];all_national={str(t['teamId']):dict(level=level(t['team'])) for t in national if t.get('participantId')==d['id'] and t.get('teamId') and t.get('team')}
    for i,t in enumerate(national):
        if t.get('participantId')!=d['id'] or not t.get('teamId') or not t.get('team'):continue
        if t.get('endDate') and t['endDate'][:10]<window_start or t.get('startDate') and t['startDate'][:10]>window_end:continue
        affiliations.append(dict(provider='FotMob',provider_player_id=str(d['id']),provider_team_id=str(t['teamId']),name=t['team'],level=level(t['team']),start_date=t.get('startDate'),end_date=t.get('endDate'),scope='EXPLICIT_NATIONAL_CAREER_SECTION;NOT_CITIZENSHIP',verification_status='PROVIDER_ASSERTED_CAREER_RANGE;NOT_CURRENT_CALLUP',source=source,source_path=f'$.props.pageProps.data.careerHistory.careerItems["national team"].teamEntries[{i}]'))
    teams=dict(all_national)
    if include_club:
        for t in career.get('senior',{}).get('teamEntries',[]):
            if str(t.get('participantId'))==str(d['id']) and t.get('teamId') and t.get('team'):
                tid=str(t['teamId'])
                if tid in teams and teams[tid].get('domain')!='CLUB':raise ValueError('CLUB_NATIONAL_TEAM_ID_COLLISION')
                teams[tid]=dict(level='SENIOR',domain='CLUB')
    matches={};allrows=list(d.get('recentMatches') or [])+list((d.get('initialMatchesPage') or {}).get('matches') or [])
    for i,m in enumerate(allrows):
        tid=str(m.get('teamId'));date=(m.get('matchDate') or {}).get('utcTime','')[:10]
        if tid not in teams or not window_start<=date<=window_end or not m.get('matchPageUrl') or not m.get('id'):continue
        own=dict(id=tid,name=m['teamName']);opponent=dict(id=str(m['opponentTeamId']),name=m['opponentTeamName']);home,away=(own,opponent) if m.get('isHomeTeam') else (opponent,own)
        match=dict(provider='FotMob',provider_match_id=str(m['id']),url=urljoin('https://www.fotmob.com',m['matchPageUrl']),home=home,away=away,kickoff=m['matchDate']['utcTime'],competition_key=str(m['leagueId']),competition_label=m['leagueName'],level=teams[tid]['level'],player_provider_id=str(d['id']),represented_team_provider_id=tid,source=source,source_path=f'personal_match_history[{i}]',discovery_claim=m)
        if include_club:match['domain']=teams[tid].get('domain','NATIONAL')
        old=matches.get(match['provider_match_id'])
        if old and any(old[k]!=match[k] for k in ['home','away','kickoff','competition_key']):raise ValueError('PERSONAL_HISTORY_FIXTURE_CONFLICT')
        matches[match['provider_match_id']]=match
    dates=[(m.get('matchDate') or {}).get('utcTime','')[:10] for m in allrows if (m.get('matchDate') or {}).get('utcTime')]
    return dict(provider_player_id=str(d['id']),affiliations=affiliations,matches=list(matches.values()),history_coverage='PUBLIC_SSR_RECENT_HISTORY_ONLY;PAGINATION_OR_TEAM_FIXTURE_FALLBACK_REQUIRED' if not dates or min(dates)>window_start else 'RECENT_WINDOW_REACHED;SELECTION_ABSENCES_NOT_INFERRED',previous_cursor=(d.get('initialMatchesPage') or {}).get('previous'),source=source)

def team_payload(raw,team_id):
    d=payload(raw);prefix='$.props.pageProps.data';fallback=d.get('fallback')
    if fallback is not None:
        key='team-'+str(team_id)
        if key not in fallback:raise ValueError('EXACT_TEAM_FALLBACK_REQUIRED')
        d=fallback[key];prefix='$.props.pageProps.fallback['+repr(key)+']'
    details=d.get('details') or {}
    if str(details.get('id'))!=team_id:raise ValueError('NATIONAL_TEAM_FIXTURE_ID_MISMATCH')
    return d,prefix

def team_fixture_hints(raw,source,affiliation,window_start,window_end):
    d,prefix=team_payload(raw,affiliation['provider_team_id'])
    f=d.get('fixtures') or {};rows=f.get('allFixtures',f.get('allMatches'))
    if isinstance(rows,dict):rows=rows.get('fixtures')
    if not isinstance(rows,list):raise ValueError('TEAM_FIXTURE_SCHEMA_UNSUPPORTED')
    out=[]
    for i,m in enumerate(rows):
        status=m.get('status') or {};kick=status.get('utcTime');date=(kick or '')[:10]
        if not kick or not window_start<=date<=window_end or not status.get('finished'):continue
        home=m.get('home') or {};away=m.get('away') or {}
        if affiliation['provider_team_id'] not in (str(home.get('id')),str(away.get('id'))):raise ValueError('NATIONAL_FIXTURE_SIDE_MISMATCH')
        url=m.get('pageUrl') or m.get('matchPageUrl')
        if not url:continue
        out.append(dict(provider='FotMob',provider_match_id=str(m['id']),url=urljoin('https://www.fotmob.com',url),home=dict(id=str(home['id']),name=home['name']),away=dict(id=str(away['id']),name=away['name']),kickoff=kick,competition_key=str((m.get('tournament') or {}).get('leagueId') or m.get('leagueId') or ''),competition_label=(m.get('tournament') or {}).get('name') or m.get('leagueName') or '',level=affiliation['level'],represented_team_provider_id=affiliation['provider_team_id'],source=source,source_path=f'{prefix}.fixtures.allFixtures.fixtures[{i}]',discovery_claim=m))
    return out


def team_identity_hints(raw,source,team_id):
    """Existing public team SSR index: identity hints only, never historical roster seeds."""
    from .live_parse import CLUBS
    origin=urlsplit(source['url'])
    if origin.scheme!='https' or origin.netloc!='www.fotmob.com' or origin.fragment:raise ValueError('PUBLIC_FOTMOB_TEAM_ORIGIN_REQUIRED')
    asked=re.search(r'/teams/([0-9]+)(?:/|$)',origin.path)
    if not asked or asked[1]!=str(team_id) or int(team_id) not in CLUBS:raise ValueError('EXACT_CONFIGURED_TEAM_REQUEST_REQUIRED')
    d,prefix=team_payload(raw,str(team_id));squad=d.get('squad') or {}
    if squad.get('isNationalTeam') is not False:raise ValueError('CLUB_SQUAD_IDENTITY_INDEX_REQUIRED')
    out=[];seen=set()
    for gi,group in enumerate(squad.get('squad',[])):
        if group.get('title') not in {'keepers','defenders','midfielders','attackers'}:continue
        for pi,row in enumerate(group.get('members',[])):
            pid=str(row.get('id',''));name=row.get('name');dob=row.get('dateOfBirth')
            if not pid.isdigit() or not name or (row.get('role') or {}).get('key')=='coach':raise ValueError('NATIVE_PLAYER_ID_NAME_REQUIRED')
            if pid in seen:raise ValueError('DUPLICATE_NATIVE_PLAYER_ID')
            seen.add(pid);out.append(dict(provider='FotMob',provider_player_id=pid,name=name,birth_date=dob,profile_url='https://www.fotmob.com/players/'+pid,url_basis='EXISTING_PUBLIC_PLAYER_ROUTE_FROM_NATIVE_ID',source_path=prefix+f'.squad.squad[{gi}].members[{pi}]',historical_membership_asserted=False))
    if not out:raise ValueError('EMPTY_IDENTITY_INDEX_IS_NOT_EMPTY_HISTORICAL_ROSTER')
    return out
