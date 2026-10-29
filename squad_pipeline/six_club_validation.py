"""Isolated six-club fixture-first experiment. Never reads roster data in discovery()."""
import json,re,html,hashlib,sqlite3,time,copy,unicodedata
from pathlib import Path
from collections import defaultdict,Counter
from datetime import datetime,date,timedelta,timezone
from zoneinfo import ZoneInfo
from .core import ROOT,BASE,read,write,digest,now
from .fotmob import LocalFotMob,extract
from .multi_source_dataset import identity,team,ALIASES,norm
from .multi_hop_reviewed import sky_rosters
from .six_player_extract import players,take,text,clock
from futbol.collection import fixtures
ALIASES.update({'olympiquelyonnais':'lyon','internazionale':'inter','parissg':'parissaintgermain','fcbayernmuenchen':'bayern','fcbayernmunchen':'bayern','fcbayernmunich':'bayern','bayernmunchen':'bayern'})
OUT=BASE/'audit/six_club'
CLUBS={8456:('Manchester City','manchestercity'),9825:('Arsenal','arsenal'),9823:('Bayern München','bayern'),9847:('Paris Saint-Germain','parissaintgermain'),8634:('FC Barcelona','barcelona'),8636:('Inter Milan','inter')}
COMPS={42:'UEFA Champions League',47:'Premier League',53:'Ligue 1',54:'Bundesliga',55:'Serie A',87:'LaLiga',133:'EFL Cup',247:'Community Shield',209:'DFB Pokal',207:'Trophée des champions',74:'UEFA Super Cup','DFL_SUPERCUP':'DFL Supercup'}
CUT='2026-10-28'; TZ=ZoneInfo('Europe/Madrid')
def canon(n):
 t=team(n);return {'fcbarcelona':'barcelona','bodøglimt':'bodoglimt','vflosnabruck':'osnabruck','racingdesantander':'racingsantander','fcbayern':'bayern','bayernmuenchen':'bayern','brighton':'brightonhovealbion'}.get(t,t)
def dt(t):return datetime.fromisoformat(t.replace('Z','+00:00'))
def day(t):return dt(t).astimezone(TZ).date().isoformat()
def dump(p,d):write(OUT/p,d)
def source(path,url=None,location=None,acquired=None):
 p=Path(path);return dict(path=str(p.relative_to(ROOT)),sha256=digest(p.read_bytes()),url=url,location=location,acquired_at=acquired)
