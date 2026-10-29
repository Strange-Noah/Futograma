"""Cache-only exact-ID reverse audit and evidence-backed official provider bridges."""
import json,re,subprocess
from pathlib import Path
from .core import ROOT,BASE,digest,write
from .identity_v2 import compact,attach_profiles
from futbol.normalization import statistics
SIX=[('psg','Illia Zabarnyi',1140003),('bayern','Bara Sapoko Ndiaye',1798782),('bayern','David Santos Daiber',1771197),('barcelona','Cancelo',361757),('barcelona','Martin',1598982),('barcelona','Bardghji',1315475)]

def lookup_ids(ids,root=ROOT):
    ids=set(ids);cmd=['rg','-l','-w','--glob','*.json']
    for pid in sorted(ids):cmd+=['-e',str(pid)]
    cmd+=[str(root/'raw')]
    found=subprocess.run(cmd,cwd=root,capture_output=True,text=True)
    if found.returncode not in (0,1):raise ValueError('Exact-ID local search failed: '+found.stderr)
    evidence={pid:[] for pid in ids};seen=set();rejected=[]
    for path in map(Path,found.stdout.splitlines()):
        if path.name.endswith('.meta.json'):continue
        try:d=json.loads(path.read_bytes())
        except (ValueError,OSError):continue
        if not isinstance(d,dict):continue
        g=d.get('general') or {};mid=int(g.get('matchId') or 0);c=d.get('content') or {};lineup=c.get('lineup') or {};stats=c.get('playerStats') or {};header=(d.get('header') or {}).get('teams') or []
        if not mid or not g.get('finished') or len(header)!=2:continue
        tids=[int((g.get(k) or {}).get('id') or -1) for k in ['homeTeam','awayTeam']]
        if [int(h.get('id') or -1) for h in header]!=tids:continue
        raw=path.read_bytes();sha=digest(raw)
        for side,tid in zip(['homeTeam','awayTeam'],tids):
            own=lineup.get(side) or {}
            if int(own.get('id') or -1)!=tid:continue
            opposite=lineup.get('awayTeam' if side=='homeTeam' else 'homeTeam') or {}
            for role in ['starters','subs']:
                for i,p in enumerate(own.get(role) or []):
                    pid=int(p.get('id') or 0)
                    if pid not in ids:continue
                    ps=stats.get(str(pid)) or {};v=statistics(ps).get('minutes_played');key=(pid,mid,sha,side,role,i)
                    if key in seen:continue
                    seen.add(key)
                    if any(int(x.get('id') or 0)==pid for k in ['starters','subs'] for x in opposite.get(k) or []):rejected.append(dict(player_id=pid,match_id=mid,reason='BOTH_SIDES'));continue
                    if ps and (int(ps.get('id') or 0)!=pid or int(ps.get('teamId') or -1)!=tid or compact(ps.get('name',''))!=compact(p.get('name',''))):rejected.append(dict(player_id=pid,match_id=mid,reason='STAT_ID_TEAM_NAME_CONFLICT'));continue
                    perf=p.get('performance') or {};subin=any(e.get('type')=='subIn' for e in perf.get('substitutionEvents') or [])
                    playing='PLAYED_STARTER' if role=='starters' else ('PLAYED_SUBSTITUTE' if subin or (isinstance(v,(int,float)) and v>0) else ('SOURCE_ZERO_MINUTES' if v==0 else 'BENCH_ONLY_PLAY_STATUS_UNKNOWN'))
                    date=g.get('matchTimeUTCDate')
                    e=dict(match_id=mid,date=date[:10] if date else '',kickoff=date,period='IDENTITY_HISTORY_ONLY',name=p.get('name'),first_name=p.get('firstName'),last_name=p.get('lastName'),country_code=p.get('countryCode'),age=p.get('age'),opta_id=str(ps.get('optaId') or p.get('optaId') or '') or None,observed_team_id=tid,home=g['homeTeam'],away=g['awayTeam'],role=role,minutes=v,participation_evidence=playing,substitution_events=perf.get('substitutionEvents'),path=str(path.relative_to(root)),sha256=sha,json_path=f'$.content.lineup.{side}.{role}[{i}]',evidence_scope='OTHER_TEAM_IDENTITY_ONLY_NOT_IMPORTING_CLUB_MEMBERSHIP',cached_profile=None)
                    evidence[pid].append(e)
    return attach_profiles(evidence),rejected

