from __future__ import annotations
import json,re,unicodedata
from collections import defaultdict
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .core import *
from futbol.collection import fixtures
from futbol.normalization import statistics
from futbol.analytics import LoadIndex
from futbol.common import instant,day_start,day_end

def canonical_name(value):
    return ''.join(c for c in unicodedata.normalize('NFKD',value).casefold() if c.isalnum())

def issue(db,club,mid,pid,kind,detail):
    key=digest(json.dumps([club,mid,pid,kind,detail],sort_keys=True).encode())
    db.execute('INSERT OR IGNORE INTO identity_issues VALUES(?,?,?,?,?,?,?)',(key,club,mid,pid,kind,detail,now()))

def period_for(kickoff,cfg):
    date=instant(kickoff).astimezone(ZoneInfo(cfg['timezone'])).date().isoformat()
    return next((p for p in cfg['periods'] if p['start']<=date<p['end_exclusive']),None),date

def competition_category(name,lid,clubs):
    if lid==42:return 'UCL'
    if any(lid==c['league_id'] for c in clubs):return 'DomesticLeague'
    if re.search('friendly|friendlies|U1[679]|U2[013]|youth|reserve',name,re.I):return 'EXCLUDED'
    if re.search('super|shield|trophée',name,re.I):return 'SuperCup'
    if re.search('world cup|intercontinental',name,re.I):return 'GlobalClubCup'
    if re.search('europa|conference',name,re.I):return 'OtherUEFA'
    if re.search('cup|copa|coppa|coupe|pokal',name,re.I):return 'DomesticCup'
    return 'REVIEW_REQUIRED'

def extract(detail,club,expected=None):
    g=detail.get('general') or {};mid=int(g.get('matchId',-1))
    home=int((g.get('homeTeam') or {}).get('id',-1));away=int((g.get('awayTeam') or {}).get('id',-1))
    if home==away or club not in (home,away):raise ValueError('Club/general team ID mismatch')
    if expected and (mid!=expected['id'] or (home,away)!=(int(expected['home']['id']),int(expected['away']['id']))):raise ValueError('Fixture/detail team or match ID conflict')
    if not g.get('finished'):raise ValueError('Match not completed')
    header=(detail.get('header') or {}).get('teams') or []
    if len(header)!=2 or [int(x['id']) for x in header]!=[home,away]:raise ValueError('Header/general side mismatch')
    lineup=(detail.get('content') or {}).get('lineup') or {};side='homeTeam' if home==club else 'awayTeam';opp='awayTeam' if home==club else 'homeTeam'
    own=lineup.get(side) or {};other=lineup.get(opp) or {}
    if int(own.get('id',-1))!=club or int(other.get('id',-1))!=(away if home==club else home):raise ValueError('Lineup side IDs mismatch')
    starters=own.get('starters');subs=own.get('subs')
    if not isinstance(starters,list) or len(starters)!=11 or not isinstance(subs,list) or not subs:raise ValueError('Incomplete starters/subs arrays (empty bench requires review)')
    ids=[int(x['id']) for x in starters+subs]
    if any(x<=0 for x in ids) or len(ids)!=len(set(ids)):raise ValueError('Duplicate or invalid own player IDs')
    opposing={int(x['id']) for k in ('starters','subs') for x in other.get(k,[])}
    if opposing.intersection(ids):raise ValueError('Player on both sides')
    allstats=(detail.get('content') or {}).get('playerStats') or {}
    complete_stats=all(statistics(allstats.get(str(x['id']),{})).get('minutes_played') is not None for x in starters)
    incoming={}
    for e in (((detail.get('content') or {}).get('matchFacts') or {}).get('events') or {}).get('events') or []:
        if e.get('type')=='Substitution' and len(e.get('swap') or [])==2:
            incoming[int(e['swap'][0]['id'])]=True
    rows=[]
    for role in ('starters','subs'):
        for n,x in enumerate(own[role]):
            pid=int(x['id']);ps=allstats.get(str(pid),{});stats=statistics(ps);value=stats.get('minutes_played')
            if not x.get('name'):raise ValueError('Missing player name')
            if ps and (int(ps.get('id',-1))!=pid or int(ps.get('teamId',-1))!=club):raise ValueError('playerStats player/team identity mismatch')
            if ps and canonical_name(ps.get('name',''))!=canonical_name(x['name']):raise ValueError('playerStats/lineup name disagreement')
            substituted=pid in incoming or any(e.get('type')=='subIn' for e in (x.get('performance') or {}).get('substitutionEvents') or [])
            played=True if role=='starters' or substituted or (isinstance(value,(int,float)) and value>0) else None
            status='match_detail' if value is not None else 'UNKNOWN'
            if value is None and role=='subs' and not substituted and complete_stats:
                value=0;played=False;status='unused_substitute_complete_starter_stats'
            elif value==0 and role=='subs' and not substituted:played=False
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not 0<=value<=150):raise ValueError('Invalid minutes')
            rows.append(dict(player_id=pid,name=x['name'],role=role,shirt_number=x.get('shirtNumber'),minutes=value,minutes_status=status,played=played,json_path=f'$.content.lineup.{side}.{role}[{n}]'))
    return rows

