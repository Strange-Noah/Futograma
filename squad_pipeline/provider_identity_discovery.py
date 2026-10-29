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

def verify_team_identities(raw,source,team_id,dossiers):
    candidates=[];keys={f.name for f in fields(Record)}
    for d in dossiers:
        if d['identity_status']!='VERIFIED':continue
        records=[Record(**{k:v for k,v in r.items() if k in keys}) for r in d['verified_source_records']]
        for r in records:r.raw_fields['_verified_identity']=True
        candidates.append(Candidate(int(d['player_id']),list(dict.fromkeys([d['name']]+[n for r in records for n in r.names])),records))
    return [(record,resolve(record,candidates)) for record in team_identity_records(raw,source,team_id)]
