"""Pure public-HTML adapters; never fetches a private endpoint or guesses event types."""
import re,json,html
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from .six_player_extract import take,text,players,clock
from .six_club_validation import canon as base_canon,CLUBS,day
from .multi_source_dataset import identity,norm
# Same-page Sky header says Fleetwood Town, lineup header abbreviates it Fleetwood.
def canon(name):return {'fleetwood':'fleetwoodtown'}.get(base_canon(name),base_canon(name))
COMP={'Premier League':47,'Bundesliga':54,'La Liga':87,'LaLiga':87,'Serie A':55,'Ligue 1':53,'UEFA Champions League':42,'Champions League':42,'Carabao Cup':133,'EFL Cup':133,'FA Cup':132,'The FA Cup':132,'DFB Pokal':209,'DFB-Pokal':209,'German DFB Cup':209,'Copa del Rey':138,'Coupe de France':141,'Coppa Italia':137,'Community Shield':247,'UEFA Super Cup':74,'Trophée des Champions':207,'DFL Supercup':'DFL_SUPERCUP'}
class ParseError(ValueError):pass
class Pending(ParseError):pass
class Node:
 def __init__(self,tag='',attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
 def all(self,cls=None,tag=None):
  out=[]
  for c in self.children:
   if not isinstance(c,Node):continue
   if (not cls or cls in c.attrs.get('class','').split()) and (not tag or tag==c.tag):out.append(c)
   out.extend(c.all(cls,tag))
  return out
 def txt(self):return ' '.join(c.txt() if isinstance(c,Node) else c for c in self.children).strip()
 def one(self,cls):
  a=self.all(cls);return a[0] if a else Node()
class Tree(HTMLParser):
 def __init__(self,raw):super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root];self.feed(raw)
 def handle_starttag(self,t,a):
  n=Node(t,a);self.stack[-1].children.append(n)
  if t not in {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}:self.stack.append(n)
 def handle_startendtag(self,t,a):self.stack[-1].children.append(Node(t,a))
 def handle_endtag(self,t):
  for i in range(len(self.stack)-1,0,-1):
   if self.stack[i].tag==t:self.stack=self.stack[:i];break
 def handle_data(self,d):self.stack[-1].children.append(d)
def src(r):return dict(provider='Sky' if 'skysports' in r['url'] or 'sport.sky.de' in r['url'] else 'Torlytics',url=r['url'],path=r.get('reused_body_path',r['path']),sha256=r.get('reused_body_sha256',r['sha256']),acquired_at=r['response_timestamp'],http_status=r['http_status'],location='HTML own-side XI/bench and explicitly typed substitution/red-card clocks')
def fixture(comp,kick,home,away,score,source):
 if comp not in COMP:raise ParseError('UNSUPPORTED_COMPETITION:'+str(comp))
 home,away=canon(home),canon(away);cid=COMP[comp]
 if home==away or not {home,away}&{x[1] for x in CLUBS.values()}:raise ParseError('TEAM_IDENTITY_MISMATCH')
 return dict(match_id=identity(cid,kick,home,away),competition_id=cid,competition=comp,kickoff=kick,scheduled_kickoff=kick,match_date=day(kick),home_team=home,away_team=away,home_id=next((t for t,x in CLUBS.items() if x[1]==home),None),away_id=next((t for t,x in CLUBS.items() if x[1]==away),None),score=score,match_state='COMPLETED',source_evidence=[source])
def nominal(c):
 if not c:return None
 b=c['base'];return 45 if b==46 and not c.get('added') else min(b,90)
