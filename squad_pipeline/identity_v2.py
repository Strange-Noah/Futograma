"""Identity policy for the supplied three-club excerpts; authority aliases stay distinct from hints."""
import json,re,html
from .core import *
from .atalanta_text import normalized

def compact(name):return normalized(name).replace(' ','')
def clean(value):return html.unescape(re.sub('<[^>]+>',' ',value)).replace('*','').strip()

def cached_authorities(specs,club):
    records=[]
    for s in specs:
        if s.get('club_key')!=club:continue
        path=ROOT/s['path']
        if not path.is_file() or digest(path.read_bytes())!=s['sha256']:raise ValueError('Cached independent identity artifact missing/corrupt')
        if s['provider']!='uefa_current_identity':continue
        text=path.read_text()
        for i,m in enumerate(re.finditer(r'<a\s+[^>]*href="([^"]*/clubs/players/(\d+)[^"]*)"[^>]*title="([^"]+)"[^>]*>(.*?)</a>',text,re.S)):
            body=m[4];names=[clean(x) for x in re.findall(r'<span[^>]*itemprop="name"[^>]*>(.*?)</span>',body,re.S)]
            country=re.search(r'<div[^>]*itemprop="country"[^>]*>(.*?)</div>',body,re.S)
            if not country or not names:continue
            records.append(dict(full_name=html.unescape(m[3]),aliases=sorted(set(names)),country_code=clean(country[1]),source_player_id=m[2],profile_url='https://www.uefa.com'+m[1],source_url=s['url'],raw_path=s['path'],sha256=s['sha256'],field_path=f'player_anchor[{i}]@{m.start()}',explicit_aliases_only=True))
    return records

def attach_profiles(pool):
    enriched={}
    for pid,observations in pool.items():
        profile=None;path=ROOT/f'raw/player_load/profiles/{pid}.json'
        if path.exists():
            raw=path.read_bytes();data=json.loads(raw)
            if int(data.get('id',-1))!=int(pid):raise ValueError('Cached FotMob profile ID mismatch')
            profile=dict(id=int(pid),name=data['name'],date_of_birth=(data.get('birthDate') or {}).get('utcTime'),country_code=next((x.get('countryCode') for x in data.get('playerInformation',[]) if x.get('translationKey')=='country_sentencecase'),None),profile_url=f'https://www.fotmob.com/players/{pid}',raw_path=str(path.relative_to(ROOT)),sha256=digest(raw))
        enriched[pid]=[dict(e,cached_profile=profile) for e in observations]
    return enriched