def immutable_evidence(path):
    path=Path(path)
    if path.resolve().is_relative_to((BASE/'raw/fotmob').resolve()):
        raw=path.read_bytes();dest=BASE/'raw/fotmob/evidence'/digest(raw)/'response.json'
        dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():dest.write_bytes(raw)
        return dest
    return path

class LocalFotMob:
    """Read-only legacy adapter. Network writes never reach legacy caches."""
    def __init__(self,cfg):self.cfg=cfg
    def catalogs(self):
        # Prefer isolated refreshed artifacts, then legacy cache. File identity checked below.
        paths=list((BASE/'raw/fotmob/catalogs').glob('*.json'))+list((ROOT/'raw/leagues').glob('*.json'))
        seen=set()
        for path in paths:
            if path.name.endswith('.meta.json'):continue
            try:
                d=read(path);g=d.get('details') or {};key=(int(g['id']),str(g['selectedSeason']))
                if key in seen:continue
                seen.add(key);yield path,d
            except (ValueError,KeyError,TypeError):continue
    def details(self):
        seen=set()
        for folder,pattern in [(BASE/'raw/fotmob/matches','*.json'),(ROOT/'raw/matches','*.json'),(ROOT,'match_*.json')]:
            for path in sorted(folder.glob(pattern)):
                if path.name.endswith('.meta.json'):continue
                m=re.search(r'(?:match_)?(\d+)(?:_api)?\.json$',path.name)
                if not m:continue
                mid=int(m[1])
                if mid in seen:continue
                seen.add(mid)
                yield mid,path