def apply_source_bridges(rows,resolutions,pool,authorities,provenance):
    """Only consume actually embedded official provider IDs or documented full-name profile slugs."""
    if not provenance.get('original_official_html'):return resolutions
    for row,x in zip(rows,resolutions):
        if x['status']=='CONFIRMED_AUTOMATIC':continue
        candidates=[]
        for pid,observations in sorted(pool.items()):
            obs=sorted(observations,key=lambda e:(e.get('date',''),e.get('match_id',0),e.get('json_path',''),e.get('path','')))
            opta={str(e['opta_id']) for e in obs if e.get('opta_id')};names={compact(e['name']) for e in obs};fullslug=compact(row.get('official_profile_url','').rsplit('/',1)[-1].replace('-',' '))
            direct=bool(row.get('official_opta_id') and opta=={row['official_opta_id']})
            alias=bool(len(fullslug)>0 and len(row.get('official_profile_url','').rsplit('/',1)[-1].split('-'))>=2 and fullslug in names)
            if not (direct or alias):continue
            if len({e['match_id'] for e in obs})<2 or len({e['country_code'] for e in obs if e.get('country_code')})!=1 or len(opta)>1:continue
            profiles=[e.get('cached_profile') for e in obs if e.get('cached_profile')]
            if row.get('date_of_birth') and any(p.get('date_of_birth') and p['date_of_birth'][:10]!=row['date_of_birth'][:10] for p in profiles):continue
            # The provider-ID bridge must not ignore a wholly unrelated name.
            if direct and not (compact(row['name']) in names or alias):continue
            candidates.append(dict(fotmob_player_id=pid,display_name=obs[-1]['name'],strong=True,conflicts=[],rule='OFFICIAL_OPTA_ID_REPEATED_MATCH_ID' if direct else 'OFFICIAL_FULL_NAME_PROFILE_SLUG_REPEATED_MATCH_IDS',raw_match_evidence=obs,independent_alias_evidence=[],official_profile_url=row.get('official_profile_url'),official_opta_id=row.get('official_opta_id')))
        if len(candidates)==1:
            z=candidates[0];x.update(status='CONFIRMED_AUTOMATIC',reason=z['rule'],player_id=z['fotmob_player_id'],name_fotmob=z['display_name']);x['evidence']=dict(policy='official_html_provider_bridge_v1',official_attributes=row,candidates=candidates,shirt_number_used_as_identifier=False,current_appearance_required=False,source_artifact_sha256=provenance['raw_sha256'])
    return resolutions

if __name__=='__main__':
    p,rejected=lookup_ids([x[2] for x in SIX]);write(BASE/'audit/six_club/reverse_id_evidence.json',dict(players={str(k):v for k,v in p.items()},rejected=rejected));print({k:dict(matches=len({e['match_id'] for e in v}),names=sorted({e['name'] for e in v}),opta=sorted({str(e['opta_id']) for e in v}),countries=sorted({str(e['country_code']) for e in v})) for k,v in p.items()})

def age_on(birth,date):
    from datetime import date as D
    b=D.fromisoformat(birth[:10]);d=D.fromisoformat(date[:10]);return d.year-b.year-((d.month,d.day)<(b.month,b.day))

