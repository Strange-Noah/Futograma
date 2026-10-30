"""Fresh cloud execution of the real daily controller, without authority promotion."""
import os,json,sqlite3,subprocess,shutil,tarfile
from pathlib import Path
from datetime import datetime,timezone
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import sha,snapshot,pack,inspect
from squad_pipeline.cloud.driver import configure_clubs,validate_monotonic
from squad_pipeline.cloud.incremental import run
from squad_pipeline.cloud.export_publish import publish_with_export_preflight,validate_export
from squad_pipeline.cloud.all_match_export import export_all_matches
from squad_pipeline.cloud.release_gate import verify_frontend
from squad_pipeline.cloud.storage import StaleWriter
from squad_pipeline.roster_policy import population
REF='acceptance/production-release-20261030'
def store(ref):return GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],ref)
def guard():
    require_hosted()
    if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='UNCONFIRMED' or os.environ.get('FUTBOL_DATA_REF')!=REF:raise RuntimeError('ISOLATED_RELEASE_PREPARATION_REQUIRED')
def write(path,value):
    with Path(path).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2)
def upload(s,release,path,work):
    a=s.upload(release['id'],path);dest=work/('verified-'+path.name);s.download(a['id'],dest)
    if sha(path)!=sha(dest):raise RuntimeError('UPLOAD_READBACK_MISMATCH')
    return dict(asset_id=a['id'],sha256=sha(path),name=path.name)
def site(work,db,head,pointer,code):
    pointer=dict(pointer,dataset_ref=REF);m=export_all_matches(db,work/'site',head,pointer,datetime.now(timezone.utc).date().isoformat(),True);validate_export(db,work/'site',pointer,head);verify_frontend(m,pointer,head,code,(work/'site/data/browser.json').read_bytes())
    for name in ['index.html','app.js','style.css']:shutil.copyfile(ROOT/'web'/name,work/'site'/name)
    return m

def producer():
    guard();work=ROOT/'.cloud/release-producer';work.mkdir(parents=True,exist_ok=False);s=store(REF);prod=store('main');production=prod.head();base,pointer=s.restore(work/'input.sqlite');snapshot(work/'input.sqlite',work/'working.sqlite');configure_clubs(work/'working.sqlite');code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    db=sqlite3.connect(work/'input.sqlite');old_refs=list(db.execute('SELECT * FROM validation_national_reference ORDER BY player_id,match_id'));old_players={r[0] for r in db.execute('SELECT player_id FROM players')};db.close()
    result=run(work/'working.sqlite',work,isolated=True)
    integrity=inspect(work/'working.sqlite');monotonic=validate_monotonic(work/'input.sqlite',work/'working.sqlite');db=sqlite3.connect(work/'working.sqlite');db.row_factory=sqlite3.Row
    assert [tuple(r) for r in db.execute('SELECT * FROM validation_national_reference ORDER BY player_id,match_id')]==old_refs
    assert old_players<={r[0] for r in db.execute('SELECT player_id FROM players')}
    groups=population(db,datetime.now(timezone.utc).date().isoformat());cohorts={state:sum(r['cohort']==state for r in groups) for state in ('FIRST_TEAM','YOUTH','UNKNOWN','EXCLUDED')}
    unknown=[dict(r) for r in db.execute('SELECT a.player_id,p.display_name,a.match_id,a.participation_status FROM international_appearances a JOIN players p USING(player_id) WHERE minutes IS NULL')];db.close()
    release=s.api('POST',s.base+'/releases',dict(tag_name='release-readiness-'+os.environ['GITHUB_RUN_ID'],target_commitish=REF,name='Isolated production release preparation',body='No production collection, promotion, public website publication or handover.',draft=True))
    evidence=upload(s,release,Path(result['evidence_archive']),work)
    # Final evidence references use a stable cloud tag before the snapshot is published.
    evidence_release=s.api('POST',s.base+'/releases',dict(tag_name=result['evidence_tag'],target_commitish=REF,name='Preserved daily-controller raw sources',body='Unresolved evidence retained; not automatic cleanup eligible.',draft=True));upload(s,evidence_release,Path(result['evidence_archive']),work/'evidence-readback' if False else work/'more') if False else None
    s.api('PATCH',s.base+'/releases/'+str(evidence_release['id']),dict(body='Raw source asset '+str(evidence['asset_id'])+' in release-readiness-'+os.environ['GITHUB_RUN_ID']+'; evidence retained without cleanup.',draft=False))
    snapshot(work/'working.sqlite',work/'snapshot.sqlite');pack(work/'snapshot.sqlite',work/'snapshot.gz');head,pointer,manifest=publish_with_export_preflight(s,work/'snapshot.sqlite',work/'snapshot.gz',base,code,work,datetime.now(timezone.utc).date().isoformat())
    try:s.publish(work/'snapshot.sqlite',work/'snapshot.gz',base,code)
    except StaleWriter:pass
    else:raise RuntimeError('STALE_WRITER_NOT_REJECTED')
    assert prod.head()==production
    report=dict(status='ISOLATED_REAL_DAILY_CONTROLLER_EXECUTED',code_commit=code,data_head=head,pointer=pointer,daily=result,cohorts=cohorts,remaining_unknown_minutes=unknown,legacy_references_preserved=len(old_refs),canonical_players_preserved=True,monotonic=monotonic,evidence=evidence,manifest=manifest,production_pointer_unchanged=True,production_authority='DISABLED',website_publication='NOT_PERFORMED')
    write(work/'release-result.json',report);upload(s,release,work/'release-result.json',work);upload(s,release,work/'site/data/browser.json',work);upload(s,release,work/'site/data/manifest.json',work);s.api('PATCH',s.base+'/releases/'+str(release['id']),dict(draft=False))
    with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('data_head='+head+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('daily','pointer')},ensure_ascii=False))

