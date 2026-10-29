"""Explicitly registered transient files only. No archive traversal, facts deletion, or age-only guesswork."""
import sqlite3,json,gzip,hashlib,re
from pathlib import Path
from datetime import datetime,timezone,timedelta
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from .quality import sha
DDL=[
'CREATE TABLE IF NOT EXISTS cloud_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL)',
'CREATE TABLE IF NOT EXISTS cloud_documents(document_id TEXT PRIMARY KEY,url TEXT,provider TEXT,provider_id TEXT,body_sha256 TEXT,acquired_at TEXT,http_status INTEGER,path_hint TEXT)',
'CREATE TABLE IF NOT EXISTS cloud_temporary_files(path TEXT PRIMARY KEY,sha256 TEXT,size INTEGER,created_at TEXT,kind TEXT,document_id TEXT,state TEXT DEFAULT \'PRESENT\')',
'CREATE TABLE IF NOT EXISTS cloud_evidence_blobs(payload_sha256 TEXT PRIMARY KEY,payload_gzip BLOB NOT NULL)',
'CREATE TABLE IF NOT EXISTS cloud_evidence_holds(hold_id TEXT PRIMARY KEY,document_id TEXT,reference_kind TEXT,reference_key TEXT,reason TEXT,payload_gzip BLOB,payload_sha256 TEXT,resolved INTEGER DEFAULT 0,created_at TEXT)']
ALLOWED=('squad_pipeline/raw/live/','logs/live/')
def now():return datetime.now(timezone.utc).isoformat()
def migrate(db):
 if db.in_transaction:raise ValueError('MIGRATION_REQUIRES_OWN_TRANSACTION')
 try:
  db.execute('BEGIN')
  for ddl in DDL:db.execute(ddl)
  db.execute('INSERT OR IGNORE INTO cloud_migrations VALUES(1,?)',(now(),));db.commit()
 except Exception:db.rollback();raise

def safe(root,relative):
 root=Path(root).resolve();p=Path(relative)
 if p.is_absolute() or '..' in p.parts or not str(p).startswith(ALLOWED):raise ValueError('UNREGISTERABLE_PATH')
 if any(part in {'archive','.git'} for part in p.parts):raise ValueError('ARCHIVE_OR_GIT_FORBIDDEN')
 if p.suffix.lower() in {'.sqlite','.sqlite3','.db'} or p.name.endswith(('-wal','-shm','-journal')):raise ValueError('DATABASE_NEVER_TEMPORARY')
 actual=root/p
 if actual.is_symlink() or not actual.resolve().is_relative_to(root):raise ValueError('SYMLINK_OR_EXTERNAL_PATH')
 allowed_root=next(root/x for x in ALLOWED if str(p).startswith(x))
 if not actual.resolve().is_relative_to(allowed_root.resolve()):raise ValueError('TEMPORARY_ROOT_ESCAPE')
 return actual

def clean_url(url):
 p=urlsplit(url)
 if p.scheme!='https' or not p.netloc or p.username or p.password:return None
 q=[(k,'REDACTED' if re.search('token|secret|api.?key|signature|credential',k,re.I) else v) for k,v in parse_qsl(p.query,keep_blank_values=True)]
 return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),''))
def index_document(db,record):
 url=clean_url(record.get('url',''))
 if not url:return None
 sha_=record.get('reused_body_sha256') or record.get('sha256') or record.get('body_sha256');time_=record.get('acquired_at') or record.get('response_timestamp') or record.get('request_timestamp') or record.get('fetched_at') or record.get('captured_at') or record.get('observed_at');path=record.get('reused_body_path') or record.get('path');key=hashlib.sha256(json.dumps([url,sha_,time_],ensure_ascii=False).encode()).hexdigest();provider=record.get('provider') or urlsplit(url).hostname;pid=record.get('provider_match_id') or record.get('provider_player_url') or record.get('provider_key');db.execute('INSERT OR IGNORE INTO cloud_documents VALUES(?,?,?,?,?,?,?,?)',(key,url,provider,str(pid) if pid is not None else None,sha_,time_,record.get('http_status'),path));return key

def register(db,root,path,kind,created_at,document_id=None,declared_temporary=False):
 if not declared_temporary:raise ValueError('TEMPORARY_CLASSIFICATION_REQUIRED')
 if kind not in {'RAW_RESPONSE','RESPONSE_METADATA','RUN_LOG','REGENERABLE_JSON'}:raise ValueError('UNSUPPORTED_TEMPORARY_KIND')
 p=safe(root,path)
 if not p.is_file() or p.stat().st_nlink!=1:raise ValueError('NOT_OWNED_REGULAR_FILE')
 db.execute('INSERT INTO cloud_temporary_files VALUES(?,?,?,?,?,?,\'PRESENT\') ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,size=excluded.size,created_at=excluded.created_at,kind=excluded.kind,document_id=excluded.document_id,state=\'PRESENT\'',(path,sha(p),p.stat().st_size,created_at,kind,document_id))

