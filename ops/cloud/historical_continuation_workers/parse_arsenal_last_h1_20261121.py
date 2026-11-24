import json,sys,re
from pathlib import Path
from dataclasses import replace,asdict
sys.path.insert(0,str(Path.cwd()))
from squad_pipeline.source_interface import SourceService,SourceRequest,RawSourceArtifact,EvidenceStore
from squad_pipeline.live_parse import Tree
from squad_pipeline.competition_identity import COMP
COMP['French Ligue 1']=53
root=globals().get('root',Path('.cloud/one-pass-arsenal-last-h1-native-20261121'));root.mkdir(exist_ok=False);s=SourceService(EvidenceStore(root,archives=[Path.cwd()]),'old-sky-native-batch',set(),max_calls=0);out=[];seen=set()
for p in globals().get('acquisition_roots',[Path('.cloud/one-pass-arsenal-last-h1-batch202-20261121')]):
 for row in json.load(open(p/'results-private.json')):
  a=row.get('artifact')
  if not a or a['content_sha256'] in seen:continue
  seen.add(a['content_sha256']);art=RawSourceArtifact(SourceRequest(**a['request']),a['content_sha256'],str((p/a['locator']).resolve()),a['metadata'],a['authority']);got=s.acquire(art.request,art);assert got.outcome=='FOUND';t=Tree(s.store.read(got.artifact).decode()).root;canonical=[x.attrs['href'] for x in t.all(tag='link') if x.attrs.get('rel')=='canonical']
  if canonical!=[row['url']]:out.append(dict(url=row['url'],state='HELD',reason='CANONICAL_URL_DIFFERENCE'));continue
  req=replace(art.request,expected_ids={'match':row['url'].split('/')[-1]},context=dict(art.request.context,accepted_source_url=canonical[0]));art=replace(art,request=req);_,o=s.observe(req,art);out.append(dict(url=row['url'],artifact=asdict(art),native=o.native,parse=o.outcome,uncertainties=o.uncertainties));print(row['url'].split('/')[-1],o.outcome)
(root/'results-private.json').write_text(json.dumps(out,indent=2))
