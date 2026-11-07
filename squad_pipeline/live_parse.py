"""Pure public-HTML adapters; never fetches a private endpoint or guesses event types."""
import re,json,html
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from .six_player_extract import take,text,players,clock
from .six_club_validation import canon as base_canon,CLUBS,day
from .multi_source_dataset import identity,norm
# Same-page Sky header says Fleetwood Town, lineup header abbreviates it Fleetwood.
def canon(name):
 value=base_canon(name)
 # Canonical club names are configuration bindings, not match/player-specific aliases.
 from .core import ROOT
 from functools import lru_cache
 mappings=configured_club_names()
 return mappings.get(value,{'fleetwood':'fleetwoodtown'}.get(value,value))

from functools import lru_cache
@lru_cache(maxsize=1)
def configured_club_names():
 from .core import ROOT
 rows=json.loads((ROOT/'ops/cloud/clubs.json').read_text());result={}
 for row in rows:
  for field in ('name','canonical_slug','sky_slug','torlytics_slug'):
   key=base_canon(row[field]);value=row['canonical_slug']
   if key in result and result[key]!=value:raise ParseError('AMBIGUOUS_CONFIGURED_CLUB_NAME')
   result[key]=value
 return result

from .competition_identity import COMP
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
def src(r):return dict(provider='Sky' if 'skysports' in r['url'] or 'sport.sky.de' in r['url'] else 'Torlytics',url=r['url'],path=r.get('reused_body_path',r['path']),sha256=r.get('reused_body_sha256',r['sha256']),acquired_at=r['response_timestamp'],http_status=r['http_status'],location='HTML own-side XI/bench and explicitly typed substitution/red-card clocks',catalog_source=(r.get('catalog_candidate') or {}).get('catalog_source'),directory_match_id=(r.get('catalog_candidate') or {}).get('catalog_provider_id'),directory_team_labels=(r.get('catalog_candidate') or {}).get('catalog_team_labels'))
def fixture(comp,kick,home,away,score,source):
 if comp not in COMP:raise ParseError('UNSUPPORTED_COMPETITION:'+str(comp))
 source_names=dict(home=home,away=away);home,away=canon(home),canon(away);cid=COMP[comp]
 if home==away or not {home,away}&{x[1] for x in CLUBS.values()}:raise ParseError('TEAM_IDENTITY_MISMATCH')
 return dict(match_id=identity(cid,kick,home,away),competition_id=cid,competition=comp,source_team_names=source_names,kickoff=kick,scheduled_kickoff=kick,match_date=day(kick),home_team=home,away_team=away,home_id=next((t for t,x in CLUBS.items() if x[1]==home),None),away_id=next((t for t,x in CLUBS.items() if x[1]==away),None),score=score,match_state='COMPLETED',source_evidence=[source])
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

def certified_catalog_side(observed,fixture,side,source):
 c=source.get('catalog_candidate') or {};labels=c.get('catalog_team_labels') or {};label=labels.get(side) or {};other=labels.get('away' if side=='home' else 'home') or {};catalog=c.get('catalog_source') or {}
 provider_id=str(c.get('catalog_provider_id') or '')
 return bool(provider_id and source['url'].rstrip('/').rsplit('/',1)[-1]==provider_id and len(catalog.get('sha256',''))==64 and catalog.get('url','').startswith('https://www.skysports.com/') and str(label.get('provider_team_id','')).isdigit() and str(other.get('provider_team_id','')).isdigit() and int(label['provider_team_id'])>0 and int(other['provider_team_id'])>0 and label['provider_team_id']!=other['provider_team_id'] and canon(label.get('full_name',''))==fixture[side+'_team'] and canon(label.get('short_name',''))==canon(observed) and c.get('catalog_'+side)==fixture[side+'_team'])