def pin(db,root,document_id,record,kind,key,reason):
 path=record.get('reused_body_path') or record.get('path');payload=json.dumps(record,ensure_ascii=False).encode();representation='SOURCE_METADATA_ONLY;RAW_NOT_LOCALLY_AVAILABLE'
 if path and 'archive' not in Path(path).parts:
  p=Path(path);p=p if p.is_absolute() else Path(root)/p
  if p.resolve().is_relative_to(Path(root).resolve()) and p.is_file() and not p.is_symlink() and not p.resolve().is_relative_to((Path(root)/'archive').resolve()):
   body=p.read_bytes()
   if len(body)>6000000:raise ValueError('HELD_EVIDENCE_TOO_LARGE')
   expected=record.get('reused_body_sha256') or record.get('sha256')
   if expected and hashlib.sha256(body).hexdigest()!=expected:raise ValueError('EVIDENCE_HOLD_HASH_MISMATCH')
   payload=body;representation='ORIGINAL_BODY_HASH_VERIFIED'
 body_sha=hashlib.sha256(payload).hexdigest();db.execute('INSERT OR IGNORE INTO cloud_evidence_blobs VALUES(?,?)',(body_sha,gzip.compress(payload,mtime=0)));hold=hashlib.sha256(json.dumps([document_id,kind,str(key)],ensure_ascii=False).encode()).hexdigest();db.execute('INSERT OR IGNORE INTO cloud_evidence_holds(hold_id,document_id,reference_kind,reference_key,reason,payload_gzip,payload_sha256,created_at) VALUES(?,?,?,?,?,?,?,?)',(hold,document_id,kind,str(key),reason+';'+representation,None,body_sha,now()))

def bootstrap_indexes(db,root):
 """Lift lightweight source dictionaries already stored in SQLite; never read archive or rescan the filesystem."""
 tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'cloud_%'")];count=0
 def walk(node,held=None):
  nonlocal count
  if isinstance(node,dict):
   if isinstance(node.get('url'),str):
    document=index_document(db,node)
    if document:count+=1
    if document and held:pin(db,root,document,node,*held)
   for v in node.values():
    if isinstance(v,(dict,list)):walk(v,held)
  elif isinstance(node,list):
   for v in node:walk(v,held)
 for table in tables:
  columns=[r[1] for r in db.execute('PRAGMA table_info("'+table+'")') if r[2].upper() in ['TEXT','JSON',''] and r[1].endswith(('_json','json'))]
  for col in columns:
   for rowid,payload in db.execute('SELECT rowid,"'+col+'" FROM "'+table+'"'):
    if not isinstance(payload,str) or not payload.lstrip().startswith(('{','[')):continue
    try:obj=json.loads(payload)
    except ValueError:continue
    held=None
    if table=='player_eligibility':
     state=db.execute('SELECT verification_status FROM player_eligibility WHERE rowid=?',(rowid,)).fetchone()[0]
     if state!='VERIFIED':held=('UNVERIFIED_MEMBERSHIP',str(rowid),'Unresolved affiliation or promotion evidence')
    elif table=='live_conflicts':
     if not db.execute('SELECT resolved FROM live_conflicts WHERE rowid=?',(rowid,)).fetchone()[0]:held=('SOURCE_CONFLICT',str(rowid),'Unresolved source or identity conflict')
    walk(obj,held)
 db.commit();return dict(source_observations_seen=count,unique_documents=db.execute('SELECT COUNT(*) FROM cloud_documents').fetchone()[0],unresolved_holds=db.execute('SELECT COUNT(*) FROM cloud_evidence_holds WHERE resolved=0').fetchone()[0])

def capture_run(db,root,records):
 statuses={r[0]:r[1] for r in db.execute("SELECT url,status FROM live_tasks WHERE status NOT IN ('NEW_MATCH','UPDATED_MATCH','UNCHANGED')")}
 for r in records:
  if r.get('transport_mode')=='TEST_TRANSPORT':continue
  document=index_document(db,r)
  if not document or not r.get('path'):continue
  captured=r.get('response_timestamp') or r.get('request_timestamp')
  for path,kind in [(r['path'],'RAW_RESPONSE'),(str(Path(r['path']).with_suffix('.metadata.json')),'RESPONSE_METADATA')]:
   if (Path(root)/path).is_file():register(db,root,path,kind,captured,document,True)
  if statuses.get(r['url']) in ['PENDING','VALIDATION_FAILED']:pin(db,root,document,r,'FAILED_MATCH_TASK',r['url'],statuses[r['url']])
 db.commit()

