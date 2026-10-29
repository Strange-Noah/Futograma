"""Diagnostic raw acquisition only. Explicitly not roster or production acceptance."""
import json,os,re,hashlib,subprocess
from pathlib import Path
from urllib.parse import urljoin
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.live_sources import Client,HOSTS
from ops.cloud.phase2_acceptance import ROSTER_SOURCES

def main():
    require_hosted()
    if os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')!='UNCONFIRMED':raise RuntimeError('ISOLATION_REQUIRED')
    HOSTS.update({'www.uefa.com','www.arsenal.com','www.fotmob.com','app.fcbayern.com','www.psv.nl','www.newcastleunited.com','next.avfc.co.uk'})
    out=ROOT/'.cloud/phase3-raw';out.mkdir(parents=True,exist_ok=False)
    client=Client('phase3-'+os.environ['GITHUB_RUN_ID'],budget=260);captures=[]
    urls=[('club:'+str(k),v) for k,v in ROSTER_SOURCES.items()]
    for cid,slug in [('52919','man-city'),('52280','arsenal'),('50037','bayern-munchen'),('52747','paris'),('50080','barcelona'),('50138','inter')]:
        urls.append(('uefa-squad',f'https://www.uefa.com/uefachampionsleague/clubs/{cid}--{slug}/squad/'))
    urls += [('registration','https://www.premierleague.com/en/news/4706139/see-all-the-202627-premier-league-squad-lists'),('news','https://www.psv.nl/en/media/article/ismael-saibari-strengthens-young-psv'),('news','https://www.tottenhamhotspur.com/news/1087177/omar-marmoush-arrives-on-loan'),('news','https://www.newcastleunited.com/en/news/newcastle-united-complete-nico-gonzalez-signing'),('news','https://www.liverpoolfc.com/news/photos-bradley-barcolas-first-look-anfield-liverpool-player'),('news','https://www.psg.fr/content/cp-ibrahim-mbaye-rejoint-aston-villa-fc'),('news','https://next.avfc.co.uk/news/2026/september/03/news-fpl-prices-confirmed-for-new-signings/'),('academy','https://www.arsenal.com/news/meet-the-academy-stars-out-in-girona-ar8uf3e9Wq3R'),('academy','https://www.arsenal.com/news/team-news-copley-captain-at-meadow-park-aU9rE5S0vY9p'),('cup','https://www.skysports.com/football/bayern-munich-vs-magdeburg/teams/5102414964343343232'),('club-profile','https://app.fcbayern.com/de/teams/profis/ismael-saibari')]
    # Candidate IDs are discovery hints, not verified equivalences. Parse public raw profiles independently.
    for pid in [534670,795179,776151,1084981,1406436,1180288,839204,1280132,1297165,1593857]:urls.append(('fotmob-profile','https://www.fotmob.com/players/'+str(pid)))
    seen=set()
    for purpose,url in urls:
        if url in seen:continue
        seen.add(url)
        try:r,body=client.fetch(url,purpose,conditional=False)
        except Exception as e:r={'url':url,'purpose':purpose,'status':'SOURCE_UNAVAILABLE','error':type(e).__name__+':'+str(e)};body=b''
        item=dict(r,purpose=purpose);captures.append(item)
        print(purpose,url,r['status'],r.get('http_status'),r.get('error'),len(body),flush=True)
        if purpose=='uefa-squad' and r['status']=='SUCCESS':
            # Follow actual stable-ID links in acquired roster, not a table of player answers.
            links=set(re.findall(r'(?:https://www.uefa.com)?(/uefachampionsleague/clubs/players/[0-9]+--[^"<> ?]+)',body.decode('utf-8','replace')))
            urls.extend(('uefa-profile',urljoin(url,x)) for x in sorted(links))
    report={'kind':'RAW_SOURCE_RECONNAISSANCE_NOT_ACCEPTANCE','code_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'captures':captures,'requests':client.calls,'production_writes':False,'roster_acceptance':'PENDING','international_coverage':'PENDING','authority_handover':'UNCONFIRMED'}
    with (out/'captures.json').open('x') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Raw-source diagnostic, not end-to-end acceptance\n'+json.dumps({k:report[k] for k in report if k!='captures'})+'\nUnavailable pages: '+str(sum(x['status']!='SUCCESS' for x in captures)))
if __name__=='__main__':main()