def reconstruct(db,cfg):
    seed(db,cfg);client=LocalFotMob(cfg);clubs={c['id']:c for c in cfg['clubs']};expected={};catalog_seen=set();catalog_audit=[];detail_audit=[];excluded=[]
    cutoff=day_end(cfg['as_of'],cfg['timezone'])
    for path,d in client.catalogs():
        det=d['details'];lid=int(det['id']);cat=competition_category(det.get('name',''),lid,cfg['clubs'])
        if cat=='EXCLUDED':continue
        conflicts=[]
        try:fs=fixtures(d,conflicts)
        except (ValueError,KeyError,TypeError) as e:
            catalog_audit.append(dict(path=str(path.relative_to(ROOT)),error=str(e)));continue
        aid=artifact(db,immutable_evidence(path),'fotmob_catalog');catalog_seen.add((lid,det['selectedSeason']))
        db.execute('INSERT OR REPLACE INTO competitions VALUES(?,?,?,?)',(lid,det['name'],cat,det.get('country')))
        for x in conflicts:
            catalog_audit.append(dict(path=str(path.relative_to(ROOT)),error=json.dumps(x)))
        for f in fs:
            try:
                tids=(int(f['home']['id']),int(f['away']['id']));t=f['status'].get('utcTime')
                if not t or not f['status'].get('finished') or f['status'].get('cancelled') or f['status'].get('awarded') or instant(t)>=cutoff:continue
                period,date=period_for(t,cfg)
                if not period:continue
                for tid in set(tids).intersection(clubs):
                    key=(tid,int(f['id']));value=dict(f,competition_id=lid,period=period,date=date,catalog_artifact_id=aid)
                    old=expected.get(key)
                    if old and (old['home']['id'],old['away']['id'])!=(f['home']['id'],f['away']['id']):
                        issue(db,tid,int(f['id']),None,'fixture_catalog_conflict',str(path));continue
                    expected[key]=value
            except (ValueError,KeyError,TypeError) as e:catalog_audit.append(dict(path=str(path.relative_to(ROOT)),error=str(e)))
    found={};all_detail_ids=set();relevant=0
    for mid,path in client.details():
        all_detail_ids.add(mid)
        try:
            d=read(path);g=d.get('general') or {};t=g.get('matchTimeUTCDate');home=int(g.get('homeTeam',{}).get('id',-1));away=int(g.get('awayTeam',{}).get('id',-1))
            selected=set([home,away]).intersection(clubs)
            if not selected:continue
            if int(g.get('matchId',-1))!=mid:raise ValueError('Cache filename/detail ID mismatch')
            if not t or not g.get('finished') or instant(t)>=cutoff:continue
            period,date=period_for(t,cfg)
            if not period:continue
            lid=int(g.get('parentLeagueId') or g['leagueId']);cat=competition_category(g.get('leagueName',''),lid,cfg['clubs'])
            if cat in ('EXCLUDED','REVIEW_REQUIRED'):
                excluded.append(dict(match_id=mid,competition_id=lid,name=g.get('leagueName'),reason=cat));continue
            db.execute('INSERT OR IGNORE INTO competitions VALUES(?,?,?,?)',(lid,g.get('leagueName'),cat,g.get('countryCode')))
            meta=path.with_suffix('.meta.json');fetched=read(meta).get('fetched_at') if meta.exists() else None
            aid=artifact(db,immutable_evidence(path),'fotmob_match',acquired_at=fetched,effective=period['id']);relevant+=1
            result=(d.get('header') or {}).get('status',{}).get('scoreStr')
            db.execute('INSERT INTO matches(match_id,home_id,away_id,kickoff,local_date,period_id,competition_id,result,artifact_id,observed_at,source_competition_id) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(match_id) DO UPDATE SET home_id=excluded.home_id,away_id=excluded.away_id,kickoff=excluded.kickoff,local_date=excluded.local_date,period_id=excluded.period_id,competition_id=excluded.competition_id,result=excluded.result,artifact_id=excluded.artifact_id,observed_at=excluded.observed_at,source_competition_id=excluded.source_competition_id',(mid,home,away,t,date,period['id'],lid,result,aid,now(),int(g['leagueId'])))
            for tid in selected:
                key=(tid,mid);fallback={'id':mid,'home':g['homeTeam'],'away':g['awayTeam'],'competition_id':lid,'period':period,'date':date,'status':{'utcTime':t}}
                f=expected.setdefault(key,fallback);found[key]=True
                old=db.execute('SELECT artifact_id,processing_status FROM match_processing WHERE club_id=? AND match_id=?',key).fetchone()
                if old and old['artifact_id']==aid and old['processing_status']=='VALID':continue
                db.execute('DELETE FROM squad_observations WHERE club_id=? AND match_id=?',key);db.execute('DELETE FROM player_match_minutes WHERE club_id=? AND match_id=?',key)
                try:
                    rows=extract(d,tid,f)
                    for x in rows:
                        pid=x['player_id'];oldp=db.execute('SELECT display_name FROM players WHERE player_id=?',(pid,)).fetchone()
                        if oldp and canonical_name(oldp[0])!=canonical_name(x['name']):issue(db,tid,mid,pid,'source_name_variant',oldp[0]+' / '+x['name'])
                        db.execute('INSERT OR IGNORE INTO players(player_id,display_name) VALUES(?,?)',(pid,x['name']))
                        db.execute('INSERT INTO squad_observations VALUES(?,?,?,?,?,?,?,?,?)',(tid,mid,pid,x['role'],x['shirt_number'],x['name'],x['json_path'],aid,now()))
                        db.execute('INSERT INTO player_match_minutes VALUES(?,?,?,?,?,?,?)',(tid,mid,pid,x['minutes'],x['minutes_status'],x['played'],aid))
                    state,reason='VALID','Structural/raw-side validation only; independent identity verification not implied'
                except (ValueError,KeyError,TypeError) as e:
                    state,reason='MALFORMED',str(e);issue(db,tid,mid,None,'lineup_structure_or_identity',reason)
                db.execute('INSERT OR REPLACE INTO match_processing VALUES(?,?,?,?,?,?,?)',(tid,mid,aid,'CACHED',state,reason,now()))
                db.commit()
        except (ValueError,KeyError,TypeError) as e:
            detail_audit.append(dict(match_id=mid,path=str(path.relative_to(ROOT)),error=str(e)))
    for key,f in expected.items():
        if key not in found:
            db.execute('DELETE FROM squad_observations WHERE club_id=? AND match_id=?',key);db.execute('DELETE FROM player_match_minutes WHERE club_id=? AND match_id=?',key)
            db.execute('INSERT OR REPLACE INTO match_processing VALUES(?,?,?,?,?,?,?)',(*key,None,'MISSING','NOT_PROCESSED','No valid local detail for expected match',now()))
    # Existing independent audit alerts are labels only, never membership inputs.
    reference=ROOT/'audit/psg_roster_chain/flagged_squad_entries.csv'
    if reference.exists():
        import csv
        with reference.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                mid=int(row['match_id']);pid=int(row['fotmob_player_id'])
                if db.execute('SELECT 1 FROM squad_observations WHERE club_id=9847 AND match_id=? AND player_id=?',(mid,pid)).fetchone():
                    issue(db,9847,mid,pid,'prior_audit_reference_not_resolved',json.dumps({k:row.get(k) for k in ('independent_matchday_affiliation','matchday_identity_certification','name_review','independent_sources')},ensure_ascii=False))
    db.commit()
    coverage=[]
    for c in cfg['clubs']:
        for p in cfg['periods']:
            if p['start']>cfg['as_of']:continue
            keys=[k for k,f in expected.items() if k[0]==c['id'] and f['period']['id']==p['id']]
            states=[db.execute('SELECT * FROM match_processing WHERE club_id=? AND match_id=?',k).fetchone() for k in keys]
            seasonlabel=p['season'][:4]+'/'+str(int(p['season'][:4])+1)
            absent=[lid for lid in c['required_competition_ids'] if (lid,seasonlabel) not in catalog_seen]
            roster=db.execute('SELECT COUNT(*) FROM historical_membership WHERE club_id=? AND period_id=?',(c['id'],p['id'])).fetchone()[0]
            bench=db.execute('SELECT COUNT(*) FROM (SELECT o.player_id FROM squad_observations o JOIN matches m USING(match_id) JOIN player_match_minutes v USING(club_id,match_id,player_id) WHERE o.club_id=? AND m.period_id=? GROUP BY o.player_id HAVING MAX(o.role="starters")=0 AND MAX(COALESCE(v.played,1))=0)',(c['id'],p['id'])).fetchone()[0]
            valid=sum(s is not None and s['processing_status']=='VALID' for s in states);retrieved=sum(s is not None and s['acquisition_status']=='CACHED' for s in states)
            coverage.append(dict(club=c['key'],club_id=c['id'],period=p['id'],season=p['season'],expected_known_matches=len(keys),expected_total_matches=None,successfully_retrieved=retrieved,valid_lineups=valid,missing_details=len(keys)-retrieved,malformed_lineups=retrieved-valid,unique_players=roster,bench_only_players=bench,missing_catalog_ids=';'.join(map(str,absent)),identity_issues=db.execute('SELECT COUNT(*) FROM identity_issues i JOIN matches m USING(match_id) WHERE i.club_id=? AND m.period_id=?',(c['id'],p['id'])).fetchone()[0],independently_verified_identity_matches=0,validated_known_coverage_complete=bool(keys) and valid==len(keys),all_official_competitions_complete=False,coverage_status='PARTIAL_UNIVERSE_NOT_INDEPENDENTLY_CERTIFIED'))
    table(BASE/'reports/coverage.csv',coverage)
    table(BASE/'reports/catalog_errors.csv',catalog_audit,['path','error']);table(BASE/'reports/detail_errors.csv',detail_audit,['match_id','path','error']);table(BASE/'reports/excluded_matches.csv',excluded,['match_id','competition_id','name','reason'])
    gaps=[]
    for key,f in expected.items():
        s=db.execute('SELECT * FROM match_processing WHERE club_id=? AND match_id=?',key).fetchone()
        if s['processing_status']!='VALID':gaps.append(dict(club=clubs[key[0]]['key'],club_id=key[0],match_id=key[1],date=f['date'],period=f['period']['id'],competition_id=f['competition_id'],home_id=f['home']['id'],away_id=f['away']['id'],status=s['processing_status'],reason=s['reason']))
    table(BASE/'reports/match_gaps.csv',gaps,['club','club_id','match_id','date','period','competition_id','home_id','away_id','status','reason'])
    table(BASE/'reports/historical_membership.csv',[dict(x) for x in db.execute("SELECT h.*,p.display_name FROM historical_membership h JOIN players p USING(player_id) JOIN periods t USING(period_id) WHERE t.season!=? ORDER BY club_id,period_id,player_id",(cfg['current_season'],))])
    table(BASE/'reports/membership_evidence.csv',[dict(x) for x in db.execute('SELECT o.*,m.local_date,m.period_id,m.competition_id FROM squad_observations o JOIN matches m USING(match_id) ORDER BY club_id,match_id,player_id')])
    current=[]
    for x in db.execute("SELECT c.*,p.display_name FROM combined_membership c JOIN players p USING(player_id) JOIN periods t USING(period_id) WHERE t.season=?",(cfg['current_season'],)):
        x=dict(x);baseline=db.execute("SELECT 1 FROM official_snapshots WHERE club_id=? AND period_id=? AND parse_status='VERIFIED'",(x['club_id'],x['period_id'])).fetchone()
        parsed=db.execute("SELECT 1 FROM official_snapshots WHERE club_id=? AND period_id=? AND parse_status='PARSED_PENDING_IDENTITY'",(x['club_id'],x['period_id'])).fetchone()
        if not baseline:x['listed_in_baseline']=None
        x.update(baseline_status='VERIFIED' if baseline else ('PARSED_IDENTITY_PENDING' if parsed else 'AWAITING_PARSED_BASELINE'),membership_basis='both' if x['listed_in_baseline'] and x['observed_in_matchday'] else ('official_baseline' if x['listed_in_baseline'] else 'match_observed'),discrepancy='MATCH_ONLY_PROVISIONAL' if baseline and not x['listed_in_baseline'] else ('BASELINE_COMPARISON_PENDING' if not baseline else 'NONE'));current.append(x)
    table(BASE/'reports/current_membership.csv',current)
    table(BASE/'reports/identity_issues.csv',[dict(x) for x in db.execute('SELECT * FROM identity_issues')])
    table(BASE/'reports/player_match_history.csv',[dict(x) for x in db.execute('SELECT v.*,o.role,o.shirt_number,o.source_name,m.kickoff,m.local_date,m.period_id,m.competition_id FROM player_match_minutes v JOIN squad_observations o USING(club_id,match_id,player_id) JOIN matches m USING(match_id)')])
    return dict(raw_match_files_indexed=len(all_detail_ids),relevant_details=relevant,coverage=coverage,gaps=len(gaps))

