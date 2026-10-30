"""Cloud workflow controller. No authoritative database is changed by local prepare/smoke/preflight."""
import argparse,json,os,sqlite3,sys,types,uuid
from pathlib import Path
from datetime import datetime,timezone
from .hosted_guard import require_mode
from .quality import inspect,snapshot,pack,readonly
from .storage import LocalStore
from .github_store import GitHubStore
from .retention import migrate,bootstrap_indexes,capture_run,cleanup,register,compact_operational_logs
from .export import export
from ..core import ROOT,BASE
RUNTIME=ROOT/'ops/cloud/runtime.json'
def write(path,obj):Path(path).parent.mkdir(parents=True,exist_ok=True);Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def formal_due(at=None):return (at or datetime.now(timezone.utc))>=datetime.fromisoformat(json.loads(RUNTIME.read_text())['first_formal_run'])
def workspace(path):
 p=Path(path).resolve()
 if not p.is_relative_to((ROOT/'.cloud').resolve()):raise ValueError('CLOUD_WORKSPACE_MUST_BE_UNDER_PROJECT_DOT_CLOUD')
 if p.exists() and any(p.iterdir()):raise ValueError('NEW_EMPTY_WORKSPACE_REQUIRED')
 p.mkdir(parents=True,exist_ok=True);return p

def seed(source,work):
 dest=work/'seed.sqlite';before=snapshot(source,dest);db=sqlite3.connect(dest);db.row_factory=sqlite3.Row;migrate(db);index=bootstrap_indexes(db,ROOT);db.close();after=inspect(dest)
 if before['tables']!=after['tables']:raise ValueError('SEED_FACTS_CHANGED')
 compressed=work/'database.sqlite.gz';pack(dest,compressed);result=dict(mode='LOCAL_PREPARATION_NOT_AUTHORITY',database=str(dest.relative_to(ROOT)),compressed=str(compressed.relative_to(ROOT)),facts_unchanged=True,source_index=index,metadata=after);write(work/'seed_manifest.json',result);return dest,compressed,result

def validate_monotonic(old_path,new_path):
 before=readonly(old_path);after=readonly(new_path)
 try:
  old={tuple(r[:3]):r for r in before.execute('SELECT club_id,match_id,player_id,participation_status,minutes,revision FROM player_match_minutes')};new={tuple(r[:3]):r for r in after.execute('SELECT club_id,match_id,player_id,participation_status,minutes,revision FROM player_match_minutes')}
  if old.keys()-new.keys():raise ValueError('EXISTING_FACT_REMOVED')
  corrections=0
  for key,row in old.items():
   updated=new[key]
   if row[3:5]==updated[3:5]:continue
   if updated[5]<=row[5] or not after.execute("SELECT 1 FROM validation_fact_revisions WHERE club_id=? AND match_id=? AND player_id=? AND reason='LIVE_VALIDATED_CORROBORATED_CORRECTION'",key).fetchone():raise ValueError('UNEXPLAINED_FACT_OVERWRITE')
   corrections+=1
  if list(before.execute('SELECT * FROM validation_national_reference ORDER BY player_id,match_id'))!=list(after.execute('SELECT * FROM validation_national_reference ORDER BY player_id,match_id')):raise ValueError('NATIONAL_REFERENCE_CHANGED_BY_CLUB_COLLECTION')
  return dict(prior_facts_preserved=True,old_rows=len(old),new_rows=len(new),corrections=corrections)
 finally:before.close();after.close()

def configure_clubs(database):
 from ..live_sources import CLUB_FEEDS
 from ..six_club_validation import CLUBS
 rows=json.loads((ROOT/'ops/cloud/clubs.json').read_text());chosen=[r for r in rows if r.get('enabled')];ids=[r['club_id'] for r in chosen]
 if not chosen or len(ids)!=len(set(ids)):raise ValueError('INVALID_EXPLICIT_CLUB_SCOPE')
 import re
 for r in chosen:
  if not isinstance(r['club_id'],int) or r['club_id']<=0 or not all(re.fullmatch('[a-z0-9-]+',r[k]) for k in ['torlytics_slug','sky_slug']):raise ValueError('INVALID_CLUB_PROVIDER_CONFIGURATION')
 CLUB_FEEDS.clear();CLUB_FEEDS.update({r['club_id']:(r['torlytics_slug'],r['sky_slug']) for r in chosen});CLUBS.clear();CLUBS.update({r['club_id']:(r['name'],r['canonical_slug']) for r in chosen})
 db=sqlite3.connect(database)
 for r in chosen:db.execute('INSERT OR IGNORE INTO clubs VALUES(?,?,?,NULL)',(r['club_id'],r['canonical_slug'],r['name']))
 db.commit();db.close();return len(chosen)

def get_store(backend,work):
 if backend=='local':return LocalStore(work/'sandbox-store')
 return GitHubStore(os.environ.get('FUTBOL_DATA_REPOSITORY',''),os.environ.get('FUTBOL_DATA_TOKEN',''),os.environ.get('FUTBOL_DATA_REF','main'))
