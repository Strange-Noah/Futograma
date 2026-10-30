"""Explicit reviewed snapshot promotion, independently from ingestion and website publication."""
import argparse,os,subprocess
from pathlib import Path
from datetime import datetime,timezone
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import sha,pack
from squad_pipeline.core import ROOT

def main():
    require_hosted();p=argparse.ArgumentParser();p.add_argument('--source-ref',required=True);p.add_argument('--expected-source-head',required=True);p.add_argument('--expected-production-head',required=True);p.add_argument('--expected-sqlite-sha256',required=True);a=p.parse_args()
    code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    if os.environ.get('FUTOGRAMA_RELEASE_APPROVAL')!='PROMOTE_REVIEWED_PHASE3' or os.environ.get('FUTOGRAMA_APPROVED_CODE_SHA')!=code:raise RuntimeError('EXPLICIT_EXACT_VERSION_PROMOTION_APPROVAL_REQUIRED')
    if not a.source_ref.startswith('acceptance/'):raise RuntimeError('REVIEWED_ISOLATED_SOURCE_REQUIRED')
    if datetime.now(timezone.utc)<datetime.fromisoformat('2026-10-31T16:00:00+08:00'):raise RuntimeError('ROLLOUT_NOT_DUE')
    work=ROOT/('.cloud/promotion-'+os.environ['GITHUB_RUN_ID']);work.mkdir(parents=True,exist_ok=False);src=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],a.source_ref);dst=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],'main')
    head,pointer=src.restore(work/'reviewed.sqlite')
    if head!=a.expected_source_head or sha(work/'reviewed.sqlite')!=a.expected_sqlite_sha256 or pointer['current']['code_commit']!=code:raise RuntimeError('REVIEWED_CANDIDATE_CHANGED')
    current,old=dst.restore(work/'rollback.sqlite')
    if current!=a.expected_production_head:raise RuntimeError('PRODUCTION_BASE_CHANGED')
    pack(work/'reviewed.sqlite',work/'reviewed.gz');new,published=dst.publish(work/'reviewed.sqlite',work/'reviewed.gz',current,code)
    if published['previous']!=old['current']:raise RuntimeError('ROLLBACK_POINTER_NOT_RETAINED')
    dst.restore(work/'verified.sqlite');assert sha(work/'verified.sqlite')==a.expected_sqlite_sha256
    print('REVIEWED_SNAPSHOT_PROMOTED',new,'COLLECTION_AND_WEBSITE_UNCHANGED')
if __name__=='__main__':main()
