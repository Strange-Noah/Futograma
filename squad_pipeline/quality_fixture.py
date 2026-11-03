"""Shared fixture acceptance contract for onboarding and incremental ingestion.

This module is additive: it neither rewrites old canonical IDs nor interprets a
populated value as independently verified. Callers supply field-level evidence
from strict source adapters. Incomplete discoveries remain pending.
"""
from datetime import datetime
import re
from urllib.parse import urlsplit

STATUSES={'SCHEDULED','IN_PROGRESS','FINISHED','POSTPONED','CANCELLED','ABANDONED','AWARDED'}
ALIASES={'NOT_STARTED':'SCHEDULED','STARTED':'IN_PROGRESS','COMPLETED':'FINISHED','FT':'FINISHED'}
EXCEPTIONAL={'POSTPONED','CANCELLED','ABANDONED','AWARDED'}
IDENTITIES=('match_id','provider','provider_match_id','home','away','competition_key','season')


def normalize(fixture):
    f=dict(fixture)
    f['status']=ALIASES.get(str(f.get('status') or f.get('match_status') or '').upper(),str(f.get('status') or f.get('match_status') or '').upper())
    f['season']=f.get('season') or f.get('edition')
    f['kickoff_utc']=f.get('kickoff_utc') or f.get('kickoff')
    for side in ('home','away'):
        value=f.get(side)
        if not isinstance(value,dict):value=dict(id=f.get(side+'_key') or f.get(side+'_team_id'),name=f.get(side+'_team'))
        f[side]=value
    return f


def retained_proof(proof, fixture_provider, evidence_provider=None):
    """Validate retained references, not the underlying football assertion.

    A source_ref is a whitespace-free internal asset/ledger locator. It may
    contain path or URI separators but never credentials, control characters,
    or parent-directory segments. No source_ref is dereferenced here.
    """
    def sha(value):
        return isinstance(value,str) and re.fullmatch(r'[0-9a-fA-F]{64}',value) is not None
    declared=[proof.get(k) for k in ('sha256','snapshot_sha256') if proof.get(k) is not None]
    if not declared or not all(sha(v) for v in declared):return False
    provider=proof.get('provider',evidence_provider if evidence_provider is not None else fixture_provider)
    if not isinstance(provider,str) or not provider.strip():return False
    url=proof.get('url')
    valid_url=False
    if isinstance(url,str) and url and not any(c.isspace() for c in url):
        try:
            u=urlsplit(url)
            valid_url=u.scheme in ('http','https') and bool(u.hostname) and not u.username and not u.password
        except ValueError:pass
    ref=proof.get('source_ref');valid_ref=False
    if isinstance(ref,str) and re.fullmatch(r'[A-Za-z0-9_./:@#?=&%+~\-]+',ref) and '..' not in ref.split('/'):
        try:
            u=urlsplit(ref);valid_ref=not u.username and not u.password
        except ValueError:pass
    return valid_url or valid_ref


