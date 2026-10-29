"""Player-ID-first local discovery. No roster database, acquisition whitelist or network."""
import json,re
from pathlib import Path
from collections import defaultdict
from urllib.parse import urlparse
from .core import ROOT,BASE,read,digest
from futbol.normalization import statistics
from futbol.common import instant
from .fotmob import canonical_name

def evidence(path,kind,json_path):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT):raise ValueError('Source outside Futbol')
    raw=path.read_bytes();meta=read(path.with_suffix('.meta.json')) if path.with_suffix('.meta.json').exists() else {}
    if meta.get('sha256') and meta['sha256']!=digest(raw):raise ValueError('Source hash mismatch: '+str(path))
    return dict(artifact_id=digest((str(path.relative_to(ROOT))+':'+digest(raw)).encode()),path=str(path.relative_to(ROOT)),sha256=digest(raw),kind=kind,source_url=meta.get('url'),acquired_at=meta.get('fetched_at'),json_path=json_path)

def history_observation(pid,r,ev):
    team=int(r['teamId']);opp=int(r['opponentTeamId']);mid=int(r['id']);kickoff=r['matchDate']['utcTime'];minutes=r.get('minutesPlayed');played=r.get('playedInMatch');errors=[]
    if min(team,opp,mid)<=0 or team==opp:errors.append('INVALID_IDS')
    if minutes is not None and (isinstance(minutes,bool) or not isinstance(minutes,(int,float)) or not 0<=minutes<=150):errors.append('INVALID_MINUTES');minutes=None
    if played is False and minutes is not None and minutes>0:errors.append('FALSE_PLAYED_POSITIVE_MINUTES')
    if ev.get('acquired_at') and instant(kickoff)>instant(ev['acquired_at']):errors.append('MATCH_AFTER_SOURCE_CAPTURE')
    if played is None and minutes is not None and minutes>0:played=True
    return dict(player_id=pid,match_id=mid,kickoff=kickoff,team_id=team,team_name=r.get('teamName'),opponent_team_id=opp,competition_id=int(r['leagueId']),source_competition_id=int(r['leagueId']),competition_name=r.get('leagueName'),match_status='SOURCE_MATCH_HISTORY',played=played,minutes=minutes,role='HISTORY_REPORTED',authoritative_minutes=True,rejected=errors,evidence=ev)

def cached_history(pid,root=ROOT):
    path=root/f'raw/player_load/profiles/{pid}.json';obs=[];errors=[];pages=0;terminal=False;profile=None
    if path.exists():
        profile=read(path)
        if int(profile.get('id',-1))!=pid:raise ValueError('Profile ID mismatch')
        page=profile.get('initialMatchesPage');seen=set();current=path;prefix='$.initialMatchesPage';kind='PLAYER_HISTORY_PROFILE'
        while isinstance(page,dict):
            pages+=1
            for i,r in enumerate(page.get('matches') or []):obs.append(history_observation(pid,r,evidence(current,kind,f'{prefix}.matches[{i}]')))
            previous=page.get('previous')
            if not previous:terminal=True;break
            parsed=urlparse(previous)
            if parsed.hostname not in ('pub.fotmob.com','www.fotmob.com') or not re.search(rf'/player/{pid}/matches$',parsed.path):errors.append('UNTRUSTED_CURSOR');break
            if previous in seen:errors.append('REPEATED_CURSOR');break
            seen.add(previous);current=root/f'raw/player_load/history/{pid}/{digest(previous.encode())[:20]}.json';prefix='$';kind='PLAYER_HISTORY_PAGE'
            if not current.exists():errors.append('MISSING_PAGE: '+previous);break
            page=read(current)
    derived=root/f'data/collected_histories/{pid}.json';d=read(derived) if derived.exists() else {}
    if d:
        if int(d.get('fotmob_player_id',-1))!=pid:raise ValueError('Collected history ID mismatch')
        for i,r in enumerate(d.get('records') or []):
            o=history_observation(pid,r,evidence(derived,'DERIVED_COLLECTED_HISTORY',f'$.records[{i}]'));o['authoritative_minutes']=False;obs.append(o)
    coverage=dict(profile_present=bool(profile),linked_history_pages=pages,history_chain_complete=terminal,source_history_reached_start=bool(d.get('source_history_reached_start')) and not d.get('errors'),requested_start=d.get('requested_start'),requested_end_exclusive=d.get('requested_end_exclusive'),latest_source_timestamp=evidence(path,'PLAYER_PROFILE','$')['acquired_at'] if profile else None,coverage_certified=False,certified_start_date=None,certified_end_date=None,coverage_status='SOURCE_HISTORY_NOT_INDEPENDENTLY_CERTIFIED' if profile else 'NO_PROFILE_PARTIAL_RAW_ONLY',provenance_json=json.dumps(dict(errors=errors,pagination_not_independent_coverage_proof=True),ensure_ascii=False))
    return profile,obs,coverage