def rolling(db,cfg):
    # Pure existing workload function: leave history completeness unknown.
    ms={};apps=[]
    for r in db.execute('SELECT v.*,m.kickoff,c.category FROM player_match_minutes v JOIN matches m USING(match_id) JOIN competitions c USING(competition_id)'):
        if not r['played'] and r['played'] is not None:continue
        key=str(r['match_id']);ms[key]=dict(match_id=key,datetime=r['kickoff'],is_national=False,competition_type='UCL' if r['category']=='UCL' else ('Domestic' if r['category'] in ('DomesticLeague','DomesticCup','SuperCup') else 'Other'))
        apps.append(dict(player_id=r['player_id'],match_id=key,minutes_played=r['minutes'],minute_conflict=False))
    index=LoadIndex(apps,list(ms.values()),{},cfg);upper=day_end(cfg['as_of'],cfg['timezone']);out=[]
    for pid in sorted(index.events):
        for w in cfg['windows_days']:
            lower=day_start((datetime.fromisoformat(cfg['as_of'])-timedelta(days=w-1)).date().isoformat(),cfg['timezone']);b=index.window(pid,lower,upper)
            out.append(dict(player_id=pid,as_of=cfg['as_of'],window_days=w,known_tracked_club_minutes=b['known']['total'],missing_observed_minutes=b['missing_minutes'],personal_total_minutes=None,personal_history_complete=False,scope='six tracked clubs only; national/other clubs uncollected',boundary='Europe/Madrid calendar days, as_of inclusive; legacy daily_table boundary semantics'))
    table(BASE/'reports/rolling_workload.csv',out)

