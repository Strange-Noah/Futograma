"""Reparse the retained real daily club sources; never writes canonical facts."""
import os,json,tarfile,sqlite3
from pathlib import Path
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.quality import sha
from squad_pipeline.live_parse import catalogs,sky_detail,torly_detail
from squad_pipeline.identity_registry import lookup
require_hosted();s=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],'acceptance/production-release-20261030');work=ROOT/'.cloud/club-raw-replay';work.mkdir(parents=True,exist_ok=False)
r=s.api('GET',s.base+'/releases/tags/release-readiness-41080166166');a=next(a for a in r['assets'] if a['name']=='release-result.json');s.download(a['id'],work/'input.json');result=json.loads((work/'input.json').read_text());s.download(result['evidence']['asset_id'],work/'raw.tar.gz');assert sha(work/'raw.tar.gz')==result['evidence']['sha256'];s.restore(work/'source.sqlite');db=sqlite3.connect('file:'+str(work/'source.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');bodies=[]
with tarfile.open(work/'raw.tar.gz') as tar:
 members={m.name:m for m in tar.getmembers() if m.isfile()}
 for name,m in members.items():
  if not name.endswith('.metadata.json'):continue
  source=json.load(tar.extractfile(m));raw=tar.extractfile(members[source['path']]).read()
  import hashlib
  assert hashlib.sha256(raw).hexdigest()==source['sha256'];bodies.append((source,raw.decode('utf-8','replace')))
candidates={};errors=[];catalog_gaps=[]
for source,raw in bodies:
 if not source['purpose'].startswith('catalog_') or source['http_status']!=200:continue
 provider='Sky' if 'www.skysports.com/' in source['url'] else 'Torlytics';cid=int(source['purpose'].split('_')[-1])
 try:
  for candidate in catalogs(raw,provider,cid,'2026-10-30'):candidate['catalog_source']=source;candidates[candidate['url']]=candidate
 except Exception as exc:catalog_gaps.append(dict(url=source['url'],error=str(exc)))
parsed=[]
for source,raw in bodies:
 if source['purpose']!='match_detail' or source['http_status']!=200:continue
 candidate=candidates.get(source['url'],{});provider='Sky' if 'www.skysports.com/' in source['url'] else 'Torlytics'
 try:
  fixture,sheets=(sky_detail if provider=='Sky' else torly_detail)(raw,dict(source,catalog_candidate=candidate));unknown=[]
  for sheet in sheets:
   for player in sheet['players']:
    if lookup(db,sheet['club_id'],player) is None:unknown.append(dict(club_id=sheet['club_id'],name=player['name'],provider_key=player['provider_key']))
  parsed.append(dict(url=source['url'],match_id=fixture['match_id'],competition=fixture['competition'],sides=[fixture['home_team'],fixture['away_team']],unresolved_identities=unknown))
 except Exception as exc:errors.append(dict(url=source['url'],error=type(exc).__name__+':'+str(exc)))
db.close();report=dict(status='READ_ONLY_REAL_CLUB_REPLAY',parsed=parsed,errors=errors,catalog_source_gaps=catalog_gaps,canonical_facts_written=0)
with (work/'club-replay.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
r=s.api('POST',s.base+'/releases',dict(tag_name='club-replay-'+os.environ['GITHUB_RUN_ID'],target_commitish=os.environ['FUTBOL_DATA_REF'],name='Read-only retained club source replay',body='No production writes.',draft=True));a=s.upload(r['id'],work/'club-replay.json');s.download(a['id'],work/'verified.json');assert sha(work/'verified.json')==sha(work/'club-replay.json');s.api('PATCH',s.base+'/releases/'+str(r['id']),dict(draft=False));print(json.dumps(report,ensure_ascii=False))
assert not errors,'RETAINED_CLUB_PARSER_ERRORS'
