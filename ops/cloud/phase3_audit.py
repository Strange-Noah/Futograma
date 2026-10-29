"""Full raw-source development audit. NOT production handover or final acceptance."""
import os,json,tarfile,hashlib,sqlite3,subprocess,copy,gzip,time
from pathlib import Path
from collections import Counter
from dataclasses import fields
from urllib.parse import urlsplit
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import inspect,sha,pack
from squad_pipeline.identity_pipeline import DDL,Record,persist_verified_alias,similarity
from squad_pipeline.roster_verification import seed_candidates,verify_records
from squad_pipeline import identity_sources as parsers
from squad_pipeline.managed_roster import migrate as management_migrate,import_verified_history,decision,add_event
from squad_pipeline.international_store import migrate as national_migrate,import_reference_evidence_package,bridge_references
from squad_pipeline.live_sources import Client,HOSTS
from squad_pipeline.cloud.release_gate import status_matrix
ASSETS=[(688571125,'.cloud/phase3-raw/captures.json'),(688619422,'.cloud/phase3-enrich/captures.json'),(688686205,'.cloud/phase3-national/captures.json')]

def write(path,data):
    with Path(path).open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2)
def minimal(record):
    r=record.evidence();raw=r.pop('raw_fields',{});r['derived_detail']={k:v for k,v in raw.items() if k in ('documented_alias_bridge','announcement','biography_excerpt','list_b_marker','current_mens_player_profile')};return r

