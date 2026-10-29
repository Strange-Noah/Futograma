"""Safe maintenance entry: default dry-run, only a cloud working copy may be modified."""
import argparse,json,sqlite3
from pathlib import Path
from ..core import ROOT
from .retention import cleanup

def main():
 p=argparse.ArgumentParser();p.add_argument('--database',required=True);p.add_argument('--apply',action='store_true');a=p.parse_args();
 if a.apply:raise SystemExit('FILESYSTEM_RETENTION_DISABLED;RUNNER_FILES_EXPIRE_WITH_JOB;LOCAL_DELETE_FORBIDDEN')
 database=(ROOT/a.database).resolve()
 if not database.is_relative_to((ROOT/'.cloud').resolve()):raise SystemExit('Only a non-authoritative .cloud working copy is accepted; never the formal database')
 db=sqlite3.connect(database)
 if not db.execute("SELECT 1 FROM sqlite_master WHERE name='cloud_temporary_files'").fetchone():raise SystemExit('No explicitly registered temporary files; refuse guessing by filename or mtime')
 result=cleanup(db,ROOT,apply=a.apply);db.close();print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
