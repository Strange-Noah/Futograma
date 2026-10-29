"""Post-deployment readback: compare public JSON version with the already published data revision."""
import json,sys,time,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlsplit
url=sys.argv[1];expected=dict(data_commit=sys.argv[2],sqlite_sha256=sys.argv[3])
if urlsplit(url).scheme!='https':raise SystemExit('HTTPS Pages URL required')
for attempt in range(8):
 try:
  with urllib.request.urlopen(url.rstrip('/')+'/data/manifest.json',timeout=20) as r:actual=json.loads(r.read(1000000))['version']
  if all(actual.get(k)==v for k,v in expected.items()):print('Published Pages manifest matches verified SQLite/data commit');break
 except (urllib.error.URLError,ValueError,KeyError):pass
 if attempt==7:raise SystemExit('Pages readback did not match the published database version')
 time.sleep(10)
