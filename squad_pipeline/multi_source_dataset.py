"""Three-player factual comparison from acquired responses. No legacy writes or online requests."""
import csv,io,json,re,html,sqlite3,unicodedata
from collections import defaultdict,Counter
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from html.parser import HTMLParser
from .core import BASE,ROOT,read,write,table,digest,now
from .dynamic_sources import CacheSources,PLAYERS,event_day
from futbol.common import instant
OUT=BASE/'audit/multi_source';START='2026-08-15';END='2026-10-28'
TARGETS={696679:('barcelona','brazil'),737066:('manchestercity','norway'),828159:('bayern','southkorea')}
ALIASES={'manchestercity':'manchestercity','mancity':'manchestercity','bayernmünchen':'bayern','bayernmunchen':'bayern','bayernmunich':'bayern','fcbayernmunchen':'bayern','fcbayernmunich':'bayern','fcbayernmunchen':'bayern','fcbayernmünchen':'bayern','fcbarcelona':'barcelona','barcelona':'barcelona','brazil':'brazil','brasil':'brazil','norway':'norway','norge':'norway','southkorea':'southkorea','korearepublic':'southkorea','afcbournemouth':'bournemouth','bournemouth':'bournemouth','coventrycity':'coventry','coventry':'coventry','ipswichtown':'ipswich','ipswich':'ipswich','manchesterunited':'manchesterunited','manunited':'manchesterunited','bayern':'bayern','vfb stuttgart':'stuttgart','vfbstuttgart':'stuttgart','fcelversberg':'elversberg','svelversberg':'elversberg','1fcunionberlin':'unionberlin','fcunionberlin':'unionberlin','scfreiburg':'freiburg','fcaugsburg':'augsburg','fcporto':'porto','norwichcity':'norwich','brasilien':'brazil','koreasouth':'southkorea','southkorea':'southkorea','rbleipzig':'rbleipzig'}
COMP_NAMES={42:'Champions League',47:'Premier League',54:'Bundesliga',87:'LaLiga',132:'FA Cup',133:'EFL Cup',138:'Copa del Rey',209:'DFB-Pokal','DFL_SUPERCUP':'German Supercup',247:'Community Shield',114:'National-team friendly',489:'Club friendly',9806:'UEFA Nations League A'}

ALIASES.update({'athleticbilbao':'athleticclub','fcschalke04':'schalke04','sv07elversberg':'elversberg'})
COUNTRIES={'BRA':'Brazil','NOR':'Norway','KOR':'South Korea','DEN':'Denmark','POR':'Portugal','WAL':'Wales','AUS':'Australia','IND':'India','ECU':'Ecuador','URU':'Uruguay','VEN':'Venezuela','UZB':'Uzbekistan'}

def norm(s):return ''.join(c for c in unicodedata.normalize('NFKD',s or '').casefold() if c.isalnum())
def team(s):
 s=re.sub(r'\{\{fb(?:-rt)?\|([A-Z]{3})\}\}',lambda m:COUNTRIES.get(m[1],m[1]),s or '')
 s=re.sub(r'\[\[(?:[^]|]+\|)?([^]]+)\]\]',r'\1',s or '');s=re.sub(r'<[^>]*>|\{\{[^{}]*\}\}','',s);n=norm(s);return ALIASES.get(n,n)
def identity(comp,kickoff,home,away):
 if not comp or not kickoff or not home or not away:return None
 return 'm_'+digest(json.dumps([comp,instant(kickoff).isoformat(),team(home),team(away)]).encode())[:24]
def ev_from_http(r,path):return dict(artifact_id=r['artifact_id'],provider=r['provider'],path=r['path'],sha256=r['sha256'],url=r['url'],acquired_at=r['request_timestamp'],http_status=r['http_status'],json_path=path,extraction_method='STRUCTURED_RESPONSE_OR_REVIEWED_PUBLIC_SECTION',access_basis=r['access_basis'])
def classify(o):
 """Participation and minute semantics are independent. Missing statistics never imply DNP."""
 if o.get('identity_errors'):return 'UNKNOWN'
 claims=[]
 if o.get('starter'):claims.append('STARTER')
 if o.get('substitution_in') or (o.get('bench') and o.get('played') is True):claims.append('SUBSTITUTE_USED')
 if o.get('unused_explicit') or (o.get('bench') and o.get('reported_minutes')==0 and o.get('played') is not True):claims.append('UNUSED_SUBSTITUTE')
 if o.get('complete_squad_verified') and o.get('in_squad') is False:claims.append('NOT_IN_MATCHDAY_SQUAD')
 return claims[0] if len(set(claims))==1 else 'UNKNOWN'