def evaluate(fixture,evidence=None):
    """Return a non-mutating acceptance decision and precise repair obligations.

    evidence.field_evidence[field] must retain sha256/snapshot_sha256, a source
    URL/ref and the exact value being asserted. Conflicting or absent evidence
    cannot make a fact verified. Kickoff TBD needs an explicit uncertainty reason
    and is permitted only for scheduled/exceptional fixtures.
    """
    f=normalize(fixture);evidence=evidence or {};proofs=evidence.get('field_evidence',{});missing=[];conflicts=[];checks={}
    def check(key,present,reason):
        value=f.get(key);proof=proofs.get(key) or {}
        retained=retained_proof(proof,f.get('provider'),evidence.get('provider'))
        same=proof.get('value')==value
        if key=='kickoff_utc' and value and proof.get('value'):
            try:same=datetime.fromisoformat(value.replace('Z','+00:00'))==datetime.fromisoformat(proof['value'].replace('Z','+00:00'))
            except (ValueError,TypeError,AttributeError):pass
        if not present:missing.append(reason);checks[key]='MISSING'
        elif proof.get('state')=='CONFLICTED' or retained and not same:conflicts.append(key+':SOURCE_VALUE_CONFLICT');checks[key]='CONFLICTED'
        elif retained and same and proof.get('state')=='VERIFIED':checks[key]='VERIFIED'
        else:missing.append(key+':VERIFICATION_REQUIRED');checks[key]='UNVERIFIED'
    for key in IDENTITIES:
        present=bool(f.get(key))
        if key in ('home','away'):present=bool(f[key].get('id') and f[key].get('name') and str(f[key]['name']).strip().lower() not in ('unknown','tbd','tbc','?'))
        check(key,present,key+':REQUIRED')
    if f['home'].get('id') is not None and f['home'].get('id')==f['away'].get('id'):conflicts.append('HOME_AWAY_IDENTICAL')
    if f.get('competition_label'):check('competition_label',True,'COMPETITION_LABEL_VERIFICATION_REQUIRED')
    actual=f.get('actual_provider_match_id')
    if actual is None:missing.append('ACTUAL_PROVIDER_MATCH_ID_REQUIRED')
    elif str(actual)!=str(f.get('provider_match_id')):conflicts.append('PROVIDER_MATCH_ID_MISMATCH')
    check('status',f['status'] in STATUSES,'MATCH_STATUS_REQUIRED_OR_UNSUPPORTED')
    kickoff=f.get('kickoff_utc');valid=False
    if kickoff:
        try:valid=datetime.fromisoformat(kickoff.replace('Z','+00:00')).tzinfo is not None
        except (ValueError,TypeError,AttributeError):pass
    if not valid and not (f['status'] in EXCEPTIONAL|{'SCHEDULED'} and f.get('kickoff_uncertainty')):
        missing.append('EXPLICIT_KICKOFF_TIMEZONE_REQUIRED')
    elif valid:
        check('kickoff_utc',True,'KICKOFF_REQUIRED')
        if f.get('kickoff_uncertainty'):check('kickoff_uncertainty',True,'KICKOFF_UNCERTAINTY_REQUIRED')
    else:check('kickoff_uncertainty',True,'KICKOFF_UNCERTAINTY_REQUIRED')
    scores=[f.get('home_score'),f.get('away_score')];valid_scores=all(isinstance(v,int) and not isinstance(v,bool) and v>=0 for v in scores)
    if f['status']=='FINISHED' or any(v is not None for v in scores):
        for key in ('home_score','away_score'):check(key,valid_scores,key+':VALID_NUMERIC_SCORE_REQUIRED')
    elif f['status'] in EXCEPTIONAL:
        check('score_unavailable_reason',bool(f.get('score_unavailable_reason')),'EXCEPTIONAL_SCORE_UNAVAILABILITY_UNEXPLAINED')
    identity_ok=all(checks.get(k)=='VERIFIED' for k in IDENTITIES) and actual is not None and str(actual)==str(f.get('provider_match_id')) and not any('IDENTICAL' in c for c in conflicts)
    decision='CONFLICTED' if conflicts else 'PENDING_VERIFICATION' if missing else 'ACCEPT'
    return dict(decision=decision,identity_state='VERIFIED' if identity_ok else 'CONFLICTED' if conflicts else 'PENDING_VERIFICATION',completeness_state='COMPLETE' if not missing else 'INCOMPLETE',obligations=sorted(set(missing+conflicts)),field_checks=checks,evidence=evidence,retry_eligible=bool(missing and not conflicts),retry_reason='TARGETED_INCOMPLETE_FIELDS' if missing and not conflicts else 'NEW_IDENTITY_EVIDENCE_REQUIRED' if conflicts else None,fixture=f)


def canonical_observation(fixture,evidence,domain='CLUB'):
    result=evaluate(fixture,evidence)
    if result['decision']!='ACCEPT':raise ValueError('FIXTURE_CONTRACT_NOT_ACCEPTED:'+','.join(result['obligations']))
    f=result['fixture'];proofs=evidence['field_evidence'];first=proofs['match_id']
    return dict(domain=domain,provider=f['provider'],provider_match_id=str(f['provider_match_id']),actual_provider_match_id=str(f['actual_provider_match_id']),home_key=str(f['home']['id']),away_key=str(f['away']['id']),competition_key=str(f['competition_key']),edition=f['season'],fields={k:v for k,v in dict(kickoff=f.get('kickoff_utc'),match_status=f['status'],home_score=f.get('home_score'),away_score=f.get('away_score'),kickoff_uncertainty=f.get('kickoff_uncertainty'),score_unavailable_reason=f.get('score_unavailable_reason')).items() if v is not None},evidence=dict(first,field_evidence=proofs))


