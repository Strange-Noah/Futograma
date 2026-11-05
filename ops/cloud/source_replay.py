"""Read-only source acceptance replay; never runs R1–R6 or writes football facts."""
import argparse,json,sqlite3,hashlib
import re
from pathlib import Path
from collections import Counter
from dataclasses import asdict
from squad_pipeline.source_interface import SourceRequest,RawSourceArtifact,EvidenceStore,sha,encode
from squad_pipeline.source_parsing import parse


def discovery_replay(reference, store_root, output, start, cutoff):
    """Read-only broad discovery from retained profiles and team entrypoints.

    Results are provider hints, not accepted fixtures, membership or appearances.
    """
    reference=Path(reference).resolve();digest=sha(reference.read_bytes())
    store=EvidenceStore(store_root);db=sqlite3.connect('file:'+str(reference)+'?mode=ro',uri=True)
    seen=set();rows=[];fixtures=set();affiliations=set()
    for provider,url,stamp,body_hash in db.execute('SELECT provider,source_url,fetched_at,body_sha256 FROM persistent_source_evidence'):
        if provider!='FotMob' or not url or (url,body_hash) in seen:continue
        player=re.search(r'/players/(\d+)',url);team=re.search(r'/teams/(\d+)',url)
        if '/players/' not in url and '/teams/' not in url:continue
        seen.add((url,body_hash));context=dict(start=start,cutoff=cutoff);ids={}
        if '/players/' in url:
            category='player_history'
            if player:ids['player']=player[1]
        else:
            category='team_history'
            if team is None:
                rows.append(dict(category=category,outcome='EVIDENCE_CONTRACT_INCOMPLETE'));continue
            ids['team']=team[1]
            levels=db.execute('SELECT DISTINCT t.level FROM football_teams t JOIN football_team_provider_ids p USING(team_key) WHERE p.provider=? AND p.provider_team_id=?',('FotMob',team[1])).fetchall()
            if len(levels)==1:context['level']=levels[0][0]
        path=store.root/'bodies'/(str(body_hash)+'.response')
        row=dict(category=category)
        if not path.is_file():row['outcome']='ARCHIVE_LOCATOR_UNRESOLVED';rows.append(row);continue
        request=SourceRequest(provider,category,url,category,expected_ids=ids,context=context)
        artifact=RawSourceArtifact(request,body_hash,str(path),dict(response_timestamp=stamp),'SOURCE_RECORD')
        body=store.read(artifact);observation=parse(artifact,body)
        assert observation==parse(artifact,body),'NONDETERMINISTIC_PARSE'
        row['outcome']=observation.outcome;rows.append(row)
        for m in observation.native.get('matches',[]):fixtures.add(m['provider_match_id'])
        for a in observation.native.get('affiliations',[]):affiliations.add((a['provider_player_id'],a['provider_team_id'],a['level']))
    db.close();assert sha(reference.read_bytes())==digest
    report=dict(captures=len(rows),categories=dict(Counter(r['category'] for r in rows)),
        outcomes=dict(Counter(r['outcome'] for r in rows)),native_fixture_hints=len(fixtures),
        native_affiliation_hints=len(affiliations),accepted_canonical_facts=0,reference_unchanged=True)
    Path(output).write_text(json.dumps(report,indent=2)+'\n');return report


def historical_replay(reference, store_root, output):
    """Replay standardized records from an already verified private body store.

    Identity bridges and manual decisions are not reconstructed from target names.
    The caller supplies archive access; this routine never accesses the network.
    """
    reference=Path(reference).resolve();digest=sha(reference.read_bytes())
    root=Path(store_root).resolve()
    index={p.stem:[str(p)] for p in (root/'bodies').glob('*.response')}
    store=EvidenceStore(root,index=index)
    db=sqlite3.connect('file:'+str(reference)+'?mode=ro',uri=True)
    clubs=dict(db.execute('SELECT club_id,name FROM clubs'))
    records={}
    for cid,value in db.execute('SELECT club_id,source_json FROM club_management_events'):
        for source in json.loads(value or '[]'):
            if isinstance(source,dict) and 'source_sha256' in source:
                records[cid,encode(source)]=source
    cache={};rows=[]
    for (cid,_),source in records.items():
        provider=source['provider'];scope=source.get('scope');locator=source.get('locator','')
        category='article' if provider=='OfficialArticle' else 'registration' if provider=='PremierLeague' else 'roster'
        if provider=='OfficialClub' and not (locator.startswith('player-card-anchor') or locator.startswith('$.props')):
            category='profile'
        acquired=store.normalize(source,provider,category)
        result=dict(provider=provider,scope=scope,acquisition=acquired.outcome)
        if acquired.outcome=='FOUND':
            artifact=acquired.artifact
            request=SourceRequest(provider,category,source['source_url'],category,
                context=dict(club_id=cid,club_name=clubs[cid],scope=scope))
            artifact=RawSourceArtifact(request,artifact.content_sha256,artifact.locator,artifact.metadata,artifact.authority)
            if artifact.key not in cache:
                body=store.read(artifact);observation=parse(artifact,body)
                assert observation==parse(artifact,body),'NONDETERMINISTIC_PARSE'
                cache[artifact.key]=observation
            observation=cache[artifact.key];result['parse']=observation.outcome
            candidates=observation.native.get('records',[])
            ids=source.get('provider_ids') or {}
            matches=[r for r in candidates if (r.get('provider_ids')==ids if ids else r.get('names')==source.get('names'))]
            if category=='article':
                result['comparison']='DOWNSTREAM_IDENTITY_BRIDGE_NOT_REINFERRED'
            elif len(matches)!=1:
                result['comparison']='NATIVE_RECORD_NOT_UNIQUE';result['candidate_count']=len(matches)
            else:
                fields=['provider_ids','names','birth_date','official_clubs','scope','assertion']
                different=[f for f in fields if matches[0].get(f)!=source.get(f)]
                result['comparison']='NATIVE_EQUAL' if not different else 'NATIVE_DIFFERENCE'
                result['different_fields']=different
        rows.append(result)
    db.close();assert sha(reference.read_bytes())==digest
    report=dict(records=len(rows),unique_parse_requests=len(cache),
        acquisitions=dict(Counter(r['acquisition'] for r in rows)),
        parses=dict(Counter(r.get('parse') for r in rows)),
        comparisons=dict(Counter(r.get('comparison') for r in rows)),
        rows=rows,reference_unchanged=True,canonical_facts_written=0)
    Path(output).write_text(json.dumps(report,indent=2)+'\n')
    return report


