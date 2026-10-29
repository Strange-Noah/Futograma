"""Fail closed before acceptance dispatch or exporting mismatched website/data versions."""
import hashlib,json

def require_ci(runs,sha,repository='Strange-Noah/Futograma'):
    matching=[r for r in runs if r.get('head_sha')==sha and r.get('path')=='.github/workflows/ci.yml' and r.get('head_repository',{}).get('full_name')==repository]
    if not matching:raise ValueError('SAME_COMMIT_CI_NOT_FOUND')
    latest=max(matching,key=lambda r:(r['run_number'],r.get('run_attempt',1)))
    if latest.get('status')!='completed' or latest.get('conclusion')!='success':raise ValueError('SAME_COMMIT_CI_NOT_SUCCESSFUL')
    return latest['id']

def verify_frontend(manifest,pointer,data_head,frontend_commit,browser_bytes=None):
    v=manifest['version'];current=pointer['current']
    expected={'data_commit':data_head,'sqlite_sha256':current['database']['sha256'],'data_version':current['version'],'code_commit':current['code_commit']}
    for k,value in expected.items():
        if v.get(k)!=value:raise ValueError('VERSION_MISMATCH:'+k)
    # Exact pin is deliberately conservative until a tested compatibility contract exists.
    if frontend_commit!=v['code_commit']:raise ValueError('FRONTEND_DATA_CODE_COMMIT_MISMATCH')
    if manifest.get('schema_version') not in (2,3):raise ValueError('UNSUPPORTED_BROWSER_SCHEMA')
    if browser_bytes is None:raise ValueError('BROWSER_PAYLOAD_REQUIRED')
    if hashlib.sha256(browser_bytes).hexdigest()!=manifest.get('browser_sha256'):raise ValueError('BROWSER_HASH_MISMATCH')
    browser=json.loads(browser_bytes)
    if browser.get('schema_version')!=manifest['schema_version'] or browser.get('version')!=v:raise ValueError('BROWSER_VERSION_MISMATCH')
    return dict(frontend_commit=frontend_commit,export_code_commit=v['code_commit'],data_commit=data_head,sqlite_sha256=v['sqlite_sha256'],browser_sha256=manifest['browser_sha256'])

def status_matrix(results):
    allowed={'PASS','FAIL','SKIPPED','DEGRADED','DISABLED','PENDING'}
    required=('software','live_sources','roster_completeness','international_coverage','data_publication','website','production_authority')
    result={k:results.get(k,'PENDING') for k in required}
    if any(v not in allowed for v in result.values()):raise ValueError('INVALID_STATUS')
    result['production_ready']=all(result[k]=='PASS' for k in required)
    return result
