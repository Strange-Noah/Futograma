"""Bounded local A/B/C source adapters. No HTTP clients, roster whitelist or source writes."""
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime,timedelta,time
from zoneinfo import ZoneInfo
from .core import ROOT,BASE,read,digest
from .player_history import Context,cached_history,raw_observations,evidence
from futbol.collection import fixtures
from futbol.common import instant
def national_rank_context(context,detail,ev,wanted):
    # Explicit provider FIFA national-team ranking is separate from player nationality.
    if (detail.get('general') or {}).get('countryCode')!='INT':return
    for i,t in enumerate((detail.get('header') or {}).get('teams') or []):
        rank=t.get('fifaRank');tid=int(t.get('id') or 0)
        if tid not in wanted or isinstance(rank,bool) or not isinstance(rank,(int,float)) or rank<=0:continue
        context.teams[tid].append(dict(type='NATIONAL',name=t.get('name'),provider_fifa_rank=rank,classification_basis='PROVIDER_FIFA_NATIONAL_RANK_CONTEXT',artifact=ev,json_path=f'$.header.teams[{i}].fifaRank'))

TZ=ZoneInfo('Europe/Madrid')
PLAYERS={696679:'Raphinha',737066:'Erling Haaland',828159:'Kim Min-jae'}

def cutoff(day):return datetime.combine(datetime.fromisoformat(day).date(),time.max,TZ)
def event_day(kickoff):return instant(kickoff).astimezone(TZ).date().isoformat()
def trustworthy(ev):
    try:
        meta=read((ROOT/ev['path']).with_suffix('.meta.json'))
        return bool(ev.get('source_url','') and ev['source_url'].startswith('https://') and ev.get('acquired_at') and instant(ev['acquired_at']) and meta.get('sha256')==ev['sha256'] and meta.get('mode')=='network')
    except (ValueError,TypeError,KeyError,FileNotFoundError):return False

def available(ev,mode,day):return mode=='EVENT_DATE' or (trustworthy(ev) and instant(ev['acquired_at'])<=cutoff(day))
def completed(record,day):
    # No historical final-whistle timestamp exists: conservative 3-hour proxy plus source finished flag.
    return bool(record.get('finished') and instant(record['kickoff'])+timedelta(hours=3)<=cutoff(day))

