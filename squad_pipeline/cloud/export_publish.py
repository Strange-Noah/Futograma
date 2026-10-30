"""Validate the complete consumer export before advancing a cloud snapshot pointer."""
import json,hashlib
from pathlib import Path
from .quality import inspect,readonly
from .all_match_export import export_all_matches

def validate_export(database,out,pointer,head):
    body=(Path(out)/'data/browser.json').read_bytes();data=json.loads(body);manifest=json.loads((Path(out)/'data/manifest.json').read_text());version=data['version'];current=pointer['current']
    if hashlib.sha256(body).hexdigest()!=manifest['browser_sha256'] or manifest['version']!=version:raise ValueError('EXPORTED_MANIFEST_BODY_MISMATCH')
    expected=dict(data_commit=head,data_version=current['version'],sqlite_sha256=current['database']['sha256'],code_commit=current['code_commit'])
    if version!=expected:raise ValueError('EXPORTED_POINTER_VERSION_MISMATCH')
    db=readonly(database)
    try:count=db.execute('SELECT COUNT(*) FROM international_appearances').fetchone()[0]
    finally:db.close()
    if sum(r.get('match_type')=='NATIONAL' and not r.get('pending') for rows in data['records'].values() for r in rows)!=count:raise ValueError('EXPORTED_INTERNATIONAL_FACT_COUNT_MISMATCH')
    return manifest

def publish_with_export_preflight(store,snapshot,compressed,expected_head,code_commit,out,as_of,acceptance=True):
    out=Path(out);stage=out/'prepublication-export';site=out/'site';staged=dict(current=dict(database=inspect(snapshot),version='UNPUBLISHED_PREFLIGHT',code_commit=code_commit),dataset_ref=store.ref)
    export_all_matches(snapshot,stage,expected_head,staged,as_of,acceptance);validate_export(snapshot,stage,staged,expected_head)
    # All consumer schema/count/version paths above execute before the compare-and-swap.
    head,pointer=store.publish(snapshot,compressed,expected_head,code_commit);pointer=dict(pointer,dataset_ref=store.ref)
    export_all_matches(snapshot,site,head,pointer,as_of,acceptance);manifest=validate_export(snapshot,site,pointer,head)
    return head,pointer,manifest
