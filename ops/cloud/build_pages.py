import json,shutil,subprocess,os
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.core import ROOT
require_hosted();site=ROOT/'private-site';p=json.loads((ROOT/'private-pointer/current.json').read_text());m=json.loads((site/'data/manifest.json').read_text());head=subprocess.check_output(['git','-C','private-pointer','rev-parse','HEAD'],text=True).strip()
assert m['version']['sqlite_sha256']==p['current']['database']['sha256'],'DATABASE_HASH_MISMATCH'
assert m['version']['data_version']==p['current']['version'],'DATA_VERSION_MISMATCH'
assert m['version']['data_commit']==head,'DATA_COMMIT_MISMATCH'
out=ROOT/'.cloud/pages';out.mkdir(parents=True,exist_ok=False)
for x in (site/'data').rglob('*.json'):
 target=out/x.relative_to(site);target.parent.mkdir(parents=True,exist_ok=True)
 with target.open('xb') as f:f.write(x.read_bytes())
for name in ['index.html','app.js','style.css']:shutil.copyfile(ROOT/'web'/name,out/name)
(out/'.nojekyll').touch()
with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('data_commit='+head+'\nsqlite_sha256='+m['version']['sqlite_sha256']+'\n')
print(json.dumps(m['version']))
