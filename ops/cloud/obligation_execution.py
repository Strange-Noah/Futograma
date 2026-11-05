"""Execute retained audit obligations through existing source and fact boundaries.

This runner owns isolated copies and audit records. It neither collects through
a second transport nor writes canonical football tables itself.
"""
import argparse,json,sqlite3,shutil
from pathlib import Path
from collections import Counter,defaultdict
from dataclasses import asdict
from squad_pipeline.source_interface import EvidenceStore,SourceService,SourceRequest,RawSourceArtifact,sha
from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
from squad_pipeline.reconstruction.engine import run,digest,day
from squad_pipeline.reconstruction.calendar import fifa_windows
from squad_pipeline.identity_registry import lookup
from squad_pipeline.minute_policy import select


def obligations(path):
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
    rows=[json.loads(v) for v, in db.execute('SELECT payload FROM reconstruction_findings')]
    db.close()
    return [r for r in rows if r.get('assessment',{}).get('confidence_status')=='NOT_CHECKED']


def adjudicate(db,packet,service,on):
    """Validate a staged packet, then call the established persistence functions."""
    from ops.cloud.coldstart_acceptance import apply_appearance
    from squad_pipeline.international_store import evidence,put_appearance,complete_missing_minutes
    from squad_pipeline.quality_pipeline import verify_participation
    raw=packet['artifact'];request=SourceRequest(**raw['request'])
    artifact=RawSourceArtifact(**dict(raw,request=request))
    acquired,parsed=service.observe(request,artifact)
    if acquired.outcome!='FOUND' or parsed is None or not parsed.completeness.get('source_identity'):
        return dict(state='HELD_SOURCE_PROOF',reason=acquired.outcome if not parsed else parsed.outcome)
    p=packet['player'];pid=packet['player_id'];mid=packet['match_id'];cid=packet['managing_club_id']
    try:resolved=lookup(db,cid,p)
    except ValueError:return dict(state='HELD_IDENTITY',reason='Existing registry reports ambiguous identity')
    if resolved is None or int(resolved)!=pid:return dict(state='HELD_IDENTITY',reason='Existing verified registry does not bind the exact source entity; acceptance is not revoked')
    if packet['domain']=='CLUB':
        existing=db.execute('SELECT * FROM matches WHERE match_id=?',(mid,)).fetchone()
        if not existing:return dict(state='HELD_FIXTURE',reason='Existing accepted fixture required')
        context=request.context
        if str(context.get('home_id'))!=str(existing['home_id']) or str(context.get('away_id'))!=str(existing['away_id']):return dict(state='HELD_FIXTURE',reason='Ordered fixture context differs')
        table='player_match_minutes';where='club_id=? AND match_id=? AND player_id=?';args=(cid,mid,pid)
    else:
        existing=db.execute('SELECT * FROM international_matches WHERE match_id=?',(mid,)).fetchone()
        if not existing or packet['team'] not in {existing['home_team_key'],existing['away_team_key']}:return dict(state='HELD_FIXTURE',reason='Exact represented team/fixture required')
        table='international_appearances';where='match_id=? AND player_id=?';args=(mid,pid)
    native=[]
    for sheet in parsed.native.get('sheets',[]):
        if packet['domain']=='CLUB':
            if str(sheet.get('provider_team_id') or sheet.get('configured_club_id'))!=str(cid):continue
        else:
            represented=sheet.get('represented_team_key')
            if represented is None:
                rows=db.execute('SELECT team_key FROM football_team_provider_ids WHERE provider=? AND provider_team_id=?',('FotMob',str(sheet.get('provider_team_id')))).fetchall()
                represented=rows[0][0] if len(rows)==1 else None
            if represented!=packet['team']:continue
        for player in sheet['players']:
            key=player.get('provider_key') or 'fotmob:'+str(player.get('provider_player_id'))
            if key==p['provider_key']:native.append(player)
    if len(native)!=1:return dict(state='HELD_SOURCE_PROOF',reason='Unique exact-player native claim required')
    observed=native[0];state=observed.get('participation_status') or observed.get('status')
    if state!=p['participation_status']:return dict(state='HELD_SOURCE_PROOF',reason='Packet participation differs from replay')
    values=[dict(status=state,minutes=observed.get('reported_minutes'),provider=packet['source']['provider']),dict(status=state,minutes=observed.get('derived_minutes'),provider='DERIVED_CLOCK')]
    selection=select(values)
    if not selection['accepted'] or selection['minutes']!=p['minutes']:return dict(state='HELD_SOURCE_PROOF',reason='Existing minute policy does not select the staged value')
    old=db.execute('SELECT * FROM '+table+' WHERE '+where,args).fetchone();before=dict(old) if old else None
    if old and (old['participation_status'],old['minutes'])==(state,p['minutes']):return dict(state='UNCHANGED_ACCEPTED',before=before,after=before)
    if old and old['minutes'] is not None:
        selection=select([dict(status=old['participation_status'],minutes=old['minutes'],provider='RETAINED_ACCEPTED'),dict(status=state,minutes=p['minutes'],provider=packet['source']['provider'])])
        return dict(state='RETAINED_ACCEPTED_POLICY' if selection['accepted'] and old['participation_status']==state else 'HELD_MATERIAL_CONTRADICTION',before=before,reason=selection['reason'])
    try:
        if packet['domain']=='CLUB':
            result=apply_appearance(db,cid,mid,p,packet['source'],on,'BACKFILL',canonical_player_id=pid,supplement_unknown=True)
            if result['state'] not in {'RECORDED','SUPPLEMENTED_UNKNOWN'}:return dict(result,before=before)
        else:
            quality=verify_participation(db,mid,p,packet['source'],on,'BACKFILL')
            if quality['decision']!='ACCEPT':return dict(state='HELD_PARTICIPATION_CONTRACT',before=before)
            eid=evidence(db,packet['source'],'EXACT_RECONSTRUCTION_RECOVERY','RAW_RESPONSE_VERIFIED')
            appearance=dict(match_id=mid,player_id=pid,represented_team_key=packet['team'],participation_status=state,minutes=p['minutes'],reported_minutes=p.get('reported_minutes'),derived_minutes=p.get('derived_minutes'),minutes_precision=p.get('minutes_precision') or p.get('minute_precision') or 'SOURCE_REPORTED_INTEGER',verification_status=p.get('verification_status') or 'CROSS_PROVIDER_CERTIFIED_NAME_AND_PERSONAL_HISTORY_MATCH',evidence_scope='EXACT_SOURCE_IDENTITY_AND_REPLAY;ACCEPTED_CANONICAL_FIXTURE',revision=1,metadata_json=json.dumps(dict(source_provider=packet['source']['provider'],source_path=p.get('source_path'),artifact_key=artifact.key),sort_keys=True))
            if old:complete_missing_minutes(db,appearance,eid)
            else:put_appearance(db,appearance)
            db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(mid,pid,eid,'lineup_and_minutes',json.dumps(appearance,sort_keys=True),1,'EXACT_VERIFIED_RECOVERY'))
        db.commit()
    except (ValueError,sqlite3.DatabaseError) as exc:
        db.rollback();return dict(state='HELD_EXISTING_ADJUDICATION_STANDARD',reason=str(exc),before=before)
    after=dict(db.execute('SELECT * FROM '+table+' WHERE '+where,args).fetchone())
    return dict(state='SUPPLEMENTED_UNKNOWN' if old else 'RECORDED_NEW',before=before,after=after)