def export_fixture_catalogue(db,domain=None):
    """Export fixtures independently of appearances, preserving pending fields.

    Only field_evidence retained by the contract-aware adapter is eligible for
    acceptance. Legacy canonical existence alone does not manufacture proof.
    All rows remain visible with their quality obligations, including fixtures
    with no player records. The caller may filter by domain/team afterwards.
    """
    import json
    from .canonical_fixture import exists,metadata
    if not exists(db,'canonical_fixtures'):return []
    catalog=[]
    for row in db.execute('SELECT match_id,domain,home_key,away_key,competition_key,edition FROM canonical_fixtures ORDER BY match_id').fetchall():
        mid,kind,home,away,comp,edition=row
        if domain is not None and kind!=domain:continue
        meta=metadata(db,mid);fields=meta['fields']
        observations=db.execute('SELECT payload_json,evidence_json FROM canonical_observations WHERE match_id=? ORDER BY observation_id',(mid,)).fetchall()
        if observations:
            # A source is selected only as a provenance candidate. Contradictory
            # registry fields below still force CONFLICTED, never last-wins.
            payload,ev=map(json.loads,observations[0]);proofs=dict(ev.get('field_evidence') or {})
        else:payload={};proofs={}
        def val(k):return (fields.get(k) or {}).get('value')
        f=dict(match_id=mid,provider=payload.get('provider'),provider_match_id=payload.get('provider_match_id'),actual_provider_match_id=payload.get('actual_provider_match_id'),home=dict(id=home,name=meta['home_team']),away=dict(id=away,name=meta['away_team']),competition_key=comp,season=edition,kickoff_utc=val('kickoff'),status=val('match_status'),home_score=val('home_score'),away_score=val('away_score'),kickoff_uncertainty=val('kickoff_uncertainty'),score_unavailable_reason=val('score_unavailable_reason'))
        # Display names may differ between a retained source label and our
        # canonical entity label. This is safe only for the exact same stable
        # canonical ID; no spelling similarity or name-based merge is used.
        source_labels={}
        for side in ('home','away'):
            asserted=(proofs.get(side) or {}).get('value')
            if isinstance(asserted,dict) and asserted.get('id') is not None and str(asserted['id'])==str(f[side]['id']):
                source_labels[side]=dict(asserted)
                f[side]=dict(id=f[side]['id'],name=asserted.get('name'))
        f=normalize(f)
        quality=evaluate(f,dict(field_evidence=proofs))
        registry_conflicts=[k for k,v in fields.items() if v['state']=='SOURCE_CONFLICT']
        if registry_conflicts:
            quality['decision']='CONFLICTED';quality['retry_eligible']=False;quality['retry_reason']='NEW_CONFLICT_RESOLUTION_EVIDENCE_REQUIRED';quality['obligations']=sorted(set(quality['obligations']+['REGISTRY_FIELD_CONFLICT:'+k for k in registry_conflicts]))
        catalog.append(dict(f,domain=kind,competition_name=meta['competition'],canonical_home_team=meta['home_team'],canonical_away_team=meta['away_team'],source_team_labels=source_labels,verification=quality,canonical_metadata=meta,record_kind='FIXTURE_FACT'))
    return catalog


def export_accepted_catalogue(db):
    """Partition contract-ledger fixtures into accepted facts and pending issues.

    Re-evaluate retained proofs and compare against the canonical registry;
    stored ACCEPT text is insufficient. This is only enabled for datasets with
    the additive quality_fixture_acceptance table.
    """
    import json
    registry={r['match_id']:r for r in export_fixture_catalogue(db)}
    accepted=[];pending=[]
    for mid,payload,result in db.execute('SELECT match_id,payload_json,result_json FROM quality_fixture_acceptance ORDER BY match_id'):
        fixture=json.loads(payload);stored=json.loads(result);fixture=fixture.get('fixture',fixture)
        fresh=evaluate(fixture,stored.get('evidence') or {})
        canonical=registry.get(mid)
        obligations=list(fresh['obligations'])
        if str(fixture.get('match_id'))!=str(mid):obligations.append('LEDGER_MATCH_ID_CONFLICT')
        if not canonical:obligations.append('CANONICAL_FIXTURE_MISSING')
        elif canonical['verification']['decision']=='CONFLICTED':obligations.extend(canonical['verification']['obligations'])
        if canonical:
            binding=db.execute('SELECT match_id FROM canonical_fixture_sources WHERE domain=? AND provider=? AND provider_match_id=?',(canonical['domain'],fixture.get('provider'),str(fixture.get('provider_match_id')))).fetchone()
            if not binding or str(binding[0])!=str(mid):obligations.append('VERIFIED_PROVIDER_FIXTURE_BINDING_REQUIRED')
        if canonical:
            expected=normalize(fixture)
            for side in ('home','away'):
                actual_id=(canonical.get(side) or {}).get('id');expected_id=(expected.get(side) or {}).get('id')
                if actual_id is None or expected_id is None or str(actual_id)!=str(expected_id):obligations.append('CANONICAL_LEDGER_CONFLICT:'+side)
            from .canonical_fixture import instant
            if instant(canonical.get('kickoff_utc'))!=instant(expected.get('kickoff_utc')):obligations.append('CANONICAL_LEDGER_CONFLICT:kickoff_utc')
            for field in ('season','status','home_score','away_score','competition_key','kickoff_uncertainty','score_unavailable_reason'):
                if canonical.get(field)!=expected.get(field):obligations.append('CANONICAL_LEDGER_CONFLICT:'+field)
        if fresh['decision']=='ACCEPT' and not obligations:
            accepted.append(dict(canonical,provider=expected['provider'],provider_match_id=expected['provider_match_id'],actual_provider_match_id=expected['actual_provider_match_id'],home=expected['home'],away=expected['away'],competition_name=fixture.get('competition_label') or canonical['competition_name'],source_provenance=fresh['evidence'],verification=fresh))
        else:
            pending.append(dict(match_id=mid,fixture=normalize(fixture),decision='CONFLICTED' if any('CONFLICT' in o for o in obligations) else 'PENDING_VERIFICATION',obligations=sorted(set(obligations)),source_provenance=fresh['evidence']))
    return dict(accepted=accepted,pending=pending)