def replay(reference, archives, output):
    reference=Path(reference).resolve();out=Path(output);out.mkdir(parents=True,exist_ok=False)
    digest=sha(reference.read_bytes());store=EvidenceStore(out/'store',archives=archives)
    db=sqlite3.connect('file:'+str(reference)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    rows=[]
    for number,row in enumerate(db.execute('SELECT * FROM validation_match_sheets ORDER BY club_id,match_id'),1):
        old=json.loads(row['payload_json']);source=old['source'];url=source.get('url')
        body=None
        for p in store.candidates(source['path']):
            if p.is_file():body=p.read_bytes();break
        if body is None:
            rows.append(dict(sequence=number,provider=row['provider'],outcome='ARCHIVE_LOCATOR_UNRESOLVED'));continue
        if sha(body)!=source['sha256']:
            rows.append(dict(sequence=number,provider=row['provider'],outcome='INTEGRITY_CONFLICT'));continue
        sidecars=[]
        for p in store.candidates(source['path']):
            for suffix in ['.meta.json','.metadata.json']:
                meta=p.with_suffix(suffix)
                if meta.is_file():sidecars.append(json.loads(meta.read_text()))
        for meta in sidecars:
            if meta.get('sha256')==source['sha256'] and meta.get('url'):url=url or meta['url']
        match=db.execute('SELECT * FROM matches WHERE match_id=?',(row['match_id'],)).fetchone()
        request=SourceRequest(row['provider'],'match',url,'fixture-detail',context=dict(
            home_id=match['home_id'],away_id=match['away_id'],kickoff=match['kickoff'],competition_id=match['source_competition_id'] or match['competition_id']))
        authority='WEB_TOOL_EXTRACT' if body.lstrip().startswith(b'[') else 'ARCHIVED_RESPONSE'
        artifact=RawSourceArtifact(request,source['sha256'],source['path'],dict(source,
            response_timestamp=source.get('response_timestamp') or source.get('acquired_at'),source_url=url),authority)
        observation=parse(artifact,body);repeat=parse(artifact,body)
        assert observation==repeat,'NONDETERMINISTIC_PARSE'
        # Keep full observations private; public result is aggregate dispositions.
        (out/('observation-'+str(number)+'.json')).write_text(json.dumps(asdict(observation),ensure_ascii=False,indent=2))
        native=observation.native;players=[]
        for sheet in native.get('sheets',[]):
            tid=sheet.get('provider_team_id') or sheet.get('configured_club_id')
            if str(tid)==str(row['club_id']):players=sheet['players'];break
        old_status=Counter(p.get('status') for p in old.get('players',[]))
        new_status=Counter(p.get('participation_status') or p.get('status') for p in players)
        # No canonical name matching here: only exact existing source identities.
        prior={str(p.get('provider_key') or 'fotmob:'+str(p.get('fotmob_id'))):p for p in old.get('players',[])}
        compared=Counter();transitions=Counter()
        for p in players:
            key=p.get('provider_key') or ('fotmob:'+str(p.get('provider_player_id')))
            if key not in prior:compared['unmatched_source_player']+=1;continue
            previous=prior[key];current=p.get('reported_minutes')
            if current==previous.get('reported_minutes'):compared['reported_minutes_equal']+=1
            elif current is None:
                compared['current_minutes_unknown']+=1
                transitions[str(previous.get('status'))+'->'+str(p.get('participation_status') or p.get('status'))]+=1
            else:compared['reported_minutes_difference']+=1
            state=p.get('participation_status') or p.get('status')
            if state==previous.get('status'):compared['participation_equal']+=1
            else:compared['participation_difference']+=1
        rows.append(dict(sequence=number,provider=row['provider'],club_id=row['club_id'],outcome=observation.outcome,
            parser=observation.parser_version,uncertainties=list(observation.uncertainties),completeness=observation.completeness,
            old_players=len(old.get('players',[])),parsed_own_players=len(players),old_status=dict(old_status),new_status=dict(new_status),comparison=dict(compared),unknown_transitions=dict(transitions),
            body_hash_verified=True,repeat_equal=True,canonical_facts_written=0,
            source_url_available=bool(url),requested_id_matched=observation.completeness.get('source_identity',False),native_parent_relation=bool(native.get('provider_parent_competition_id'))))
    assert sha(reference.read_bytes())==digest;db.close()
    report=dict(source_count=len(rows),dispositions=dict(Counter(r['outcome'] for r in rows)),
        provider_counts=dict(Counter(r['provider'] for r in rows)),rows=rows,reference_unchanged=True,canonical_facts_written=0)
    (out/'dispositions.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reference',required=True);p.add_argument('--archive',action='append',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    r=replay(a.reference,a.archive,a.output);print(json.dumps({k:v for k,v in r.items() if k!='rows'},indent=2))
