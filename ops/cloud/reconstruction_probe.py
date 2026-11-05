"""Isolated source-readiness probe, explicitly not six-club acceptance."""
import argparse,json,sqlite3,shutil,hashlib
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
from squad_pipeline.reconstruction.engine import run
from squad_pipeline.reconstruction.retained_adapter import RetainedAdapter
from squad_pipeline.reconstruction.calendar import fifa_windows
from squad_pipeline.source_interface import EvidenceStore,SourceService,SourceRequest


def snapshot(db):
    return {name:Counter(tuple(r) for r in db.execute('SELECT * FROM "'+name+'"'))
        for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def probe(reference, archive, work, calendar, start, cutoff, budget, source_store=None, capture_index=None):
    reference,archive,work,calendar=map(Path,(reference,archive,work,calendar))
    work.mkdir(parents=True,exist_ok=False)
    original_hash=hashlib.sha256(reference.read_bytes()).hexdigest()
    dbpath=work/'candidate.sqlite';shutil.copyfile(reference,dbpath)
    db=sqlite3.connect(dbpath);db.row_factory=sqlite3.Row
    before=snapshot(db)
    observed=datetime.fromtimestamp(calendar.stat().st_mtime,timezone.utc).isoformat()
    index={}
    if source_store:
        index={p.stem:[str(p.resolve())] for p in (Path(source_store)/'bodies').glob('*.response')}
    archives=[archive]+([Path(source_store)] if source_store else [])
    records=json.loads(Path(capture_index).read_text()) if capture_index else []
    service=SourceService(EvidenceStore(work/'sources',archives=archives,index=index,records=records),'reconstruction-probe',set(),max_calls=0)
    boundary_calls=Counter();bodies=set();observe=service.observe
    def traced_observe(request,prior=None):
        acquired,parsed=observe(request,prior)
        boundary_calls[(request.provider,request.category,acquired.outcome,parsed.outcome if parsed else None)]+=1
        if acquired.artifact:bodies.add(acquired.artifact.content_sha256)
        return acquired,parsed
    service.observe=traced_observe
    calendar_url='https://inside.fifa.com/tournament-organisation/international-match-calendars'
    request=SourceRequest('FIFA','calendar',calendar_url,'international-match-calendar')
    artifact=service.store.import_body(request,calendar.read_bytes(),dict(retained_file_mtime=observed,timestamp_basis='RETAINED_FILE_MTIME_ONLY;NOT_HTTP_RETRIEVAL_TIME'),'ARCHIVED_RESPONSE')
    acquired,observation=service.observe(request,artifact)
    if acquired.outcome!='FOUND':raise ValueError('CALENDAR_ACQUISITION_'+acquired.outcome)
    calendars=fifa_windows(calendar.read_bytes(),calendar_url,observed,start,cutoff,time_precision='RETAINED_FILE_MTIME',observation=observation)
    outcomes=[]
    for club in db.execute('SELECT * FROM clubs ORDER BY club_id').fetchall():
        adapter=RetainedAdapter(db,archive,club['club_id'],calendars,source_service=service)
        first=run(db,adapter,start,cutoff,max_attempts=budget)
        second=run(db,adapter,start,cutoff,max_attempts=budget)
        # Private project-local audit artifacts preserve reasoning and references;
        # only sanitized aggregates are candidates for communication publication.
        (work/('audit-'+str(club['club_id'])+'.json')).write_text(json.dumps(first,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')
        outcomes.append(dict(club=club['name'],coverage=adapter.coverage,
            metrics=first['metrics'],repeat_metrics=second['metrics'],calendar=first['calendar_coverage']))
        db.commit()
    after=snapshot(db)
    losses={n:sum((rows-after[n]).values()) for n,rows in before.items() if rows!=after[n]}
    assert not losses,losses
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert hashlib.sha256(reference.read_bytes()).hexdigest()==original_hash
    result=dict(probe_only=True,audit_interval=dict(start=start,cutoff=cutoff),source_reference_unchanged=True,old_tables_preserved=len(before),
        source_boundary=dict(calls=[dict(provider=k[0],category=k[1],acquisition=k[2],parse=k[3],count=v) for k,v in sorted(boundary_calls.items())],
            distinct_body_hashes=len(bodies),network_requests=service.client.calls,
            parser_calls=service.parse_calls,parser_cache_hits=service.parse_cache_hits),
        outcomes=outcomes,six_club_gate='NOT_ACCEPTED: remaining discovery, active resolution and adjudicated-persistence acceptance outstanding',
        audit_authority='Existing on-date roster decisions and canonical facts retained; source replay gaps are separate; no canonical adjudication invoked',
        real_madrid='NOT_STARTED;SIX_CLUB_GATE_REQUIRED',production='NO-GO')
    (work/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    db.close();return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--reference',required=True);p.add_argument('--archive',required=True);p.add_argument('--work',required=True);p.add_argument('--calendar',required=True);p.add_argument('--start',required=True);p.add_argument('--cutoff',required=True);p.add_argument('--budget',type=int,default=500);p.add_argument('--source-store');p.add_argument('--capture-index');a=p.parse_args()
    print(json.dumps(probe(a.reference,a.archive,a.work,a.calendar,a.start,a.cutoff,a.budget,a.source_store,a.capture_index),indent=2))
