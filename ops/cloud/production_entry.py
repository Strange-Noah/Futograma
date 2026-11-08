"""Approved application entrypoint with configured-timezone current-roster publication."""
import json,os,sqlite3,tarfile
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from squad_pipeline.live_sources import HOSTS
from ops.cloud import platform as application
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.all_match_export import export_all_matches

def roster_day(at=None):
    settings=json.loads((ROOT/'ops/cloud/runtime.json').read_text())
    return (at or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings['timezone'])).date().isoformat()

def publish_site(s,work,code_commit):
    work.mkdir(parents=True,exist_ok=False);head,p=s.restore(work/'source.sqlite')
    p=dict(p,dataset_ref=s.ref);m=export_all_matches(work/'source.sqlite',work/'site',head,p,roster_day(),False);application.ensure_ref(s,'site',head)
    files={str(x.relative_to(work/'site')):x.read_bytes() for x in (work/'site').rglob('*') if x.is_file()}
    files['publication.json']=json.dumps(dict(data_commit=head,version=m['version'],code_commit=code_commit,roster_reference_timezone='Asia/Shanghai')).encode()
    commit=application.commit_tree(s,'site',files,'site: '+m['version']['data_version'])
    return dict(commit=commit,version=m['version'])

def run_audits(database, work, captures, start, cutoff):
    """Run the accepted supplementary engine; failures never become football facts."""
    from squad_pipeline.reconstruction.engine import run as audit
    from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
    from squad_pipeline.source_interface import EvidenceStore,SourceService
    work=Path(work);work.mkdir(parents=True,exist_ok=False)
    service=SourceService(EvidenceStore(work/'sources',archives=[ROOT],records=captures),
        'daily-audits-'+os.environ['GITHUB_RUN_ID'],set(HOSTS),max_calls=20,max_bytes=12000000)
    db=sqlite3.connect(database);db.row_factory=sqlite3.Row;results=[];errors=[]
    try:
        clubs=[r[0] for r in db.execute('SELECT club_id FROM clubs ORDER BY club_id')]
        for club in clubs:
            db.execute('SAVEPOINT supplementary_audit')
            try:
                adapter=RetainedAdapter(db,ROOT,club,source_service=service)
                result=audit(db,adapter,start,cutoff,mode='INCREMENTAL',max_attempts=20)
                payload=json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False)
                (work/('club-'+str(club)+'.json')).write_text(payload+'\n')
                results.append(dict(club_id=club,execution='PASS',metrics=result['metrics'],
                    calendar_coverage=result['calendar_coverage']))
                db.execute('RELEASE supplementary_audit')
            except Exception as exc:
                db.execute('ROLLBACK TO supplementary_audit');db.execute('RELEASE supplementary_audit')
                errors.append(dict(club_id=club,classification='OPERATIONAL_FAILURE',
                    component='R1_R6',error_type=type(exc).__name__))
        db.commit()
    finally:db.close()
    records=[]
    for item in service.client.records:
        item=dict(item)
        if item.get('path'):item['path']=str((service.store.root/item['path']).relative_to(ROOT))
        records.append(item)
    summary=dict(stages=['R1','R2','R5','R3_R4','R6'],clubs=results,operational_errors=errors,
        source_calls=service.client.calls,source_bytes=service.bytes,
        canonical_authority='UNCHANGED;SUPPLEMENTARY_AUDIT_ONLY')
    (work/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary,records

def with_audits(original):
    def update(database,work,isolated=False,at=None):
        from squad_pipeline.cloud.incremental import bounds
        at=at or datetime.now(timezone.utc);work=Path(work)
        db=sqlite3.connect(database)
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE name='pipeline_checkpoints'").fetchone()
        checkpoint=db.execute("SELECT completed_at FROM pipeline_checkpoints WHERE scope='international'").fetchone() if exists else None
        db.close();settings=json.loads((ROOT/'ops/cloud/runtime.json').read_text())
        start,end=bounds(checkpoint[0] if checkpoint else None,at,settings['lookback_hours'])
        result=original(database,work,isolated=isolated,at=at)
        captures=[]
        with tarfile.open(result['evidence_archive'],'r:gz') as archive:
            for item in archive:
                if item.isfile() and item.name.startswith('metadata/') and item.name.endswith('.json'):
                    captures.append(json.load(archive.extractfile(item)))
        try:
            summary,_=run_audits(database,work/'audits',captures,start.date().isoformat(),(end.date()+timedelta(days=1)).isoformat())
        except Exception as exc:
            summary=dict(operational_errors=[dict(component='R1_R6',classification='OPERATIONAL_FAILURE',error_type=type(exc).__name__)])
        summary['audit_tool_commit']=os.environ.get('REPORTING_CODE_SHA','UNKNOWN')
        (work/'audits').mkdir(parents=True,exist_ok=True)
        (work/'audits/summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
        # Preserve the original archive and all supplementary source evidence.
        combined=work/'daily-with-audits.tar.gz'
        with tarfile.open(result['evidence_archive'],'r:gz') as previous,tarfile.open(combined,'x:gz') as target:
            for item in previous:target.addfile(item,previous.extractfile(item) if item.isfile() else None)
            for path in (work/'audits').rglob('*'):
                if path.is_file():target.add(path,arcname='audits/'+str(path.relative_to(work/'audits')))
        print(json.dumps(dict(supplementary_audits=summary),ensure_ascii=False))
        return dict(result,audits=summary,evidence_archive=str(combined))
    return update


def main():
    from squad_pipeline.cloud import incremental
    incremental.run=with_audits(incremental.run)
    application.publish_site=publish_site
    application.main()
if __name__=='__main__':main()
