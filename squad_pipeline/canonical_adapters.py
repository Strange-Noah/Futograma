"""Evidence-bound adapters for legacy fixtures, failures, replay and national data."""
import json
from . import canonical_fixture as c


def initialized(db):
    c.migrate(db)
    if not db.execute('SELECT 1 FROM canonical_fixtures LIMIT 1').fetchone():
        c.bootstrap(db)


def observe_fixture(db, f, source, create=False, domain='CLUB', registered_id=None):
    initialized(db)
    ev = dict(source)
    if not ev.get('sha256'):
        # Legacy callers already store materialized accepted fixture objects.
        ev = dict(snapshot_sha256=c.digest(f), basis='LEGACY_TYPED_FIXTURE_IMPORT')
    provider=source.get('provider') or 'LEGACY_ACCEPTED'
    labels=source.get('directory_team_labels') or {}
    def side_key(side):
        label=labels.get(side) or {}
        return c.resolve_entity(db,'TEAM',provider,alias=(f.get('source_team_names') or {}).get(side),provider_id=label.get('provider_team_id')) or c.resolve_entity(db,'TEAM','LEGACY_ACCEPTED',f[side+'_team'])
    home,away=side_key('home'),side_key('away')
    if (home is None or away is None) and f.get('kickoff'):
        for row in db.execute('SELECT match_id,home_key,away_key FROM canonical_fixtures WHERE domain=? AND competition_key=?',(domain,str(f['competition_id']))):
            if c.value(db,row[0],'kickoff')[0]==c.instant(f['kickoff']) and (home==row[1] or away==row[2]):
                raise ValueError('POSSIBLE_EXISTING_FIXTURE_REQUIRES_ENTITY_EVIDENCE')

    def new_side(side):
        known=f.get(side+'_id')
        if known is not None:return c.legacy_team(db,f[side+'_team'],known,ev)
        provider_key=(f.get('provider_team_keys') or {}).get(side) or (labels.get(side) or {}).get('provider_team_id')
        if provider_key:
            key=c.resolve_entity(db,'TEAM',provider,provider_id=provider_key)
            if key:return key
            key='team_'+__import__('uuid').uuid4().hex
            c.entity(db,'TEAM',key,(f.get('source_team_names') or {}).get(side) or f[side+'_team'])
            c.assert_alias(db,'TEAM',provider,(f.get('source_team_names') or {}).get(side) or f[side+'_team'],key,ev,provider_id=provider_key)
            return key
        if registered_id is not None:return c.legacy_team(db,f[side+'_team'],None,ev)
        raise ValueError('VERIFIED_PROVIDER_TEAM_KEY_REQUIRED;PENDING_METADATA_DISCOVERY')
    if not home:home=new_side('home')
    if not away:away=new_side('away')
    c.entity(db,'COMPETITION',str(f['competition_id']),f.get('competition') or str(f['competition_id']))

    fields=dict(kickoff=f.get('kickoff'),match_status=f.get('match_state'),**c.score_fields(f.get('score')))
    provider=source.get('provider') or 'LEGACY_ACCEPTED'
    provider_id=source.get('directory_match_id') or source.get('provider_match_id') or source.get('url') or f['match_id']
    obs=dict(domain=domain,provider=provider,provider_match_id=str(provider_id),home_key=home,away_key=away,competition_key=str(f['competition_id']),edition=f.get('competition_edition') or c.club_period(f['match_date'])[1],fields=fields,evidence=ev)
    existing=db.execute('SELECT 1 FROM matches WHERE match_id=?',(f['match_id'],)).fetchone() if c.exists(db,'matches') else None
    try:
        result=c.reconcile(db,obs,create=False,registered_id=f['match_id'] if existing else None)
    except ValueError as exc:
        if not str(exc).startswith('UNRESOLVED_FIXTURE_OBSERVATION') or not create:raise
        result=c.reconcile(db,obs,create=True,registered_id=registered_id)

    return result