class Text(HTMLParser):
 def __init__(self):super().__init__();self.s=[];self.skip=0
 def handle_starttag(self,t,a):
  if t in ('script','style'):self.skip+=1
  if t in ('p','br','h1','h2','h3','li'):self.s.append('\n')
 def handle_endtag(self,t):
  if t in ('script','style') and self.skip:self.skip-=1
  if t=='p':self.s.append('\n')
 def handle_data(self,s):
  if not self.skip:self.s.append(s)
def lines(raw):
 p=Text();p.feed(raw);return [x for x in (re.sub(r'\s+',' ',v).strip() for v in ''.join(p.s).splitlines()) if x]
def split_players(s):return [x.strip(' .') for x in re.split(r',(?![^()]*\))',s) if x.strip(' .')]
def city_section(ls):
 for i,line in enumerate(ls):
  m=re.match(r'^(?:MAN(?:CHESTER)? )?CITY(?: XI)?\s*:\s*(.*)',line,re.I)
  if not m:continue
  xi=split_players(m[1]);subline=next((x for x in ls[i+1:i+3] if re.match(r'^(?:Subs|Substitutes)',x,re.I)),None)
  if not subline:return None
  bench=split_players(re.sub(r'^(?:(?:Subs|Substitutes)(?: not used)?:\s*)+','',subline,flags=re.I));used=[]
  for s in xi:
   for name,minute in re.findall(r'\(([^()\d]+?)\s+(\d+(?:\+\d+)?)\s*[’\x27]?\)',s):used.append(dict(name=name.strip(),minute=minute))
  original=[re.sub(r'\([^()]*\)','',s).strip(' .') for s in xi]
  names=original+[x['name'] for x in used]+bench
  complete=len(xi)==11 and len(bench)>0 and len({norm(x) for x in names})==len(names)
  return dict(starters=original,substitutions=used,unused=bench,source_line=i,complete=complete,verification_basis='OFFICIAL_FINAL_REPORT_FULL_TEAMS_SECTION;XI+EXPLICIT_USED_SUBSTITUTIONS+REMAINING_SUBS;ALL_NAMES_UNIQUE',source_lines=[line,subline])
 return None

def football_boxes(s):
 for m in re.finditer(r'\{\{football\s*box(?: collapsible)?\b',s,re.I):
  i=m.start();depth=0;j=i
  while j<len(s)-1:
   if s[j:j+2]=='{{':depth+=1;j+=2
   elif s[j:j+2]=='}}':
    depth-=1;j+=2
    if depth==0:yield i,s[i:j];break
   else:j+=1

def wiki_date(s,year=None):
 m=re.search(r'\{\{(?:start date|date)\|(\d{4})\|(\d+)\|(\d+)',s,re.I)
 if m:return datetime(*map(int,m.groups())).date().isoformat()
 s=re.sub(r'<ref.*|<!--.*','',s).strip()
 if year and not re.search(r'\b\d{4}\b',s):s+=' '+str(year)
 try:return datetime.strptime(s,'%d %B %Y').date().isoformat()
 except ValueError:return None