def certified_badge_sides(cols,fixture,source):
 """Same-page ordered badge captions bind abbreviated lineup headings.

 This proves a presentation label only, never a cross-source team identity.
 Both columns must independently agree with the already parsed match header.
 """
 from urllib.parse import urlsplit
 if len(cols)!=2 or urlsplit(source.get('url','')).hostname!='www.skysports.com':return False
 if not re.fullmatch(r'[a-f0-9]{64}',source.get('sha256','')):return False
 ids=[]
 for side,col in zip(('home','away'),cols):
  badges=col.all('sdc-site-team-lineup__team-badge-image')
  if len(badges)!=1:return False
  b=badges[0].attrs;u=urlsplit(b.get('src',''))
  m=re.fullmatch(r'/football/badges/96/([1-9][0-9]*)\.png',u.path)
  if u.scheme!='https' or u.netloc!='e0.365dm.com' or u.query or u.fragment or not m:return False
  if canon(b.get('alt',''))!=fixture[side+'_team']:return False
  own=col.one('sdc-site-team-lineup__team-name').txt()
  full=re.findall(r'[a-z0-9]+',b.get('alt','').lower());short=re.findall(r'[a-z0-9]+',own.lower())
  # Permit ordered whole words or >=3-letter prefixes; initials require another word.
  if canon(own)!=fixture[side+'_team']:
   pos=0
   for word in short:
    while pos<len(full) and not (word==full[pos] or len(word)>=3 and full[pos].startswith(word) or len(word)==1 and len(short)>1 and any(len(w)>=3 for w in short) and full[pos].startswith(word)):pos+=1
    if pos==len(full):break
    pos+=1
   else:
    if short:ids.append(m[1]);continue
   if ''.join(short)!=''.join(w[0] for w in full) or len(short)!=1:return False
  ids.append(m[1])
 return len(ids)==2 and ids[0]!=ids[1]

def _sky_lineups(root,f,r):
 cols=root.all('sdc-site-team-lineup__col');sheets=[];all_names=[]
 if len(cols)!=2:raise Pending('SKY_TWO_LINEUP_COLUMNS_REQUIRED')
 for side,col in zip(['home','away'],cols):
  own=col.one('sdc-site-team-lineup__team-name').txt()
  if canon(own)!=f[side+'_team'] and not certified_catalog_side(own,f,side,r) and not certified_badge_sides(cols,f,r):raise ParseError('SKY_ROSTER_SIDE_MISMATCH')
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
   s=finish(ps,evs,src(r),tid,'Sky');s['match_id']=f['match_id'];s['source_label_binding']='SAME_PAGE_ORDERED_BADGE_CAPTIONS' if certified_badge_sides(cols,f,r) else 'HEADER_OR_CERTIFIED_DIRECTORY';sheets.append(s)
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
   out.append(dict(url=url,date=date_,provider=provider,club_id=tid,catalog_completed=bool(d['isResult']) and d['status']=='FT',catalog_provider_id=str(d['id']),catalog_competition=d['competition']['name']['full'],catalog_home=canon(d['teams']['home']['name']['full']),catalog_away=canon(d['teams']['away']['name']['full']),date_precision='CURRENT_MONTH_DIRECTORY;DETAIL_VERIFIES_YEAR_AND_KICKOFF',catalog_team_labels={side:dict(provider_team_id=str(d['teams'][side].get('id','')),full_name=d['teams'][side]['name']['full'],short_name=d['teams'][side]['name'].get('short')) for side in ('home','away')}))
 if not out:raise ParseError('EMPTY_DIRECTORY_IS_NOT_NO_MATCHES')
 return out