class CacheSources:
    def __init__(self,player_ids):
        self.ids=list(player_ids);self.context=Context();self.profiles={};self.history={};self.coverage={};self.errors=[];self.details=[];self.catalogs=[];self.team_catalogs=[]
        self.raw=raw_observations(self.ids)
        for pid in self.ids:
            try:
                profile,obs,cov=cached_history(pid);self.profiles[pid]=profile;self.history[pid]=obs;self.coverage[pid]=cov
            except Exception as exc:self.history[pid]=[];self.coverage[pid]={'error':str(exc)}
        paths=list((ROOT/'raw/matches').glob('*.json'))+list(ROOT.glob('match_*.json'))+list((BASE/'raw/fotmob/matches').glob('*.json'))
        for p in sorted(paths):
            if p.name.endswith('.meta.json'):continue
            try:
                d=read(p);g=d.get('general') or {};mid=int(g.get('matchId') or 0)
                if not mid or not g.get('matchTimeUTCDate'):continue
                teams=[int((g.get(s) or {}).get('id') or 0) for s in ['homeTeam','awayTeam']]
                if teams!=[int(x.get('id') or 0) for x in (d.get('header') or {}).get('teams',[])]:raise ValueError('DETAIL_HEADER_IDS')
                ev=evidence(p,'MATCH_DETAIL','$.general');record=dict(match_id=mid,kickoff=g['matchTimeUTCDate'],teams=teams,team_names=[(g.get(s) or {}).get('name') for s in ['homeTeam','awayTeam']],competition_id=int(g.get('parentLeagueId') or g.get('leagueId') or 0),competition_name=g.get('leagueName'),finished=bool(g.get('finished')),evidence=ev)
                self.details.append(record)
                national_rank_context(self.context,d,ev,{o['team_id'] for obs in self.raw.values() for o in obs})
            except Exception as exc:self.errors.append(dict(path=str(p.relative_to(ROOT)),error=str(exc)))
        for p in sorted(list((ROOT/'raw/leagues').glob('*.json'))+list((ROOT/'reference').glob('league_*.json'))+list((BASE/'raw/fotmob/catalogs').glob('*.json'))):
            if p.name.endswith('.meta.json'):continue
            try:
                d=read(p);dt=d.get('details') or {};errs=[];fs=fixtures(d,errs)
                if not dt.get('id'):continue
                ev=evidence(p,'COMPETITION_DIRECTORY','$.fixtures / $.playoff')
                self.catalogs.append(dict(competition_id=int(dt['id']),name=dt.get('name'),season=dt.get('selectedSeason'),evidence=ev,fixtures=fs,errors=errs))
            except Exception as exc:self.errors.append(dict(path=str(p.relative_to(ROOT)),error=str(exc)))
        # Optional explicitly delivered provider team directories, kept separate from C.
        for p in sorted((BASE/'raw/dynamic/team_fixtures').glob('*.json')):
            if p.name.endswith('.meta.json'):continue
            try:
                d=read(p);tid=int(d['teamId']);ev=evidence(p,'TEAM_DIRECTORY','$.fixtures');self.team_catalogs.append(dict(team_id=tid,evidence=ev,fixtures=fixtures(d,[])))
            except Exception as exc:self.errors.append(dict(path=str(p.relative_to(ROOT)),error=str(exc)))

        from .dynamic_acquisition import get_affiliations
        self.affiliation_clues=get_affiliations(self.ids)

    def snapshot_context(self,mode,day):
        # Remove metadata not yet acquired in TRUE_AS_OF; no current context silently leaks backward.
        c=object.__new__(Context);c.teams=defaultdict(list);c.competitions=defaultdict(list);c.catalogs=defaultdict(list);c.artifacts={}
        for name in ['teams','competitions','catalogs']:
            for k,claims in getattr(self.context,name).items():
                getattr(c,name)[k]=[x for x in claims if available(x['artifact'],mode,day)]
        return c

    def hints(self,pid,mode,day):
        # Individual evidence, not membership: any former/borrowing/youth/national team can enter.
        teams=set();comps=set()
        for o in self.history[pid]+self.raw.get(pid,[]):
            if available(o['evidence'],mode,day) and event_day(o['kickoff'])<=day:
                teams.add(o['team_id']);comps.add(o['competition_id'])
        profile=self.profiles.get(pid)
        if profile:
            ev=evidence(ROOT/f'raw/player_load/profiles/{pid}.json','PLAYER_PROFILE','$')
            if available(ev,mode,day):
                for group in (profile.get('careerHistory',{}).get('careerItems') or {}).values():
                    teams.update(int(t['teamId']) for t in group.get('teamEntries') or [] if int(t.get('teamId') or 0)>0)
        for a in self.affiliation_clues.get(pid,{}).get('historical_evidence',[]):
            if not a['national_team_id'] or (a.get('observed_match_date') and a['observed_match_date']>day):continue
            if a.get('valid_from') and a['valid_from']>day:continue
            if a.get('valid_to') and a['valid_to']<day:continue
            if mode=='TRUE_AS_OF' and (not a['evidence_timestamp'] or instant(a['evidence_timestamp'])>cutoff(day)):continue
            teams.add(a['national_team_id'])
        return teams,comps

    def observations(self,pid,mid,mode,day):
        return [o for o in self.history[pid]+self.raw.get(pid,[]) if o['match_id']==mid and available(o['evidence'],mode,day) and instant(o['kickoff'])+timedelta(hours=3)<=cutoff(day)]

    def check(self,path,pid,mode,day,start):
        teams,comps=self.hints(pid,mode,day);candidates=[];checked=[];limitations=[]
        def add(r,ev):
            if r['kickoff'] and start<=event_day(r['kickoff'])<=day and completed(r,day):candidates.append(dict(**r,evidence=ev) if 'evidence' not in r else r)
        if path=='A':
            obs=[o for o in self.history[pid] if available(o['evidence'],mode,day)]
            for o in obs:
                checked.append(o['evidence']);add(dict(match_id=o['match_id'],kickoff=o['kickoff'],teams=[o['team_id'],o['opponent_team_id']],competition_id=o['competition_id'],competition_name=o['competition_name'],finished=True),o['evidence'])
            if self.profiles.get(pid):
                ev=evidence(ROOT/f'raw/player_load/profiles/{pid}.json','PLAYER_PROFILE','$')
                if available(ev,mode,day):checked.append(ev)
            cov=self.coverage[pid]
            if not cov.get('profile_present'):limitations.append('PLAYER_PROFILE_AND_LINKED_PAGES_UNAVAILABLE')
            if not cov.get('history_chain_complete'):limitations.append('PAGINATION_NOT_EXHAUSTED_OR_MISSING')
            limitations.append('PROVIDER_HISTORY_NOT_INDEPENDENT_COMPLETENESS_PROOF')
        elif path=='B':
            for cat in self.team_catalogs:
                if cat['team_id'] not in teams or not available(cat['evidence'],mode,day):continue
                checked.append(cat['evidence'])
                for f in cat['fixtures']:self.add_fixture(f,cat['evidence'],cat['team_id'],None,add)
            # Raw team sheets are a valid reverse lookup but NOT a complete team fixture directory.
            for r in self.details:
                if not teams.intersection(r['teams']) or not available(r['evidence'],mode,day):continue
                checked.append(r['evidence']);add(r,r['evidence'])
            limitations.append('RAW_TEAM_SHEETS_PARTIAL;NO_CERTIFIED_TEAM_FIXTURE_COVERAGE')
            for tid in sorted(teams):
                if not any(c['team_id']==tid and available(c['evidence'],mode,day) for c in self.team_catalogs):limitations.append('TEAM_DIRECTORY_UNAVAILABLE:'+str(tid))
        elif path=='C':
            # Independently traverse competition directories; no B result is used as C's fixture list.
            for cat in self.catalogs:
                if not available(cat['evidence'],mode,day):continue
                if cat['competition_id'] not in comps and not any({int(f['home']['id']),int(f['away']['id'])}&teams for f in cat['fixtures']):continue
                checked.append(cat['evidence'])
                for f in cat['fixtures']:
                    if {int(f['home']['id']),int(f['away']['id'])}&teams:self.add_fixture(f,cat['evidence'],None,cat,add)
                if cat['errors']:limitations.append('DIRECTORY_IDENTITY_CONFLICT:'+str(cat['competition_id']))
            present={c['competition_id'] for c in self.catalogs if available(c['evidence'],mode,day)}
            limitations.extend('COMPETITION_DIRECTORY_UNAVAILABLE:'+str(cid) for cid in sorted(comps-present))
            limitations.append('RELEVANT_COMPETITIONS_NOT_INDEPENDENTLY_EXHAUSTIVE')
        else:raise ValueError('Unknown discovery path')
        artifacts={e['artifact_id']:e for e in checked}
        # These cached sources are partial and stale, even after a successful inspection.
        status='PARTIAL_COVERAGE' if checked else 'SOURCE_UNAVAILABLE'
        return dict(path=path,status=status,candidates=candidates,checked_artifacts=list(artifacts.values()),limitations=limitations,network_requests=0,local_source_count=len(artifacts),source_bytes=sum((ROOT/e['path']).stat().st_size for e in artifacts.values()),latest_acquired_at=max((e['acquired_at'] for e in artifacts.values() if e.get('acquired_at')),default=None),freshness='STATIC_CACHE_NOT_LIVE',errors=self.errors)

    @staticmethod
    def add_fixture(f,ev,tid,cat,add):
        st=f.get('status') or {};kick=st.get('utcTime')
        if not kick or st.get('cancelled') or st.get('awarded'):return
        teams=[int(f[s]['id']) for s in ['home','away']]
        if tid and tid not in teams:return
        ev=dict(ev,json_path=ev['json_path']+' match_id='+str(f['id']))
        add(dict(match_id=int(f['id']),kickoff=kick,teams=teams,team_names=[f[s].get('name') for s in ['home','away']],competition_id=cat['competition_id'] if cat else None,competition_name=cat['name'] if cat else None,finished=bool(st.get('finished'))),ev)