def wiki_kickoff(day,t):
 m=re.search(r'(\d{1,2}):(\d{2})',t)
 if not m:return None
 hour,minute=map(int,m.groups());zone=None
 utz=re.search(r'\{\{UTZ\|[^|]+\|([+−-]?\d+(?:\.\d+)?)\s*\}\}',t,re.I)
 if utz:zone=timezone(timedelta(hours=float(utz[1].replace('−','-'))))
 if zone is not None:pass
 elif re.search('CEST|CET|Central European',t,re.I):zone=ZoneInfo('Europe/Berlin')
 elif re.search('BST|GMT|British Summer',t,re.I):zone=ZoneInfo('Europe/London')
 elif re.search('AEST',t):zone=timezone(timedelta(hours=10))
 elif re.search('KST',t):zone=timezone(timedelta(hours=9))
 elif re.search('IST|Indian Standard',t):zone=timezone(timedelta(hours=5,minutes=30))
 elif re.search('UTC',t):
  z=re.search(r'UTC\s*(?:\]\]|\|)?\s*([+−-])\s*(\d{1,2})(?::(\d{2}))?',t)
  if z:zone=timezone(timedelta(minutes=(int(z[2])*60+int(z[3] or 0))*(1 if z[1]=='+' else -1)))
  else:zone=timezone.utc
 if zone is None:return None
 return datetime.fromisoformat(day).replace(hour=hour,minute=minute,tzinfo=zone).astimezone(timezone.utc).isoformat()

def wiki_comp(prefix,fields,title):
 round_=fields.get('round','')
 if 'national football team' in title or title=='2026 in Brazilian football':
  before=prefix[-6000:]
  if re.search('Nations League',round_,re.I):return 9806
  if re.search('Friendly|Friendlies|Amistoso',round_,re.I):return 114
  return None
 if re.search('Friendly|Exhibition|Telekom|Tour',round_,re.I):return 489
 headings=re.findall(r'^={2,5}\s*(.*?)\s*={2,5}\s*$',prefix,re.M)
 for h in reversed(headings):
  for pat,cid in [('UEFA Champions League',42),('La Liga',87),('Bundesliga',54),('DFB.Pokal',209),('Franz Beckenbauer Supercup|DFL.Supercup','DFL_SUPERCUP'),('Premier League',47),('EFL Cup|Carabao',133),('Community Shield',247),('FA Cup',132),('friendl|pre.season',489)]:
   if re.search(pat,h,re.I):return cid
 return None

class Dataset:
 def __init__(self):self.matches={};self.observations=[];self.maps=[];self.backlog=[];self.availability=[];self.source_checks=[];self.excluded=[]
 def candidate(self,pid,comp,kick,home,away,provider,provider_id,evidence,score=None,day=None,route=None):
  if comp==489:
   if kick and START<=event_day(kick)<=END:self.excluded.append(dict(player_id=pid,competition_id=comp,kickoff=kick,home=home,away=away,provider=provider,provider_match_id=provider_id,evidence=evidence,reason='CLUB_FRIENDLY_OUTSIDE_SCOPE'))
   return None
  key=identity(comp,kick,home,away)
  if not key:
   self.backlog.append(dict(player_id=pid,provider=provider,provider_match_id=provider_id,date=day,home=home,away=away,competition_id=comp,issue='MATCH_IDENTITY_OR_TIMEZONE_PENDING',source=evidence));return None
  local=event_day(kick)
  if not START<=local<=END:return None
  k=(pid,key);represented=next((t for t in [home,away] if team(t) in TARGETS[pid]),None)
  if represented is None:return None
  opponent=away if team(represented)==team(home) else home
  row=self.matches.setdefault(k,dict(player_id=pid,player_name=PLAYERS[pid],match_id=key,match_date=local,kickoff=instant(kick).isoformat(),competition_id=comp,competition=COMP_NAMES.get(comp,str(comp)),represented_team=team(represented),opponent=team(opponent),home_team=team(home),away_team=team(away),score_claims=[],discoveries=[]))
  if score is not None:row['score_claims'].append(dict(provider=provider,score=score))
  mapped=dict(player_id=pid,canonical_match_id=key,provider=provider,provider_match_id=provider_id,source_record_id=evidence.get('json_path'),identity_status='EXACT_COMPETITION_UTC_HOME_AWAY',source=evidence,discovery_path=route)
  self.maps.append(mapped);row['discoveries'].append(mapped);return key
 def observe(self,pid,key,provider,ev,**claims):
  if key is None:return
  obs=dict(player_id=pid,match_id=key,provider=provider,evidence=ev,**claims);obs['participation_status']=classify(obs);obs['observation_id']=digest(json.dumps(obs,sort_keys=True,ensure_ascii=False).encode());self.observations.append(obs)

