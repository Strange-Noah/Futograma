"""Diagnose all201 tasks from immutable raw archives, with bounded public probes.

This program never resolves a task or inserts an appearance. It separates42
unique acquisition URLs from201 dependent task records and retains each result.
"""
import os,json,gzip,sqlite3,subprocess,hashlib
from collections import Counter,defaultdict
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,urlencode
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.match_diagnostics import inspect_match
from ops.cloud.canonical_stage import archive,SNAPSHOT_ASSET,SNAPSHOT_SHA


def main():
    require_hosted();ref=os.environ['FUTBOL_DATA_REF']
    if not ref.startswith('acceptance/') or os.environ.get('FUTBOL_CLOUD_ENABLED')!='false':raise ValueError('ISOLATED_DIAGNOSIS_ONLY')
    store=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],ref);prod=GitHubStore(store.repository,store.token,'main');original,_=prod.head()
    work=ROOT/'.cloud'/('mismatch-stage-'+os.environ['GITHUB_RUN_ID']);work.mkdir(parents=True,exist_ok=False);store.download(SNAPSHOT_ASSET,work/'baseline.gz');body=gzip.decompress((work/'baseline.gz').read_bytes());assert hashlib.sha256(body).hexdigest()==SNAPSHOT_SHA
    db=sqlite3.connect(':memory:');db.deserialize(body);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
    latest={r['task_key']:json.loads(r['result_json']) for r in db.execute('SELECT * FROM international_acquisition_history ORDER BY observed_at,event_id')};groups=defaultdict(list)
    for r in db.execute("SELECT * FROM international_acquisition_queue WHERE state!='RESOLVED'"):
        if 'RETURNED_MATCH_ID_MISMATCH' in (latest.get(r['task_key'],{}).get('error') or ''):groups[r['url']].append(dict(r))
    assert sum(map(len,groups.values()))==201 and len(groups)==42
    records={};archives=[]
    for tag in ['phase3-international-evidence-40925476053','phase3-international-evidence-40929244888']:
        if len(records)==len(groups):break
        tar,bodies,meta=archive(store,tag,work/(tag+'.tar.gz'));archives.append(meta)
        for url,tasks in groups.items():
            if url in records or url not in bodies:continue
            source,raw=bodies[url];fixture=json.loads(tasks[0]['payload_json'])['fixture'];d=inspect_match(raw,fixture['provider_match_id'],source);d.update(dependent_tasks=len(tasks),task_keys=[r['task_key'] for r in tasks],archive=tag);records[url]=d
        tar.close()
    unresolved=[url for url in groups if url not in records]
    # Two bounded public page probes: never restricted APIs or fabricated identity.
    probes=[]
    if os.environ.get('CANONICAL_PUBLIC_PROBES')=='true':
        from squad_pipeline.live_sources import Client,HOSTS
        from squad_pipeline.source_redirects import fetch_public_redirects
        HOSTS.add('www.fotmob.com');client=Client('canonical-routing-probe-'+os.environ['GITHUB_RUN_ID'],budget=12)
        for url in sorted(records)[:2]:
            expected=records[url]['expected_id'];parts=urlsplit(url)
            urls=[url,urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode({'matchId':expected}),''))]
            for target in urls:
                source,raw=fetch_public_redirects(client,target,'canonical_expected_actual_diagnostic')
                if source['status']=='SUCCESS':d=inspect_match(raw.decode('utf-8','replace'),expected,source)
                else:d=dict(expected_id=expected,source_url=target,classification='SOURCE_UNAVAILABLE',http_status=source.get('http_status'),error=source.get('error'))
                probes.append(d)
    weights=Counter()
    for d in records.values():weights[d['classification']]+=d['dependent_tasks']
    by_wire=defaultdict(list)
    for url,d in records.items():by_wire[d['request_url_without_fragment']].append(d)
    same_wire=[{'wire_url':url,'expected_ids':sorted({r['expected_id'] for r in rs}),'actual_ids':sorted({str(r['actual_id']) for r in rs}),'body_hashes':sorted({r['body_sha256'] for r in rs})} for url,rs in by_wire.items() if len(rs)>1]
    result=dict(status='MISMATCH_CLUSTER_DIAGNOSED' if not unresolved else 'MISMATCH_CLUSTER_PARTIAL_EVIDENCE',task_count=201,unique_urls=42,covered_urls=len(records),weighted_classification=dict(weights),archives=archives,missing_archived_urls=unresolved,records=list(records.values()),shared_wire_routes=same_wire,public_probes=probes,tasks_resolved=0,player_facts_written=0,production_changed=False,interpretation='URL fragment is not transmitted by HTTP. Returned general/lineup IDs and hashes distinguish wrong-event responses from absent SSR structure. Query probes are diagnostics only; no source is accepted merely because an alternative URL returned200.')
    assert prod.head()[0]==original
    f=work/'mismatch-result.json';f.write_text(json.dumps(result,ensure_ascii=False,indent=2));release=store.api('POST',store.base+'/releases',dict(tag_name='canonical-mismatch-'+os.environ['GITHUB_RUN_ID'],target_commitish=ref,name='Canonical mismatch diagnostics',body='Isolated investigation. No tasks bulk-resolved. Retain required evidence.',draft=False));store.upload(release['id'],f);print('MISMATCH_RESULT '+json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