def psg_catalogue(raw, source_url, club_id, season):
 """Native official season discovery, with retained identities and visible coverage."""
 from urllib.parse import urlsplit,urljoin
 from .identity_sources import flight_objects
 u=urlsplit(source_url);match=re.fullmatch(r'/en/mens-football/fixtures/season/(20\d{2}-\d{2})',u.path)
 if club_id!=9847 or u.scheme!='https' or u.netloc!='www.psg.fr' or u.query or u.fragment or not match or match[1]!=season:raise ParseError('PSG_CATALOGUE_REQUEST_IDENTITY_MISMATCH')
 if int(season[-2:])!=(int(season[:4])+1)%100:raise ParseError('PSG_CATALOGUE_SEASON_INVALID')
 payloads=[(path,v) for _,_,path,v in flight_objects(raw) if 'initialItems' in v and 'seasonId' in v and 'teamIds' in v]
 if len(payloads)!=1:raise Pending('PSG_UNIQUE_CATALOGUE_REQUIRED')
 path,v=payloads[0]
 if v['seasonId']!=season or len(v['teamIds'])!=1:raise ParseError('PSG_CATALOGUE_RETURNED_SEASON_MISMATCH')
 team_id=str(v['teamIds'][0]);out=[];seen=set()
 for i,item in enumerate(v['initialItems']):
  props=item.get('properties') or {};m=props.get('mdmMatch') or {};mid=m.get('id_statsperf')
  if item.get('contentType')!='matchSheet' or not mid or m.get('id')!=mid or props.get('mdmMatchFixture')!=mid or mid in seen:raise ParseError('PSG_CATALOGUE_NATIVE_MATCH_ID_INVALID')
  teams={side:m.get(side+'_team_id') or {} for side in ['home','away']};own=[t for t in teams.values() if str(t.get('id'))==team_id]
  if len(own)!=1 or own[0].get('Gender')!='Male' or own[0].get('Local_section')!='Pros' or canon(own[0].get('Name_official',''))!=canon('Paris Saint-Germain'):raise ParseError('PSG_CATALOGUE_TEAM_SCOPE_MISMATCH')
  route=(item.get('route') or {}).get('path','');url=urljoin(source_url,route).rstrip('/');target=urlsplit(url)
  if target.scheme!='https' or target.netloc!='www.psg.fr' or target.query or target.fragment or not re.fullmatch(r'/en/matches/mens-football/20\d{6}/[^/]+',target.path):raise ParseError('PSG_CATALOGUE_DETAIL_LINK_SCOPE_MISMATCH')
  kickoff=m.get('timeUtc');dt=datetime.fromisoformat((kickoff or '').replace('Z','+00:00'))
  if dt.tzinfo is None:raise Pending('PSG_CATALOGUE_EXPLICIT_UTC_REQUIRED')
  seen.add(mid);out.append(dict(provider='PSG',provider_match_id=mid,provider_team_id=team_id,club_id=club_id,source_season=season,kickoff=kickoff,source_match_date=m.get('matchDate'),source_local_datetime=m.get('localmatchdatetime'),teams=teams,competition=props.get('mdmCompetition') or m.get('tournament_id'),source_status=m.get('match_status'),home_score=m.get('home_team_score'),away_score=m.get('away_team_score'),url=url,source_field_path=path+f'.initialItems[{i}]',canonical_adjudication=False))
 visible={urljoin(source_url,n.attrs['href']).rstrip('/') for n in Tree(raw).root.all(tag='a') if '/en/matches/mens-football/' in n.attrs.get('href','')}
 if visible!={f['url'] for f in out}:raise Pending('PSG_CATALOGUE_VISIBLE_NATIVE_COVERAGE_DIFFERS')
 if not out:raise Pending('EMPTY_DIRECTORY_IS_NOT_NO_MATCHES')
 return out

