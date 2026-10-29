"""Fail CI if archive, databases, credentials or local evidence entered the code repository."""
import subprocess,re
from pathlib import PurePosixPath
files=subprocess.check_output(['git','ls-files','-z'],text=True).split('\0');bad=[]
for name in files:
 if not name:continue
 p=PurePosixPath(name);parts=p.parts
 forbidden=parts[0] in {'archive','.venv','.cloud','raw','reference','data','outputs','logs','official_registration_experiment_2025_26'} or any(x in parts for x in ['__pycache__','node_modules','.git']) or re.search(r'\.(?:sqlite(?:3)?|db)(?:-|$)|\.pyc$|(?:^|/)\.env(?:\.|$)',name) or name.startswith(('squad_pipeline/raw/','squad_pipeline/audit/','squad_pipeline/state/','squad_pipeline/imports/','squad_pipeline/manual_inputs/','ops/live/'))
 if forbidden:bad.append(name)
if bad:raise SystemExit('Forbidden tracked paths: '+', '.join(bad))
print('Tracked-path policy passed; no archive, SQLite, raw cache, local credentials or environment')