def load_http():return [json.loads(l) for l in (OUT/'http_requests.jsonl').read_text().splitlines()]
def corpus(ds):
 sources=CacheSources(PLAYERS);ctx=sources.context
 # Every route is evaluated; restricted online FotMob paths are never invoked.
 for pid in PLAYERS:
  for route in 'ABC':
   result=sources.check(route,pid,'EVENT_DATE',END,START);ds.source_checks.append(dict(provider='FotMob',player_id=pid,path=route,status=result['status'],limitations=result['limitations'],network_requests=0))
   for c in result['candidates']:
    names=c.get('team_names')
    if not names:
     g=next((r for r in sources.details if r['match_id']==c['match_id']),None);names=g['team_names'] if g else None
    if not names:continue
    ev=dict(c['evidence'],provider='FotMob',http_status=None,extraction_method='READ_ORIGINAL_CACHE_WITH_HASH_AND_ID_CHECKS',access_basis='PREEXISTING_LOCAL_CACHE_ONLY_NO_NEW_REQUEST')
    ds.candidate(pid,c['competition_id'],c['kickoff'],*names,'FotMob',str(c['match_id']),ev,route=route)
  for o in sources.raw.get(pid,[]):
   if not START<=event_day(o['kickoff'])<=END:continue
   raw=read(ROOT/o['evidence']['path']);g=raw['general'];ev=dict(o['evidence'],provider='FotMob',http_status=None,extraction_method='RAW_OWN_SIDE_ID_AND_STATS_VERIFICATION',access_basis='PREEXISTING_CACHE_ONLY')
   key=ds.candidate(pid,o['competition_id'],o['kickoff'],g['homeTeam']['name'],g['awayTeam']['name'],'FotMob',str(o['match_id']),ev,(raw.get('header',{}).get('status') or {}).get('scoreStr'),route='B')
   ds.observe(pid,key,'FotMob',ev,starter=o['role']=='starters',bench=o['role']=='subs',in_squad=o['role'] in ('starters','subs'),substitution_in=o['played'] is True and o['role']=='subs',played=o['played'],reported_minutes=o['minutes'],calculated_minutes=None,complete_squad_verified=False,identity_errors=o['rejected'],minute_convention='PROVIDER_REPORTED;NO_INFERRED_BENCH_ZERO')
 return sources

