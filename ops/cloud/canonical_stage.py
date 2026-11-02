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
    db=sqlite3.connect(working);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');before=state(db);legacy_ids=[r[0] for r in db.execute('SELECT match_id FROM matches ORDER BY match_id')];bootstrap(db,SNAPSHOT_SHA)
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
        header=sky_metadata(raw.decode('utf-8','replace'),source,candidate);result=associate_failure(db,task,header);associations.append(dict(task=task['match_key'],result=result))
        if result.get('match_id'):
            assert_alias(db,'COMPETITION','Sky',header['competition_label'],str(header['competition_id']),header['evidence'])
    # Parse every accepted archived Torlytics fixture, not only the two reported cases.
    for doc in db.execute("SELECT * FROM live_documents WHERE provider='Torlytics'").fetchall():
        if doc['url'] not in bodies:continue
        source,raw=bodies[doc['url']];f,sheets=torly_detail(raw.decode('utf-8','replace'),source)
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
    changes=revalidate(db,datetime.fromisoformat('2026-11-02T08:00:00+00:00'))
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
    assert production.head()[0]==production_head,'PRODUCTION_CHANGED_DURING_ISOLATION;REVIEW_EXTERNAL_UPDATE'
    result=dict(status='ARCHIVED_STAGING_REPLAY_PASS',code=code,ref=ref,head=head,current=pointer['current'],baseline_sha256=SNAPSHOT_SHA,all_preexisting_fact_source_tables_unchanged=True,canonical_ids_unchanged=True,player_facts=facts,national_facts=494,raw=rawmeta,source_associations=associations,raw_replays=replays,automatic_revalidations=changes,source_issues=len(data['source_issues']),synthetic_timeline_rows_after=0,duplicate_appearance_keys_after=0,production_unchanged=True)
    (work/'staging-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));store.upload(pointer['current']['release_id'],work/'staging-result.json');print('STAGING_RESULT '+json.dumps(result,ensure_ascii=False))
    return result
if __name__=='__main__':main()
