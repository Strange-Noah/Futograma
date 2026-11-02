"""Real archived-response replay on an isolated GitHub snapshot. Never publishes main."""
import os,json,gzip,hashlib,sqlite3,tarfile,subprocess,re
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import sha,pack,inspect
from squad_pipeline.canonical_fixture import bootstrap,metadata,encoded,digest,assert_alias
from squad_pipeline.canonical_adapters import associate_failure,observe_fixture
from squad_pipeline.canonical_revalidation import revalidate
from squad_pipeline.match_diagnostics import inspect_match,record
from squad_pipeline.live_parse import Tree,fixture,src,canon,uk_datetime,torly_detail,sky_detail,COMP,catalogs
from squad_pipeline.cloud.all_match_export import export_all_matches

SNAPSHOT_ASSET=698620418
SNAPSHOT_SHA='c446ebc6a9d501923cc4392e1509fd9e91efa65fc581e689b407faec874de38f'


def archive(store,tag,path):
    release=store.api('GET',store.base+'/releases/tags/'+tag)
    asset=next(a for a in release['assets'] if a['name'].endswith('.tar.gz'))
    store.download(asset['id'],path)
    tar=tarfile.open(path,'r:gz');records=[]
    for member in tar.getmembers():
        if not member.isfile() or not member.name.endswith('.json'):continue
        try:d=json.load(tar.extractfile(member))
        except (ValueError,UnicodeDecodeError):continue
        if isinstance(d,dict) and isinstance(d.get('captures'),list):records.extend(d['captures'])
        if isinstance(d,dict) and d.get('url') and d.get('path') and d.get('sha256'):records.append(d)
    unique={r['path']:r for r in records if r.get('path') and r.get('sha256')}
    bodies={}
    for name,r in unique.items():
        try:body=tar.extractfile(name).read()
        except KeyError:continue
        if hashlib.sha256(body).hexdigest()!=r.get('reused_body_sha256',r['sha256']):raise ValueError('ARCHIVED_BODY_HASH_MISMATCH')
        bodies[r['url']]=(r,body)
    return tar,bodies,dict(tag=tag,asset_id=asset['id'],sha256=sha(path),records=len(bodies))