def psg_match_squad(raw, source_url, match_id, team_id, club_id):
 """Read the retained official match payload, never the related profile-card list."""
 from urllib.parse import urlsplit
 from .identity_sources import flight_objects
 u=urlsplit(source_url)
 if club_id!=9847 or u.scheme!='https' or u.netloc!='www.psg.fr' or not re.fullmatch(r'/en/matches/mens-football/20\d{6}/[^/]+',u.path) or u.query or u.fragment:
  raise ParseError('PSG_MATCH_REQUEST_SCOPE_MISMATCH')
 if not match_id or not team_id:raise Pending('PSG_INDEPENDENT_MATCH_AND_TEAM_IDS_REQUIRED')
 payloads=[(path,v) for _,_,path,v in flight_objects(raw) if isinstance(v.get('initialMatchData'),dict) and 'matchId' in v]
 if len(payloads)!=1:raise Pending('PSG_UNIQUE_MATCH_PAYLOAD_REQUIRED')
 path,v=payloads[0];m=v['initialMatchData']
 if str(v['matchId'])!=str(match_id) or m.get('id') not in [None,'',str(match_id)]:raise ParseError('PSG_RETURNED_MATCH_ID_MISMATCH')
 teams=m.get('teams') or {};own=[(side,t) for side,t in teams.items() if str(t.get('id'))==str(team_id)]
 if len(own)!=1:raise ParseError('PSG_RETURNED_TEAM_ID_MISMATCH')
 side,t=own[0]
 if t.get('Gender')!='Male' or t.get('Local_section')!='Pros' or canon(t.get('Name_official') or t.get('officialName') or '')!=canon('Paris Saint-Germain'):raise ParseError('PSG_MATCH_TEAM_SCOPE_MISMATCH')
 kickoff=m.get('matchStartDateTime')
 if not isinstance(kickoff,str):raise Pending('PSG_MATCH_KICKOFF_REQUIRED')
 kickoff=kickoff.removeprefix('$D');dt=datetime.fromisoformat(kickoff.replace('Z','+00:00'))
 if dt.tzinfo is None:raise Pending('PSG_MATCH_EXPLICIT_TIMEZONE_REQUIRED')
 fixture=dict(provider='PSG',provider_match_id=str(match_id),kickoff=kickoff,source_teams=teams,competition=m.get('competition'),match_state=m.get('matchStatus'),source_field_path=path+'.initialMatchData')
 availability=m.get('lineupStatus') or {};players=t.get('players')
 if availability.get('available') is not True or availability.get('show') is not True or not isinstance(players,list) or not players:
  return dict(fixture=fixture,sheets=[],lineup_available=False,related_profile_cards_are_not_squad=True)
 if not m.get('id'):raise Pending('PSG_SQUAD_PAYLOAD_MATCH_ID_REQUIRED')
 out=[];seen=set()
 for i,p in enumerate(players):
  native_id=p.get('id');name=' '.join(str(p.get(k) or '') for k in ['firstName','lastName']).strip()
  if not native_id or str(native_id) in seen or not name or '$' in name:raise ParseError('PSG_PLAYER_ID_OR_NAME_INVALID')
  seen.add(str(native_id));stats=p.get('stat') or [];started=[x.get('value') for x in stats if x.get('type')=='gameStarted'];bench=p.get('position')=='Substitute'
  if started not in [[],['1']] or bench and started:raise ParseError('PSG_PLAYER_ROLE_CONFLICT')
  if not bench and started!=['1']:raise Pending('PSG_STARTER_ROLE_EVIDENCE_REQUIRED')
  out.append(dict(provider_key='PSG_STATS_PERFORM:'+str(native_id),provider_player_id=str(native_id),provider_id_namespace='PSG_STATS_PERFORM',name=name,source_names=[n for n in [p.get('knownName'),p.get('matchName')] if isinstance(n,str) and n and not n.startswith('$')],role='bench' if bench else 'starters',reported_stats=stats,substitution=p.get('substitution'),json_path=path+f'.initialMatchData.teams.{side}.players[{i}]'))
 if sum(p['role']=='starters' for p in out)!=11:raise Pending('PSG_ELEVEN_STARTERS_REQUIRED')
 return dict(fixture=fixture,sheets=[dict(provider='PSG',configured_club_id=club_id,provider_team_id=str(team_id),players=out,complete_squad=True,source_completeness_basis='Explicit available lineup with eleven source-typed starters and source-listed substitutes; canonical identity not adjudicated')],lineup_available=True,related_profile_cards_are_not_squad=True)