def city(ds,logs):
 result=next(r for r in logs if r['provider']=='ManchesterCity' and r['purpose']=='team_results' and r['http_status']==200);raw=(ROOT/result['path']).read_text();eventlist=[]
 for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',raw,re.S):
  try:arr=json.loads(html.unescape(block))
  except json.JSONDecodeError:continue
  if not isinstance(arr,list):continue
  for i,x in enumerate(arr):
   obj=x.get('object') or {}
   if obj.get('@type')=='SportsEvent' and START<=obj.get('startDate','')[:10]<=END:eventlist.append((i,obj,x))
 for r in logs:
  if r['provider']!='ManchesterCity' or r['purpose']!='match_report' or r['http_status']!=200:continue
  body=(ROOT/r['path']).read_text();ls=lines(body);published=re.search(r'"datePublished"\s*:\s*"([^"]+)',body);day=published[1][:10] if published else None;events=[e for e in eventlist if e[1]['startDate'][:10]==day]
  if len(events)!=1 or r['url'] not in html.unescape(raw):ds.backlog.append(dict(provider='ManchesterCity',issue='OFFICIAL_REPORT_EVENT_LINK_PENDING',source=ev_from_http(r,'article')));continue
  i,event,x=events[0];home,away=event['name'].split(' vs ');text=' '.join(ls);mark=text.find('Match Report');head=text[mark:mark+1500];cm=re.search(r'Champions League|Premier League|Carabao Cup|Community Shield',head,re.I)
  cid={'champions league':42,'premier league':47,'carabao cup':133,'community shield':247}.get(cm[0].lower()) if cm else None
  ev=ev_from_http(r,'article Teams section');key=ds.candidate(737066,cid,event['startDate'],home,away,'ManchesterCity',r['url'].rsplit('-',1)[-1],ev,x.get('result'),route='B')
  if key is None:continue
  ds.maps.append(dict(player_id=737066,canonical_match_id=key,provider='ManchesterCity',provider_match_id=None,source_record_id=f'JSON-LD event[{i}]',identity_status='OFFICIAL_RESULTS_EXACT_EVENT_PLUS_LINKED_REPORT',source=ev_from_http(result,f'$.JSONLD[{i}]'),discovery_path='B'))
  sec=city_section(ls)
  if not sec:ds.backlog.append(dict(player_id=737066,match_id=key,provider='ManchesterCity',issue='TEAM_SECTION_PARSER_REVIEW',source=ev));continue
  preproof=[]
  finalnames={norm(n) for n in sec['starters']+sec['unused']+[x['name'] for x in sec['substitutions']]}
  for pre in logs:
   if pre['provider']!='ManchesterCity' or pre['purpose']!='prematch_squad' or pre['http_status']!=200:continue
   prebody=(ROOT/pre['path']).read_text();prelines=lines(prebody);psec=city_section(prelines)
   if not psec or team(away) not in norm(' '.join(prelines)) and team(home) not in norm(' '.join(prelines)):continue
   pm=re.search(r'"datePublished"\s*:\s*"([^"]+)',prebody)
   prenames={norm(n) for n in psec['starters']+psec['unused']+[x['name'] for x in psec['substitutions']]}
   when=' '.join(prelines[prelines.index('WHEN AND WHERE')+1:prelines.index('WHEN AND WHERE')+3]) if 'WHEN AND WHERE' in prelines else ''
   eventuk=instant(event['startDate']).astimezone(ZoneInfo('Europe/London'))
   explicit_date=bool(re.search(r'\b'+str(eventuk.day)+r'\s+'+eventuk.strftime('%B')+r'\b',when,re.I))
   explicit_time=eventuk.strftime('%H:%M') in when and '(UK)' in when
   if pm and pm[1][:4]==day[:4] and explicit_date and explicit_time and finalnames==prenames and len(finalnames)>=18:
    preproof.append(dict(source=ev_from_http(pre,'article confirmed starting XI and full substitutes'),roster_size=len(finalnames),exact_unique_name_set_agreement=True))
  sec['cross_check']=preproof
  if preproof:sec['verification_basis']+=';PREMATCH_POSTMATCH_FULL_SET_EXACT_AGREEMENT'
  starters={norm(s) for s in sec['starters']};bench={norm(s) for s in sec['unused']};used=[x for x in sec['substitutions'] if norm(x['name'])=='haaland'];start='haaland' in starters;unused='haaland' in bench;in_squad=start or unused or bool(used);calc=None
  for s in split_players(next(x for x in sec['source_lines'] if ':' in x).split(':',1)[1]):
   if norm(re.sub(r'\([^()]*\)','',s))=='haaland':
    m=re.search(r'\([^()]*?\s(\d+(?:\+\d+)?)\s*[’\x27]?\)',s)
    if m:calc=sum(map(int,m[1].split('+')))
  ds.observe(737066,key,'ManchesterCity',ev,starter=start,bench=unused or bool(used),unused_explicit=unused and sec['complete'] and bool(preproof),in_squad=in_squad,substitution_in=bool(used),played=True if start or used else False if sec['complete'] else None,reported_minutes=None,calculated_minutes=calc,complete_squad_verified=sec['complete'] and bool(preproof),identity_errors=[],player_identity_basis='OFFICIAL_CLUB_REPORT_HAALAND_PLUS_OFFICIAL_PLAYER_PROFILE;NO_ASSIGNED_PROVIDER_PLAYER_ID',minute_convention='SUBSTITUTION_CLOCK_SEPARATE;NO_AUTOMATIC_FULL_MATCH_90',complete_squad_basis=sec['verification_basis'],team_section=sec)
  if 'Haaland rested' in text:
   ds.availability.append(dict(player_id=737066,match_id=key,category='REST_OR_ROTATION_CONFIRMED',condition='Rested',confirmation='OFFICIAL_REPORT_EXPLICIT',publication_date=day,valid_from=day,valid_to=None,explains_nonappearance=True,explains_squad_absence=True,evidence=ev))
  if sec['complete'] and not start and not used and not unused and 'Haaland rested' not in text:ds.backlog.append(dict(player_id=737066,match_id=key,issue='NONSELECTION_REASON_IF_NOT_EXPLICIT',source=ev))
 # Coach explicitly explains the same match's nonappearance; no diagnosis or assumed full fitness.
 for r in logs:
  if r['provider']=='ManchesterCity' and r['purpose']=='absence_reason' and r['http_status']==200:
   text=' '.join(lines((ROOT/r['path']).read_text()))
   if 'Erling had a rest' in text:
    matches=[key for (pid,key),row in ds.matches.items() if pid==737066 and row['match_date']=='2026-10-20' and row['opponent']=='aekathens']
    if len(matches)==1:ds.availability.append(dict(player_id=737066,match_id=matches[0],category='REST_OR_ROTATION_CONFIRMED',condition='Coach explicitly confirms rest',confirmation='COACH_STATEMENT_OFFICIAL_PUBLICATION',publication_date='2026-10-20',valid_from='2026-10-20',valid_to=None,explains_nonappearance=True,explains_squad_absence=False,evidence=ev_from_http(r,'article coach statement')))
 return eventlist

