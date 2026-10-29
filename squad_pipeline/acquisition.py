from __future__ import annotations
import json,mimetypes,time,urllib.request,urllib.error,urllib.parse,urllib.robotparser,email.utils,re,socket
from .core import *
UA='FutbolLocalStudy/1.0'
TRANSIENT={429,500,502,503,504}
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None

def http_get(url,timeout=25):
    # A redirect is an explicit review boundary: never send an unaudited second-host request.
    opener=urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=timeout) as r:return r.status,dict(r.headers),r.read()
    except urllib.error.HTTPError as e:return e.code,dict(e.headers),e.read()

def detect_type(raw,header='',filename=''):
    head=raw.lstrip()[:256].lower()
    if raw.startswith(b'%PDF-'):return 'application/pdf','.pdf'
    if head.startswith((b'<!doctype html',b'<html')) or 'text/html' in header:return 'text/html','.html'
    if head.startswith((b'{',b'[')) or 'json' in header:return 'application/json','.json'
    if filename.lower().endswith('.csv') or 'text/csv' in header:return 'text/csv','.csv'
    return (header.split(';')[0] or mimetypes.guess_type(filename)[0] or 'application/octet-stream'),Path(filename).suffix or '.bin'

def preserve(db,source,raw,headers,status,kind='official',location='response'):
    mime,ext=detect_type(raw,headers.get('Content-Type',headers.get('content-type','')))
    sha=digest(raw);folder=within(BASE/'raw'/kind/source['source_id']/sha);folder.mkdir(parents=True,exist_ok=True);path=folder/(location+ext)
    if not path.exists():path.write_bytes(raw)
    aid=artifact(db,path,kind,source['source_id'],now(),source.get('period_id'))
    meta=folder/(location+'.metadata.json')
    if not meta.exists():write(meta,dict(url=source['url'],http_status=status,headers=headers,content_type=mime,sha256=sha,retrieved_at=now(),source_id=source['source_id'],publication_permission=source.get('publication_permission')))
    return aid,path,mime

def robots_rules(text,url,agent=UA):
    groups=[];agents=[];rules=[];delay=None
    def save():
        if agents:groups.append((list(agents),list(rules),delay))
    for raw in text.splitlines():
        line=raw.split('#',1)[0].strip()
        if ':' not in line:continue
        key,value=[x.strip() for x in line.split(':',1)];key=key.lower()
        if key=='user-agent':
            if rules or delay is not None:save();agents=[];rules=[];delay=None
            agents.append(value.lower())
        elif key in ('allow','disallow') and agents:rules.append((key,value))
        elif key=='crawl-delay' and agents:
            try:delay=float(value)
            except ValueError:pass
    save();name=agent.lower();specific=[g for g in groups if any(a!='*' and a in name for a in g[0])];chosen=specific or [g for g in groups if '*' in g[0]]
    if specific:
        size=max(len(a) for g in chosen for a in g[0] if a in name);chosen=[g for g in chosen if any(len(a)==size and a in name for a in g[0])]
    u=urllib.parse.urlparse(url);target=urllib.parse.unquote(u.path or '/')+('?' + urllib.parse.unquote(u.query) if u.query else '');matching=[];wait=2
    for _,rules,d in chosen:
        wait=max(wait,d or 0)
        for kind,rule in rules:
            if not rule:continue
            pattern='^'+re.escape(urllib.parse.unquote(rule)).replace(r'\*','.*')
            if rule.endswith('$'):pattern=pattern[:-2]+'$'
            if re.search(pattern,target):matching.append((len(rule.replace('*','').rstrip('$')),kind=='allow'))
    return (max(matching)[1] if matching else True),wait

