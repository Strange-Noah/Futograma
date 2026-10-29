"""International raw acquisition diagnostic: player history -> evidenced national teams -> details."""
import os,json,tarfile,re,time,gzip,hashlib
from urllib.parse import urlsplit
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.live_sources import Client,HOSTS
from squad_pipeline.international_discovery import profile_hints,team_fixture_hints

def main():
    require_hosted()
    if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='UNCONFIRMED':raise RuntimeError('ISOLATION_REQUIRED')
    out=ROOT/'.cloud/phase3-national';out.mkdir(parents=True,exist_ok=False);s=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],'main');production=s.head();captures=[];hints=[];errors=[];affiliations={};bodies={};peek={'profile_views':[],'raw_samples':[]};torly_samples=0
    for aid,label,manifest in [(688571125,'first','.cloud/phase3-raw/captures.json'),(688619422,'enrich','.cloud/phase3-enrich/captures.json')]:
        s.download(aid,out/(label+'.tar.gz'));tar=tarfile.open(out/(label+'.tar.gz'));rs=json.load(tar.extractfile(manifest))['captures']
        for r in rs:
            if r['status']=='SUCCESS' and (r['purpose'].startswith('recovered_roster:') or r['purpose']=='torly-profile' and torly_samples<2):
                peek['raw_samples'].append({'capture':r,'body':tar.extractfile(r['path']).read().decode('utf-8','replace')})
                if r['purpose']=='torly-profile':torly_samples+=1
            if r['purpose']!='fotmob-profile' or r['status']!='SUCCESS':continue
            try:
                raw=tar.extractfile(r['path']).read().decode();h=profile_hints(raw,r,'2026-07-01','2026-10-29');hints.append(h)
                from squad_pipeline.international_discovery import payload
                d=payload(raw);keep=['id','name','birthDate','primaryTeam','careerHistory','recentMatches','initialMatchesPage','matchFilters','relatedLinksData','meta','isCoach','gender']
                peek['profile_views'].append({'capture':r,'fixture_type':'FIELD_PROJECTION_NOT_ORIGINAL_RESPONSE','data':{k:d[k] for k in keep if k in d}})
                for a in h['affiliations']:affiliations[a['provider_team_id']]=a
            except Exception as e:errors.append(dict(url=r['url'],error=type(e).__name__+':'+str(e)))
        if label=='enrich':seed=json.load(tar.extractfile('.cloud/phase3-enrich/canonical_discovery_hints.json'))
    refs={rid for row in seed['validation_national_reference'] for rid in json.loads(row['payload_json']).get('source_refs',[])}
    peek['seed']={k:seed[k] for k in ['clubs','players','validation_player_aliases','validation_roster_membership','player_eligibility','validation_national_reference']}
    peek['seed']['validation_source_evidence']=[e for e in seed['validation_source_evidence'] if e['evidence_id'] in refs]
    peek_path=out/'development-peek.json.gz'
    with peek_path.open('xb') as f:f.write(gzip.compress(json.dumps(peek,ensure_ascii=False).encode(),mtime=0))
    release=s.api('POST',s.base+'/releases',dict(tag_name='phase3-development-extract-'+os.environ['GITHUB_RUN_ID'],target_commitish='acceptance/phase3-unified',name='Phase 3 development field projection and raw parser samples',body='Debug projections are not original responses and are not acceptance inputs. Full raw archives remain immutable private assets.',draft=True))
    asset=s.upload(release['id'],peek_path);s.download(asset['id'],out/'peek-readback.gz')
    assert hashlib.sha256(peek_path.read_bytes()).digest()==hashlib.sha256((out/'peek-readback.gz').read_bytes()).digest()
    s.api('PATCH',s.base+'/releases/'+str(release['id']),dict(draft=False));print('DEVELOPMENT_PEEK_ASSET',asset['id'],flush=True)
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
                for m in team_fixture_hints(b.decode('utf-8','replace'),r,a,'2026-07-01','2026-10-29'):fixtures.setdefault(m['provider_match_id'],m)
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
