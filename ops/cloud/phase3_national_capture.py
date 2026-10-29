"""International raw acquisition diagnostic: player history -> evidenced national teams -> details."""
import os,json,tarfile,re,time
from urllib.parse import urlsplit
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.live_sources import Client,HOSTS
from squad_pipeline.international_discovery import profile_hints,team_fixture_hints

def main():
    require_hosted()
    if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='UNCONFIRMED':raise RuntimeError('ISOLATION_REQUIRED')
    out=ROOT/'.cloud/phase3-national';out.mkdir(parents=True,exist_ok=False);s=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],'main');production=s.head();captures=[];hints=[];errors=[];affiliations={};bodies={}
    for aid,label,manifest in [(688571125,'first','.cloud/phase3-raw/captures.json'),(688619422,'enrich','.cloud/phase3-enrich/captures.json')]:
        s.download(aid,out/(label+'.tar.gz'));tar=tarfile.open(out/(label+'.tar.gz'));rs=json.load(tar.extractfile(manifest))['captures']
        for r in rs:
            if r['purpose']!='fotmob-profile' or r['status']!='SUCCESS':continue
            try:
                h=profile_hints(tar.extractfile(r['path']).read().decode(),r,'2026-09-01','2026-10-29');hints.append(h)
                for a in h['affiliations']:affiliations[a['provider_team_id']]=a
            except Exception as e:errors.append(dict(url=r['url'],error=type(e).__name__+':'+str(e)))
        if label=='enrich':seed=json.load(tar.extractfile('.cloud/phase3-enrich/canonical_discovery_hints.json'))
    HOSTS.update({'www.fotmob.com','www.uefa.com','www.englandfootball.com','www.thefa.com','www.arsenal.com','app.fcbayern.com','www.psv.nl','www.newcastleunited.com','next.avfc.co.uk'})
    client=Client('phase3-national-'+os.environ['GITHUB_RUN_ID'],budget=600);start=time.monotonic();seen=set();fixtures={}
    for h in hints:
        for m in h['matches']:fixtures.setdefault(m['provider_match_id'],m)
    for tid,a in affiliations.items():
        if time.monotonic()-start>1080:errors.append(dict(team_id=tid,error='TIME_BUDGET_DEFERRED'));continue
        slug=re.sub('[^a-z0-9-]','',a['name'].casefold().replace(' ','-'));url='https://www.fotmob.com/teams/'+tid+'/fixtures/'+slug
        try:
            r,b=client.fetch(url,'national_team_fixtures',conditional=False);captures.append(r)
            if r['status']=='SUCCESS':
                for m in team_fixture_hints(b.decode('utf-8','replace'),r,a,'2026-09-01','2026-10-29'):fixtures.setdefault(m['provider_match_id'],m)
        except Exception as e:errors.append(dict(url=url,error=type(e).__name__+':'+str(e)))
    for mid,m in fixtures.items():
        url=m['url']
        if url in seen:continue
        seen.add(url)
        if time.monotonic()-start>1200:captures.append(dict(url=url,purpose='national_match_detail',status='DEFERRED',error='TIME_BUDGET_DEFERRED'));continue
        try:r,b=client.fetch(url,'national_match_detail',conditional=False);captures.append(dict(r,discovered_match_id=mid))
        except Exception as e:captures.append(dict(url=url,purpose='national_match_detail',status='SOURCE_UNAVAILABLE',error=type(e).__name__+':'+str(e),discovered_match_id=mid))
        print('national_match',mid,url,captures[-1]['status'],flush=True)
    # Also retain the actual prior-reference URLs, rather than relying solely on legacy processed payloads.
    refs={rid for row in seed['validation_national_reference'] for rid in json.loads(row['payload_json']).get('source_refs',[])}
    for row in seed['validation_source_evidence']:
        if row['evidence_id'] not in refs:continue
        e=json.loads(row['payload_json']);urls=[e.get(k) for k in ('url','source_url') if isinstance(e.get(k),str)]
        for url in urls:
            if not url.startswith('https://') or '/api/' in url or url in seen:continue
            seen.add(url)
            if time.monotonic()-start>1260:continue
            try:r,b=client.fetch(url,'legacy_national_reference_source',conditional=False);captures.append(r)
            except Exception as exc:errors.append(dict(url=url,error=type(exc).__name__+':'+str(exc)))
    report=dict(kind='RAW_INTERNATIONAL_DISCOVERY_NOT_ACCEPTANCE',captures=captures,player_histories=hints,affiliations=list(affiliations.values()),fixtures=list(fixtures.values()),discovery_errors=errors,production_pointer_unchanged=s.head()==production,production_writes=False,roster_changes=False)
    assert report['production_pointer_unchanged']
    with (out/'captures.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## International raw discovery diagnostic, not ingestion acceptance\nHistories: '+str(len(hints))+'; evidenced teams: '+str(len(affiliations))+'; fixtures: '+str(len(fixtures))+'; errors: '+str(len(errors))+'; production unchanged.\n')
if __name__=='__main__':main()
