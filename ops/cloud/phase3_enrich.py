"""Raw candidate-profile enrichment. Read-only baseline, cloud-only fresh workspace."""
import json,os,re,subprocess,tarfile,time,sqlite3
from pathlib import Path
from urllib.parse import urlsplit,urljoin
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.live_sources import Client,HOSTS

def main():
    require_hosted()
    if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='UNCONFIRMED':raise RuntimeError('ISOLATION_REQUIRED')
    out=ROOT/'.cloud/phase3-enrich';out.mkdir(parents=True,exist_ok=False)
    store=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],'main');head,pointer=store.head();store.restore(out/'baseline.sqlite')
    db=sqlite3.connect('file:'+str(out/'baseline.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
    seed={name:[dict(r) for r in db.execute('SELECT * FROM '+name)] for name in ['clubs','players','validation_player_aliases','validation_roster_membership','player_eligibility','validation_national_reference','validation_source_evidence']}
    seed.update(production_head=head,production_pointer=pointer)
    with (out/'canonical_discovery_hints.json').open('x') as f:json.dump(seed,f,ensure_ascii=False,indent=2)
    # The original capture is immutable; reuse only verified body hashes, never processed player decisions.
    store.download(688571125,out/'initial-raw.tar.gz');tar=tarfile.open(out/'initial-raw.tar.gz');first=json.load(tar.extractfile('.cloud/phase3-raw/captures.json'))['captures'];seen={r['url'] for r in first if r['status']=='SUCCESS'}
    urls=[]
    for a in seed['validation_player_aliases']:
        key=a['provider_key']
        if key.startswith('fotmob:') and key.split(':',1)[1].isdigit():urls.append(('fotmob-profile','https://www.fotmob.com/players/'+key.split(':',1)[1]))
        elif key.startswith('https://torlytics.de/spieler/'):urls.append(('torly-profile',key))
    # Recover changed official navigation by following links found in acquired official pages.
    for r in first:
        if r.get('path') and 'arsenal.com' in r['url']:
            raw=tar.extractfile(r['path']).read().decode('utf-8','replace')
            for href in re.findall(r'href="([^"<>]+)"',raw):
                if re.fullmatch(r'/fixtures/men/players/?',href):urls.append(('recovered_roster:9825',urljoin(r['url'],href)))
    HOSTS.update({'www.uefa.com','www.arsenal.com','www.fotmob.com','app.fcbayern.com','www.psv.nl','www.newcastleunited.com','next.avfc.co.uk'})
    client=Client('phase3-enrich-'+os.environ['GITHUB_RUN_ID'],budget=500);captures=[];start=time.monotonic()
    for purpose,url in urls:
        if url in seen:continue
        seen.add(url)
        if time.monotonic()-start>1080:captures.append(dict(url=url,purpose=purpose,status='DEFERRED',error='DIAGNOSTIC_TIME_BUDGET'));continue
        try:r,body=client.fetch(url,purpose,conditional=False)
        except Exception as e:r=dict(url=url,purpose=purpose,status='SOURCE_UNAVAILABLE',error=type(e).__name__+':'+str(e));body=b''
        captures.append(dict(r,purpose=purpose));print(purpose,url,r['status'],r.get('http_status'),r.get('error'),len(body),flush=True)
    assert store.head()==(head,pointer),'PRODUCTION_POINTER_CHANGED_DURING_READONLY_DIAGNOSTIC'
    report=dict(kind='RAW_PROFILE_DIAGNOSTIC_NOT_ACCEPTANCE',captures=captures,requests=client.calls,production_head=head,production_pointer_unchanged=True,production_writes=False)
    with (out/'captures.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Raw profile enrichment, not acceptance\n'+str(len(captures))+' profile/recovery attempts; production pointer unchanged.\n')
if __name__=='__main__':main()