def state(db):
    # Entire pre-existing table rows (including source evidence) remain compared;
    # only explicitly allowed task metadata and newly added registry may change.
    result={}
    for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'canonical_%'"):
        if name in ('match_retry_queue','international_acquisition_queue'):continue
        rows=[list(r) for r in db.execute('SELECT * FROM "'+name+'"')]
        def default(v):return {'blob_hex':v.hex()} if isinstance(v,bytes) else str(v)
        result[name]=hashlib.sha256(json.dumps(sorted(rows,key=lambda r:json.dumps(r,default=default,sort_keys=True)),default=default,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return result


from squad_pipeline.canonical_adapters import failed_source_header as sky_metadata


def main():
    require_hosted();ref=os.environ['FUTBOL_DATA_REF']
    if not ref.startswith('acceptance/') or os.environ.get('FUTBOL_CLOUD_ENABLED')!='false':raise ValueError('ISOLATED_DISABLED_AUTHORITY_REQUIRED')
    store=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],ref);production=GitHubStore(store.repository,store.token,'main');production_head,_=production.head();code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    work=ROOT/'.cloud'/('canonical-stage-'+os.environ['GITHUB_RUN_ID']);work.mkdir(parents=True,exist_ok=False)
    store.download(SNAPSHOT_ASSET,work/'baseline.gz');body=gzip.decompress((work/'baseline.gz').read_bytes());assert hashlib.sha256(body).hexdigest()==SNAPSHOT_SHA
    baseline=work/'baseline.sqlite';baseline.write_bytes(body);working=work/'candidate.sqlite';working.write_bytes(body)
    baseline_source=Path(os.environ['BASELINE_SOURCE_PATH'])
    baseline_pointer=dict(current=dict(database=inspect(baseline),version='PINNED_BASELINE',code_commit='6e3078bb15ca2e83323e80af86f7234e54910fd8'),dataset_ref=ref)
    env=dict(os.environ,PYTHONPATH=str(baseline_source))
    subprocess.run(['python','-c',"import sys,json;from squad_pipeline.cloud.all_match_export import export_all_matches;export_all_matches(sys.argv[1],sys.argv[2],'BASELINE',json.loads(sys.argv[3]),'2026-11-02',True)",str(baseline),str(work/'baseline-export'),json.dumps(baseline_pointer)],cwd=baseline_source,env=env,check=True)
    baseline_export=json.loads((work/'baseline-export/data/browser.json').read_text())
    db=sqlite3.connect(working);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');before=state(db);legacy_ids=[r[0] for r in db.execute('SELECT match_id FROM matches ORDER BY match_id')];bootstrap(db,SNAPSHOT_SHA)
    from squad_pipeline.international_store import team as national_team,put_match as national_match
    national_replayed=0
    for r in db.execute('SELECT p.*,t.display_name,t.level FROM football_team_provider_ids p JOIN football_teams t USING(team_key)').fetchall():
        assert national_team(db,r['display_name'],r['level'],r['evidence_id'],r['provider'],r['provider_team_id'])==r['team_key']
    for r in db.execute('SELECT * FROM international_match_provider_ids').fetchall():
        m=dict(db.execute('SELECT * FROM international_matches WHERE match_id=?',(r['match_id'],)).fetchone())
        assert national_match(db,m,r['provider'],r['provider_match_id'],r['evidence_id'])==r['match_id'];national_replayed+=1
    seeded=db.serialize();operations=[]
    tar,bodies,rawmeta=archive(store,'daily-evidence-41317204106',work/'club-raw.tar.gz');associations=[];replays=[]
    # Every retained source task whose response is archived goes through the same generic adapter.
    for row in db.execute('SELECT * FROM match_retry_queue').fetchall():
        task=dict(row);url=task['url']
        if url not in bodies or task['provider']!='Sky':continue
        source,raw=bodies[url];candidate=json.loads(task['candidate_json']);source=dict(source,catalog_candidate=candidate)
        # Verify the actual directory body too, not just a copied candidate label.
        directory=bodies.get(candidate['catalog_source']['url'])
        if not directory or directory[0]['sha256']!=candidate['catalog_source']['sha256']:raise ValueError('DIRECTORY_EVIDENCE_NOT_FOUND')
        seen=catalogs(directory[1].decode('utf-8','replace'),'Sky',task['club_id'],task['match_date'])
        exact=[x for x in seen if x.get('catalog_provider_id')==task['provider_match_id']]
        if len(exact)!=1 or exact[0]['catalog_team_labels']!=candidate['catalog_team_labels']:raise ValueError('DIRECTORY_ID_SIDE_EVIDENCE_MISMATCH')
        header=sky_metadata(raw.decode('utf-8','replace'),source,candidate);operations.append(('failure',task,header));result=associate_failure(db,task,header);associations.append(dict(task=task['match_key'],result=result))
        if result.get('match_id'):
            assert_alias(db,'COMPETITION','Sky',header['competition_label'],str(header['competition_id']),header['evidence'])
    # Parse every accepted archived Torlytics fixture, not only the two reported cases.
    for doc in db.execute("SELECT * FROM live_documents WHERE provider='Torlytics'").fetchall():
        if doc['url'] not in bodies:continue
        source,raw=bodies[doc['url']];f,sheets=torly_detail(raw.decode('utf-8','replace'),source);operations.append(('fixture',json.loads(json.dumps(f)),f['source_evidence'][0]))
        result=observe_fixture(db,f,f['source_evidence'][0],create=False)
        if result['match_id']!=doc['match_id']:raise ValueError('HISTORICAL_CANONICAL_ID_CHANGED')
        own=next(s for s in sheets if s['club_id']==doc['club_id'])
        # Re-resolve player identities and compare semantic minutes without rewriting facts.
        from squad_pipeline.live_ingest import resolve_player
        from squad_pipeline.six_club_experiment import incremental_row
        compared=[]
        for player in own['players']:
            player['player_id']=resolve_player(db,doc['club_id'],player)
        f['match_id']=result['match_id']
        for player in own['players']:
            row=incremental_row(doc['club_id'],player['player_id'],f,[own]);old=db.execute('SELECT participation_status,minutes FROM player_match_minutes WHERE club_id=? AND match_id=? AND player_id=?',(doc['club_id'],result['match_id'],player['player_id'])).fetchone()
            if not old or tuple(old)!=(row['status'],row['minutes']):raise ValueError('ARCHIVED_APPEARANCE_SEMANTIC_CHANGED')
            compared.append(player['player_id'])
        replays.append(dict(url=doc['url'],canonical_match_id=result['match_id'],players_compared=len(compared),body_sha256=source['sha256'],operation=result['operation']))
        # Canonical display names come from the identity-verified parsed event;
        # accepted legacy club IDs/names remain unchanged.
        info=metadata(db,result['match_id'])
        for side in ('home','away'):
            key=info[side+'_team_id'];label=f['source_team_names'][side]
            assert_alias(db,'TEAM','Torlytics',label,key,dict(sha256=source['sha256'],url=source['url'],canonical_match_id=result['match_id']))
            if key.startswith('legacy-team:'):db.execute("UPDATE canonical_entities SET name=? WHERE kind='TEAM' AND entity_id=?",(label,key))
    # Replay the actual archived observations in both orders on the same verified seed.
    order_results=[]
    for sequence in (operations,list(reversed(operations))):
        clone=sqlite3.connect(':memory:');clone.deserialize(seeded);clone.row_factory=sqlite3.Row
        def apply(op):
            kind,item,context=op
            return associate_failure(clone,item,context) if kind=='failure' else observe_fixture(clone,item,context,create=False)
        for op in sequence:apply(op)
        def counters():return {t:clone.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['canonical_fixtures','canonical_observations','canonical_field_claims','canonical_reconciliation_events']}
        counts=counters()
        for op in sequence:apply(op)
        assert counters()==counts,'ARCHIVED_REPLAY_NOT_IDEMPOTENT'
        signature=[list(r) for r in clone.execute('SELECT match_id,field,value_json,state FROM canonical_field_values ORDER BY match_id,field')]
        bindings=[list(r) for r in clone.execute('SELECT domain,provider,provider_match_id,match_id FROM canonical_fixture_sources ORDER BY domain,provider,provider_match_id')]
        order_results.append(dict(field_hash=digest(signature),binding_hash=digest(bindings),counts=counts));clone.close()
    assert order_results[0]==order_results[1],'ARCHIVED_SOURCE_ORDER_CHANGED_CANONICAL_RESULT'
    from squad_pipeline.canonical_adapters import associate_pending_international
    national_associations=associate_pending_international(db)
    queue_before={t:{r[0]:r[1] for r in db.execute('SELECT state,COUNT(*) FROM '+t+' GROUP BY state')} for t in ('match_retry_queue','international_acquisition_queue')}
    changes=revalidate(db,datetime.fromisoformat('2026-11-02T08:00:00+00:00'))
    recovery_attempts=[]
    for row in db.execute("SELECT * FROM match_retry_queue WHERE state='PENDING'").fetchall():
        task=dict(row)
        if task['url'] not in bodies:continue
        source,raw=bodies[task['url']];source=dict(source,catalog_candidate=json.loads(task['candidate_json']))
        clone=sqlite3.connect(':memory:');clone.deserialize(db.serialize());clone.row_factory=sqlite3.Row
        try:
            from squad_pipeline.live_ingest import apply_sheet
            f,sheets=sky_detail(raw.decode('utf-8','replace'),source)
            own=next(s for s in sheets if s['club_id']==task['club_id'])
            result=apply_sheet(clone,f,own,'CANONICAL_ARCHIVED_RECOVERY','2026-11-02',dry=True)
            recovery_attempts.append(dict(task_key=task['match_key'],result=result))
        except Exception as exc:recovery_attempts.append(dict(task_key=task['match_key'],error=type(exc).__name__+':'+str(exc),resolved=False))
        finally:clone.close()
    queue_after={t:[dict(r) for r in db.execute('SELECT state,failure_classification,COUNT(*) AS count FROM '+t+' GROUP BY state,failure_classification')] for t in queue_before}
    db.commit();after=state(db)
    assert before==after,'PRE_EXISTING_FACT_OR_SOURCE_TABLE_CHANGED'
    assert legacy_ids==[r[0] for r in db.execute('SELECT match_id FROM matches ORDER BY match_id')]
    assert not db.execute('PRAGMA foreign_key_check').fetchone()
    facts=db.execute('SELECT COUNT(*) FROM player_match_minutes').fetchone()[0]
    db.close();pack(working,work/'candidate.gz');head,_=store.head();head,pointer=store.publish(working,work/'candidate.gz',head,code);pointer=dict(pointer,dataset_ref=ref)
    export_all_matches(working,work/'export',head,pointer,'2026-11-02',True);data=json.loads((work/'export/data/browser.json').read_text())
    assert not any(r.get('pending') for rows in data['records'].values() for r in rows),'SOURCE_TASK_BECAME_APPEARANCE'
    assert sum(r['match_type']=='NATIONAL' for rows in data['records'].values() for r in rows)==494
    assert sum(r['match_type']=='CLUB' for rows in data['records'].values() for r in rows)==facts
    for rows in data['records'].values():
        keys=[(r['match_type'],r.get('club_id'),r['match_id']) for r in rows];assert len(keys)==len(set(keys))
    before_pending=sum(bool(r.get('pending')) for rs in baseline_export['records'].values() for r in rs)
    metadata_metrics=dict(club_appearance_rows_before=sum(r.get('match_type')=='CLUB' and not r.get('pending') for rs in baseline_export['records'].values() for r in rs),club_appearance_rows_after=facts,synthetic_timeline_rows_before=before_pending,synthetic_timeline_rows_after=0,verified_score_matches_before=len({r['match_id'] for rs in baseline_export['records'].values() for r in rs if r.get('match_type')=='CLUB' and r.get('home_score') is not None}),verified_score_matches_after=len({r['match_id'] for rs in data['records'].values() for r in rs if r.get('match_type')=='CLUB' and r.get('score_state')=='VERIFIED'}))
    assert production.head()[0]==production_head,'PRODUCTION_CHANGED_DURING_ISOLATION;REVIEW_EXTERNAL_UPDATE'
    result=dict(status='ARCHIVED_STAGING_REPLAY_PASS',code=code,ref=ref,head=head,current=pointer['current'],baseline_sha256=SNAPSHOT_SHA,all_preexisting_fact_source_tables_unchanged=True,canonical_ids_unchanged=True,player_facts=facts,national_facts=494,national_provider_records_replayed=national_replayed,national_source_associations=national_associations,queue_before=queue_before,queue_after=queue_after,recovery_attempts=recovery_attempts,raw=rawmeta,source_associations=associations,raw_replays=replays,source_order_and_idempotency=order_results,automatic_revalidations=changes,source_issues=len(data['source_issues']),metadata_metrics=metadata_metrics,synthetic_timeline_rows_after=0,duplicate_appearance_keys_after=0,production_unchanged=True)
    semantic=dict(status='ALL_PREEXISTING_FACT_SOURCE_TABLES_UNCHANGED',table_count=len(before),tables={t:dict(before=before[t],after=after[t]) for t in sorted(before)},excluded_task_queues=['match_retry_queue','international_acquisition_queue'],canonical_ids_unchanged=True,club_matches=79,national_matches=133)
    (work/'semantic-before-after.json').write_text(json.dumps(semantic,indent=2));store.upload(pointer['current']['release_id'],work/'semantic-before-after.json')
    (work/'staging-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));store.upload(pointer['current']['release_id'],work/'staging-result.json');print('STAGING_RESULT '+json.dumps(result,ensure_ascii=False))
    return result
if __name__=='__main__':main()