def mancity_report_squad(raw, source_url, club_id):
 """Official report labels, without invented identities, unused status or minutes."""
 from urllib.parse import urlsplit
 from .identity_sources import jsonld
 u=urlsplit(source_url)
 if club_id!=8456 or u.scheme!='https' or u.netloc!='www.mancity.com' or not u.path.startswith('/news/mens/') or u.query or u.fragment:
  raise ParseError('OFFICIAL_REPORT_REQUEST_IDENTITY_MISMATCH')
 articles=[a for a in jsonld(raw) if a.get('@type')=='NewsArticle' and isinstance(a.get('mainEntityOfPage'),dict) and a['mainEntityOfPage'].get('@id')==source_url]
 if len(articles)!=1:raise ParseError('OFFICIAL_REPORT_RETURNED_IDENTITY_MISMATCH')
 paragraphs=[' '.join(p.txt().split()) for p in Tree(raw).root.all(tag='p')]
 xi_prefix=r'^CITY(?: XI)?\s*[:|]\s*'
 bench_prefix=r'^(Subs(?: not used)?|Unused subs)\s*:\s*'
 indices=[i for i,p in enumerate(paragraphs) if re.match(xi_prefix,p)]
 if len(indices)!=1:raise Pending('OFFICIAL_REPORT_UNIQUE_CITY_XI_REQUIRED')
 i=indices[0]
 if i+1>=len(paragraphs) or not re.match(bench_prefix,paragraphs[i+1],re.I):raise Pending('OFFICIAL_REPORT_ADJACENT_BENCH_REQUIRED')
 bench_label=re.match(bench_prefix,paragraphs[i+1],re.I)[1]
 xi=re.sub(xi_prefix,'',paragraphs[i]);bench=re.sub(bench_prefix,'',paragraphs[i+1],flags=re.I)
 entries=[v.strip() for v in xi.split(',')]
 if len(entries)!=11 or any(not v for v in entries):raise Pending('OFFICIAL_REPORT_ELEVEN_STARTERS_REQUIRED')
 starters=[];replacement_mentions=[]
 for entry in entries:
  if entry.count('(')!=entry.count(')'):raise Pending('OFFICIAL_REPORT_UNBALANCED_SUBSTITUTION_TEXT')
  name=re.sub(r'\([^)]*\)','',entry).strip()
  if not name or re.search(r'\d',name):raise Pending('OFFICIAL_REPORT_PLAYER_LABEL_REQUIRED')
  starters.append(name)
  replacement_mentions.extend(v.strip() for v in re.findall(r'\(([^)]*)\)',entry) if v.strip().upper()!='C')
 if len({base_canon(n) for n in starters})!=11:raise Pending('OFFICIAL_REPORT_DUPLICATE_STARTER_LABEL')
 listed=[v.strip() for v in bench.split(',')]
 if not listed or any(not v or re.search(r'[()\d]',v) for v in listed):raise Pending('OFFICIAL_REPORT_BENCH_LABEL_REQUIRED')
 return dict(club_id=club_id,starters=starters,listed_bench=listed,replacement_mentions=replacement_mentions,raw_xi=xi,raw_bench=bench,bench_wording=bench_label,article_date=articles[0].get('datePublished'),article_headline=articles[0].get('headline'),complete_squad=False,canonical_adjudication=False,unused_substitutes_confirmed=False)