def main():
    require_hosted()
    if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='UNCONFIRMED':raise RuntimeError('ISOLATION_REQUIRED')
    work=ROOT/'.cloud/phase3-audit';work.mkdir(parents=True,exist_ok=False);store=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],'main');production=store.head();store.restore(work/'working.sqlite');before=inspect(work/'working.sqlite')['tables'];db=sqlite3.connect(work/'working.sqlite');db.row_factory=sqlite3.Row
    players=[dict(r) for r in db.execute('SELECT * FROM players')];aliases=[dict(r) for r in db.execute('SELECT * FROM validation_player_aliases')];club_players={r['club_id']:[p[0] for p in db.execute('SELECT player_id FROM validation_roster_membership WHERE club_id=? UNION SELECT player_id FROM player_eligibility WHERE club_id=?',(r['club_id'],r['club_id']))] for r in db.execute('SELECT * FROM clubs').fetchall()};candidates=seed_candidates(players,aliases);captures=[];original=[];national_report=None;records=[];parse_errors=[];source_health=[]
    for aid,manifest_path in ASSETS:
        path=work/('raw-'+str(aid)+'.tar.gz');store.download(aid,path);metadata=store.api('GET',store.base+'/releases/assets/'+str(aid));assert metadata.get('digest')=='sha256:'+sha(path),'RAW_ASSET_DIGEST_MISMATCH'
        tar=tarfile.open(path);manifest=json.load(tar.extractfile(manifest_path))
        if aid==688686205:national_report=manifest
        for capture in manifest['captures']:
            capture=dict(capture,source_asset_id=aid);source_health.append({k:capture.get(k) for k in ['url','status','http_status','error','source_asset_id']})
            if capture['status']!='SUCCESS' or not capture.get('path'):continue
            body=tar.extractfile(capture['path']).read();assert hashlib.sha256(body).hexdigest()==capture['sha256'],'RAW_BODY_HASH_MISMATCH';original.append((capture,body.decode('utf-8','replace')))
    def parse(capture,raw):
        purpose=capture.get('purpose','');rows=[]
        if purpose=='fotmob-profile':rows=parsers.fotmob(raw,capture)
        elif purpose=='torly-profile':rows=parsers.torly_profile(raw,capture)
        elif purpose=='uefa-profile':rows=parsers.uefa_profile(raw,capture)
        elif purpose=='uefa-squad':rows=parsers.uefa_squad(raw,capture)
        elif purpose.startswith(('club:','recovered_roster:')):rows=parsers.official_roster(raw,capture,int(purpose.split(':')[1]))
        elif purpose=='registration':rows=parsers.registration(raw,capture,'Arsenal',9825)+parsers.registration(raw,capture,'Manchester City',8456)
        elif purpose=='academy':rows=parsers.academy_biographies(raw,capture,9825)
        elif purpose=='club-profile':rows=parsers.person_profile(raw,capture,9823)
        for row in rows:row.raw_fields['retained_raw_asset']=capture.get('source_asset_id')
        return rows
    for capture,raw in original:
        try:records.extend(parse(capture,raw))
        except Exception as exc:parse_errors.append(dict(url=capture['url'],purpose=capture.get('purpose'),error=type(exc).__name__+':'+str(exc)))
    # Render a successful public shell normally. Never re-render a denied/challenged source to bypass access controls.
    client=Client('phase3-audit-'+os.environ['GITHUB_RUN_ID'],budget=140);HOSTS.update({'www.uefa.com','www.arsenal.com','app.fcbayern.com','www.psv.nl','www.newcastleunited.com','next.avfc.co.uk','datencenter.dfb.de'})
    def render(url,index):
        folder=work/('render-'+str(index));folder.mkdir();subprocess.run(['node','ops/cloud/official_browser_capture.mjs',url,str(folder)],check=True,timeout=100);receipt=json.loads((folder/'official-rendered.json').read_text());receipt['path']=str((folder/'official-rendered.html').relative_to(ROOT));receipt['purpose']='recovered_roster:9825' if url.rstrip('/')=='https://www.arsenal.com/fixtures/men/players' else 'official_profile';captures.append(receipt)
        if receipt['status']=='SUCCESS':return receipt,(folder/'official-rendered.html').read_text()
        return receipt,''
    shell=next(((r,b) for r,b in original if r.get('purpose')=='recovered_roster:9825'),None)
    if shell:
        try:
            r,b=render(shell[0]['url'],0)
            if b:records.extend(parsers.official_roster(b,r,9825))
        except Exception as exc:parse_errors.append(dict(url=shell[0]['url'],stage='NORMAL_BROWSER_RENDER',error=type(exc).__name__+':'+str(exc)))
    dfb_url='https://datencenter.dfb.de/competitions/champions-league/seasons/2026-2027/teams/bayern-muenchen'
    try:
        r,b=client.fetch(dfb_url,'official_federation_competition_roster',conditional=False);captures.append(r)
        if r['status']=='SUCCESS':records.extend(parsers.dfb_roster(b.decode('utf-8','replace'),r,9823))
    except Exception as exc:parse_errors.append(dict(url=dfb_url,error=type(exc).__name__+':'+str(exc)))
    stage1=verify_records(candidates,records,'2026-10-29',club_players);clubs_by_player={}
    for cid,pids in club_players.items():
        for pid in pids:clubs_by_player.setdefault(pid,[]).append(cid)
    for capture,raw in original:
        if capture.get('purpose')=='news':records.extend(parsers.documented_alias_articles(raw,capture,candidates,8640 if 'psv.nl' in capture['url'] else None));records.extend(parsers.departure_articles(raw,capture,candidates,clubs_by_player))
    stage2=verify_records(candidates,records,'2026-10-29',club_players)
    # Source URLs are leads only. Match unresolved names to URLs, then verify acquired content; no per-player truth assertions.
    catalog=json.loads((ROOT/'ops/cloud/verification-source-catalog.json').read_text())['urls'];unresolved=[d for d in stage2['players'] if d['identity_status']!='VERIFIED' or d['management']['state']=='UNKNOWN'];seen={r['url'] for r,_ in original};attempted=[]
    for dossier in unresolved:
        p=next(c for c in candidates if str(c.player_id)==dossier['player_id']);leads=[]
        for url in catalog:
            if any(parsers.key(n) in parsers.key(url) for n in p.names if len(n.split())>=2):leads.append(url)
        for old in db.execute('SELECT source_json FROM player_eligibility WHERE player_id=?',(p.player_id,)):
            for source in json.loads(old[0] or '[]'):
                if isinstance(source,dict) and str(source.get('url','')).startswith('https://'):leads.append(source['url'])
        for url in list(dict.fromkeys(leads))[:3]:
            if url in seen:continue
            seen.add(url)
            try:
                r,b=client.fetch(url,'unresolved_identity_or_management_lead',conditional=False);captures.append(r);attempted.append(dict(player_id=str(p.player_id),url=url,status=r['status'],error=r.get('error')))
                if r['status']!='SUCCESS':continue
                raw=b.decode('utf-8','replace');rows=parsers.current_official_profile(raw,r,dossier['club_id'])
                if not rows and url.startswith('https://www.arsenal.com/fixtures/men/players/'):
                    rr,rendered=render(url,len(captures))
                    if rendered:rows=parsers.current_official_profile(rendered,rr,dossier['club_id'])
                if not rows:rows=parsers.person_profile(raw,r,dossier['club_id'])
                records.extend(rows)
            except Exception as exc:attempted.append(dict(player_id=str(p.player_id),url=url,status='AUTO_PATH_FAILED',error=type(exc).__name__+':'+str(exc)))
    management_migrate(db);import_verified_history(db);db.commit();prior={(cid,pid):decision(db,cid,pid,'2026-10-29') for cid,pids in club_players.items() for pid in pids}
    result=verify_records(candidates,records,'2026-10-29',club_players,prior=prior);db.executescript(DDL)
    for r in result['source_results']:
        if r['identity_status']=='VERIFIED':
            record=Record(**{k:v for k,v in r['source_record'].items() if k in {f.name for f in fields(Record)}});persist_verified_alias(db,record,r,'2026-10-29')
    for dossier in result['players']:
        cid=dossier['club_id'];pid=int(dossier['player_id']);d=dossier['management'];old=list(db.execute("SELECT * FROM player_eligibility WHERE club_id=? AND player_id=? AND verification_status='VERIFIED' AND classification LIKE 'ACADEMY%'",(cid,pid)));dossier['academy_affiliation_status']='VERIFIED_LEGACY_ACADEMY_ASSOCIATION' if old else 'VERIFIED_CURRENT_ACADEMY_EVIDENCE' if d.get('academy_affiliation') else 'NO_ACADEMY_ASSERTION';dossier['academy_legacy_sources']=[json.loads(r['source_json']) for r in old];dossier['automatic_paths_attempted']=[a for a in attempted if a['player_id']==str(pid)]
        if d['state'] in ('INCLUDED','EXCLUDED') and d.get('evidence'):add_event(db,cid,pid,d['status'],d.get('effective_from'),'2026-10-29',d['evidence'],'Unified raw-source verified management',end_exclusive=d.get('end_exclusive'),precision=d.get('date_precision','OBSERVED_SNAPSHOT'))
        compact=dict(dossier);compact['verified_source_records']=[{k:v for k,v in r.items() if k!='raw_fields'} for r in dossier['verified_source_records']];compact.pop('candidate_source_records',None);data=json.dumps(compact,ensure_ascii=False,sort_keys=True);key=hashlib.sha256(data.encode()).hexdigest();db.execute('INSERT OR IGNORE INTO roster_verification_dossiers VALUES(?,?,?,?,?,?,?,?,?)',(key,cid,pid,'2026-10-29',dossier['identity_status'],dossier['academy_affiliation_status'],'REGISTERED' if d.get('registration') else 'UNCONFIRMED',d['state'],data))
    db.commit();cup_result={'status':'PENDING'}
    cup_source=next(((r,b) for r,b in original if r.get('purpose')=='cup'),None)
    if cup_source:
        db.execute('SAVEPOINT isolated_cup_validation')
        try:
            from squad_pipeline.live_parse import sky_detail
            from squad_pipeline.live_ingest import apply_sheet
            r,body=cup_source;file=work/'cup.response'
            with file.open('xb') as f:f.write(body.encode())
            r=dict(r,path=str(file.relative_to(ROOT)));fixture,sheets=sky_detail(body,r);own=next(sh for sh in sheets if sh['club_id']==9823);ids=[]
            from squad_pipeline.identity_registry import lookup
            for p in own['players']:
                pid=lookup(db,9823,p)
                if pid is None:raise ValueError('CUP_PLAYER_UNVERIFIED:'+p['name'])
                ids.append(pid)
            if len(ids)!=len(set(ids)):raise ValueError('CUP_CANONICAL_PLAYER_ID_COLLISION')
            applied=apply_sheet(db,fixture,own,'phase3-isolated-cup-validation','2026-10-29')
            cup_result={'status':'PASS','fixture':fixture,'squad_count':len(ids),'starting_xi':sum(p['role']=='starters' for p in own['players']),'canonical_player_ids':list(map(str,ids)),'players':own['players'],'apply_result':applied,'fact_write_committed':False}
        except Exception as exc:cup_result={'status':'FAIL','error':type(exc).__name__+':'+str(exc),'fact_write_committed':False}
        finally:db.execute('ROLLBACK TO isolated_cup_validation');db.execute('RELEASE isolated_cup_validation')
    db.commit();national_migrate(db);package_path=work/'legacy-evidence.tar.gz';store.download(688695104,package_path);assert sha(package_path)=='93a0d9f0dda4d86c844b2d5830669b1e43440986d0305419ad96349e4af8ad86';tar=tarfile.open(package_path);manifest=json.load(tar.extractfile('manifest.json'))
    for row in manifest['source_records']:assert hashlib.sha256(tar.extractfile(row['retained_body_path']).read()).hexdigest()==row['retained_body_sha256']
    repaired=import_reference_evidence_package(db,manifest,'github://Strange-Noah/Futograma-data/releases/assets/688695104');bridged=bridge_references(db);db.commit();assert len(bridged)==21 and all(x['state']=='INTEGRATED_PRIOR_TARGET_VERIFICATION' for x in bridged);assert bridge_references(db)==[]
    count=dict(db.execute('SELECT COUNT(*) AS total,SUM(minutes>0) AS positive,SUM(minutes=0) AS zero FROM international_appearances').fetchone());assert count==dict(total=21,positive=13,zero=8);db.close();assert inspect(work/'working.sqlite')['tables']==before,'LEGACY_FACTS_CHANGED'
    # Small original international responses for parser development; final acceptance always reads the full immutable raw archive.
    samples=[]
    for r,b in original:
        if r.get('purpose')=='national_match_detail' and (len(samples)<2 or not any('u21' in x['capture']['url'].lower() for x in samples) and 'u21' in r['url'].lower()):
            samples.append(dict(capture=r,body=b))
            if len(samples)>=3:break
    peek=dict(cup_validation=cup_result,national_report={k:v for k,v in national_report.items() if k!='captures'},raw_match_samples=samples,source_health=source_health,parse_errors=parse_errors,roster=result,legacy_reference_bridge=count,repaired_evidence=repaired,browser_captures=captures)
    report=dict(cup_validation={k:v for k,v in cup_result.items() if k not in ('players','fixture','canonical_player_ids')},kind='DEVELOPMENT_RAW_AUDIT_NOT_FINAL_ACCEPTANCE',code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),club_counts={str(cid):dict(Counter(d['management']['state'] for d in result['players'] if d['club_id']==cid)) for cid in club_players},legacy_reference_bridge=count,legacy_fact_tables_unchanged=True,production_pointer_unchanged=store.head()==production,production_database_modified=False,source_parse_errors=parse_errors,automatic_paths_attempted=attempted,status=status_matrix(dict(software='PASS',live_sources='DEGRADED',roster_completeness='PENDING',international_coverage='PENDING',data_publication='DISABLED',website='PENDING',production_authority='DISABLED')))
    assert report['production_pointer_unchanged'];write(work/'result.json',report)
    with (work/'development-audit.json.gz').open('xb') as f:f.write(gzip.compress(json.dumps(peek,ensure_ascii=False).encode(),mtime=0))
    release=store.api('POST',store.base+'/releases',dict(tag_name='phase3-audit-'+os.environ['GITHUB_RUN_ID'],target_commitish='acceptance/phase3-unified',name='Phase 3 full raw development audit',body='Development audit only. Full raw identity analysis, original international parser samples, preserved 21-record bridge. No production authority or final acceptance.',draft=True))
    for file in [work/'result.json',work/'development-audit.json.gz']:
        asset=store.upload(release['id'],file);store.download(asset['id'],work/('readback-'+file.name));assert sha(file)==sha(work/('readback-'+file.name));print('AUDIT_ASSET',asset['id'],file.name,flush=True)
    store.api('PATCH',store.base+'/releases/'+str(release['id']),dict(draft=False));print(json.dumps(report,ensure_ascii=False))
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Development raw audit — not final acceptance\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n')
if __name__=='__main__':main()
