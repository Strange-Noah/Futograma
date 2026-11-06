"""Bounded, anonymous public HTML acquisition. No restricted provider APIs."""
import json,re,time,ssl,urllib.request,urllib.error,urllib.parse,socket,email.utils
from datetime import datetime,timezone
from pathlib import Path
from .core import ROOT,BASE,read,write,digest,now
from .acquisition import robots_rules,NoRedirect
OUT=BASE/'audit/live_deployment';RAW=BASE/'raw/live';UA='FutbolResearch/1.0 (personal football participation research)'
HOSTS={'torlytics.de','www.skysports.com','sport.sky.de','www.fcbarcelona.com','www.fcbarcelona.es','www.liverpoolfc.com','www.premierleague.com','www.inter.it','fcbayern.com','www.mancity.com','www.psg.fr','www.laliga.com','www.bvb.de','www.bundesliga.com','www.tottenhamhotspur.com','media.slbenfica.pt','arsenal-fc.shorthandstories.com'}
CLUB_FEEDS={8456:('manchester-city','manchester-city'),9825:('arsenal','arsenal'),9823:('fc-bayern-muenchen','bayern-munich'),9847:('paris-saint-germain','paris-saint-germain'),8634:('fc-barcelona','barcelona'),8636:('inter','inter-milan')}
class Client:
 def __init__(self,run_id,budget=60,transport=None,sleeper=time.sleep,storage_root=None,allowed_hosts=None):
  self.isolated_storage=storage_root is not None
  if self.isolated_storage and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,180}',run_id):raise ValueError('INVALID_SOURCE_RUN_ID')
  self.root=Path(storage_root).resolve() if storage_root is not None else ROOT
  self.raw_dir=self.root/'raw/live' if storage_root is not None else RAW
  self.output_dir=self.root/'audit/live_deployment' if storage_root is not None else OUT
  self.hosts=HOSTS if allowed_hosts is None else set(allowed_hosts)
  self.run_id=run_id;self.budget=budget;self.records=[];self.rules={};self.last={};self.blocked=set();self.transport=transport;self.sleep=sleeper;self.cachepath=(self.root/'state/live_http_cache.json') if storage_root is not None else BASE/'state/live_http_cache.json';self.cache=read(self.cachepath) if self.cachepath.exists() else {};self.calls=0;self.catalogue_allowlist=set()
 def _write(self,path,value):
  if not self.isolated_storage:return write(path,value)
  path=Path(path).resolve()
  if not path.is_relative_to(self.root):raise ValueError('SOURCE_STORAGE_BOUNDARY')
  path.parent.mkdir(parents=True,exist_ok=True)
  temporary=path.with_suffix(path.suffix+'.tmp')
  temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False));temporary.replace(path)
 def _request(self,url,headers):
  if self.transport:return self.transport(url,headers)
  try:
   req=urllib.request.Request(url,headers=headers)
   with urllib.request.build_opener(NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context())).open(req,timeout=18) as r:return r.status,dict(r.headers),r.read(6000001)
  except urllib.error.HTTPError as e:return e.code,dict(e.headers),e.read(200000)
 def raw(self,url,purpose,conditional=True):
  host=urllib.parse.urlsplit(url).netloc;headers={'User-Agent':UA,'Accept':'text/html,text/plain,application/xhtml+xml'};cached=self.cache.get(url)
  if cached and self.isolated_storage and not (self.root/cached['path']).resolve().is_relative_to(self.root):
   self.cache.pop(url,None);cached=None
  if cached:
   previous=self.root/cached['path']
   if not previous.is_file() or digest(previous.read_bytes())!=cached['sha256']:self.cache.pop(url,None);cached=None
  if conditional and cached:
   if cached.get('etag'):headers['If-None-Match']=cached['etag']
   if cached.get('last_modified'):headers['If-Modified-Since']=cached['last_modified']
  final=None
  for attempt in range(2):
   if self.calls>=self.budget:raise RuntimeError('NETWORK_BUDGET_EXHAUSTED')
   self.sleep(max(0,self.last.get(host,-1e8)+2-time.monotonic()));started=now();code=None;h={};body=b'';error=None
   try:code,h,body=self._request(url,headers)
   except (urllib.error.URLError,TimeoutError,OSError,socket.timeout) as e:error=type(e).__name__+': '+str(e)
   self.calls+=1;self.last[host]=time.monotonic();sha=digest(body);rid=digest((self.run_id+url+started+str(attempt)).encode());folder=self.raw_dir/self.run_id
   if self.isolated_storage and not folder.resolve().is_relative_to(self.root):raise ValueError('SOURCE_STORAGE_BOUNDARY')
   folder.mkdir(parents=True,exist_ok=True);path=folder/(rid+'.response');
   if path.exists():
    if path.read_bytes()!=body:raise ValueError('IMMUTABLE_RESPONSE_COLLISION')
   else:
    with path.open('xb') as archived:archived.write(body)
   rec=dict(transport_mode='TEST_TRANSPORT' if self.transport else 'REAL_HTTP',run_id=self.run_id,url=url,purpose=purpose,request_timestamp=started,response_timestamp=now(),http_status=code,headers=h,path=str(path.relative_to(self.root)),sha256=sha,size=len(body),attempt=attempt+1,error=error,conditional_headers={k:v for k,v in headers.items() if k.startswith('If-')},status='SUCCESS' if code in [200,304] else 'SOURCE_UNAVAILABLE')
   self._write(path.with_suffix('.metadata.json'),rec);self.records.append(rec)
   log=self.output_dir/'http_requests.jsonl'
   if self.isolated_storage and not log.resolve().is_relative_to(self.root):raise ValueError('SOURCE_STORAGE_BOUNDARY')
   log.parent.mkdir(parents=True,exist_ok=True)
   with log.open('a') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
   final=(rec,body)
   if code in [401,403]:self.blocked.add(host);break
   if code not in [429,500,502,503,504,None]:break
   if attempt==0:
    wait=3
    try:wait=max(wait,float(h.get('Retry-After','0')))
    except ValueError:
     try:wait=max(wait,(email.utils.parsedate_to_datetime(h.get('Retry-After','')).astimezone(timezone.utc)-datetime.now(timezone.utc)).total_seconds())
     except (ValueError,TypeError,AttributeError):pass
    if wait>20:rec['retry_deferred']=True;break
    self.sleep(wait)
  rec,body=final
  if code==304 and cached:
   p=self.root/cached['path']
   if p.exists() and digest(p.read_bytes())==cached['sha256']:rec['reused_body_path']=cached['path'];rec['reused_body_sha256']=cached['sha256'];body=p.read_bytes()
   else:rec['status']='SOURCE_UNAVAILABLE';rec['error']='304 cached body missing/hash mismatch'
  elif code==200 and len(body)<=6000000:
   self.cache[url]=dict(path=rec['path'],sha256=sha,etag=h.get('ETag') or h.get('Etag'),last_modified=h.get('Last-Modified'));self._write(self.cachepath,self.cache)
  if len(body)>6000000:rec['status']='VALIDATION_FAILED';rec['error']='BODY_SIZE_LIMIT'
  self._write(path.with_suffix('.metadata.json'),rec)
  return rec,body
 def fetch(self,url,purpose,conditional=True):
  p=urllib.parse.urlsplit(url);host=p.netloc
  if p.scheme!='https' or host not in self.hosts or p.username or p.password or p.path.startswith('/api/') or '/api/' in p.path or p.query and host=='torlytics.de' and url not in self.catalogue_allowlist:return dict(url=url,purpose=purpose,status='SOURCE_UNAVAILABLE',error='SOURCE_POLICY_RESTRICTED'),b''
  if host in self.blocked:return dict(url=url,purpose=purpose,status='SOURCE_UNAVAILABLE',error='HOST_ACCESS_DENIED_THIS_RUN'),b''
  if host not in self.rules:
   r,b=self.raw('https://'+host+'/robots.txt','robots',conditional=False)
   if r['http_status']==200:self.rules[host]=b.decode('utf-8','replace')
   elif r['http_status'] in [404,410]:self.rules[host]=''
   else:return dict(url=url,purpose=purpose,status='SOURCE_UNAVAILABLE',error='ROBOTS_UNAVAILABLE',robots=r),b''
  allowed,delay=robots_rules(self.rules[host],url,UA)
  if not allowed:return dict(url=url,purpose=purpose,status='SOURCE_UNAVAILABLE',error='ROBOTS_DISALLOWED'),b''
  if delay>2:self.sleep(max(0,self.last.get(host,-1e8)+delay-time.monotonic()))
  r,b=self.raw(url,purpose,conditional)
  if r['status']=='SUCCESS' and (not b or re.search(r'<title[^>]*>[^<]*(?:access denied|just a moment|verify)',b.decode('utf-8','replace'),re.I) or b'cf-chl-' in b):r['status']='SOURCE_UNAVAILABLE';r['error']='EMPTY_OR_ACCESS_CHALLENGE'
  if r.get('path'):self._write((self.root/r['path']).with_suffix('.metadata.json'),r)
  return r,b

def preflight_capture():
 client=Client('preflight_'+now().replace(':','').replace('.',''));results=[]
 for tid,(torly,sky) in CLUB_FEEDS.items():
  for provider,url in [('Torlytics','https://torlytics.de/verein/'+torly),('Sky','https://www.skysports.com/'+sky+'-results')]:
   r,b=client.fetch(url,'schedule_'+str(tid));results.append(dict(club_id=tid,provider=provider,**r));print(tid,provider,r['status'],r.get('http_status'),len(b),flush=True)
 write(OUT/'network_preflight_catalogs.json',dict(run_id=client.run_id,results=results,requests=client.calls))
if __name__=='__main__':preflight_capture()