def mancity_catalogue(raw, source_url, club_id):
 """Retained seasonal results format; discovery observations, not fixture admission."""
 from urllib.parse import urlsplit,urljoin
 from .identity_sources import jsonld
 u=urlsplit(source_url);m=re.fullmatch(r'/results/mens/season-(20[0-9]{2})-([0-9]{2})',u.path)
 if u.scheme!='https' or u.netloc!='www.mancity.com' or u.query or u.fragment or not m or club_id!=8456:raise ParseError('OFFICIAL_CATALOGUE_REQUEST_IDENTITY_MISMATCH')
 year=int(m[1])
 if int(m[2])!=(year+1)%100:raise ParseError('OFFICIAL_CATALOGUE_SEASON_IDENTITY_MISMATCH')
 root=Tree(raw).root;bindings=[n.attrs['data-season-id'] for n in root.all() if 'data-season-id' in n.attrs]
 if bindings!=[str(year)]:raise ParseError('OFFICIAL_CATALOGUE_SEASON_IDENTITY_MISMATCH')
 selected=[n.attrs.get('value') for n in root.all(tag='option') if 'selected' in n.attrs and n.attrs.get('value','').startswith('/results/mens')]
 if selected!=[u.path]:raise ParseError('OFFICIAL_CATALOGUE_SELECTED_SEASON_IDENTITY_MISMATCH')
 actions=[d for d in jsonld(raw) if isinstance(d,dict) and isinstance(d.get('object'),dict) and d.get('@type') in {'WinAction','LoseAction','TieAction'} and d.get('object',{}).get('@type')=='SportsEvent']
 blocks=root.all('fixtures-list-item')
 if not actions or len(actions)!=len(blocks):raise Pending('OFFICIAL_CATALOGUE_VISIBLE_STRUCTURED_COVERAGE_DIFFERS')
 out=[];seen=set()
 for d,block in zip(actions,blocks):
  e=d['object'];names=html.unescape(e.get('name','')).split(' vs ')
  if len(names)!=2 or 'manchestercity' not in {canon(x) for x in names}:raise ParseError('OFFICIAL_CATALOGUE_TEAM_IDENTITY_MISMATCH')
  if canon(block.attrs.get('aria-label',''))!=canon(names[0]+' versus '+names[1]):raise ParseError('OFFICIAL_CATALOGUE_VISIBLE_TEAM_IDENTITY_MISMATCH')
  start=html.unescape(e['startDate']);instant=datetime.fromisoformat(start)
  if instant.tzinfo is None:raise ParseError('OFFICIAL_CATALOGUE_EXPLICIT_TIMEZONE_REQUIRED')
  score=d.get('result');visible=[x.txt() for x in block.all('fixture-body__score-goals')]
  if not re.fullmatch(r'[0-9]+-[0-9]+',score or '') or visible!=score.split('-'):raise ParseError('OFFICIAL_CATALOGUE_SCORE_IDENTITY_MISMATCH')
  times=block.all(tag='time')
  if len(times)!=1:raise Pending('OFFICIAL_CATALOGUE_VISIBLE_DATE_REQUIRED')
  shown=times[0].attrs.get('datetime');shown_dt=datetime.fromisoformat(shown)
  if shown_dt.tzinfo is None:raise ParseError('OFFICIAL_CATALOGUE_EXPLICIT_VISIBLE_TIMEZONE_REQUIRED')
  key=(start,*names)
  if key in seen:raise ParseError('OFFICIAL_CATALOGUE_DUPLICATE_EVENT')
  seen.add(key)
  links=[dict(url=urljoin(source_url,n.attrs['href']),label=n.txt()) for n in block.all(tag='a') if n.attrs.get('href')]
  out.append(dict(provider='Mancity',club_id=club_id,source_season=f'{year}/{str(year+1)[2:]}',source_event_name=e['name'],source_start=start,visible_datetime=shown,datetime_conflict=shown_dt!=instant,date_conflict=shown_dt.date()!=instant.date(),home_name=names[0],away_name=names[1],score=score,competition_label=None,detail_links=links,canonical_adjudication=False,scope_verified=False))
 return out