def other_sources(ds,logs):
 for r in logs:
  if r['http_status']!=200:continue
  if r['provider']=='OpenLigaDB' and r['purpose'] in ('team_fixtures','competition_round'):
   for i,x in enumerate(read(ROOT/r['path'])):
    if 40 not in (x['team1']['teamId'],x['team2']['teamId']) or not x['matchIsFinished']:continue
    ds.candidate(828159,54,x.get('matchDateTimeUTC'),x['team1']['teamName'],x['team2']['teamName'],'OpenLigaDB',str(x['matchID']),ev_from_http(r,f'$[{i}]'),score=next((str(s['pointsTeam1'])+'-'+str(s['pointsTeam2']) for s in x['matchResults'] if s['resultTypeID']==2),None),route='B' if r['purpose']=='team_fixtures' else 'C')
  if r['provider']=='Wikimedia' and r['purpose'] in ('licensed_match_reference','licensed_national_reference'):
   p=read(ROOT/r['path']).get('parse',{});s=(p.get('wikitext') or {}).get('*','');title=p.get('title','')
   if title=='2026 in Brazilian football':continue # Senior national-team page supplies the same three matches.
   pid=696679 if 'Barcelona' in title or title=='Brazil national football team' else 828159 if 'Bayern' in title or title=='South Korea national football team' else 737066
   for number,(offset,block) in enumerate(football_boxes(s)):
    fields={m[1].lower():m[2].strip() for m in re.finditer(r'^\|\s*(\w+)\s*=(.*?)(?=^\|\s*\w+\s*=|\Z)',block,re.M|re.S)}
    years=re.findall(r'^={2,5}\s*(20\d{2})\s*={2,5}\s*$',s[:offset],re.M);day=wiki_date(fields.get('date',''),years[-1] if years else None)
    if not day or not START<=day<=END:continue
    home=fields.get('team1','');away=fields.get('team2','');cid=wiki_comp(s[:offset],fields,title);kick=wiki_kickoff(day,fields.get('time',''));score=fields.get('score','')
    if not re.search(r'\d\s*[–−-]\s*\d',score):continue
    ev=ev_from_http(r,f'$.parse.wikitext FootballBox[{number}] char={offset}');ev['page_id']=p.get('pageid');ev['upstream_report']=fields.get('report');ev['source_dependency']='SECONDARY_COMPILATION;UPSTREAM_REPORT_NOT_INDEPENDENT'
    ds.candidate(pid,cid,kick,home,away,'Wikimedia',None,ev,score=score,day=day,route='B')
  if r['provider']=='FootballDataCSV' and r['purpose']=='league_fixtures':
   cid=54 if r['url'].endswith('D1.csv') else 47;pid=828159 if cid==54 else 737066;rows=list(csv.DictReader(io.StringIO((ROOT/r['path']).read_text())))
   for i,x in enumerate(rows):
    if not ({team(x['HomeTeam']),team(x['AwayTeam'])}&set(TARGETS[pid])):continue
    day=datetime.strptime(x['Date'],'%d/%m/%Y').date().isoformat()
    if not START<=day<=END:continue
    # CSV doesn't label timezone. Accept its alignment only per-row against already timestamped canonical evidence.
    guess=datetime.fromisoformat(day+'T'+x['Time']).replace(tzinfo=ZoneInfo('Europe/London')).astimezone(timezone.utc).isoformat()
    key=identity(cid,guess,x['HomeTeam'],x['AwayTeam']);ev=ev_from_http(r,f'CSV line {i+2}');ev['raw_time']=x['Time'];ev['timezone_basis']='PER_ROW_EMPIRICAL_ALIGNMENT_WITH_EXISTING_UTC_IDENTITY;NOT_PROVIDER_DOCUMENTED'
    if (pid,key) not in ds.matches:ds.backlog.append(dict(player_id=pid,provider='FootballDataCSV',issue='CSV_TIMEZONE_OR_TEAM_ALIAS_UNVERIFIED',date=day,home=x['HomeTeam'],away=x['AwayTeam'],source=ev));continue
    ds.candidate(pid,cid,guess,x['HomeTeam'],x['AwayTeam'],'FootballDataCSV',None,ev,score=str(x['FTHG'])+'-'+str(x['FTAG']),route='C')