def update(db,cfg,online=False):
    before={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('matches','squad_observations','player_match_minutes','players')}
    if online:
        from .feeds import run_feeds
        run_feeds(db,cfg,read(BASE/'config/fotmob_feeds.json'))
    result=reconstruct(db,cfg);jobs=[];today=instant(cfg['as_of']+'T00:00:00+00:00')
    for c in cfg['clubs']:
        for lid in c['required_competition_ids']:
            year=int(cfg['current_season'][:4]);jobs.append(('catalog',f'https://www.fotmob.com/api/data/leagues?id={lid}&season={year}%2F{year+1}'))
    for x in db.execute('SELECT DISTINCT p.match_id,m.kickoff,p.processing_status FROM match_processing p LEFT JOIN matches m USING(match_id)'):
        if x['processing_status']!='VALID' or (x['kickoff'] and today-instant(x['kickoff'])<timedelta(days=cfg['correction_days'])):jobs.append(('match',f"https://www.fotmob.com/api/data/matchDetails?matchId={x['match_id']}"))
    for kind,url in set(jobs):
        # This endpoint is explicitly prohibited today. Do not silently turn a Boolean into permission.
        status='TERMS_RESTRICTED';reason=cfg['network']['reason']
        db.execute('INSERT OR REPLACE INTO update_jobs VALUES(?,?,?,?,?,?)',(digest(url.encode()),kind,url,status,now(),reason))
    rolling(db,cfg);db.commit()
    after={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in before}
    info=dict(before=before,after=after,network_requested=online,network_requests_made=0,discovery_status='TERMS_RESTRICTED',reason=cfg['network']['reason'],queued_jobs=len(set(jobs)),new_player_ids=after['players']-before['players'],processing='LOCAL_CYCLE_EXECUTED',acquisition='BLOCKED',full_personal_workload='NOT_RECOMPUTED; tracked-club known values only')
    write(BASE/'reports/latest_update.json',info);return info