def cleanup(db,root,apply=False,at=None,days=7):
 if apply:raise RuntimeError('FILESYSTEM_RETENTION_DISABLED;LOCAL_DELETE_FORBIDDEN')
 at=at or datetime.now(timezone.utc);cut=at-timedelta(days=days);result=dict(mode='APPLY' if apply else 'DRY_RUN',older_than=cut.isoformat(),candidates=[],deleted=[],protected=[],skipped=[])
 for row in list(db.execute("SELECT path,sha256,created_at,document_id FROM cloud_temporary_files WHERE state='PRESENT'")):
  path,expected,created,document=row
  try:p=safe(root,path)
  except ValueError as e:result['protected'].append(dict(path=path,reason=str(e)));continue
  if db.execute('SELECT 1 FROM cloud_evidence_holds WHERE document_id=? AND resolved=0',(document,)).fetchone():result['protected'].append(dict(path=path,reason='UNRESOLVED_EVIDENCE_HOLD'));continue
  if not created or datetime.fromisoformat(created.replace('Z','+00:00'))>=cut:continue
  if not p.exists():result['skipped'].append(dict(path=path,reason='ALREADY_ABSENT'));continue
  if not p.is_file() or p.stat().st_nlink!=1 or sha(p)!=expected:result['protected'].append(dict(path=path,reason='TYPE_OWNERSHIP_OR_HASH_CHANGED'));continue
  result['candidates'].append(path)
 if apply:db.commit()
 return result

def compact_operational_logs(db,at=None):
 """Keep lightweight provenance receipts; expire only replaceable old run-log detail and accepted staging copies."""
 from .hosted_guard import require_cloud_database
 require_cloud_database(db)
 at=at or datetime.now(timezone.utc);cut=at-timedelta(days=7);runs=0;staging=0
 for run_id,started,payload in list(db.execute('SELECT run_id,started_at,payload_json FROM live_runs')):
  if not started or datetime.fromisoformat(started.replace('Z','+00:00'))>=cut:continue
  p=json.loads(payload or '{}')
  if p.get('compacted'):continue
  result_rows=[]
  for r in p.get('results',[]):
   row={k:r[k] for k in ['club_id','match_id','match_date','status','error','repeat_apply'] if k in r};source=r.get('source') or {};row['source']={k:source[k] for k in ['url','sha256','acquired_at'] if k in source};result_rows.append(row)
  mini={k:p[k] for k in ['run_id','origin','started_at','finished_at','as_of','preflight','status','status_counts','requests','http_success','first_formal_run','lookback_hours'] if k in p};mini.update(compacted=True,source_index='cloud_documents',results=result_rows,catalogs=[{k:r[k] for k in ['club_id','provider','status','discovered'] if k in r} for r in p.get('catalogs',[])],coverage_limitations=p.get('coverage_limitations',[]));db.execute('UPDATE live_runs SET payload_json=? WHERE run_id=?',(json.dumps(mini,ensure_ascii=False),run_id));runs+=1
 if db.execute("SELECT 1 FROM sqlite_master WHERE name='live_pending_sheets'").fetchone():
  for tid,mid,provider,version,payload,staged in list(db.execute('SELECT * FROM live_pending_sheets')):
   if datetime.fromisoformat(staged.replace('Z','+00:00'))>=cut:continue
   source=json.loads(payload)['source'];url=source['url']
   if db.execute("SELECT 1 FROM live_tasks WHERE url=? AND status NOT IN ('NEW_MATCH','UPDATED_MATCH','UNCHANGED')",(url,)).fetchone():continue
   if db.execute('SELECT 1 FROM live_conflicts WHERE club_id=? AND match_id=? AND resolved=0',(tid,mid)).fetchone():continue
   if not db.execute('SELECT 1 FROM live_sheets WHERE club_id=? AND match_id=? AND provider=? AND version_sha=?',(tid,mid,provider,version)).fetchone():continue
   db.execute('DELETE FROM live_pending_sheets WHERE club_id=? AND match_id=? AND provider=? AND version_sha=?',(tid,mid,provider,version));staging+=1
 db.commit();return dict(compacted_run_details=runs,removed_accepted_staging_duplicates=staging,football_facts_deleted=0)
