"""Single daily controller used by production and isolated prelaunch acceptance."""
import json,os,sqlite3,types,hashlib,tarfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from .hosted_guard import require_hosted
from ..core import ROOT,BASE
from ..live_sources import Client,HOSTS
from ..source_redirects import fetch_public_redirects
from ..international_incremental import acquire
from ..minute_reconciliation import apply_existing

def bounds(last_completed,at,lookback=72):
    current=at.astimezone(timezone.utc);start=current-timedelta(hours=lookback)
    if last_completed:start=min(start,datetime.fromisoformat(last_completed.replace('Z','+00:00')))
    return start,current

def run(database,work,isolated=False,at=None):
    require_hosted();at=at or datetime.now(timezone.utc);work=Path(work)
    if isolated:
        if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='UNCONFIRMED' or not os.environ.get('FUTBOL_DATA_REF','').startswith('acceptance/'):raise RuntimeError('ISOLATED_REF_AND_DISABLED_AUTHORITY_REQUIRED')
    elif os.environ.get('FUTBOL_CLOUD_ENABLED')!='true' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='CONFIRMED':raise RuntimeError('PRODUCTION_AUTHORITY_REQUIRED')
    settings=json.loads((ROOT/'ops/cloud/runtime.json').read_text())
    if not isolated and at<datetime.fromisoformat(settings['first_formal_run']):raise RuntimeError('FIRST_FORMAL_RUN_NOT_DUE')
    db=sqlite3.connect(database);db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS pipeline_checkpoints(scope TEXT PRIMARY KEY,completed_at TEXT)')
    old=db.execute("SELECT completed_at FROM pipeline_checkpoints WHERE scope='international'").fetchone();start,end=bounds(old[0] if old else None,at,settings['lookback_hours']);db.commit();db.close()
    os.environ.update(FUTBOL_DATABASE_PATH=str(database),FUTBOL_SETTINGS_PATH=str(ROOT/'ops/cloud/runtime.json'),FUTBOL_RUN_ORIGIN='github_actions_isolated_prelaunch' if isolated else 'github_actions_automatic')
    client=Client('daily-national-'+os.environ['GITHUB_RUN_ID'],budget=750);captures=[]
    HOSTS.update({'www.mancity.com','www.arsenal.com','fcbayern.com','www.psg.fr','www.fcbarcelona.com','www.inter.it','www.fotmob.com'})
    def fetch(url,purpose):
        r,b=fetch_public_redirects(client,url,purpose);r=dict(r,provider='Sky' if 'www.skysports.com/' in url else 'FotMob');captures.append(r)
        if r['status']!='SUCCESS':raise ValueError('SOURCE_UNAVAILABLE:'+str(r.get('error') or 'HTTP_'+str(r.get('http_status'))))
        return r,b.decode('utf-8','replace')
    from ..roster_refresh import refresh
    from ..international_store import migrate
    from ..managed_roster import migrate as management_migrate,import_verified_history
    from ..pending_matches import migrate as retry_migrate
    db=sqlite3.connect(database);db.row_factory=sqlite3.Row;migrate(db);management_migrate(db);import_verified_history(db);retry_migrate(db)
    roster=refresh(db,fetch,end.date().isoformat(),'daily-evidence-'+os.environ['GITHUB_RUN_ID']);db.close()
    db=sqlite3.connect(database);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
    asset_ref='daily-evidence-'+os.environ['GITHUB_RUN_ID'];national=acquire(db,fetch,client,start.date().isoformat(),end.date().isoformat(),os.environ['GITHUB_RUN_ID'],asset_ref,not_before_utc=start.isoformat(),not_after_utc=end.isoformat())
    # Actual failed discoveries are durable tasks, so the scan checkpoint can advance without losing them.
    db.execute('INSERT OR REPLACE INTO pipeline_checkpoints VALUES(?,?)',('international',end.isoformat()));db.commit();db.close()
    from .. import live_ingest
    previous=live_ingest.DB;live_ingest.DB=Path(database)
    try:club_code=live_ingest.run(types.SimpleNamespace(preflight=False,recheck=False,budget=160,max_details=36,isolated_prelaunch=isolated))
    finally:live_ingest.DB=previous
    if client.records:
        from ..live_sources import OUT
        log=OUT/'http_requests.jsonl'
        if log.is_file():
            for line in log.read_text().splitlines():
                item=json.loads(line)
                if item.get('path') and item.get('sha256') and str(item.get('request_timestamp',''))>=str(at.date()):captures.append(item)
    archive=work/'daily-evidence.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        for item in captures:
            if not item.get('path'):continue
            raw=ROOT/item['path']
            if not raw.is_relative_to(ROOT):raise ValueError('EVIDENCE_OUTSIDE_RUNNER')
            if raw.is_file():
                assert hashlib.sha256(raw.read_bytes()).hexdigest()==item['sha256'];t.add(raw,arcname=item['path'])
                metadata=work/(item['sha256']+'.metadata.json')
                if not metadata.exists():
                    with metadata.open('x') as f:json.dump(item,f)
                    t.add(metadata,arcname='metadata/'+metadata.name)
    return dict(roster=roster,club_exit=club_code,national=national,evidence_archive=str(archive),evidence_tag=asset_ref,isolated=isolated,formal_ingestion=not isolated)