def finish(ps,events,source,tid,provider):
 if sum(p['role']=='starters' for p in ps)!=11 or not any(p['role']=='bench' for p in ps):raise Pending('XI_OR_FULL_BENCH_UNAVAILABLE')
 keys=[p['provider_key'] for p in ps]
 if len(keys)!=len(set(keys)):raise ParseError('DUPLICATE_PLAYER_ID')
 incoming={};outgoing={};reds={};pitch={p['provider_key'] for p in ps if p['role']=='starters'};bench=set(keys)-pitch
 # Torlytics events carry a named pair; Sky carries independently typed endpoints.
 for e in events:
  k=e.get('provider_key');typ=e['type'];c=e.get('clock')
  if typ in ['sub','sub-on','sub-off','red','yellow-red','second-yellow','red-card','yellow-red-card'] and (not c or c['base']>90):raise Pending('EXTRA_TIME_OR_CLOCK_UNSUPPORTED')
  if typ=='sub':
   out=e['out_key']
   if out not in pitch or k not in bench or k in incoming:raise ParseError('INVALID_SUBSTITUTION_SEQUENCE')
   outgoing[out]=c;incoming[k]=c;pitch.remove(out);pitch.add(k)
  elif typ=='sub-on':
   if k not in bench or k in incoming:raise ParseError('INVALID_INCOMING_PLAYER')
   incoming[k]=c
  elif typ=='sub-off':
   if k not in keys or k in outgoing:raise ParseError('INVALID_OUTGOING_PLAYER')
   outgoing[k]=c
  elif typ in ['red','yellow-red','second-yellow','red-card','yellow-red-card']:
   if k not in keys:raise ParseError('RED_PLAYER_UNKNOWN')
   reds[k]=c
 if len(incoming)!=len(outgoing):raise ParseError('UNBALANCED_SUBSTITUTIONS')
 in_clocks=sorted((x['base'],x.get('added',0)) for x in incoming.values());out_clocks=sorted((x['base'],x.get('added',0)) for x in outgoing.values())
 if in_clocks!=out_clocks:raise ParseError('SUBSTITUTION_CLOCK_DISAGREEMENT')
 for p in ps:
  k=p['provider_key'];entry=incoming.get(k);exit_=outgoing.get(k) or reds.get(k);start=0 if p['role']=='starters' else nominal(entry);end=nominal(exit_) if exit_ else 90
  if p['role']=='starters' and k in incoming:raise ParseError('STARTER_MARKED_SUB_ON')
  if k in outgoing and p['role']=='bench' and k not in incoming:raise ParseError('UNUSED_PLAYER_MARKED_SUB_OFF')
  if k in reds and p['role']=='bench' and k not in incoming:raise ParseError('BENCH_RED_NOT_ON_FIELD;MANUAL_REVIEW')
  if start is not None and end<start:raise ParseError('NEGATIVE_PLAYING_INTERVAL')
  p.update(status='STARTER' if p['role']=='starters' else 'SUBSTITUTE_USED' if entry else 'UNUSED_SUBSTITUTE',derived_minutes=(end-start) if start is not None else 0,entry=entry,exit=exit_)
  if p.get('reported_minutes') is not None and abs(p['reported_minutes']-p['derived_minutes'])>=5:raise ParseError('REPORTED_DERIVED_CONFLICT')
 return dict(club_id=tid,provider=provider,players=ps,events=events,complete_squad=True,complete_events=True,source=source,issues=['SIX_PLUS_SUBSTITUTIONS_AUTHORIZATION_UNVERIFIED'] if len(incoming)>5 else [],minute_convention='NOMINAL_90_EXCLUDES_ADDED_TIME;HALFTIME_46_TO45;LATE_ENTRANT_MAY_ZERO')