def matching_identity(row,pool,authorities):
    source=normalized(row['name']);hint=normalized(row.get('candidate_full_name') or row['name']);result=[]
    for pid,observations in sorted(pool.items()):
        obs=sorted(observations,key=lambda e:(e.get('date',''),e.get('match_id',0),e.get('json_path','')))
        raw_names={compact(e['name']) for e in obs}|{compact(' '.join(x for x in [e.get('first_name'),e.get('last_name')] if x)) for e in obs}
        exact=compact(row['name']) in raw_names
        expanded=hint!=source
        hint_hits=compact(row.get('candidate_full_name') or row['name']) in raw_names
        auth=[a for a in authorities if compact(a['full_name']) in raw_names]
        literal_alias=[a for a in auth if source in {normalized(x) for x in a['aliases']}]
        given_alias=[e for e in obs if e.get('first_name') and source==normalized(e['first_name']) and source!=normalized(e['last_name'] or '')]
        last_alias=[e for e in obs if e.get('last_name') and source==normalized(e['last_name'])]
        # Include plausible shortened/transliteration candidates, without making them strong.
        words=source.split();partial=[e for e in obs if words and normalized(e['name']).split() and words[-1]==normalized(e['name']).split()[-1] and (len(words)==1 or words[0][:1]==normalized(e['name']).split()[0][:1])]
        prefix=[e for e in obs if len(normalized(e['name']).split())>=2 and (source.startswith(normalized(e['name'])+' ') or hint.startswith(normalized(e['name'])+' '))]
        if not exact and not hint_hits and not literal_alias and not given_alias and not last_alias and not partial and not prefix:continue
        nations={e['country_code'] for e in obs if e.get('country_code')};national_coherent=len(nations)==1
        profile=next((e.get('cached_profile') for e in obs if e.get('cached_profile')),None)
        profile_consistent=bool(profile and compact(profile['name']) in raw_names and profile.get('date_of_birth') and profile.get('country_code') in nations)
        authority_consistent=[a for a in auth if a['country_code'] in nations]
        conflicts=[]
        if not national_coherent:conflicts.append('FOTMOB_NATIONALITY_MISSING_OR_CONFLICT')
        if row.get('country_code') and nations!={row['country_code']}:conflicts.append('OFFICIAL_NATIONALITY_CONFLICT')
        if profile and profile.get('country_code') and profile['country_code'] not in nations:conflicts.append('PROFILE_NATIONALITY_CONFLICT')
        if row.get('date_of_birth') and profile and profile.get('date_of_birth') and row['date_of_birth'][:10]!=profile['date_of_birth'][:10]:conflicts.append('DOB_CONFLICT')
        opta_ids={e['opta_id'] for e in obs if e.get('opta_id')}
        stable=bool(obs) and all(int(e.get('match_id',0))>0 for e in obs) and len(opta_ids)<=1 and bool(opta_ids or profile_consistent or authority_consistent or len({e['match_id'] for e in obs})>=2)
        repeat=len({e['match_id'] for e in obs})>=2
        multiword=len(source.split())>=2
        strong=False;rule='WEAK_NAME_OR_UNSUPPORTED_EXPANSION'
        if not conflicts and stable:
            if exact and not expanded and multiword and (repeat or profile_consistent or authority_consistent):strong=True;rule='FULL_SOURCE_NAME_STABLE_CLUB_RECORDS'
            elif exact and not expanded and not multiword and (profile_consistent or (authority_consistent and literal_alias)):strong=True;rule='DOCUMENTED_MONONYM_STABLE_IDENTITIES'
            elif literal_alias and authority_consistent:strong=True;rule='EXPLICIT_AUTHORITY_ALIAS_FULL_NAME_NATIONALITY_STABLE_IDS'
            elif given_alias and (profile_consistent or (repeat and authority_consistent)):strong=True;rule='DOCUMENTED_GIVEN_NAME_PROFILE_OR_REPEATED_AUTHORITY_BRIDGE'
        result.append(dict(fotmob_player_id=int(pid),display_name=obs[-1]['name'],rule=rule,strong=strong,conflicts=conflicts,raw_match_evidence=obs,independent_alias_evidence=auth,literal_authority_alias=bool(literal_alias),hint_is_not_confirmation=expanded,given_name_alias=bool(given_alias),profile_consistent=profile_consistent,official_nationality_missing=row.get('country_code') is None))
    if len(result)>1:status,reason='UNRESOLVED','MULTIPLE_PLAUSIBLE_IDS'
    elif not result:status,reason='UNRESOLVED','NO_RELIABLE_LOCAL_FOTMOB_IDENTITY'
    elif not result[0]['strong']:status,reason='UNRESOLVED',(';'.join(result[0]['conflicts']) or 'MISSING_DOCUMENTED_ALIAS_OR_FULL_IDENTITY_BRIDGE; candidate/asset expansion alone insufficient')
    else:status,reason='CONFIRMED_AUTOMATIC',result[0]['rule']
    return dict(status=status,reason=reason,player_id=result[0]['fotmob_player_id'] if status=='CONFIRMED_AUTOMATIC' else None,name_fotmob=result[0]['display_name'] if status=='CONFIRMED_AUTOMATIC' else None,evidence=dict(policy='roster_identity_v2',official_attributes=row,candidates=result,independent_identity_hints=[a for a in authorities if source in {normalized(n) for n in a['aliases']} or hint==normalized(a['full_name'])],position_used_as_constraint=False,current_appearance_required=False,shirt_number_used_as_identifier=False,candidate_expansion_authoritative=False,scope='Operational identity evidence; source nationality remains null when not supplied'))


