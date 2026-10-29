"""Optional authorised FotMob data delivery adapter, empty configuration by default.
This is not a workaround for the prohibited public /api routes.
"""
import json,urllib.parse
from datetime import timedelta
from .core import *
from .acquisition import Controller,preserve
from .fotmob import LocalFotMob,period_for
from futbol.collection import fixtures
from futbol.common import instant

def acquire_feed(db,cfg,spec,controller):
    source=dict(spec,period_id=spec.get('period_id','2026-27_H1'))
    proof=source.get('permission_evidence')
    if source.get('policy')!='OWNER_AUTHORIZED' or not proof or not within(BASE/proof).is_file():return controller.log(source,'AUTHORIZATION_REQUIRED','An actual permitted delivery route and documented owner permission are required')
    url=source['url'];parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https':return controller.log(source,'ACCESS_DENIED','HTTPS required')
    # Keep the publicly prohibited route blocked; configure a permitted delivery URL instead.
    if parsed.hostname in ('fotmob.com','www.fotmob.com') and parsed.path.startswith('/api/'):
        return controller.log(source,'TERMS_RESTRICTED','Public API route blocked; no bypass or impersonation')
    # Reuse general policy/robots/status/rate-limit/retry machinery, but do not create official baseline snapshots.
    r=controller.acquire(dict(source,policy='ONE_OFF_LOCAL_CAPTURE',purpose='fotmob_feed'),refresh=True)
    if r['status']!='ACQUIRED':return r
    a=db.execute('SELECT path FROM raw_artifacts WHERE artifact_id=?',(r['artifact_id'],)).fetchone()
    try:
        d=read(ROOT/a[0])
        if spec['kind']=='catalog':
            det=d['details'];lid=int(det['id'])
            if lid!=int(spec['competition_id']) or str(det['selectedSeason'])!=spec['season']:raise ValueError('Feed catalog ID/season mismatch')
            fs=fixtures(d,[]);path=BASE/'raw/fotmob/catalogs'/f"{lid}_{spec['season'].replace('/','-')}.json"
        elif spec['kind']=='match':
            mid=int(spec['match_id'])
            if int(d['general']['matchId'])!=mid:raise ValueError('Feed match ID mismatch')
            path=BASE/'raw/fotmob/matches'/f'{mid}.json';fs=[]
        else:raise ValueError('Unknown feed kind')
        write(path,d);write(path.with_suffix('.meta.json'),dict(fetched_at=now(),feed_artifact_id=r['artifact_id'],url=url));r['staged_path']=str(path.relative_to(BASE));r['fixtures']=fs
        return r
    except (ValueError,KeyError,TypeError,OSError) as e:return controller.log(source,'CONTENT_INCOMPLETE','Feed schema validation failed: '+str(e),aid=r['artifact_id'])

def run_feeds(db,cfg,sources,transport=None):
    ctl=Controller(db,**({'transport':transport} if transport else {}));results=[];clubs={c['id'] for c in cfg['clubs']};old=dict(LocalFotMob(cfg).details());recent=instant(cfg['as_of']+'T00:00:00+00:00')-timedelta(days=cfg['correction_days'])
    for spec in sources:
        r=acquire_feed(db,cfg,spec,ctl);results.append({k:v for k,v in r.items() if k!='fixtures'})
        if spec.get('kind')!='catalog' or r['status']!='ACQUIRED' or not spec.get('match_url_template'):continue
        for f in r.get('fixtures',[]):
            if not f.get('status',{}).get('finished') or f['status'].get('cancelled') or f['status'].get('awarded'):continue
            if not {int(f[x]['id']) for x in ('home','away')}.intersection(clubs):continue
            t=f['status'].get('utcTime')
            if not t or not period_for(t,cfg)[0] or instant(t)>instant(cfg['as_of']+'T23:59:59+00:00'):continue
            mid=int(f['id'])
            if mid in old and instant(t)<recent:continue
            url=spec['match_url_template'].format(match_id=mid)
            if urllib.parse.urlparse(url).netloc!=urllib.parse.urlparse(spec['url']).netloc:
                results.append(dict(source_id=spec['source_id'],status='POLICY_REVIEW_REQUIRED',reason='Match template changes authorised delivery host'));continue
            child=dict(spec,source_id=spec['source_id']+'_match_'+str(mid),kind='match',match_id=mid,url=url)
            results.append(acquire_feed(db,cfg,child,ctl))
    write(BASE/'reports/feed_cycle.json',dict(results=results,network_requests=ctl.calls));return results
