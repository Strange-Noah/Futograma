"""Read-only quality checks, SQLite backup snapshots, bounded release metadata."""
import sqlite3,hashlib,json,gzip,shutil
from pathlib import Path
from datetime import datetime,timezone
REQUIRED={'clubs','periods','competitions','players','matches','squad_observations','player_match_minutes','validation_player_aliases','validation_match_aliases','validation_roster_membership','validation_national_reference','player_affiliations','player_eligibility','validation_fact_revisions','validation_match_sheets','validation_source_evidence'}
FACT_TABLES=sorted(REQUIRED-{'periods','competitions'})
def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def readonly(path):return sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True)
def inspect(path,expected=None):
 db=readonly(path)
 try:
  if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('SQLITE_INTEGRITY_FAILED')
  if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('SQLITE_FOREIGN_KEYS_FAILED')
  tables={x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
  if REQUIRED-tables:raise ValueError('MISSING_REQUIRED_TABLES:'+','.join(sorted(REQUIRED-tables)))
  checks={}
  for t in FACT_TABLES:
   h=hashlib.sha256();count=0
   for r in db.execute('SELECT * FROM "'+t+'" ORDER BY rowid'):
    h.update(json.dumps(list(r),ensure_ascii=False,separators=(',',':')).encode());h.update(b'\n');count+=1
   checks[t]=dict(rows=count,sha256=h.hexdigest())
  if db.execute("SELECT 1 FROM player_match_minutes WHERE minutes<0 OR minutes>130 OR (participation_status IN ('UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD') AND minutes IS NOT 0) LIMIT 1").fetchone():raise ValueError('INVALID_PARTICIPATION_MINUTES')
  if db.execute("SELECT 1 FROM squad_observations GROUP BY club_id,match_id HAVING SUM(role='starters')<>11 LIMIT 1").fetchone():raise ValueError('INVALID_STARTING_XI_COUNT')
  schema=hashlib.sha256(json.dumps(list(db.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")),ensure_ascii=False).encode()).hexdigest()
  result=dict(sha256=sha(path),size=Path(path).stat().st_size,schema_sha256=schema,tables=checks,checked_at=datetime.now(timezone.utc).isoformat(),integrity='ok')
  if expected:
   for k in ['sha256','size','schema_sha256']:
    if k in expected and result[k]!=expected[k]:raise ValueError('SNAPSHOT_METADATA_MISMATCH:'+k)
  return result
 finally:db.close()
def snapshot(source,target):
 source,target=Path(source).resolve(),Path(target).resolve()
 if source==target or target.exists():raise ValueError('SNAPSHOT_MUST_BE_NEW_PATH')
 target.parent.mkdir(parents=True,exist_ok=True);a=readonly(source);b=sqlite3.connect(target)
 try:a.backup(b)
 finally:b.close();a.close()
 return inspect(target)
def pack(source,target):
 target=Path(target)
 if target.exists():raise ValueError('PACK_TARGET_EXISTS')
 with Path(source).open('rb') as src,target.open('xb') as dst:
  with gzip.GzipFile(filename='',fileobj=dst,mode='wb',mtime=0) as zipped:shutil.copyfileobj(src,zipped)
 return dict(sha256=sha(target),size=target.stat().st_size)
def unpack(source,target,max_bytes=1900000000):
 target=Path(target)
 if target.exists():raise ValueError('REFUSE_OVERWRITE_RESTORED_DATABASE')
 count=0
 try:
  with gzip.open(source,'rb') as src,target.open('xb') as dst:
   for b in iter(lambda:src.read(1048576),b''):
    count+=len(b)
    if count>max_bytes:raise ValueError('DATABASE_SIZE_LIMIT')
    dst.write(b)
  return inspect(target)
 except Exception:
  # Preserve even a newly created failed restore for diagnosis; never delete local files.
  raise