def resolve_all(ds):
 group=defaultdict(list)
 # Observations retain duplicates in provenance, but distinct content IDs deduplicate facts.
 ds.observations=list({o['observation_id']:o for o in ds.observations}.values())
 for o in ds.observations:group[(o['player_id'],o['match_id'])].append(o)
 results=[];reviews=[]
 for key,row in sorted(ds.matches.items(),key=lambda x:(x[0][0],x[1]['match_date'],x[0][1])):
  obs=group[key];statuses={o['participation_status'] for o in obs if o['participation_status']!='UNKNOWN'};reported={o['reported_minutes'] for o in obs if o.get('reported_minutes') is not None and not o.get('identity_errors')};calc={o['calculated_minutes'] for o in obs if o.get('calculated_minutes') is not None};issues=[]
  if len(statuses)>1:issues.append('PARTICIPATION_CONFLICT')
  if len(reported)>1:issues.append('REPORTED_MINUTES_CONFLICT')
  participation=next(iter(statuses)) if len(statuses)==1 else 'UNKNOWN'
  minutes=next(iter(reported)) if len(reported)==1 and not issues else None;basis='SOURCE_REPORTED' if minutes is not None else 'MISSING'
  if participation in ('UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD'):
   if minutes not in (0,None):issues.append('NONAPPEARANCE_POSITIVE_MINUTES');minutes=None;participation='UNKNOWN'
   elif not issues:minutes=0;basis='CONFIRMED_NONAPPEARANCE_ZERO'
  # Derived substitution-clock claims remain separately visible; never overwrite reported minutes.
  if reported and calc and reported!=calc:issues.append('REPORTED_VS_SUBSTITUTION_CLOCK_DIFFERENCE')
  health=[a for a in ds.availability if (a['player_id'],a['match_id'])==key]
  providers=sorted({x['provider'] for x in row['discoveries']});partproviders=sorted({o['provider'] for o in obs if o['participation_status']!='UNKNOWN'})
  scores={re.sub(r'\s','',s['score']).replace('–','-').replace('−','-') for s in row['score_claims']}
  if len(scores)>1:issues.append('SCORE_DISAGREEMENT')
  unresolved=[]
  if participation=='UNKNOWN':unresolved.append('PARTICIPATION_EVIDENCE_MISSING')
  if minutes is None:unresolved.append('MINUTES_MISSING_OR_CONFLICT')
  if not health:unresolved.append('AVAILABILITY_REASON_UNKNOWN;DOES_NOT_INVALIDATE_PARTICIPATION')
  for issue in issues:reviews.append(dict(review_id=digest(json.dumps([*key,issue]).encode()),queue='HUMAN_REVIEW',player_id=key[0],match_id=key[1],match_date=row['match_date'],issue_type=issue,conflicting_claims=obs if 'SCORE' not in issue else row['score_claims'],source_evidence=[o['evidence'] for o in obs],attempted_resolution='Compared acquired official report, open fixture sources and original cache; preserved source conventions, no majority vote',recommended_decision='Obtain an authorised primary match sheet/minute definition; do not silently prefer a provider',consequence='Disputed fact remains unresolved; raw evidence retained'))
  for issue in unresolved[:2]:ds.backlog.append(dict(queue='ACQUISITION_BACKLOG',player_id=key[0],match_id=key[1],issue=issue,providers_checked=providers,recommended_action='Obtain permitted complete team sheet and explicit player minutes'))
  result={k:v for k,v in row.items() if k not in ('discoveries','score_claims')};result.update(participation_status=participation,minutes=minutes,minutes_basis=basis,calculated_minutes=sorted(calc),availability_status=sorted({h['category'] for h in health}) or ['REASON_UNKNOWN'],source_count=len(providers),providers=providers,participation_source_count=len(partproviders),participation_providers=partproviders,source_agreement='CONFLICT' if issues else ('AGREE_AVAILABLE_FACTS' if len(partproviders)>1 else 'SINGLE_PARTICIPATION_PROVIDER' if partproviders else 'PARTICIPATION_UNRESOLVED'),independent_provider_groups='NOT_CERTIFIED;WIKIMEDIA_REFERENCES_UPSTREAM;FOOTBALLDATACSV_HAS_MIXED_FEEDS',confidence='REVIEW_REQUIRED' if issues else 'SOURCE_SUPPORTED' if participation!='UNKNOWN' else 'INSUFFICIENT',unresolved_issues=issues+unresolved,observation_ids=[o['observation_id'] for o in obs],score_claims=row['score_claims'])
  results.append(result)
 return results,reviews