def local_other_team_evidence(rows,root=ROOT):
    """Targeted cache-only identity lookup. Never creates matchday membership for the importing club."""
    import subprocess
    terms=sorted({normalized(r['name']).split()[-1] for r in rows if normalized(r['name']) and len(normalized(r['name']).split()[-1])>=4})
    if not terms:return {}
    command=['rg','-l','-i','-F','--glob','*.json']
    for term in terms:command+=['-e',term]
    command+=[str(root/'raw/matches')]
    try:
        found=subprocess.run(command,cwd=root,capture_output=True,text=True,check=False)
        if found.returncode not in (0,1):raise ValueError('Local identity cache search failed')
        paths=[Path(v) for v in found.stdout.splitlines()]
    except FileNotFoundError:
        paths=[p for p in (root/'raw/matches').glob('*.json') if not p.name.endswith('.meta.json') and any(t.encode() in p.read_bytes().lower() for t in terms)]
    result={}
    for path in paths:
        if path.name.endswith('.meta.json'):continue
        raw=path.read_bytes();d=json.loads(raw);g=d.get('general') or {};content=d.get('content') or {};lineup=content.get('lineup') or {};stats=content.get('playerStats') or {}
        if not g.get('finished') or str(g.get('matchId'))!=path.stem:continue
        home=(g.get('homeTeam') or {}).get('id');away=(g.get('awayTeam') or {}).get('id');header=(d.get('header') or {}).get('teams') or []
        if len(header)!=2 or [int(x['id']) for x in header]!=[int(home),int(away)]:continue
        for side,tid in [('homeTeam',home),('awayTeam',away)]:
            team=lineup.get(side) or {}
            if int(team.get('id',-1))!=int(tid):continue
            for role in ('starters','subs'):
                for index,player in enumerate(team.get(role) or []):
                    pid=int(player.get('id') or 0);name=player.get('name','')
                    # id=0 is explicitly not a FotMob operational identity (e.g. some U17 records).
                    if pid<=0 or not any(t in normalized(name) for t in terms):continue
                    ps=stats.get(str(pid)) or {};opta=ps.get('optaId')
                    if int(ps.get('id') or 0)!=pid or int(ps.get('teamId') or -1)!=int(tid) or compact(ps.get('name',''))!=compact(name) or not opta:continue
                    if player.get('optaId') and str(player['optaId'])!=str(opta):continue
                    opposite=lineup.get('awayTeam' if side=='homeTeam' else 'homeTeam') or {}
                    if any(int(x.get('id') or 0)==pid for k in ('starters','subs') for x in opposite.get(k) or []):continue
                    date_value=g.get('matchTimeUTCDate') or (d.get('header') or {}).get('status',{}).get('utcTime')
                    if not date_value or not player.get('countryCode'):continue
                    result.setdefault(pid,[]).append(dict(match_id=int(g['matchId']),date=date_value[:10],period='IDENTITY_HISTORY_ONLY',path=str(path.relative_to(root)),sha256=digest(raw),json_path=f'$.content.lineup.{side}.{role}[{index}]',name=name,first_name=player.get('firstName'),last_name=player.get('lastName'),country_code=player.get('countryCode'),position_id=player.get('usualPlayingPositionId'),shirt_number=player.get('shirtNumber'),age=player.get('age'),opta_id=str(opta),observed_team_id=int(tid),evidence_scope='OTHER_TEAM_IDENTITY_ONLY_NOT_IMPORTING_CLUB_MEMBERSHIP',cached_profile=None))
    return result

def verify_new_canonical_player(row):
    import re
    data=json.loads(row['identity_evidence']);pid=row['fotmob_player_id'];candidates=data.get('candidates') or []
    chosen=[c for c in candidates if c['fotmob_player_id']==pid and c.get('strong')]
    if len(chosen)!=1:return False
    valid=0
    for e in chosen[0].get('raw_match_evidence') or []:
        if e.get('evidence_scope')!='OTHER_TEAM_IDENTITY_ONLY_NOT_IMPORTING_CLUB_MEMBERSHIP':continue
        path=(ROOT/e['path']).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or digest(path.read_bytes())!=e['sha256']:return False
        d=json.loads(path.read_text());m=re.fullmatch(r'\$\.content\.lineup\.(homeTeam|awayTeam)\.(starters|subs)\[(\d+)\]',e['json_path'])
        if not m:return False
        g=d['general'];tid=int(g[m[1]]['id']);team=d['content']['lineup'][m[1]];p=team[m[2]][int(m[3])];ps=(d['content'].get('playerStats') or {}).get(str(pid)) or {}
        if int(p['id'])!=pid or pid<=0 or int(team['id'])!=tid or int(ps.get('teamId') or -1)!=tid or int(ps.get('id') or 0)!=pid or str(ps.get('optaId'))!=str(e['opta_id']):return False
        valid+=1
    return valid>=2
