"""Reject production execution outside a GitHub-hosted Linux checkout."""
import os,platform,sqlite3
from pathlib import Path
from ..core import ROOT

def require_hosted():
 workspace=os.environ.get('GITHUB_WORKSPACE')
 temporary=os.environ.get('RUNNER_TEMP')
 if (os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_ENVIRONMENT')!='github-hosted'
     or platform.system()!='Linux' or not workspace or not temporary
     or Path(workspace).resolve()!=ROOT.resolve()
     or not Path(temporary).resolve().is_relative_to(Path('/home/runner'))
     or not ROOT.resolve().is_relative_to(Path('/home/runner/work'))):
  raise RuntimeError('GITHUB_HOSTED_RUNNER_REQUIRED;LOCAL_FILESYSTEM_ACCESS_DENIED')
 return ROOT.resolve()

def require_cloud_database(db):
 root=require_hosted();filename=next((r[2] for r in db.execute('PRAGMA database_list') if r[1]=='main'),None)
 if not filename or not Path(filename).resolve().is_relative_to(root/'.cloud'):
  raise RuntimeError('ONLY_GITHUB_RUNNER_WORKING_DATABASE_MAY_BE_COMPACTED')

def require_mode(mode):
 require_hosted()
 if mode in ('update','recover'):
  if os.environ.get('FUTBOL_CLOUD_ENABLED')!='true' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='CONFIRMED':
   raise RuntimeError('INGESTION_DISABLED_UNTIL_REMOTE_ACCEPTANCE_AND_EXPLICIT_HANDOVER')
 else:
  if os.environ.get('FUTBOL_REMOTE_ACCEPTANCE_ENABLED')!='true' and os.environ.get('FUTBOL_CLOUD_ENABLED')!='true':
   raise RuntimeError('OWNER_REMOTE_ACCEPTANCE_OR_PRODUCTION_ENABLE_REQUIRED')