def execute(reference,seed,archive,source_store,capture_index,calendar,work,start,cutoff):
    reference,seed,archive,source_store,work=map(lambda p:Path(p).resolve(),(reference,seed,archive,source_store,work))
    for p in [reference,seed,source_store,work,Path(capture_index).resolve(),Path(calendar).resolve()]:
        if not p.is_relative_to(archive):raise ValueError('AUTHORIZED_PROJECT_BOUNDARY_REQUIRED')
    work.mkdir(parents=True,exist_ok=False);original=sha(reference.read_bytes());frozen=obligations(seed)
    dbpath=work/'candidate.sqlite';shutil.copyfile(reference,dbpath);db=sqlite3.connect(dbpath);db.row_factory=sqlite3.Row
    from ops.cloud.reconstruction_probe import snapshot
    preserved=snapshot(db)
    index={p.stem:[str(p)] for p in (source_store/'bodies').glob('*.response')}
    store=EvidenceStore(work/'sources',archives=[archive,source_store],index=index,records=json.loads(Path(capture_index).read_text()))
    service=SourceService(store,'obligation-replay',set(),max_calls=0)
    from datetime import datetime,timezone
    raw=Path(calendar).read_bytes();stamp=datetime.fromtimestamp(Path(calendar).stat().st_mtime,timezone.utc).isoformat()
    request=SourceRequest('FIFA','calendar','https://inside.fifa.com/tournament-organisation/international-match-calendars','calendar')
    artifact=store.import_body(request,raw,dict(retained_file_mtime=stamp),'ARCHIVED_RESPONSE');_,parsed=service.observe(request,artifact)
    windows=fifa_windows(raw,request.url,stamp,start,cutoff,'RETAINED_FILE_MTIME',parsed)
    required={(r['domain'],mid,r['player']) for r in frozen for mid in (r.get('opportunities',[]) if r['trigger']=='R3' else [r['canonical']])}
    packets={};audits={};adapters={}
    for club in db.execute('SELECT club_id FROM clubs ORDER BY club_id').fetchall():
        adapter=RetainedAdapter(db,archive,club[0],windows,source_service=service);result=run(db,adapter,start,cutoff,max_attempts=1500);db.commit()
        adapters[club[0]]=adapter;audits[club[0]]=result
        (work/('staged-'+str(club[0])+'-private.json')).write_text(json.dumps(list(adapter.candidate_packets.values()),sort_keys=True))
        for packet in adapter.candidate_packets.values():
            if (packet['domain'],packet['match_id'],str(packet['player_id'])) in required:packets[digest(packet)]=packet
    grouped=defaultdict(list)
    for packet in packets.values():grouped[packet['domain'],packet['match_id'],packet['player_id']].append(packet)
    decisions=[]
    for key,items in sorted(grouped.items()):
        if len({p['player']['participation_status'] for p in items})!=1:
            decisions.append(dict(key=key,state='HELD_SOURCE_CONFLICT'));continue
        choice=select([dict(status=p['player']['participation_status'],minutes=p['player']['minutes'],provider=p['source']['provider'],source_url=p['source']['url']) for p in items])
        if not choice['accepted']:decisions.append(dict(key=key,state='HELD_SOURCE_CONFLICT'));continue
        items.sort(key=lambda p:(p['player']['minutes']!=choice['minutes'],p['source']['provider'],p['source']['url']))
        packet=items[0];result=adjudicate(db,packet,service,cutoff);db.commit();repeat=adjudicate(db,packet,service,cutoff);db.commit()
        decisions.append(dict(key=key,source_sha256=packet['source']['sha256'],result=result,repeat=repeat))
    final={}
    for cid,adapter in adapters.items():final[cid]=run(db,adapter,start,cutoff,max_attempts=1500);db.commit()
    transitions=[]
    for obligation in frozen:
        relevant=[f for a in final.values() for f in a['final_opportunities'] if f['player']==obligation['player'] and f['team']==obligation['team'] and f['domain']==obligation['domain'] and (f['canonical']==obligation.get('canonical') if obligation['trigger']=='R4' else f['canonical'] in obligation.get('opportunities',[]))]
        unresolved=[f for f in relevant if f['state'] in {'INSUFFICIENT_COVERAGE','CONFLICT'} or f.get('challenge')]
        resolutions=[r for a in final.values() for r in a['resolutions'] if r['player']==obligation['player'] and r['team']==obligation['team'] and (r.get('canonical')==obligation.get('canonical') if obligation['trigger']=='R4' else r.get('canonical') in obligation.get('opportunities',[]))]
        checked=[a for r in resolutions for a in r['attempts'] if a['outcome'] in {'FOUND','NO_MATCH','CONFLICT','ACCESS_RESTRICTED','TEMPORARILY_FAILED','UNAVAILABLE'}]
        state='RESOLVED_ACCEPTED_EVIDENCE' if relevant and not unresolved and all(f.get('accepted_state_retained') for f in relevant) else 'RESOLVED_DEFENSIBLE_INFERENCE' if relevant and not unresolved else 'INVESTIGATED_PARTIAL' if checked else 'NOT_CHECKED_REQUIRES_SOURCE_WORK'
        refs={e['sha256'] for f in relevant for e in f.get('assessment',{}).get('evidence',[]) if e.get('sha256')}
        transitions.append(dict(claim='Sufficient exact matchday status within the original obligation scope',direction='SUPPORTS' if state.startswith('RESOLVED') else 'NO_SUFFICIENT_DIRECTION',confidence_status='CONFIRMED' if state=='RESOLVED_ACCEPTED_EVIDENCE' else 'SUPPORTED_BUT_UNCONFIRMED' if state=='RESOLVED_DEFENSIBLE_INFERENCE' else 'NOT_CHECKED' if state.startswith('NOT_CHECKED') else 'INDETERMINATE',limitations='Accepted scoped baseline reused; no new source claim' if state=='RESOLVED_ACCEPTED_EVIDENCE' else 'Complete exact-source squad exclusion; accepted provider alias or shared identity certificate; not an absence cause or canonical correction',next_action='Preserve accepted facts' if state=='RESOLVED_ACCEPTED_EVIDENCE' else 'Retain directional audit evidence; canonical adjudication required before any fact change',evidence_hashes=sorted(refs),claim_key=obligation['claim_key'],trigger=obligation['trigger'],domain=obligation['domain'],before='NOT_CHECKED',after=state,relevant_fixture_obligations=len(relevant),unresolved_fixture_obligations=len(unresolved),actual_evidence_path_checks=len(checked),human_pending=False))
    after=snapshot(db)
    lost={name:sum((rows-after[name]).values()) for name,rows in preserved.items() if rows!=after[name]}
    # Any canonical update must be individually accounted for by the established writer.
    permitted=defaultdict(Counter)
    for decision in decisions:
        result=decision.get('result',{})
        if result.get('state')=='SUPPLEMENTED_UNKNOWN':
            before=result['before'];table='player_match_minutes' if decision['key'][0]=='CLUB' else 'international_appearances'
            columns=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
            assert before['minutes'] is None
            permitted[table][tuple(before[c] for c in columns)]+=1
    for table,allowed in permitted.items():
        missing=(preserved[table]-after[table]);missing.subtract(allowed)
        lost[table]=sum(v for v in missing.values() if v>0)
    assert not any(lost.values()),lost
    repeat_stable=all(audits[cid]['final_opportunities']==final[cid]['final_opportunities'] and audits[cid]['final_gaps']==final[cid]['final_gaps'] for cid in audits) if not decisions else None
    assert repeat_stable is not False
    assert sha(reference.read_bytes())==original
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok' and not db.execute('PRAGMA foreign_key_check').fetchall()
    report=dict(original_tables_preserved=len(preserved),original_tables_unchanged=all(preserved[n]==after[n] for n in preserved),repeat_semantics_identical=repeat_stable,coverage={str(cid):dict(source=adapters[cid].coverage,first=audits[cid]['metrics'],repeat=final[cid]['metrics']) for cid in adapters},frozen_obligations=len(frozen),transitions=transitions,transition_counts=dict(Counter(t['after'] for t in transitions)),candidate_groups=len(grouped),decisions=decisions,network_requests=service.client.calls,parser_calls=service.parse_calls,parser_cache_hits=service.parse_cache_hits,reference_unchanged=True,production='NO-GO',gate_b='NOT_ACCEPTED_PENDING_FULL_ACCEPTANCE')
    (work/'result-private.json').write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')
    (work/'audits-private.json').write_text(json.dumps(final,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n');db.close();return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['reference','seed','archive','source-store','capture-index','calendar','work','start','cutoff']:p.add_argument('--'+name,required=True)
    a=p.parse_args();r=execute(a.reference,a.seed,a.archive,a.source_store,a.capture_index,a.calendar,a.work,a.start,a.cutoff)
    print(json.dumps({k:v for k,v in r.items() if k not in {'transitions','decisions'}},indent=2))