def key(f):return identity(f['competition_id'],f['kickoff'],canon(f['home_team']),canon(f['away_team']))
def discovery():
 fs={};sheets=[];errors=[];rawreads=0
 def fixture(cid,kick,home,away,mid=None,hid=None,aid=None,status='COMPLETED',score=None,ev=None):
  if cid not in COMPS or not kick or not '2026-07-01'<=day(kick)<=CUT:return None
  home,away=canon(home),canon(away)
  if not {home,away}&{v[1] for v in CLUBS.values()}:return None
  if dt(kick)>datetime.now(timezone.utc):status='SCHEDULED'
  f=dict(competition_id=cid,competition=COMPS[cid],kickoff=kick,scheduled_kickoff=kick,match_date=day(kick),home_team=home,away_team=away,home_id=hid,away_id=aid,provider_match_id=mid,match_state=status,score=score,source_evidence=[ev] if ev else [])
  # Provider binding keeps postponed schedules from creating another physical match.
  old=next((x for x in fs.values() if mid and x['provider_match_id']==mid),None)
  if old:
   old['source_evidence']+=f['source_evidence']
   if status=='COMPLETED':old.update({k:v for k,v in f.items() if k!='source_evidence' and v is not None})
   return old
  f['match_id']=key(f)
  if f['match_id'] in fs:
   old=fs[f['match_id']];old['source_evidence']+=f['source_evidence']
   for field in ['provider_match_id','home_id','away_id','score']:
    if old.get(field) is None:old[field]=f[field]
   if status=='COMPLETED':old['match_state']='COMPLETED'
   return old
  fs[f['match_id']]=f;return f
 for p in (ROOT/'raw/leagues').glob('*2026*.json'):
  if p.name.endswith('.meta.json'):continue
  try:
   d=read(p);cid=int(d['details']['id'])
   if cid not in COMPS:continue
   ev=source(p,location='fixtures[*]')
   for f in fixtures(d,[]):
    st=f.get('status',{});fixture(cid,st.get('utcTime'),f['home']['name'],f['away']['name'],int(f['id']),int(f['home']['id']),int(f['away']['id']),'COMPLETED' if st.get('finished') else 'SCHEDULED',st.get('scoreStr'),ev)
  except Exception as e:errors.append(dict(path=str(p),error=str(e)))
 for mid,p in LocalFotMob({}).details():
  try:
   d=read(p);rawreads+=1;g=d.get('general',{});kick=g.get('matchTimeUTCDate');cid=int(g.get('parentLeagueId') or g.get('leagueId') or 0)
   if cid not in COMPS or not g.get('finished') or not kick:continue
   ev=source(p,location='$.general;$.content.lineup;$.content.matchFacts.events')
   f=fixture(cid,kick,g['homeTeam']['name'],g['awayTeam']['name'],mid,int(g['homeTeam']['id']),int(g['awayTeam']['id']),score=d.get('header',{}).get('status',{}).get('scoreStr'),ev=ev)
   if not f:continue
   for tid,(name,slug) in CLUBS.items():
    if tid not in [f['home_id'],f['away_id']]:continue
    try:rows=extract(d,tid)
    except Exception as e:errors.append(dict(match=mid,club_id=tid,error=str(e)));continue
    side='homeTeam' if tid==f['home_id'] else 'awayTeam';own=d['content']['lineup'][side];events=d['content'].get('matchFacts',{}).get('events',{}).get('events',[])
    own_events=[e for e in events if e.get('isHome')==(side=='homeTeam') and (e.get('type')=='Substitution' or e.get('card') in ['Red','YellowRed','SecondYellow'])]
    outgoing={};incoming={};reds={}
    for e in own_events:
     t=e.get('time');a=e.get('overloadTime') or 0;c=dict(base=t,added=a,total=(t or 0)+a,raw=str(e.get('timeStr')))
     if e.get('type')=='Substitution' and len(e.get('swap',[]))==2:incoming[int(e['swap'][0]['id'])]=c;outgoing[int(e['swap'][1]['id'])]=c
     elif e.get('playerId'):reds[int(e['playerId'])]=c
    ps=[]
    for r in rows:
     pid=r['player_id'];st='STARTER' if r['role']=='starters' else 'SUBSTITUTE_USED' if pid in incoming or r['played'] else 'UNUSED_SUBSTITUTE' if r['played'] is False else 'UNKNOWN'
     ps.append(dict(provider_key='fotmob:'+str(pid),fotmob_id=pid,name=r['name'],shirt=str(r['shirt_number']),role='starters' if st=='STARTER' else 'bench',status=st,reported_minutes=r['minutes'],derived_minutes=None,entry=incoming.get(pid),exit=outgoing.get(pid) or reds.get(pid),json_path=r['json_path']))
    sheets.append(dict(match_id=f['match_id'],club_id=tid,provider='FotMob',players=ps,events=own_events,complete_squad=True,complete_events=bool(events),source=ev,issues=[]))
  except Exception as e:errors.append(dict(path=str(p),error=str(e)))
 # Public HTML archive: provider IDs and complete lineups, all named players, no whitelist.
 for log in [BASE/'audit/multi_hop/http_requests.jsonl',BASE/'audit/six_player_validation/http_requests.jsonl',OUT/'http_requests.jsonl']:
  if not log.exists():continue
  for line in log.read_text().splitlines():
   r=json.loads(line)
   if r.get('http_status')!=200 or '/spiel/' not in r.get('url',''):continue
   p=ROOT/r['path'];raw=p.read_text();rawreads+=1
   try:js=[json.loads(x) for x in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',raw,re.S)]
   except Exception:continue
   event=next((x for x in js if isinstance(x,dict) and x.get('@type')=='SportsEvent'),None)
   if not event:continue
   cid={'Bundesliga':54,'La Liga':87,'Premier League':47,'Serie A':55,'Ligue 1':53}.get(event.get('organizer',{}).get('name'))
   ev=source(p,r['url'],'SportsEvent;lineup-col;event-list;werte-table',r.get('request_timestamp'));f=fixture(cid,event.get('startDate'),event.get('homeTeam',{}).get('name'),event.get('awayTeam',{}).get('name'),status='COMPLETED' if text(take(r'match-score__state">(.*?)</span>',raw))=='Ende' else 'SCHEDULED',ev=ev)
   if not f or f['match_state']!='COMPLETED':continue
   stats={}
   for tb in re.findall(r'<table class="data-table werte-table">(.*?)</table>',raw,re.S):
    for tr in re.findall(r'<tr>(.*?)</tr>',tb,re.S):
     a=re.search(r'<a href="([^\"]+)">(.*?)</a>',tr,re.S);n=text(take(r'<td class="num">(.*?)</td>',tr))
     if a and n.isdigit():stats[html.unescape(a[1])]=int(n)
   events=[]
   for side,b in re.findall(r'<li class="event event--(home|away)">(.*?)</li>',raw,re.S):
    typ=take(r'event__icon--([a-z-]+)',b);url=take(r'event__name">.*?<a href="([^\"]+)"',b)
    events.append(dict(side=side,type=typ,clock=clock(text(take(r'event__minute">(.*?)</span>',b))),provider_key=url,player_name=text(take(r'<a[^>]*>(.*?)</a>',b)),out_name=text(take(r'event__sub-out">(.*?)</span>',b)).replace('↓ raus:','').strip()))
   blocks=re.split(r'<div class="lineup-col lineup-col--(home|away)">',raw)
   for i in range(1,len(blocks),2):
    side,b=blocks[i:i+2];slug=f[side+'_team'];tid=next((k for k,v in CLUBS.items() if v[1]==slug),None)
    if tid is None:continue
    xi=players(take(r'<ol class="lineup-list lineup-section--xi">(.*?)</ol>',b));bn=players(take(r'<ol class="lineup-list lineup-list--bench">(.*?)</ol>',b));own_ev=[e for e in events if e['side']==side];ps=[];issues=[];pitch={p['provider_player_url'] for p in xi};used=set();entries={};exits={};names={norm(p['name']):p['provider_player_url'] for p in xi+bn}
    for e in own_ev:
     if e['type']=='sub':
      inc=e['provider_key'];out=names.get(norm(e['out_name']));entries[inc]=e['clock']
      if out not in pitch:issues.append('OUT_NOT_ON_PITCH:'+e['out_name'])
      if inc in pitch or inc in used or inc not in {p['provider_player_url'] for p in bn}:issues.append('INVALID_INCOMING:'+str(inc))
      if out:exits[out]=e['clock'];pitch.discard(out)
      pitch.add(inc);used.add(inc)
     if e['type'] in ['red','yellow-red','second-yellow']:
      exits[e['provider_key']]=e['clock'];pitch.discard(e['provider_key'])
    for role,group in [('starters',xi),('bench',bn)]:
     for p1 in group:
      u=p1['provider_player_url'];entry=entries.get(u);exit_=exits.get(u);st='STARTER' if role=='starters' else 'SUBSTITUTE_USED' if u in entries else 'UNUSED_SUBSTITUTE';derived=0 if st=='UNUSED_SUBSTITUTE' else None
      if st in ['STARTER','SUBSTITUTE_USED'] and not issues:
       start=0 if role=='starters' else (entry or {}).get('base');end=(exit_ or {}).get('base',90)
       if start is not None and end is not None and not (entry or {}).get('added') and not (exit_ or {}).get('added'):
        if start==46:start=45
        derived=max(0,end-start)
      ps.append(dict(provider_key=u,name=p1['name'],shirt=p1['shirt'],role=role,status=st if not issues or role=='starters' or u in entries else 'UNKNOWN',reported_minutes=stats.get(u),derived_minutes=derived,entry=entry,exit=exit_,json_path='.lineup-col--'+side+'.lineup-section--xi/lineup-list--bench'))
    complete=len(xi)==11 and bool(bn) and len({p['provider_key'] for p in ps})==len(ps)
    sheets.append(dict(match_id=f['match_id'],club_id=tid,provider='Torlytics',players=ps,events=own_ev,complete_squad=complete,complete_events=not issues,source=ev,issues=issues+(['SUBSTITUTION_AUTHORIZATION_NOT_VERIFIED'] if len(used)>5 else [])))
 # Full Sky raw text; line markers do not identify event types. Typed changes are reviewed below.
 sky_specs={
 '577605':(42,'2026-09-09T19:00:00Z','napoli','arsenal',[(65,23,39),(66,10,17),(73,7,20),(73,14,29),(82,4,36)],[],1),
 '577615':(42,'2026-10-13T19:00:00Z','arsenal','lille',[(65,20,8),(65,23,7),(65,14,29),(78,12,4),(85,10,56)],[],0),
 '577577':(133,'2026-09-15T19:00:00Z','ipswich','arsenal',[(45,6,15),(64,39,45),(76,20,17),(76,10,8),(85,36,44)],[],1),
 '578610':(133,'2026-10-27T20:00:00Z','fleetwoodtown','arsenal',[(45,12,4),(45,89,8),(66,53,10),(82,36,39),(82,49,15)],[(85,56)],1),
 '577602':(42,'2026-09-08T19:00:00Z','realmadrid','inter',[(45,28,6),(60,23,7),(60,20,8),(60,9,14),(83,30,22)],[],1),
 '577618':(42,'2026-10-13T19:00:00Z','inter','clubbrugge',[(67,20,8),(67,9,94),(78,23,14),(78,32,99),(88,7,5)],[],0),
 '577646':(42,'2026-10-21T19:00:00Z','inter','shakhtardonetsk',[(59,99,17),(59,20,7),(59,32,31),(73,8,11),(75,14,22)],[(68,95)],0),
 '577609':(42,'2026-09-10T19:00:00Z','bayern','bodoglimt',[(65,27,44),(65,10,34),(80,19,21),(80,6,8),(80,14,42)],[],0),
 '577622':(42,'2026-10-13T19:00:00Z','viking','bayern',[(45,11,19),(45,4,2),(45,42,34),(76,14,44),(83,34,21)],[(73,17)],1),
 '577643':(42,'2026-10-21T19:00:00Z','bayern','arsenal',[(45,3,4),(68,7,42),(68,34,14),(85,19,11),(90,14,39)],[],0),
 '556658':(209,'2026-09-02T18:45:00Z','osnabruck','bayern',[(62,11,19),(62,34,42),(80,8,45),(87,17,27),(87,9,39)],[],1),
 '577617':(42,'2026-10-13T19:00:00Z','galatasaray','barcelona',[(58,20,9),(59,17,14),(73,24,23),(73,8,22),(82,7,6)],[],1)}
 for path in [BASE/'raw/multi_hop/web_extracts.json',BASE/'raw/six_player_validation/web_extracts.json']:
  for e in read(path):
   sid=e['source_url'].rstrip('/').split('/')[-1]
   if sid not in sky_specs:continue
   cid,kick,home,away,swaps,reds,ownside=sky_specs[sid];ev=source(path,e['source_url'],'source_url='+e['source_url'],e.get('reviewed_at'));f=fixture(cid,kick,home,away,ev=ev)
   rr=sky_rosters(e['source_lines'])
   if len(rr)!=2:errors.append(dict(url=e['source_url'],error='SKY_ROSTER_PARSE'));continue
   for side_idx,rost in enumerate(rr):
    slug=[home,away][side_idx];tid=next((k for k,v in CLUBS.items() if v[1]==slug),None)
    if tid is None:continue
    ss=swaps if side_idx==ownside else ([(10,7,10),(68,29,14),(69,39,49),(85,8,23)] if sid=='577643' else None);red=reds if side_idx==ownside else [];inc={i:t for t,o,i in ss or []};outs={o:t for t,o,i in ss or []};outs.update({p:t for t,p in red});ps=[]
    for role in ['starters','bench']:
     for p in rost[role]:
      p=dict(p,name=re.sub(r'\s*\(c\)', '', p['name']).strip());shirt=p['shirt'];st='STARTER' if role=='starters' else 'SUBSTITUTE_USED' if shirt in inc else 'UNUSED_SUBSTITUTE' if ss is not None else 'UNKNOWN';m=outs.get(shirt,90)-inc.get(shirt,0) if st in ['STARTER','SUBSTITUTE_USED'] and ss is not None else 0 if st=='UNUSED_SUBSTITUTE' else None
      ps.append(dict(provider_key='sky:'+str(tid)+':'+norm(p['name']),name=p['name'],shirt=str(shirt),role=role,status=st,reported_minutes=None,derived_minutes=m,entry=inc.get(shirt),exit=outs.get(shirt),json_path=ev['location']+';side='+str(side_idx)+';shirt='+str(shirt)))
    sheets.append(dict(match_id=f['match_id'],club_id=tid,provider='Sky',players=ps,events=dict(substitution_groups=[dict(minute=t,outgoing=[o for tm,o,i in ss if tm==t],incoming=[i for tm,o,i in ss if tm==t]) for t in sorted({t for t,o,i in ss})] if ss is not None else None,reds=red),complete_squad=len(rost['starters'])==11 and bool(rost['bench']),complete_events=ss is not None,source=ev,issues=[] if ss is not None else ['TYPED_EVENTS_NOT_YET_VERIFIED']))
 # Two archived non-Sky full match sheets, parsed independently of any roster.
 path=BASE/'raw/multi_hop/web_extracts.json'
 for e in read(path):
  if 'bvb.de/' in e['source_url']:
   ev=source(path,e['source_url'],'L675-679; official opposing club full sheet',e.get('reviewed_at'));f=fixture('DFL_SUPERCUP','2026-08-22T18:30:00Z','borussiadortmund','bayern',score='1-2',ev=ev)
   line=re.search(r'L675: FC Bayern Munich: (.*)',e['source_lines'])[1];unused=re.search(r'L676: Bench:.*? – (.*)',e['source_lines'])[1]
   parts=re.split(r'\s+–\s+|,\s+',line);xi=[];bn=[];events=[]
   for part in parts:
    n=re.sub(r'\s*\(.*','',part).strip();m=re.search(r"\((\d+)'(?:\+(\d+))? (.*?)\)",part)
    xi.append((n,m))
    if m:bn.append((m[3],m));events.append(dict(type='SUB',out_name=n,in_name=m[3],base=int(m[1]),added=int(m[2] or 0)))
   bn.extend((n.strip(),None) for n in unused.split(','));ps=[]
   for role,group in [('starters',xi),('bench',bn)]:
    for n,m in group:
     st='STARTER' if role=='starters' else 'SUBSTITUTE_USED' if m else 'UNUSED_SUBSTITUTE';minutes=(int(m[1]) if role=='starters' else 90-int(m[1])) if m and not m[2] else 90 if role=='starters' and not m else 0 if st=='UNUSED_SUBSTITUTE' else None
     ps.append(dict(provider_key='bvb:9823:'+norm(n),name=n,shirt=None,role=role,status=st,reported_minutes=None,derived_minutes=minutes,entry=dict(base=int(m[1]),added=int(m[2] or 0)) if m and role=='bench' else None,exit=dict(base=int(m[1]),added=int(m[2] or 0)) if m and role=='starters' else None,json_path='L675-676:'+n))
   sheets.append(dict(match_id=f['match_id'],club_id=9823,provider='BVB official',players=ps,events=events,complete_squad=len(xi)==11 and bool(bn),complete_events=True,source=ev,issues=['ADDED_TIME_NUMERIC_MINUTES_UNRESOLVED_FOR_TWO_PLAYERS']))
  if 'vi.nl/wedstrijden/2026-09-09/' in e['source_url']:
   ev=source(path,e['source_url'],'L162-168;L189-193;L212-218',e.get('reviewed_at'));f=fixture(42,'2026-09-09T16:45:00Z','barcelona','feyenoord',ev=ev);lines={int(m[1]):m[2] for m in re.finditer(r'^L(\d+): (.*)',e['source_lines'],re.M)}
   def refs(ns):return [re.sub(r'^2 ','',name) for n in ns for name in re.findall(r'【\d+†([^】]+)】',lines.get(n,''))]
   xi=refs([162,164,166,168]);used=refs(range(189,194));unused=refs(range(212,219));swaps=[(63,'Pedri','Marc Bernal'),(63,'K. Adeyemi','A. Gordon'),(71,'Raphinha','Fermín López'),(83,'Rodri','Gavi'),(83,'Dani Olmo','Gabriel Jesus')];inc={norm(i):t for t,o,i in swaps};outs={norm(o):t for t,o,i in swaps};ps=[]
   for role,group in [('starters',xi),('bench',used+unused)]:
    for n in group:
     k=norm(n);st='STARTER' if role=='starters' else 'SUBSTITUTE_USED' if k in inc else 'UNUSED_SUBSTITUTE';m=outs.get(k,90)-inc.get(k,0) if st!='UNUSED_SUBSTITUTE' else 0
     ps.append(dict(provider_key='vi:8634:'+k,name=n,shirt=None,role=role,status=st,reported_minutes=None,derived_minutes=m,entry=inc.get(k),exit=outs.get(k),json_path=ev['location']+':'+n))
   sheets.append(dict(match_id=f['match_id'],club_id=8634,provider='VI',players=ps,events=swaps,complete_squad=len(xi)==11 and len(used+unused)==12,complete_events=True,source=ev,issues=[]))
 # Upcoming DFB cup from archived official matchplan, no participation rows.
 fixture(209,'2026-10-28T19:45:00Z','bayern','magdeburg',status='SCHEDULED',ev=dict(path='squad_pipeline/raw/multi_hop/web_extracts.json',url='https://www.bundesliga.com/en/dfb-cup/matchday/2026-2027/round-2/fc-bayern-muenchen-vs-1-fc-magdeburg/liveticker/',location='saved upcoming fixture'))
 # Normalize provider-backed fixture aliases and current actual kickoff from public HTML.
 for f in list(fs.values()):
  if f['provider_match_id']==5802972:
   f['scheduled_kickoff']=f['kickoff'];f['kickoff']='2026-10-26T19:30:00Z';f['match_date']='2026-10-26';f['match_state']='COMPLETED';f['previous_match_id']=f['match_id'];f['match_id']=key(f)
 # Merge same physical event by competition, sides and local date after reschedule correction.
 grouped={};aliases={}
 for f in fs.values():
  k=(str(f['competition_id']),f['home_team'],f['away_team'],f['match_date']);old=grouped.get(k)
  if old:
   aliases[f['match_id']]=old['match_id'];old['source_evidence']+=f['source_evidence']
   if f['match_state']=='COMPLETED':old['match_state']='COMPLETED'
   for field in ['provider_match_id','home_id','away_id','score']:
    if old.get(field) is None:old[field]=f[field]
  else:grouped[k]=f
 fs=list(grouped.values())
 for s in sheets:s['match_id']=aliases.get(s['match_id'],s['match_id'])
 # Strip pending future fixtures from discovery denominator but retain schedule state.
 dump('backfill/fixtures.json',fs);dump('backfill/raw_sheets.json',sheets);dump('backfill/discovery_errors.json',errors)
 dump('backfill/discovery_manifest.json',dict(phase='FIXTURE_FIRST_NO_ROSTER_READ',raw_reads=rawreads,raw_sheets=len(sheets),fixture_contexts=sum(sum(slug in [f['home_team'],f['away_team']] for name,slug in CLUBS.values()) for f in fs if f['match_state']=='COMPLETED'),created_at=now(),code_sha256=digest(Path(__file__).read_bytes())))
 print('fixtures',len(fs),'sheets',len(sheets),'byclub',Counter(s['club_id'] for s in sheets))
if __name__=='__main__':discovery()

def freeze():
 fs=read(OUT/'backfill/fixtures.json');sheets=read(OUT/'backfill/raw_sheets.json');byclub=defaultdict(list)
 for s in sheets:
  for p in s['players']:byclub[s['club_id']].append(p)
 parent={p['provider_key']:p['provider_key'] for ps in byclub.values() for p in ps}
 def find(x):
  while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
  return x
 def union(a,b):
  a,b=find(a),find(b)
  if a!=b:parent[max(a,b)]=min(a,b)
 def tokens(n):return [norm(t) for t in re.findall(r"[^\W\d_]+",n,flags=re.U) if norm(t) not in ['de','el','ben','dos','da']]
 def compatible(a,b):
  if norm(a['name'])==norm(b['name']):return True
  ta,tb=tokens(a['name']),tokens(b['name'])
  if not ta or not tb:return False
  common={x for x in ta if len(x)>2}&{x for x in tb if len(x)>2}
  if not common:return False
  if len(ta)==1 or len(tb)==1:return True
  return ta[0][0]==tb[0][0] and (len(ta[0])==1 or len(tb[0])==1 or ta[0]==tb[0])
 aliaslog=[]
 for tid,ps in byclub.items():
  records={p['provider_key']:p for p in ps};vals=list(records.values())
  # Exact names cannot join two distinct source IDs within the same provider.
  for i,a in enumerate(vals):
   for b in vals[i+1:]:
    if a['provider_key'].split(':')[0]==b['provider_key'].split(':')[0]:continue
    if not compatible(a,b):continue
    if a.get('shirt') and b.get('shirt') and a['shirt']!=b['shirt']:continue
    # No merge if a line-up names them as two different players.
    if any(a['provider_key'] in {p['provider_key'] for p in s['players']} and b['provider_key'] in {p['provider_key'] for p in s['players']} for s in sheets if s['club_id']==tid):continue
    # Unique candidate per target provider protects initial+surname ambiguities.
    candidates=[x for x in vals if x['provider_key'].split(':')[0]==b['provider_key'].split(':')[0] and compatible(a,x) and (not a.get('shirt') or not x.get('shirt') or a['shirt']==x['shirt'])]
    if len(candidates)==1:union(a['provider_key'],b['provider_key']);aliaslog.append(dict(club_id=tid,a=a['provider_key'],b=b['provider_key'],method='EXACT_OR_UNIQUE_INITIAL_SURNAME_WITH_SHIRT_WHERE_PRESENT'))
 groups=defaultdict(list)
 for ps in byclub.values():
  for p in ps:groups[find(p['provider_key'])].append(p)
 players={};mapping={}
 for root,ps in groups.items():
  fm={p['fotmob_id'] for p in ps if p.get('fotmob_id')}
  if len(fm)>1:raise ValueError('Unsafe multiple FotMob IDs '+str(fm))
  pid=next(iter(fm)) if fm else -int(digest(root.encode())[:15],16)
  best=max(ps,key=lambda p:(len(tokens(p['name'])),len(p['name'])));name=best['name']
  if any(p.get('fotmob_id') for p in ps):name=next(p['name'] for p in ps if p.get('fotmob_id'))
  players[pid]=dict(player_id=pid,display_name=name,fotmob_id=next(iter(fm)) if fm else None,provider_keys=sorted({p['provider_key'] for p in ps}),source_names=sorted({p['name'] for p in ps}),identity_status='SOURCE_IDS_AND_UNIQUE_NAME_RESOLUTION;NOT_ALL_INDEPENDENT_BIOGRAPHIES_VERIFIED')
  for p in ps:mapping[p['provider_key']]=pid
 for s in sheets:
  for p in s['players']:p['player_id']=mapping[p['provider_key']]
  if len({p['player_id'] for p in s['players']})!=len(s['players']):s['complete_squad']=False;s['issues'].append('CANONICAL_ID_COLLISION')
 roster={str(t):sorted({p['player_id'] for s in sheets if s['club_id']==t for p in s['players']}) for t in CLUBS}
 # Fixture completeness uses XI+bench independently of event/numeric completeness.
 contexts=[];matrix=[]
 for f in fs:
  if f['match_state']!='COMPLETED':continue
  for tid,(name,slug) in CLUBS.items():
   if slug not in [f['home_team'],f['away_team']]:continue
   ss=[s for s in sheets if s['club_id']==tid and s['match_id']==f['match_id']];complete=any(s['complete_squad'] for s in ss)
   contexts.append(dict(club_id=tid,match_id=f['match_id'],complete_xi=any(sum(p['role']=='starters' for p in s['players'])==11 for s in ss),complete_squad=complete,complete_events=any(s['complete_events'] for s in ss),sources=[s['source'] for s in ss],issues=sorted({i for s in ss for i in s['issues']})))
   for pid in roster[str(tid)]:
    obs=[dict(p,provider=s['provider'],source=s['source']) for s in ss for p in s['players'] if p['player_id']==pid];states={p['status'] for p in obs if p['status']!='UNKNOWN'}
    st=next(iter(states)) if len(states)==1 else 'UNKNOWN' if states or not complete else 'NOT_IN_MATCHDAY_SQUAD'
    reported=[p['reported_minutes'] for p in obs if p['reported_minutes'] is not None];derived=[p['derived_minutes'] for p in obs if p['derived_minutes'] is not None];vals=reported+derived;issues=[]
    if len(states)>1:issues.append('PARTICIPATION_SOURCE_CONFLICT')
    if vals and max(vals)-min(vals)>=5:issues.append('MATERIAL_MINUTE_SOURCE_CONFLICT')
    selected=0 if st in ['NOT_IN_MATCHDAY_SQUAD','UNUSED_SUBSTITUTE'] else vals[0] if vals and 'MATERIAL_MINUTE_SOURCE_CONFLICT' not in issues and st!='UNKNOWN' else None
    matrix.append(dict(club_id=tid,match_id=f['match_id'],player_id=pid,status=st,minutes=selected,reported_minutes=reported[0] if reported else None,derived_minutes=derived[0] if derived else None,observations=obs,source_refs=[s['source'] for s in ss],issues=issues,minute_method='SOURCE_REPORTED' if reported else 'EVENT_CLOCK_NOMINAL_REGULATION' if selected is not None and st in ['STARTER','SUBSTITUTE_USED'] else 'VERIFIED_NONAPPEARANCE' if selected==0 else 'UNKNOWN'))
 dump('backfill/players.json',list(players.values()));dump('backfill/identity_aliases.json',aliaslog);dump('backfill/sheets.json',sheets);dump('backfill/observed_rosters.json',roster);dump('backfill/matrix.json',matrix);dump('backfill/match_completeness.json',contexts)
 paths=['backfill/fixtures.json','backfill/players.json','backfill/observed_rosters.json','backfill/matrix.json','backfill/sheets.json']
 freeze_path=OUT/'backfill/freeze.json'
 if freeze_path.exists():raise RuntimeError('Frozen observed universe exists; explicit revision required, never overwrite')
 dump('backfill/freeze.json',dict(frozen_at=now(),reference_rosters_read=True,discovery_uses_reference_names=False,revision='Mechanical captain-marker and nonbreaking-space parser fixes; original frozen artifacts retained under revisions/parser_v1',hashes={p:digest((OUT/p).read_bytes()) for p in paths},roster_sizes={t:len(ps) for t,ps in roster.items()},contexts=len(contexts),rows=len(matrix)))
 print('FROZEN',len(players),'canonical players',len(matrix),'pairs',Counter(r['status'] for r in matrix),'minutes missing',sum(r['minutes'] is None for r in matrix))

def reference_and_resolve():
 assert (OUT/'backfill/freeze.json').exists(),'Observed roster must be frozen first'
 frozen=read(OUT/'backfill/freeze.json')
 assert all(digest((OUT/p).read_bytes())==h for p,h in frozen['hashes'].items())
 ps={p['player_id']:p for p in read(OUT/'backfill/players.json')};sheets=read(OUT/'backfill/sheets.json');fs=read(OUT/'backfill/fixtures.json');refs=[];mapping={pid:pid for pid in ps};decisions=[];newplayers={}
 logs=[json.loads(l) for l in (OUT/'http_requests.jsonl').read_text().splitlines()]
 def http(purpose):
  r=next((x for x in logs if x['purpose']==purpose and x['http_status']==200),None)
  if r:return r,(ROOT/r['path']).read_text()
  path=OUT/'raw'/(purpose+'.json');f=read(path);return dict(path=str(path.relative_to(ROOT)),url=f['url'],request_timestamp=f['observed_at'],http_status=None),f['facts']
 def aliases(x):
  out=[]
  if isinstance(x,dict):
   for k,v in x.items():
    if k in ['full_name','display_name','name','alias'] and isinstance(v,str):out.append(v)
    if k=='aliases' and isinstance(v,list):out+=v
    elif isinstance(v,(list,dict)):out+=aliases(v)
  elif isinstance(x,list):
   for y in x:out+=aliases(y)
  return out
 def same(a,b):
  trans=str.maketrans({'ø':'o','Ø':'O','ł':'l','Ł':'L','đ':'d','Đ':'D'});a=a.translate(trans);b=b.translate(trans)
  a=re.sub(r'\s*[©ⓒ]','',a).strip();b=re.sub(r'\s*[©ⓒ]','',b).strip()
  if norm(a)==norm(b):return True
  ta=[norm(t) for t in re.findall(r"[^\W\d_]+",a)];tb=[norm(t) for t in re.findall(r"[^\W\d_]+",b)]
  if not ta or not tb:return False
  if len(ta)==1:return ta[0] in tb and len(ta[0])>3
  if len(tb)==1:return tb[0] in ta and len(tb[0])>3
  return ta[0][0]==tb[0][0] and ta[-1]==tb[-1] and (len(ta[0])==1 or len(tb[0])==1 or ta[0]==tb[0])
 for slug,tid in [('manchester_city',8456),('bayern',9823),('psg',9847),('barcelona',8634),('inter',8636)]:
  path=BASE/f'imports/{slug}/2026-27/H1/standardized_roster.json'
  for r in read(path):
   names=[r['player_name_official'],r.get('player_name_fotmob') or '']+aliases(json.loads(r['identity_evidence']));candidates={p['player_id'] for p in ps.values() if p['player_id'] in {p1['player_id'] for s in sheets if s['club_id']==tid for p1 in s['players']} and any(same(n,a) for n in p['source_names'] for a in names if a)}
   pid=r.get('fotmob_player_id')
   if pid in ps:candidates.add(pid)
   if pid is None:pid=next(iter(candidates)) if len(candidates)==1 else -int(digest(('official:'+str(tid)+r['official_member_id']).encode())[:15],16)
   for old in candidates:
    if old>0 and old!=pid:continue
    mapping[old]=pid;decisions.append(dict(old_player_id=old,player_id=pid,method='EXISTING_VERIFIED_FULL_NAME_AND_ALIAS_BRIDGE_AFTER_FREEZE',source=source(path,location='official_member_id='+r['official_member_id'])))
   display=r.get('player_name_fotmob') or re.sub(r'\s*[©ⓒ]','',r['player_name_official']).strip()
   if pid not in ps:newplayers[pid]=dict(player_id=pid,display_name=display,fotmob_id=r.get('fotmob_player_id'),provider_keys=[],source_names=names[:2],identity_status='REFERENCE_IDENTITY;NOT_IMPLIED_MATCHDAY_MEMBERSHIP')
   refs.append(dict(club_id=tid,player_id=pid,display_name=display,official_name=r['player_name_official'],shirt=r['shirt_number'],definition='Club public first-team page; user-supplied transcription for Bayern/PSG/Barcelona',effective_from=r['effective_from'],effective_to=r['effective_to'],captured_at=r['captured_at'],season_label_verified=False,source=source(path,r['source_url'],location='official_member_id='+r['official_member_id'],acquired=r['captured_at']),identity_status=r['identity_status']))
 # Arsenal official league shirt list: exact published scope, not all registered U21s.
 log,raw=http('arsenal_reference_shirt_list');body=re.search(r'<h5[^>]*>.*?>Arsenal</a></h5>(.*?)(?:<h5|$)',raw,re.S)[1]
 for line in re.split(r'<br\s*/?>',take(r'<p>(.*?)</p>',body)):
  m=re.match(r'(\d+)\s+(.*)',text(line))
  if not m:continue
  shirt,name=m[1],m[2];ids={p['player_id'] for s in sheets if s['club_id']==9825 for p in s['players'] if p.get('shirt')==shirt and (same(p['name'],name) or norm(p['name']) in norm(name) or norm(name) in norm(p['name']))}
  pid=next(iter(ids)) if len(ids)==1 else -int(digest(('pl:arsenal:'+norm(name)).encode())[:15],16)
  if pid not in ps:newplayers[pid]=dict(player_id=pid,display_name=name,fotmob_id=None,provider_keys=[],source_names=[name],identity_status='OFFICIAL_LEAGUE_REFERENCE')
  refs.append(dict(club_id=9825,player_id=pid,display_name=name,official_name=name,shirt=shirt,definition='Premier League 2026/27 published shirt-number list: senior and selected academy; NOT complete U21 registration list',effective_from='2026-09-22',effective_to=None,captured_at=log['request_timestamp'],season_label_verified=True,source=source(ROOT/log['path'],log['url'],'Arsenal section number/name',log['request_timestamp']),identity_status='EXACT_OR_UNIQUE_NAME_SHIRT_CORROBORATION'))
 assert sum(r['club_id']==9825 for r in refs)==25
 # Expand Arsenal youth abbreviations using the independently published U21 list.
 reglog,regraw=http('arsenal_registration_context');section=re.search(r'<h5[^>]*>.*?>Arsenal</a></h5>(.*?)(?:<h5|$)',regraw,re.S)[1]
 reglines=[text(x).strip().replace('*','') for x in re.split(r'<br\s*/?>|</p>|<p[^>]*>',section)]
 for p in ps.values():
  if p['player_id'] not in {x['player_id'] for s in sheets if s['club_id']==9825 for x in s['players']}:continue
  hits=[]
  for line in reglines:
   if ',' not in line:continue
   surname,given=line.split(',',1);full=given.strip()+' '+surname.strip()
   if any(same(n,full) or (len(n.split())==2 and norm(n.split()[0])[:1]==norm(given.strip())[:1] and norm(n.split()[-1]) in norm(surname)) for n in p['source_names']):hits.append(full)
  if len(set(hits))==1 and p['player_id']<0:p['display_name']=hits[0];p['source_names'].append(hits[0]);decisions.append(dict(old_player_id=p['player_id'],player_id=p['player_id'],method='OFFICIAL_PL_U21_FULL_NAME_EXPANSION',name=hits[0],source=source(ROOT/reglog['path'],reglog['url'],'Arsenal U21 section',reglog['request_timestamp'])))
 # Evidence-specific identity corrections. These merge already discovered names only.
 def bridge(oldname,target,tid,purpose,required,reason):
  log,raw=http(purpose);assert all(t.lower() in html.unescape(raw).lower() for t in required)
  ids=[p['player_id'] for p in ps.values() if oldname in p['source_names']]
  assert len(ids)==1,(oldname,ids)
  mapping[ids[0]]=target;decisions.append(dict(old_player_id=ids[0],player_id=target,method=reason,source=source(ROOT/log['path'],log['url'],location='verified full name and match/club context',acquired=log['request_timestamp'])))
 bridge('N Ndiaye',1798782,9823,'ndiaye_match_identity',['Bara Sapoko Ndiaye'],'DFB_SAME_MATCH_AND_SURNAME_AND_SHIRT39_CORRECTS_SKY_INITIAL')
 bridge('P Paez Gaviria',1279040,8634,'gavi_identity',['Pablo','Gavira'],'OFFICIAL_BARCELONA_FULL_NAME_GAVI_ALIAS;SKY_SURNAME_VARIANT')
 bridge('H Selim',1708842,8634,'hamza_identity',['HAMZA','SELIM','ABDELKARIM'],'OFFICIAL_LALIGA_FULL_NAME_AND_SHIRT29_AND_DOB_2008')
 # Full names documented in official public profiles/articles.
 for oldname,newname,purpose,required in [('T Binder','Tim Binder','binder_identity',['Tim Binder']),('D Podar-Stiube','David Podar-Stiube','youth_identity',['David Podar-Stiube']),('Cardozo','Maycon Cardozo','youth_identity',['Maycon Cardozo'])]:
  l,raw=http(purpose);assert all(x in html.unescape(raw) for x in required)
  for p in ps.values():
   if oldname in p['source_names']:p['display_name']=newname;p['source_names'].append(newname);decisions.append(dict(old_player_id=p['player_id'],player_id=p['player_id'],name=newname,method='OFFICIAL_CLUB_FULL_NAME_EXPANSION',source=source(ROOT/l['path'],l['url'],acquired=l['request_timestamp'])))
 for oldname,newname in [('F Stante','Francesco Stante'),('L Bovio','Leonardo Bovio')]:
  l,raw=http('inter_under23_reference');assert all(part in raw for part in newname.split())
  for p in ps.values():
   if oldname in p['source_names']:p['display_name']=newname;p['source_names'].append(newname);decisions.append(dict(old_player_id=p['player_id'],player_id=p['player_id'],name=newname,method='OFFICIAL_INTER_U23_FULL_NAME_PROFILE_AND_FIRST_TEAM_MATCH_SHEET',source=source(ROOT/l['path'],l['url'],acquired=l['request_timestamp'])))
 # Canonical club aliases retain accepted registry IDs without duplicating fixtures.
 aliases_match={identity(209,'2026-09-02T18:45:00Z','osnabruck','bayern'):identity(209,'2026-09-02T18:45:00Z','vflosnabruck','bayern'),identity(42,'2026-09-10T19:00:00Z','bayern','bodoglimt'):identity(42,'2026-09-10T19:00:00Z','bayern','bodøglimt')}
 for f in fs:
  if f['match_id'] in aliases_match:f['discovery_match_id']=f['match_id'];f['match_id']=aliases_match[f['match_id']]
 for s in sheets:
  s['match_id']=aliases_match.get(s['match_id'],s['match_id'])
  for p in s['players']:
   p['discovery_player_id']=p['player_id'];p['player_id']=mapping[p['player_id']]
   if s['provider']=='BVB official' and isinstance(p.get('entry') or p.get('exit'),dict) and (p.get('entry') or p.get('exit')).get('added'):
    p['derived_minutes']=90 if p['role']=='starters' else 0;p['minute_convention']='NOMINAL_REGULATION_EXCLUDES_ADDED_TIME;RAW_CLOCK_PRESERVED_95;LATE_SUB_ZERO_NOMINAL'
  if len({p['player_id'] for p in s['players']})!=len(s['players']):raise ValueError('Identity merge collides inside single raw squad')
 merged={}
 for p in list(ps.values())+list(newplayers.values()):
  pid=mapping.get(p['player_id'],p['player_id']);q=merged.setdefault(pid,dict(p,player_id=pid));q['provider_keys']=sorted(set(q['provider_keys']+p['provider_keys']));q['source_names']=sorted(set(q['source_names']+p['source_names']))
 for r in refs:
  r['player_id']=mapping.get(r['player_id'],r['player_id'])
  if r['player_id'] in merged and r['player_id']>0:merged[r['player_id']]['fotmob_id']=r['player_id']
 roster={str(t):sorted({p['player_id'] for s in sheets if s['club_id']==t for p in s['players']}) for t in CLUBS}
 dump('backfill/resolved/players.json',list(merged.values()));dump('backfill/resolved/sheets.json',sheets);dump('backfill/resolved/fixtures.json',fs);dump('backfill/resolved/observed_rosters.json',roster);dump('reference_rosters.json',refs);dump('identity_corrections.json',decisions);dump('match_id_aliases.json',aliases_match)
 print('Resolved observed',sum(len(v) for v in roster.values()),'roster counts',[(CLUBS[t][0],len(roster[str(t)]),sum(r['club_id']==t for r in refs)) for t in CLUBS])