def main():
 OUT.mkdir(exist_ok=True,parents=True);protected=read(BASE/'audit/dynamic_acquisition/before_hashes.json');before={p:digest((ROOT/p).read_bytes()) for p in protected};assert before==protected
 if not (OUT/'before_hashes.json').exists():write(OUT/'before_hashes.json',before)
 ds=Dataset();sources=corpus(ds);logs=load_http();city(ds,logs);other_sources(ds,logs);results,reviews=resolve_all(ds)
 # Separate policy/credential gaps from material source disagreements.
 for pid in PLAYERS:
  for provider in ['API-Football','Sportmonks','Football-Data.org']:
   ds.backlog.append(dict(queue='ACQUISITION_BACKLOG',player_id=pid,provider=provider,issue='DOCUMENTED_API_CREDENTIALS_AND_TARGET_COVERAGE_REQUIRED',recommended_action='Provide authorised subscription/key and exact data retention rights; no account created'))
 write(OUT/'excluded_fixtures.json',ds.excluded)
 write(OUT/'matches.json',results);write(OUT/'source_observations.json',ds.observations);write(OUT/'cross_provider_match_mapping.json',list({digest(json.dumps(r,sort_keys=True).encode()):r for r in ds.maps}.values()));write(OUT/'availability_evidence.json',ds.availability);write(OUT/'human_review.json',reviews);write(OUT/'acquisition_backlog.json',ds.backlog);write(OUT/'three_path_checks.json',ds.source_checks)
 table(OUT/'matches.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(list,dict)) else v for k,v in r.items()} for r in results])
 assert {p:digest((ROOT/p).read_bytes()) for p in protected}==before
 counts={pid:dict(candidates=sum(r['player_id']==pid for r in results),statuses=dict(Counter(r['participation_status'] for r in results if r['player_id']==pid)),minutes_known=sum(r['player_id']==pid and r['minutes'] is not None for r in results)) for pid in PLAYERS}
 summary=dict(players=counts,source_observations=len(ds.observations),human_reviews=len(reviews),backlog=len(ds.backlog),http_requests=len(logs),successful_data_providers=sorted({r['provider'] for r in logs if r['http_status']==200 and r['purpose'] in ['team_fixtures','competition_round','league_fixtures','match_report','licensed_match_reference','licensed_national_reference']}),protected_databases_unchanged=True)
 write(OUT/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