def edit_distance(a,b):
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        nxt=[i]
        for j,y in enumerate(b,1):nxt.append(min(nxt[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=nxt
    return row[-1]

def independent_age(a):
    path=ROOT/a['raw_path'];text=path.read_text();url=a['profile_url'].replace('https://www.uefa.com','');pos=text.find(url);block=text[pos:text.find('</pk-table-row>',pos)]
    match=re.search(r'column-key="age">\s*(\d+)',block)
    meta=path.parent/'metadata.json'
    if not match or not meta.is_file():return None
    m=json.loads(meta.read_text())
    if m.get('sha256')!=digest(path.read_bytes()):return None
    return dict(age=int(match[1]),observed_at=m['retrieved_at'],raw_path=a['raw_path'],sha256=a['sha256'],field_path=a['field_path']+'/following::column-key=age')

def dfl_alias_evidence(name):
    path=ROOT/'official_registration_experiment_2025_26/raw/dfl_bayern_directory/hydration.json';meta=path.parent/'metadata.json';htmlpath=path.parent/'response.html'
    if not path.exists():return []
    # Hydration is secondary cached extraction; also demand the literal official name in raw HTML.
    if name not in htmlpath.read_text():return []
    result=[]
    def walk(v,p='$'):
        if isinstance(v,dict):
            n=v.get('name')
            if isinstance(n,dict) and compact(n.get('alias') or '')==compact(name) and v.get('id'):
                result.append(dict(full_name=n.get('full'),alias=n['alias'],country=(v.get('nationality') or {}).get('firstNationalityCode'),source_player_id=v['id'],path=str(path.relative_to(ROOT)),sha256=digest(path.read_bytes()),raw_html_path=str(htmlpath.relative_to(ROOT)),raw_html_sha256=digest(htmlpath.read_bytes()),field_path=p))
            for k,x in v.items():walk(x,p+'.'+k)
        elif isinstance(v,list):
            for i,x in enumerate(v):walk(x,f'{p}[{i}]')
    walk(json.loads(path.read_text()));return result

def validated_reverse_pool():
    path=BASE/'audit/six_club/reverse_id_evidence.json'
    if not path.is_file():return {}
    data=json.loads(path.read_text())['players'];result={}
    for pid,obs in data.items():
        for e in obs:
            p=ROOT/e['path']
            if not p.resolve().is_relative_to(ROOT) or digest(p.read_bytes())!=e['sha256']:raise ValueError('Reverse identity cache hash mismatch')
            d=json.loads(p.read_bytes());m=re.fullmatch(r'\$\.content\.lineup\.(homeTeam|awayTeam)\.(starters|subs)\[(\d+)\]',e['json_path']);player=d['content']['lineup'][m[1]][m[2]][int(m[3])]
            if int(player['id'])!=int(pid) or player['name']!=e['name'] or int(d['general']['matchId'])!=e['match_id']:raise ValueError('Reverse identity observation mismatch')
        result[int(pid)]=obs
    return result

def resolve_reverse_bridges(row,x,pool,authorities):
    """Confirm multicomponent identities only; asset surnames remain review entries."""
    if x['status']=='CONFIRMED_AUTOMATIC':return x
    from .atalanta_text import normalized
    source=normalized(row['name']).split()
    if len(source)<2:return x
    choices=[]
    for candidate in x['evidence']['candidates']:
        pid=candidate['fotmob_player_id'];obs=pool.get(pid,[]);nations={e['country_code'] for e in obs if e.get('country_code')};opta={e['opta_id'] for e in obs if e.get('opta_id')}
        if len({e['match_id'] for e in obs})<2 or len(nations)!=1 or len(opta)!=1:continue
        authority=[a for a in authorities if compact(a['full_name'])==compact(row['name']) and a['country_code'] in nations]
        if not authority:continue
        exact=[e for e in obs if compact(e['name'])==compact(row['name'])];rule=None;extra=[]
        if exact:rule='REVERSE_EXACT_FULL_NAME_SAME_FOTMOB_OPTA_ID_INDEPENDENT_AUTHORITY'
        else:
            profiles=[e['cached_profile'] for e in obs if e.get('cached_profile')];age=independent_age(authority[0]);rawparts=[normalized(e['name']).split() for e in obs]
            if profiles and age and all(v[-1]==source[-1] and edit_distance(v[0],source[0])<=2 for v in rawparts):
                profile=profiles[0]
                if profile.get('date_of_birth') and profile.get('country_code') in nations and age_on(profile['date_of_birth'],age['observed_at'])==age['age'] and all(e.get('age') is None or age_on(profile['date_of_birth'],e['date'])==int(e['age']) for e in obs):rule='REVERSE_FULL_SURNAME_GIVEN_VARIANT_PROFILE_DOB_AUTHORITY_AGE';extra=[age]
            if not rule and len(source)>=3 and age and all(v[0]==source[0] and v[-1]==source[-1] for v in rawparts):
                dfl=dfl_alias_evidence(row['name']);recent=[e for e in obs if e['date'][:4]==age['observed_at'][:4]]
                if dfl and any(a['country']=='SN' and nations=={'SEN'} for a in dfl) and recent and all(e.get('age')==age['age'] for e in recent):rule='REVERSE_FIRST_LAST_STABLE_ID_UEFA_DFL_FULL_NAME_NATIONALITY_AGE';extra=[age,*dfl]
        if rule:
            choices.append(dict(candidate,rule=rule,strong=True,raw_match_evidence=obs,independent_alias_evidence=authority,additional_identity_evidence=extra))
    if len(choices)==1:
        c=choices[0];x=dict(x,status='CONFIRMED_AUTOMATIC',reason=c['rule'],player_id=c['fotmob_player_id'],name_fotmob=c['display_name']);x['evidence']=dict(x['evidence'],policy='reverse_match_identity_v1',candidates=choices,identity_and_membership_separate=True,weak_surname_alone_rejected=True)
    return x