def associate_failure(db, task, header):
    """Associate metadata only. Never resolve appearance/identity tasks here.

    header is independently parsed from the retained body and must certify the
    catalog fixture ID, ordered provider team IDs, full-time, UTC kickoff, score
    and competition. Contradictory existing provider/team bindings fail closed.
    """
    initialized(db)
    if not header.get('certified_provider_fixture'):
        return dict(state='UNVERIFIED_SOURCE_CONTEXT')
    evidence=header['evidence'];c.proof(evidence)
    if str(header['provider_match_id'])!=str(task['provider_match_id']):
        return dict(state='RETURNED_MATCH_ID_MISMATCH')
    comp=str(header['competition_id']);kick=c.instant(header['kickoff'])
    sides=header['teams'];known=[]
    for side in ('home','away'):
        t=sides[side]
        key=c.resolve_entity(db,'TEAM',header['provider'],provider_id=t['provider_id'])
        # The tracked club's configured aliases were previously verified; no fuzzy match.
        if key is None and t.get('verified_club_id') is not None:
            key='club:'+str(t['verified_club_id'])
        known.append(key)
    hits=[]
    for row in db.execute("SELECT * FROM canonical_fixtures WHERE domain='CLUB' AND competition_key=?",(comp,)):
        row=dict(row)
        if c.value(db,row['match_id'],'kickoff')[0]!=kick:continue
        if not any(known):continue
        if any(key and key!=row[side+'_key'] for side,key in zip(('home','away'),known)):continue
        score=c.score_fields(header.get('score'))
        if len(score)!=2 or any(c.value(db,row['match_id'],k)[0]!=v for k,v in score.items()):continue
        # Exact entity names within each provider's own directory/header are
        # certified by the adapter. Cross-provider similarity is never used.
        if not header.get('catalog_header_ids_consistent'):continue
        hits.append(row)
    if len(hits)!=1:
        oid=c.digest(['FAILED_SOURCE_METADATA',task['match_key'],header])
        db.execute('INSERT OR IGNORE INTO canonical_observations VALUES(?,?,?,?,?,?,?)',(oid,'CLUB',header['provider'],str(header['provider_match_id']),None,c.encoded(dict(task_key=task['match_key'],failed_source_header=header)),c.encoded(evidence)))
        return dict(state='AMBIGUOUS_FIXTURE' if hits else 'UNRESOLVED_FIXTURE')

    fixture=hits[0]
    proof=dict(evidence,basis='CERTIFIED_PROVIDER_ID_ORDERED_SIDES_TRACKED_CLUB_EXACT_UTC_COMPETITION_SCORE_UNIQUE_FIXTURE',canonical_match_id=fixture['match_id'])
    for side in ('home','away'):
        t=sides[side];key=fixture[side+'_key']
        c.assert_alias(db,'TEAM',header['provider'],t['name'],key,proof,provider_id=t['provider_id'])
    result=c.reconcile(db,dict(domain='CLUB',provider=header['provider'],provider_match_id=str(header['provider_match_id']),home_key=fixture['home_key'],away_key=fixture['away_key'],competition_key=comp,fields=dict(kickoff=kick,**c.score_fields(header.get('score'))),evidence=proof))
    key=task['match_key'];reason=task.get('last_error') or task.get('failure_classification')
    db.execute('UPDATE match_retry_queue SET canonical_match_id=? WHERE match_key=?',(result['match_id'],key))
    db.execute('INSERT OR REPLACE INTO canonical_source_issues VALUES(?,?,?,?,?,?,?)',(key,'CLUB',result['match_id'],header['provider'],reason,'SOURCE_ONLY;PLAYER_IDENTITY_UNRESOLVED',c.encoded({k:task.get(k) for k in ('match_key','provider','provider_match_id','url','state','failure_classification','last_error')})))
    return dict(result,state='ASSOCIATED_NOT_RESOLVED',player_issues_preserved=True)


def national(db, match, provider, provider_id, evidence):
    initialized(db)
    for side in ('home','away'):
        key=match[side+'_team_key'];row=db.execute('SELECT display_name FROM football_teams WHERE team_key=?',(key,)).fetchone()
        if not row:raise ValueError('VERIFIED_NATIONAL_TEAM_REQUIRED')
        c.entity(db,'TEAM',key,row[0])
    meta=json.loads(match.get('metadata_json') or '{}')
    c.entity(db,'COMPETITION',str(match['competition_key']),meta.get('competition') or str(match['competition_key']))
    obs=dict(domain='NATIONAL',provider=provider,provider_match_id=str(provider_id),home_key=match['home_team_key'],away_key=match['away_team_key'],competition_key=str(match['competition_key']),fields=dict(kickoff=match['kickoff_utc'],match_status=match.get('match_state'),duration_minutes=match.get('duration_minutes'),extra_time_status=match.get('extra_time_status'),**c.score_fields(match.get('score'))),evidence=evidence)
    existing=c.exists(db,'international_matches') and db.execute('SELECT 1 FROM international_matches WHERE match_id=?',(match['match_id'],)).fetchone()
    try:
        result=c.reconcile(db,obs,create=False,registered_id=match['match_id'] if existing else None)
    except ValueError as exc:
        if not str(exc).startswith('UNRESOLVED_FIXTURE_OBSERVATION'):raise
        result=c.reconcile(db,obs,create=True,registered_id=match['match_id'])
    return result