def output(work,result):
 write(work/'result.json',result)
 path=os.environ.get('GITHUB_OUTPUT')
 if path:
  with Path(path).open('a') as f:
   f.write('publish_site='+str(bool(result.get('publish_site'))).lower()+'\n');f.write('status='+result['status']+'\n');f.write('site_path='+str(work/'site')+'\n')
   version=result.get('data_version') or result.get('version') or {}
   f.write('data_commit='+version.get('data_commit','')+'\n');f.write('sqlite_sha256='+version.get('sqlite_sha256','')+'\n')
 print(json.dumps(result,ensure_ascii=False,indent=2));return result

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['prepare','smoke','preflight','update','bootstrap','site','restore','recover']);parser.add_argument('--backend',choices=['local','github'],default='local');parser.add_argument('--source');parser.add_argument('--expected-data-head');parser.add_argument('--previous',action='store_true');parser.add_argument('--work',default='.cloud/work/'+uuid.uuid4().hex);parser.add_argument('--prune-managed-releases',action='store_true');args=parser.parse_args()
 if args.mode not in ['prepare','smoke']:require_mode(args.mode)
 work=workspace(ROOT/args.work)
 if args.mode=='update' and not formal_due():return output(work,dict(status='WAITING_FOR_FIRST_FORMAL_RUN',first_formal_run='2026-10-31T16:00:00+08:00',requests=0,publish_site=False))
 if args.mode=='recover':
  if args.backend!='github' or os.environ.get('FUTBOL_RECOVERY_CONFIRMATION')!='PROMOTE_PREVIOUS':raise ValueError('EXPLICIT_CLOUD_RECOVERY_CONFIRMATION_REQUIRED')
  if not args.expected_data_head:raise ValueError('REVIEWED_CURRENT_DATA_HEAD_REQUIRED')
  store=get_store(args.backend,work);dbpath=work/'recovery.sqlite';head,pointer=store.restore(dbpath,previous=True)
  if head!=args.expected_data_head:raise ValueError('RECOVERY_REVIEW_BASE_CHANGED')
  zipped=work/'database.sqlite.gz';pack(dbpath,zipped)
  # Re-upload the verified previous DB twice: both current and required previous become verified recovery copies.
  new,pointer=store.publish(dbpath,zipped,head,os.environ.get('GITHUB_SHA',''))
  new,pointer=store.publish(dbpath,zipped,new,os.environ.get('GITHUB_SHA',''))
  manifest=export(dbpath,work/'site',new,pointer,ROOT/'web')
  return output(work,dict(status='CLOUD_RECOVERY_PUBLISHED_AND_VERIFIED',publish_site=True,formal_ingestion=False,data_version=manifest['version']))
 if args.mode=='restore':
  if args.backend!='github':raise ValueError('EXPLICIT_GITHUB_STORE_REQUIRED_FOR_MANUAL_RESTORE')
  store=get_store(args.backend,work);head,pointer=store.restore(work/'restored.sqlite',previous=args.previous);selected=pointer['previous'] if args.previous else pointer['current'];return output(work,dict(status='PREVIOUS_SNAPSHOT_RESTORED_FOR_REVIEW' if args.previous else 'CURRENT_SNAPSHOT_RESTORED_FOR_REVIEW',source_head=head,version=selected['version'],verified=True,publish_site=False,authority_changed=False))
 if args.mode in ['prepare','smoke','bootstrap']:
  if not args.source:raise ValueError('EXPLICIT_SEED_SOURCE_REQUIRED')
  if args.mode=='bootstrap' and (args.backend!='github' or os.environ.get('FUTBOL_REMOTE_BOOTSTRAP_CONFIRMED')!='true'):raise ValueError('REMOTE_BOOTSTRAP_REQUIRES_EXPLICIT_CONFIRMATION')
  dbpath,zipped,prepared=seed(ROOT/args.source,work)
  if args.mode=='prepare':return output(work,dict(status='LOCAL_SEED_PREPARED',details=prepared,publish_site=False))
  store=get_store(args.backend,work);head,_=store.head()
  if args.mode=='bootstrap':
   new,pointer=store.publish(dbpath,zipped,head,os.environ.get('GITHUB_SHA','USER_CONFIRMED_BOOTSTRAP'),allow_bootstrap=True)
   # Two independently uploaded initial seed snapshots provide immediate previous-version recovery, without collecting any match.
   new,pointer=store.publish(dbpath,zipped,new,os.environ.get('GITHUB_SHA','USER_CONFIRMED_BOOTSTRAP'))
  else:new,pointer=store.publish(dbpath,zipped,head,'LOCAL_SANDBOX_NOT_GITHUB')
  restored=work/'restored.sqlite';store.restore(restored);manifest=export(restored,work/'site',new,pointer,ROOT/'web');return output(work,dict(status='LOCAL_SMOKE_PASSED' if args.mode=='smoke' else 'BOOTSTRAPPED',restored=True,facts_unchanged=True,data_version=manifest['version'],publish_site=False))
 if args.backend!='github':raise ValueError('PRODUCTION_OR_CLOUD_PREFLIGHT_REQUIRES_GITHUB_STORE;LOCAL_SMOKE_IS_NOT_CLOUD')
 store=get_store(args.backend,work);restored=work/'restored.sqlite';base,pointer=store.restore(restored)
 if args.mode=='site':
  manifest=export(restored,work/'site',base,pointer,ROOT/'web');return output(work,dict(status='EXISTING_PUBLISHED_SNAPSHOT_EXPORTED',publish_site=True,version=manifest['version']))
 working=work/'working.sqlite';snapshot(restored,working);os.environ['FUTBOL_DATABASE_PATH']=str(working);os.environ['FUTBOL_SETTINGS_PATH']=str(RUNTIME);os.environ['FUTBOL_RUN_ORIGIN']='github_actions_preflight' if args.mode=='preflight' else 'github_actions_automatic';os.environ['PYTHONDONTWRITEBYTECODE']='1'
 club_count=configure_clubs(working)
 from .. import live_ingest
 # Explicitly pass a working-copy path even if this module is imported in a longer-lived process.
 if args.mode=='preflight':
  original=live_ingest.DB;live_ingest.DB=working
  try:code=live_ingest.run(types.SimpleNamespace(preflight=True,recheck=False,budget=max(64,4*club_count+32),max_details=club_count))
  finally:live_ingest.DB=original
 else:
  from .incremental import run as daily_run
  daily=daily_run(working,work);code=daily['club_exit']
  # Necessary unresolved/raw evidence is persisted before a candidate snapshot can become authoritative.
  evidence_release=store.api('POST',store.base+'/releases',dict(tag_name=daily['evidence_tag'],target_commitish=store.ref,name='Daily preserved source evidence',body='Required source evidence; unresolved evidence must not be pruned.',draft=True))
  uploaded=store.upload(evidence_release['id'],Path(daily['evidence_archive']));store.download(uploaded['id'],work/'evidence-readback.tar.gz')
  from .quality import sha
  if sha(work/'evidence-readback.tar.gz')!=sha(Path(daily['evidence_archive'])):raise ValueError('EVIDENCE_UPLOAD_READBACK_MISMATCH')
  store.api('PATCH',store.base+'/releases/'+str(evidence_release['id']),dict(draft=False))
 monotonic=validate_monotonic(restored,working)
 if args.mode=='preflight':
  if inspect(restored)['tables']!=inspect(working)['tables']:raise ValueError('PREFLIGHT_CHANGED_PERMANENT_FACTS')
  return output(work,dict(status='CLOUD_REAL_NETWORK_PREFLIGHT_PASSED' if code==0 else 'CLOUD_PREFLIGHT_INCOMPLETE',network_exit=code,facts_unchanged=True,publish_site=False))
 db=sqlite3.connect(working);db.row_factory=sqlite3.Row;migrate(db);latest=json.loads((BASE/'audit/live_deployment/latest_run.json').read_text());records=[];log=BASE/'audit/live_deployment/http_requests.jsonl'
 if log.exists():
  for line in log.read_text().splitlines():
   record=json.loads(line)
   if record.get('run_id')==latest['run_id']:records.append(record)
 capture_run(db,ROOT,records);bootstrap_indexes(db,ROOT)
 run_log=ROOT/'logs/live'/(latest['run_id']+'.json')
 if run_log.is_file():register(db,ROOT,str(run_log.relative_to(ROOT)),'RUN_LOG',latest['finished_at'],declared_temporary=True);db.commit()
 retention=cleanup(db,ROOT,apply=False);retention['filesystem_policy']='NO_DELETION;GITHUB_RUNNER_FILES_EXPIRE_WITH_JOB';retention['operational_compaction']=compact_operational_logs(db);write(work/'retention.json',retention);db.close();candidate=work/'candidate.sqlite';snapshot(working,candidate);zipped=work/'database.sqlite.gz';pack(candidate,zipped);from .export_publish import publish_with_export_preflight;new,pointer,manifest=publish_with_export_preflight(store,candidate,zipped,base,os.environ.get('GITHUB_SHA','UNKNOWN_CODE_COMMIT'),work,datetime.now(timezone.utc).astimezone(__import__('zoneinfo').ZoneInfo('Europe/Madrid')).date().isoformat(),acceptance=False);pruning=store.prune(apply=args.prune_managed_releases);return output(work,dict(status='VALID_SNAPSHOT_AND_SITE_EXPORTED' if code==0 else 'VALID_SNAPSHOT_EXPORTED_WITH_PENDING_NETWORK_TASKS',publish_site=True,network_exit=code,validation=monotonic,data_version=manifest['version'],release_retention=pruning))
if __name__=='__main__':
 result=main()
 if result.get('status')=='CLOUD_PREFLIGHT_INCOMPLETE':raise SystemExit(2)