def raw_observations(player_ids,root=ROOT):
    wanted=set(player_ids);result=defaultdict(list);paths=list((root/'raw/matches').glob('*.json'))+list(root.glob('match_*.json'))+list((BASE/'raw/fotmob/matches').glob('*.json'))
    for path in sorted(paths):
        if path.name.endswith('.meta.json'):continue
        d=read(path);g=d.get('general') or {};c=d.get('content') or {};lu=c.get('lineup') or {};ps=c.get('playerStats') or {};mid=int(g.get('matchId') or 0)
        if not mid:continue
        index=defaultdict(list)
        for side in ['homeTeam','awayTeam']:
            own=lu.get(side) or {}
            for role in ['starters','subs','unavailable']:
                for i,p in enumerate(own.get(role) or []):
                    if int(p.get('id') or 0) in wanted:index[int(p['id'])].append((side,role,i,p,own))
        for s in ps.values():
            if int(s.get('id') or 0) in wanted and int(s['id']) not in index:index[int(s['id'])]=[]
        for pid,entries in index.items():
            errors=[];stat=ps.get(str(pid)) or {};team=int(stat.get('teamId') or (entries[0][4].get('id') if entries else 0) or 0);home=int((g.get('homeTeam') or {}).get('id') or 0);away=int((g.get('awayTeam') or {}).get('id') or 0);headers=[int(x.get('id') or 0) for x in (d.get('header') or {}).get('teams') or []]
            if headers!=[home,away] or home==away or team not in [home,away]:errors.append('GENERAL_HEADER_TEAM_MISMATCH')
            fm=re.search(r'(?:match_)?(\d+)(?:_api)?\.json$',path.name)
            if fm and int(fm[1])!=mid:errors.append('REQUEST_RETURN_ID_MISMATCH')
            chosen=[e for e in entries if e[1] in ('starters','subs')]
            if len(chosen)>1:errors.append('DUPLICATE_PLAYER_BOTH_SIDES_OR_ROLES')
            entry=chosen[0] if chosen else (entries[0] if entries else None)
            if entry:
                side,role,i,p,own=entry;jpath=f'$.content.lineup.{side}.{role}[{i}]'
                if int(own.get('id') or -1)!=team or int(g[side]['id'])!=team:errors.append('LINEUP_TEAM_MISMATCH')
                if stat and (int(stat.get('id') or -1)!=pid or int(stat.get('teamId') or -1)!=team or canonical_name(stat.get('name',''))!=canonical_name(p.get('name',''))):errors.append('STATS_ID_TEAM_NAME_MISMATCH')
            else:role='STATS_ONLY';p={};jpath=f'$.content.playerStats["{pid}"]'
            value=statistics(stat).get('minutes_played');subin=any(e.get('type')=='subIn' for e in (p.get('performance') or {}).get('substitutionEvents') or []);events=(((c.get('matchFacts') or {}).get('events') or {}).get('events')) or []
            subin=subin or any(e.get('type')=='Substitution' and e.get('swap') and int(e['swap'][0].get('id') or 0)==pid for e in events)
            played=True if role=='starters' or subin or (isinstance(value,(int,float)) and not isinstance(value,bool) and value>0) else None
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=150):errors.append('INVALID_MINUTES');value=None
            if not g.get('finished'):errors.append('NOT_FINISHED')
            ev=evidence(path,'MATCH_DETAIL',jpath);ownmeta=g.get('homeTeam') if team==home else g.get('awayTeam')
            result[pid].append(dict(player_id=pid,match_id=mid,kickoff=g.get('matchTimeUTCDate'),team_id=team,team_name=(ownmeta or {}).get('name'),player_name=stat.get('name') or p.get('name'),opponent_team_id=away if team==home else home,competition_id=int(g.get('parentLeagueId') or g.get('leagueId') or 0),source_competition_id=int(g.get('leagueId') or 0),competition_name=g.get('leagueName'),match_status='FINISHED' if g.get('finished') else 'NOT_FINISHED',played=played,minutes=value,role=role,authoritative_minutes=True,rejected=errors,evidence=ev))
    return result