def torly_detail(raw,r):
 js=[json.loads(x) for x in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',raw,re.S)];e=next((x for x in js if isinstance(x,dict) and x.get('@type')=='SportsEvent'),None)
 if not e:raise ParseError('SPORTSEVENT_MISSING')
 if text(take(r'match-score__state">(.*?)</span>',raw))!='Ende':raise Pending('NOT_CONFIRMED_FULL_TIME')
 f=fixture(e.get('organizer',{}).get('name'),e['startDate'],e['homeTeam']['name'],e['awayTeam']['name'],text(take(r'match-score__value">(.*?)</span>',raw)).replace('–','-').replace(' ','').replace(':','-'),src(r))
 if datetime.fromisoformat(f['kickoff'].replace('Z','+00:00'))>datetime.now(timezone.utc):raise Pending('FUTURE_KICKOFF')
 if re.search(r'Verlängerung|nach Verl|120\s*\+',text(raw),re.I):raise Pending('EXTRA_TIME_REQUIRES_VALIDATED_ADAPTER')
 stats={}
 for tb in re.findall(r'<table class="data-table werte-table">(.*?)</table>',raw,re.S):
  for tr in re.findall(r'<tr>(.*?)</tr>',tb,re.S):
   a=re.search(r'<a href="([^\"]+)">(.*?)</a>',tr,re.S);num=text(take(r'<td class="num">(.*?)</td>',tr))
   if a and num.isdigit():stats[html.unescape(a[1])]=int(num)
 sheets=[];blocks=re.split(r'<div class="lineup-col lineup-col--(home|away)">',raw)
 both=[]
 for i in range(1,len(blocks),2):
  side,b=blocks[i:i+2];xi=players(take(r'<ol class="lineup-list lineup-section--xi">(.*?)</ol>',b));bn=players(take(r'<ol class="lineup-list lineup-list--bench">(.*?)</ol>',b));allp=xi+bn;both.append({p['provider_player_url'] for p in allp});tid=f.get(side+'_id')
  # Header in each roster column independently binds the side to the SportsEvent.
  heading=text(take(r'<h3 class="lineup-col__title">(.*?)<span',b))
  if canon(heading)!=f[side+'_team']:raise ParseError('ROSTER_HEADER_TEAM_MISMATCH:'+heading)
  if not tid:continue
  ps=[dict(provider_key=p['provider_player_url'],name=p['name'],shirt=p['shirt'],role=role,reported_minutes=stats.get(p['provider_player_url']),json_path='.lineup-col--'+side+'/'+role) for role,group in [('starters',xi),('bench',bn)] for p in group];ns={norm(p['name']):p['provider_key'] for p in ps};evs=[]
  if len(ns)!=len(ps):raise ParseError('AMBIGUOUS_OUTGOING_NAME')
  for body in re.findall(r'<li class="event event--'+side+r'">(.*?)</li>',raw,re.S):
   typ=take(r'event__icon--([a-z-]+)',body);u=html.unescape(take(r'event__name">.*?<a href="([^\"]+)"',body));c=clock(text(take(r'event__minute">(.*?)</span>',body)));ev=dict(type=typ,provider_key=u,clock=c)
   if typ=='sub':ev['out_key']=ns.get(norm(text(take(r'event__sub-out">(.*?)</span>',body)).replace('↓ raus:','').strip()))
   evs.append(ev)
  s=finish(ps,evs,src(r),tid,'Torlytics');s['match_id']=f['match_id'];sheets.append(s)
 if len(both)!=2 or both[0]&both[1]:raise ParseError('TWO_SIDE_IDENTITY_INTEGRITY_FAILED')
 return f,sheets

def uk_datetime(s):
 s=re.sub(r'(\d)(?:st|nd|rd|th)\b',r'\1',s.strip().rstrip('.'));d=datetime.strptime(s,'%I:%M%p, %A %d %B %Y').replace(tzinfo=ZoneInfo('Europe/London'));return d.astimezone(timezone.utc).isoformat()
def sky_detail(raw,r):
 root=Tree(raw).root;headers=root.all('sdc-site-match-header')
 if not headers:raise ParseError('SKY_MATCH_HEADER_MISSING')
 h=headers[0]
 if h.attrs.get('data-status')!='4':raise Pending('SKY_NOT_FULL_TIME')
 # Never infer FT from hidden status template labels.
 if 'After Extra Time' in h.one('sdc-site-match-header__detail-fixture').txt():raise Pending('EXTRA_TIME_REVIEW')
 label=h.one('sdc-site-match-header__detail-fixture').txt();comp=next((k for k in sorted(COMP,key=len,reverse=True) if re.search(r'\.\s*'+re.escape(k)+r'\b',label)),None)
 names=h.all('sdc-site-match-header__team-name-block-target');kick=uk_datetime(h.one('sdc-site-match-header__detail-time').txt());scores=[n.txt() for n in h.all('sdc-site-match-header__team-score-block')]
 if len(names)!=2 or len(scores)!=2:raise ParseError('SKY_TEAM_SCORE_MISSING')
 f=fixture(comp,kick,names[0].txt(),names[1].txt(),'-'.join(scores),src(r));return _sky_lineups(root,f,r)

def _sky_lineups(root,f,r):
 cols=root.all('sdc-site-team-lineup__col');sheets=[];all_names=[]
 if len(cols)!=2:raise Pending('SKY_TWO_LINEUP_COLUMNS_REQUIRED')
 for side,col in zip(['home','away'],cols):
  own=col.one('sdc-site-team-lineup__team-name').txt()
  if canon(own)!=f[side+'_team']:raise ParseError('SKY_ROSTER_SIDE_MISMATCH')
  tid=f.get(side+'_id');ps=[];evs=[]
  for group in col.all('sdc-site-team-lineup__players'):
   role='bench' if 'sdc-site-team-lineup__players--subs' in group.attrs.get('class','') else 'starters';numbers=group.all('sdc-site-team-lineup__player-number');dds=group.all('sdc-site-team-lineup__player-name')
   if len(numbers)!=len(dds):raise ParseError('SKY_PLAYER_PAIRING_MISMATCH')
   for n,dd in zip(numbers,dds):
    full=(dd.one('sdc-site-team-lineup__player-initial').attrs.get('title','')+' '+dd.one('sdc-site-team-lineup__player-surname').txt()).strip()
    if not full:raise ParseError('SKY_FULL_NAME_MISSING')
    key='sky-name:'+str(tid or own)+':'+norm(full);ps.append(dict(provider_key=key,name=full,shirt=n.txt(),role=role,reported_minutes=None,json_path='sdc-site-team-lineup__col/'+side+'/'+role+'/full-title+surname'))
    for e in dd.all('sdc-site-team-lineup__event'):
     icon=e.one('sdc-site-team-lineup__item-event-icon');types=[x.split('icon--',1)[1] for x in icon.attrs.get('class','').split() if 'icon--' in x]
     if len(types)!=1:raise ParseError('SKY_UNTYPED_EVENT')
     evs.append(dict(type=types[0],provider_key=key,clock=clock(e.one('sdc-site-team-lineup__event_time').txt())))
  all_names.append({norm(p['name']) for p in ps})
  if tid:
   s=finish(ps,evs,src(r),tid,'Sky');s['match_id']=f['match_id'];sheets.append(s)
 if all_names[0]&all_names[1]:raise ParseError('PLAYER_ON_BOTH_SIDES')
 return f,sheets

def catalogs(raw,provider,tid,asof):
 """Directory discovery independent of roster and cached fixture seeds."""
 out=[]
 if provider=='Torlytics':
  for url in sorted(set(html.unescape(x) for x in re.findall(r'href="(https://torlytics.de/spiel/[^\"]+)"',raw))):
   m=re.search(r'(\d{4}-\d{2}-\d{2})$',url)
   if m:
    anchor=re.search(r'<a href="'+re.escape(url)+r'" class="upcoming-fixture">(.*?)</a>',raw,re.S)
    scored=bool(anchor and re.search(r'upcoming-fixture__score[^>]*>\s*\d+\s*[:–-]\s*\d+',anchor[1]))
    out.append(dict(url=url,date=m[1],provider=provider,club_id=tid,catalog_completed=scored if anchor else m[1]<asof))
 else:
  for payload in re.findall(r'data-state="([^"<>]*&quot;matchURL&quot;[^"<>]*)"',raw):
   d=json.loads(html.unescape(payload));ds=re.sub(r'(\d)(?:st|nd|rd|th)\b',r'\1',d['start']['date']);year=int(asof[:4]);md=datetime.strptime(ds,'%A %d %B');year=year-1 if md.month>int(asof[5:7])+6 else year;date_=md.replace(year=year).date().isoformat();path=d['matchURL'];parts=path.rsplit('/',1);url='https://www.skysports.com'+parts[0]+'/teams/'+parts[1]
   out.append(dict(url=url,date=date_,provider=provider,club_id=tid,catalog_completed=bool(d['isResult']) and d['status']=='FT',catalog_provider_id=str(d['id']),catalog_competition=d['competition']['name']['full'],catalog_home=canon(d['teams']['home']['name']['full']),catalog_away=canon(d['teams']['away']['name']['full']),date_precision='CURRENT_MONTH_DIRECTORY;DETAIL_VERIFIES_YEAR_AND_KICKOFF'))
 if not out:raise ParseError('EMPTY_DIRECTORY_IS_NOT_NO_MATCHES')
 return out
