"""Discover provider identities from public team SSR; never authorizes club management."""
from dataclasses import fields
from .identity_pipeline import Candidate,Record,resolve
from .international_discovery import payload

def team_identity_records(raw,source,team_id):
    d=payload(raw);prefix='$.props.pageProps.data'
    if 'fallback' in d:
        key='team-'+str(team_id)
        if key not in d['fallback']:raise ValueError('EXACT_TEAM_FALLBACK_REQUIRED')
        d=d['fallback'][key];prefix='$.props.pageProps.fallback['+repr(key)+']'
    if str((d.get('details') or {}).get('id'))!=str(team_id):raise ValueError('IDENTITY_TEAM_ID_MISMATCH')
    groups=(d.get('squad') or {}).get('squad')
    if not isinstance(groups,list):raise ValueError('PROVIDER_TEAM_SQUAD_SCHEMA_REQUIRED')
    rows=[];seen=set()
    for gi,g in enumerate(groups):
        if g.get('title') in ('coach','coaches','staff'):continue
        for pi,p in enumerate(g.get('members') or []):
            if (p.get('role') or {}).get('key') in ('coach','manager'):continue
            pid=p.get('id');dob=p.get('dateOfBirth')
            if not pid or not p.get('name') or not dob:continue
            if str(pid) in seen:raise ValueError('DUPLICATE_TEAM_PROVIDER_PLAYER_ID')
            seen.add(str(pid));rows.append(Record(source['url'],source['sha256'],f'{prefix}.squad.squad[{gi}].members[{pi}]','FotMob',[p['name']],provider_ids={'FotMob':str(pid)},birth_date=dob[:10],official_clubs=[str(team_id)],scope='IDENTITY_ONLY',observed_on=(source.get('response_timestamp') or '')[:10],authority='PUBLIC_PROVIDER',raw_fields={'provider_team_roster_not_management_proof':True}))
    return rows

def provider_candidates(dossiers,source_results=()):
    candidates=[];keys={f.name for f in fields(Record)}
    from .identity_pipeline import ranked_candidates
    for d in dossiers:
        records=[Record(**{k:v for k,v in r.items() if k in keys}) for r in d['verified_source_records']]
        for r in records:r.raw_fields['_verified_identity']=True
        candidates.append(Candidate(int(d['player_id']),list(dict.fromkeys([d['name']]+[n for r in records for n in r.names])),records))
    byid={str(c.player_id):c for c in candidates}
    # Official biographies not yet cross-source verified may seed DOB evidence, never stable IDs.
    for item in source_results:
        raw=item.get('source_record') or {};r=Record(**{k:v for k,v in raw.items() if k in keys})
        if not r.birth_date or not r.retrieved or r.source_status!='SUCCESS' or not r.source_sha256:continue
        ranked=ranked_candidates(r,candidates)
        if not ranked or ranked[0]['score']<.82 or len(ranked)>1 and ranked[0]['score']-ranked[1]['score']<.03:continue
        c=byid[ranked[0]['player_id']]
        if c.dobs() and r.birth_date not in c.dobs():continue
        if any((old.source_url,old.locator)==(r.source_url,r.locator) for old in c.records):continue
        r.raw_fields['_candidate_only']=True;c.records.append(r)
    return candidates

def verify_team_identities(raw,source,team_id,dossiers,source_results=()):
    candidates=provider_candidates(dossiers,source_results)
    return [(record,resolve(record,candidates)) for record in team_identity_records(raw,source,team_id)]


def verify_profile_aliases(db,raw,source,canonical_id,candidates,on):
    """A fetched exact-ID personal profile can add its documented names to the shared registry."""
    from .identity_sources import fotmob
    from .identity_pipeline import persist_verified_alias
    record=fotmob(raw,source)[0];result=resolve(record,candidates)
    if result['identity_status']!='VERIFIED' or int(result['player_id'])!=canonical_id:raise ValueError('PROFILE_ALIAS_CANONICAL_IDENTITY_CONFLICT')
    persist_verified_alias(db,record,result,on);return result