def failed_source_header(raw,source,candidate):
    from .live_parse import Tree,COMP,canon,uk_datetime
    from .six_club_validation import CLUBS
    import re
    root=Tree(raw).root;h=root.one('sdc-site-match-header')
    names=[n.txt() for n in h.all('sdc-site-match-header__team-name-block-target')]
    scores=[n.txt() for n in h.all('sdc-site-match-header__team-score-block')]
    label=h.one('sdc-site-match-header__detail-fixture').txt()
    comp=next((k for k in sorted(COMP,key=len,reverse=True) if re.search(r'\.\s*'+re.escape(k)+r'\b',label)),None)
    if len(names)!=2 or len(scores)!=2 or comp is None:raise ValueError('SOURCE_FIXTURE_METADATA_INCOMPLETE')
    labels=candidate.get('catalog_team_labels') or {};pid=str(candidate.get('catalog_provider_id'))
    consistent=(source['url'].rstrip('/').rsplit('/',1)[-1]==pid and all(str(labels.get(side,{}).get('provider_team_id','')).isdigit() and canon(labels[side]['full_name'])==canon(names[i]) for i,side in enumerate(('home','away'))) and labels['home']['provider_team_id']!=labels['away']['provider_team_id'])
    teams={side:dict(provider_id=labels[side]['provider_team_id'],name=names[i],verified_club_id=next((cid for cid,v in CLUBS.items() if v[1]==canon(names[i])),None)) for i,side in enumerate(('home','away'))}
    return dict(provider='Sky',provider_match_id=pid,competition_id=COMP[comp],competition_label=comp,kickoff=uk_datetime(h.one('sdc-site-match-header__detail-time').txt()),score='-'.join(scores),teams=teams,certified_provider_fixture=consistent and h.attrs.get('data-status')=='4',catalog_header_ids_consistent=consistent,evidence=dict(sha256=source['sha256'],url=source['url'],catalog_sha256=candidate['catalog_source']['sha256']))


def reconcile_failed_sources(db):
    from .core import ROOT
    from pathlib import Path
    import hashlib
    initialized(db);out=[]
    if not c.exists(db,'match_retry_queue'):return out
    for row in db.execute("SELECT * FROM match_retry_queue WHERE state!='RESOLVED'").fetchall():
        task=dict(row)
        if task['provider']!='Sky':continue
        ev=json.loads(task['evidence_json']);source=ev.get('source') or {};rawpath=source.get('path')
        if not rawpath:continue
        path=(ROOT/rawpath).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():continue
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=source.get('sha256'):continue
        try:header=failed_source_header(raw.decode('utf-8','replace'),source,json.loads(task['candidate_json']));out.append(associate_failure(db,task,header))
        except (ValueError,KeyError):continue
    out.extend(associate_pending_international(db))
    return out


def associate_pending_international(db):
    """Attach expected fixture context, never the wrongly returned lineup.

    Exact accepted provider-event and ordered provider-team bindings are required;
    unresolved acquisition/player tasks retain their state, error and history.
    """
    initialized(db);out=[]
    if not c.exists(db,'international_acquisition_queue'):return out
    for row in db.execute("SELECT * FROM international_acquisition_queue WHERE state!='RESOLVED'").fetchall():
        task=dict(row);payload=json.loads(task['payload_json']);f=payload.get('fixture') or {}
        provider=f.get('provider') or task['provider'];pid=f.get('provider_match_id')
        if pid is None:continue
        binding=db.execute("SELECT match_id,evidence_json FROM canonical_fixture_sources WHERE domain='NATIONAL' AND provider=? AND provider_match_id=?",(provider,str(pid))).fetchone()
        if not binding:continue
        mid=binding[0];canonical=db.execute('SELECT * FROM canonical_fixtures WHERE match_id=?',(mid,)).fetchone()
        keys=[]
        for side in ('home','away'):
            expected=(f.get(side) or {}).get('id')
            mapped=db.execute('SELECT team_key FROM football_team_provider_ids WHERE provider=? AND provider_team_id=?',(provider,str(expected))).fetchone() if expected is not None else None
            keys.append(mapped[0] if mapped else None)
        if tuple(keys)!=(canonical['home_key'],canonical['away_key']):continue
        if str(f.get('competition_key'))!=canonical['competition_key'] or c.instant(f.get('kickoff'))!=c.value(db,mid,'kickoff')[0]:continue
        if task['match_id'] is not None and task['match_id']!=mid:continue
        history=db.execute('SELECT result_json FROM international_acquisition_history WHERE task_key=? ORDER BY observed_at DESC,event_id DESC LIMIT 1',(task['task_key'],)).fetchone()
        reason=(json.loads(history[0]).get('error') or task['failure_classification']) if history else task['failure_classification']
        proof=dict(basis='EXPECTED_VERIFIED_PROVIDER_FIXTURE;ORDERED_TEAM_IDS_UTC_COMPETITION;WRONG_RESPONSE_NOT_ACCEPTED',provider_match_id=str(pid),accepted_provider_binding=json.loads(binding[1]),discovery_source={k:(f.get('source') or {}).get(k) for k in ('url','sha256','response_timestamp')})
        db.execute('UPDATE international_acquisition_queue SET match_id=? WHERE task_key=?',(mid,task['task_key']))
        db.execute('INSERT OR REPLACE INTO canonical_source_issues VALUES(?,?,?,?,?,?,?)',(task['task_key'],'NATIONAL',mid,provider,reason,task['entity_type']+';UNRESOLVED',c.encoded(proof)))
        out.append(dict(task_key=task['task_key'],match_id=mid,state='ASSOCIATED_NOT_RESOLVED',task_state=task['state']))
    return out