class Context:
    """Team/competition type evidence only; no membership or transfer interval output."""
    def __init__(self,root=ROOT):
        self.teams=defaultdict(list);self.competitions=defaultdict(list);self.catalogs=defaultdict(list);self.artifacts={}
        for path in sorted((root/'raw/player_load/profiles').glob('*.json')):
            if path.name.endswith('.meta.json'):continue
            d=read(path)
            if int(d.get('id',-1))!=int(path.stem):raise ValueError('Context profile ID mismatch')
            ev=evidence(path,'PLAYER_PROFILE','$');self.artifacts[ev['artifact_id']]=ev
            for kind,g in (d.get('careerHistory',{}).get('careerItems') or {}).items():
                if kind not in ('senior','youth','national team'):continue
                for i,t in enumerate(g.get('teamEntries') or []):
                    if int(t.get('teamId') or 0)<=0:continue
                    typ='NATIONAL' if kind=='national team' else ('YOUTH_OR_RESERVE' if kind=='youth' else 'CLUB')
                    if typ=='NATIONAL' and re.search(r'U\d{2}|Olympic',t.get('team',''),re.I):typ='NATIONAL_YOUTH'
                    self.teams[int(t['teamId'])].append(dict(type=typ,name=t.get('team'),artifact=ev,json_path=f'$.careerHistory.careerItems["{kind}"].teamEntries[{i}]'))
                for i,s in enumerate(g.get('seasonEntries') or []):
                    for j,t in enumerate(s.get('tournamentStats') or []):
                        if t.get('leagueId') and isinstance(t.get('isFriendly'),bool):self.competitions[int(t['leagueId'])].append(dict(is_friendly=t['isFriendly'],name=t.get('leagueName'),context=kind,artifact=ev,json_path=f'$.careerHistory.careerItems["{kind}"].seasonEntries[{i}].tournamentStats[{j}]'))
        # Competition descriptors only: never enumerate fixtures to discover player matches.
        paths=list((root/'raw/leagues').glob('*.json'))+list((root/'reference').glob('league_*.json'))
        for path in sorted(paths):
            if path.name.endswith('.meta.json'):continue
            d=read(path).get('details') or {}
            if not d.get('id'):continue
            ev=evidence(path,'COMPETITION_METADATA','$.details');self.artifacts[ev['artifact_id']]=ev
            self.catalogs[int(d['id'])].append(dict(name=d.get('name'),country=d.get('country'),provider_type=d.get('type'),artifact=ev,json_path='$.details'))
    def team_type(self,tid):
        types={t['type'] for t in self.teams.get(tid,[])};nt=types&{'NATIONAL','NATIONAL_YOUTH'};club=types&{'CLUB','YOUTH_OR_RESERVE'}
        if nt and club:return 'UNKNOWN_CONFLICT'
        if nt:return 'NATIONAL' if 'NATIONAL' in nt else 'NATIONAL_YOUTH'
        if club:return 'CLUB' if 'CLUB' in club else 'YOUTH_OR_RESERVE'
        return 'UNKNOWN'
    def classify(self,competition_id,team_id,name=''):
        typ=self.team_type(team_id);claims=self.competitions.get(competition_id,[]);flags={c['is_friendly'] for c in claims};basis=[]
        policy={114:True,489:True,42:False,47:False,53:False,54:False,55:False,87:False,132:False,133:False,134:False,138:False,141:False,209:False,247:False,71:False}
        if competition_id in policy:flags.add(policy[competition_id]);basis.append('EXPLICIT_COMPETITION_ID_POLICY')
        if claims:basis.append('PROVIDER_ISFRIENDLY_METADATA')
        if len(flags)!=1 or typ=='UNKNOWN_CONFLICT':return 'REVIEW','UNKNOWN','MISSING_OR_CONFLICTING_CONTEXT',typ,basis
        if next(iter(flags)):
            if typ in ('NATIONAL','NATIONAL_YOUTH'):return 'INCLUDE','NATIONAL_TEAM_FRIENDLY','NATIONAL_FRIENDLY_COUNTS',typ,basis
            if typ in ('CLUB','YOUTH_OR_RESERVE') or competition_id==489:return 'EXCLUDE','CLUB_FRIENDLY','NONCOMPETITIVE_CLUB_FRIENDLY',typ,basis
            return 'REVIEW','UNKNOWN','FRIENDLY_UNKNOWN_TEAM_TYPE',typ,basis
        if typ in ('NATIONAL','NATIONAL_YOUTH'):cat='NATIONAL_TEAM_COMPETITIVE'
        elif typ=='YOUTH_OR_RESERVE':cat='OFFICIAL_YOUTH_RESERVE'
        elif competition_id==42:cat='UCL'
        elif competition_id in (47,53,54,55,87):cat='DOMESTIC_LEAGUE'
        elif competition_id in (132,133,134,138,141,209):cat='DOMESTIC_CUP'
        elif re.search(r'Champions League|Europa|Conference|Libertadores|Sudamericana|AFC|CONCACAF',name or '',re.I):cat='CONTINENTAL_CLUB'
        elif any(c.get('country') and not c['country'].startswith('INT') and c['country'] not in ('World','International','EUR','Europe') for c in getattr(self,'catalogs',{}).get(competition_id,[])) and not re.search(r'super|shield',name or '',re.I):
            cat='DOMESTIC_CUP' if re.search(r'cup|copa|coupe|coppa|pokal|taça|taca',name or '',re.I) else 'DOMESTIC_LEAGUE';basis.append('PROVIDER_COUNTRY_AND_COMPETITION_DESCRIPTOR_BREAKDOWN')
        else:cat='OTHER_OFFICIAL_CLUB' if typ=='CLUB' else 'OTHER_ELIGIBLE_OFFICIAL'
        return 'INCLUDE',cat,'EXPLICIT_NONFRIENDLY_METADATA',typ,basis