def recover():
    guard();work=ROOT/'.cloud/release-recovery';work.mkdir(parents=True,exist_ok=False);s=store(REF);prod=store('main');production=prod.head();head,pointer=s.restore(work/'current.sqlite');assert head==os.environ['EXPECTED_DATA_HEAD'];s.restore(work/'previous.sqlite',previous=True);code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();manifest=site(work,work/'current.sqlite',head,pointer,code)
    # Rollback rehearsal on a new isolated ref, using the actual production backup, without changing main.
    prod.restore(work/'production-backup.sqlite');prod.restore(work/'production-previous.sqlite',previous=True);ref='acceptance/rollback-'+os.environ['GITHUB_RUN_ID'];s.api('POST',s.base+'/git/refs',dict(ref='refs/heads/'+ref,sha=production[0]));rollback=store(ref)
    pack(work/'current.sqlite',work/'candidate.gz');rh,rp=rollback.publish(work/'current.sqlite',work/'candidate.gz',production[0],code);pack(work/'production-backup.sqlite',work/'rollback.gz');rh,rp=rollback.publish(work/'production-backup.sqlite',work/'rollback.gz',rh,production[1]['current']['code_commit']);rollback.restore(work/'rollback-check.sqlite');assert sha(work/'rollback-check.sqlite')==production[1]['current']['database']['sha256'];assert prod.head()==production
    report=dict(status='FRESH_RECOVERY_VERSION_GATE_AND_ROLLBACK_REHEARSAL_PASS',data_head=head,version=manifest['version'],current_and_previous_restored=True,rollback_rehearsal_ref=ref,rollback_sqlite_sha256=sha(work/'rollback-check.sqlite'),rollback_code_commit=production[1]['current']['code_commit'],production_pointer_unchanged=True,public_website_published=False)
    write(work/'recovery-result.json',report);release=s.api('GET',s.base+'/releases/tags/release-readiness-'+os.environ['GITHUB_RUN_ID']);upload(s,release,work/'recovery-result.json',work)
    print(json.dumps(report))
if __name__=='__main__':recover() if '--recover' in __import__('sys').argv else producer()