class Controller:
    def __init__(self,db,transport=http_get,sleeper=time.sleep,clock=time.monotonic):
        self.db=db;self.transport=transport;self.sleep=sleeper;self.clock=clock;self.host_last={};self.host_delay={};self.calls=0
    def request(self,url,delay=2):
        host=urllib.parse.urlparse(url).netloc
        self.sleep(max(0,self.host_last.get(host,-100000)+max(delay,self.host_delay.get(host,0))-self.clock()))
        for attempt in range(3):
            try:return self.transport(url)
            except (urllib.error.URLError,TimeoutError,OSError,socket.timeout):
                if attempt==2:raise
                self.sleep(2**(attempt+1))
            finally:self.calls+=1;self.host_last[host]=self.clock()
    def log(self,source,status,reason,http=None,aid=None):
        self.db.execute('INSERT INTO acquisition_attempts(source_id,status,http_status,reason,artifact_id,attempted_at) VALUES(?,?,?,?,?,?)',(source['source_id'],status,http,reason,aid,now()));self.db.commit()
        return dict(source_id=source['source_id'],status=status,reason=reason,http_status=http,artifact_id=aid)
    def acquire(self,s,refresh=False):
        if s['policy']=='RESTRICTED':return self.log(s,'TERMS_RESTRICTED',s['policy_note'])
        if s['policy']=='REVIEW_REQUIRED':return self.log(s,'POLICY_REVIEW_REQUIRED',s['policy_note'])
        if s['policy']!='ONE_OFF_LOCAL_CAPTURE':return self.log(s,'POLICY_REVIEW_REQUIRED','Unrecognised acquisition policy; no request sent')
        prior=self.db.execute("SELECT artifact_id FROM official_snapshots WHERE source_id=? AND period_id=? AND acquisition_status='ACQUIRED' ORDER BY captured_at DESC LIMIT 1",(s['source_id'],s['period_id'])).fetchone()
        if prior and not refresh:return self.log(s,'ACQUIRED','Existing frozen local artifact reused; no new request',aid=prior[0])
        parsed=urllib.parse.urlparse(s['url'])
        if parsed.scheme!='https' or parsed.username or parsed.password:return self.log(s,'ACCESS_DENIED','Only anonymous HTTPS sources supported')
        robots=urllib.parse.urlunparse((parsed.scheme,parsed.netloc,'/robots.txt','','',''))
        try:
            code,h,raw=self.request(robots);preserve(self.db,s,raw,h,code,'policy','robots')
            if code in (401,403):return self.log(s,'ACCESS_DENIED','Robots access denied; target not requested',code)
            if code==200:
                allowed,wait=robots_rules(raw.decode('utf-8','replace'),s['url'])
                if not allowed:return self.log(s,'ROBOTS_RESTRICTED','Target path disallowed; no target request',code)
                self.host_delay[parsed.netloc]=wait
            elif code not in (404,410):return self.log(s,'RETRYABLE_ERROR','Robots could not be evaluated; target not requested',code)
            for attempt in range(3):
                code,h,raw=self.request(s['url']);aid,path,mime=preserve(self.db,s,raw,h,code)
                if code in TRANSIENT:
                    if attempt<2:
                        retry=h.get('Retry-After') or h.get('retry-after');wait=2**(attempt+1)
                        if retry:
                            try:wait=max(wait,float(retry))
                            except ValueError:
                                try:wait=max(wait,(email.utils.parsedate_to_datetime(retry)-datetime.now(UTC)).total_seconds())
                                except (ValueError,TypeError):pass
                        if wait>60:return self.log(s,'RETRYABLE_ERROR','Retry-After exceeds bounded run; retry in later cycle',code,aid)
                        self.sleep(wait);continue
                    return self.log(s,'RETRYABLE_ERROR','Transient HTTP after three attempts',code,aid)
                if code==401:return self.log(s,'AUTHENTICATION_REQUIRED','Anonymous request requires authentication; stopped',code,aid)
                if code==403:return self.log(s,'ACCESS_DENIED','HTTP 403; no browser or identity workaround attempted',code,aid)
                if 300<=code<400:return self.log(s,'BROWSER_INTERACTION_REQUIRED','Redirect target must be reviewed separately: '+h.get('Location',''),code,aid)
                if code in (404,410):return self.log(s,'SOURCE_NOT_FOUND','Target not found',code,aid)
                if code!=200:return self.log(s,'ACCESS_DENIED','Unexpected HTTP status',code,aid)
                text=raw.decode('utf-8','replace');tokens=s.get('roster_evidence_tokens') or []
                if re.search('verify you are human|cf-chl-|captcha|access denied',text,re.I):return self.log(s,'BROWSER_INTERACTION_REQUIRED','Challenge content received; stopped without solving',code,aid)
                # Evidence presence check only; no player extraction, mapping or universal parser.
                if s.get('purpose')!='fotmob_feed' and mime=='text/html' and not all(t.casefold() in text.casefold() for t in tokens):return self.log(s,'CONTENT_INCOMPLETE','HTTP 200 lacks configured visible/embedded roster evidence; browser review required',code,aid)
                if not raw:return self.log(s,'CONTENT_INCOMPLETE','Empty response',code,aid)
                if s.get('purpose')=='fotmob_feed':return self.log(s,'ACQUIRED','Authorised feed raw response preserved; schema validation follows',code,aid)
                sid=digest((s['source_id']+s['period_id']+aid).encode())
                self.db.execute('INSERT OR IGNORE INTO official_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',(sid,s['source_id'],s['club_id'],s['period_id'],aid,now(),None,None,'ACQUIRED','AWAITING_PROVIDER_PARSER','ROSTER_TOKENS_PRESENT_COMPLETENESS_UNVERIFIED' if mime=='text/html' else 'CONTENT_AWAITING_HUMAN_REVIEW'))
                return self.log(s,'ACQUIRED',('Roster-bearing HTML preserved' if mime=='text/html' else 'Raw file preserved; roster content unverified')+'; completeness, sporting effective date and parsing pending',code,aid)
        except (urllib.error.URLError,TimeoutError,OSError,socket.timeout) as e:return self.log(s,'RETRYABLE_ERROR',str(e))

def seed_sources(db,sources):
    for s in sources:db.execute('INSERT OR REPLACE INTO acquisition_sources VALUES(?,?,?,?,?)',(s['source_id'],s['club_id'],s['organization'],s['url'],json.dumps(s,ensure_ascii=False)))
    db.commit()

def run_official(db,sources,online=False):
    seed_sources(db,sources);controller=Controller(db);out=[]
    for s in sources:
        try:
            if online or s['policy'] in ('RESTRICTED','REVIEW_REQUIRED'):r=controller.acquire(s)
            else:
                prior=db.execute("SELECT artifact_id FROM official_snapshots WHERE source_id=? AND acquisition_status='ACQUIRED'",(s['source_id'],)).fetchone()
                r=controller.log(s,'ACQUIRED','Offline reuse of preserved artifact',aid=prior[0]) if prior else controller.log(s,'MANUAL_DOWNLOAD_REQUIRED','Offline mode; no additional request; consult prior attempt')
        except Exception as e:r=controller.log(s,'RETRYABLE_ERROR',type(e).__name__+': '+str(e))
        out.append(r);print(s['source_id'],r['status'],flush=True)
    record=dict(at=now(),network_calls=controller.calls,results=out);write(BASE/'reports/official_cycle.json',record);write(BASE/'reports/official_cycles'/(datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.json'),record);return out
