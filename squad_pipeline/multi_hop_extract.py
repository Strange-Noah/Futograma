"""One-off evidence extraction from acquired public pages. Does not open any database."""
import json,re,html
from collections import Counter
from .core import BASE,ROOT,write,digest
from .multi_source_dataset import identity,norm
OUT=BASE/'audit/multi_hop'
TARGET={696679:'raphinha',828159:'min-jae-kim',737066:'erling-haaland'}
def text(s):return html.unescape(re.sub(r'\s+',' ',re.sub('<[^>]+>',' ',s))).strip()
def take(pattern,s):
 m=re.search(pattern,s,re.S);return m[1] if m else ''
def players(s):
 return [dict(provider_player_url=html.unescape(m[1]),name=text(m[2]),shirt=text(take(r'lineup-entry__number">(.*?)</span>',li))) for li in re.findall(r'<li class="lineup-entry[^\"]*">(.*?)</li>',s,re.S) for m in [re.search(r'<a href="([^\"]+)" class="lineup-entry__name">(.*?)</a>',li,re.S)] if m]
def clock(s):
 m=re.search(r'(\d+)(?:\s*\+\s*(\d+))?',s.replace("'",'').replace('′',''));return dict(base=int(m[1]),added=int(m[2] or 0),raw=s,total=int(m[1])+int(m[2] or 0)) if m else None
def parse(r,baseline):
 raw=(ROOT/r['path']).read_text();j=[json.loads(x) for x in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',raw,re.S)];event=next((x for x in j if isinstance(x,dict) and x.get('@type')=='SportsEvent'),None)
 if not event:return []
 comp={'Bundesliga':54,'La Liga':87,'Premier League':47}.get(event.get('organizer',{}).get('name'))
 key=identity(comp,event.get('startDate'),event.get('homeTeam',{}).get('name'),event.get('awayTeam',{}).get('name'))
 matches=[x for x in baseline if x['match_id']==key]
 if not matches:return [dict(eligible=False,issue='IDENTITY_NOT_MATCHED_BASELINE;NEW_FIXTURE_REVIEW',url=r['url'],event=event)]
 rosters={};blocks=re.split(r'<div class="lineup-col lineup-col--(home|away)">',raw)
 for i in range(1,len(blocks),2):
  side=blocks[i];b=blocks[i+1];xi=players(take(r'<ol class="lineup-list lineup-section--xi">(.*?)</ol>',b));bench=players(take(r'<ol class="lineup-list lineup-list--bench">(.*?)</ol>',b));rosters[side]=dict(starters=xi,bench=bench)
 events=[]
 for side,b in re.findall(r'<li class="event event--(home|away)">(.*?)</li>',raw,re.S):
  typ=take(r'event__icon--([a-z-]+)',b);c=clock(text(take(r'event__minute">(.*?)</span>',b)));name=text(take(r'event__name">(.*?)</span>',b));url=take(r'event__name">.*?<a href="([^\"]+)"',b)
  ev=dict(side=side,type=typ,clock=c,player_name=text(take(r'<a[^>]*>(.*?)</a>',b)),provider_player_url=url or None)
  if typ=='sub':ev['out_name']=text(take(r'event__sub-out">(.*?)</span>',b)).replace('↓ raus:','').strip()
  events.append(ev)
 fulltime=text(take(r'match-score__state">(.*?)</span>',raw))=='Ende';errors=[];sets={};entries={};exits={}
 for side,rost in rosters.items():
  xi=rost['starters'];bn=rost['bench'];allids=[p['provider_player_url'] for p in xi+bn]
  if len(xi)!=11:errors.append(side+':STARTERS_NOT_11')
  if not bn:errors.append(side+':BENCH_UNAVAILABLE')
  if len(allids)!=len(set(allids)):errors.append(side+':DUPLICATE_PLAYER')
  pitch={p['provider_player_url'] for p in xi};bench={p['provider_player_url'] for p in bn};name_ids={norm(p['name']):p['provider_player_url'] for p in xi+bn};used=set();windows=set()
  for e in [x for x in events if x['side']==side]:
   if e['type']=='sub':
    incoming=e['provider_player_url'];outgoing=name_ids.get(norm(e.get('out_name','')))
    if outgoing not in pitch:errors.append(side+':OUT_NOT_ON_PITCH:'+e.get('out_name',''))
    if incoming not in bench or incoming in pitch or incoming in used:errors.append(side+':IN_NOT_ELIGIBLE:'+e['player_name'])
    if outgoing:pitch.discard(outgoing);exits[outgoing]=e['clock']
    pitch.add(incoming);used.add(incoming);entries[incoming]=e['clock'];windows.add((e['clock'] or {}).get('base'))
   elif e['type'] in ('red','yellow-red','second-yellow'):
    if e['provider_player_url'] not in pitch:errors.append(side+':RED_PLAYER_NOT_ON_PITCH')
    pitch.discard(e['provider_player_url']);exits[e['provider_player_url']]=e['clock']
  if len(used)>5:errors.append(side+':MORE_THAN_5_REGULATION_SUBS')
  # Halftime is displayed as 45 or 46 by different feeds; at most three in-play windows.
  # Rounded event minutes cannot establish the number of stoppage windows.
  sets[side]=dict(final_pitch=sorted(pitch),used=sorted(used),substitutions=len(used),windows=sorted(windows))
 if len(rosters)!=2:errors.append('BOTH_TEAM_ROSTERS_UNAVAILABLE')
 if not fulltime:errors.append('FULLTIME_UNVERIFIED')
 # Published player minutes remain independent of inferred event-clock minutes.
 stats={}
 for tb in re.findall(r'<table class="data-table werte-table">(.*?)</table>',raw,re.S):
  for tr in re.findall(r'<tr>(.*?)</tr>',tb,re.S):
   a=re.search(r'<a href="([^\"]+)">(.*?)</a>',tr,re.S);num=take(r'<td class="num">(.*?)</td>',tr)
   if a and re.fullmatch(r'\d+',text(num)):stats[html.unescape(a[1])]=int(text(num))
 output=[]
 for row in matches:
  pid=row['player_id'];target='https://torlytics.de/spieler/'+TARGET[pid];own=next((side for side in ('home','away') if any(p['provider_player_url']==target for p in rosters.get(side,{}).get('starters',[])+rosters.get(side,{}).get('bench',[]))),None)
  is_start=own and any(p['provider_player_url']==target for p in rosters[own]['starters']);in_bench=own and any(p['provider_player_url']==target for p in rosters[own]['bench'])
  if is_start:status='STARTER'
  elif target in entries:status='SUBSTITUTE_USED'
  elif in_bench and not errors:status='UNUSED_SUBSTITUTE'
  elif len(rosters)==2 and not errors:status='NOT_IN_MATCHDAY_SQUAD'
  else:status='UNKNOWN'
  derived=None;method='UNRESOLVED';precision='UNKNOWN';issues=list(errors)
  if status in ('UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD'):derived=0;method='VERIFIED_NONAPPEARANCE';precision='EXACT_NONAPPEARANCE'
  elif status in ('STARTER','SUBSTITUTE_USED') and not errors:
   entry=entries.get(target,dict(base=0,added=0,total=0,raw='START'));exit_=exits.get(target,dict(base=90,added=0,total=90,raw='REGULATION_END'))
   if not entry['added'] and not exit_['added'] and 0<=entry['base']<=exit_['base']<=90:
    begin=45 if entry['base']==46 and entry['raw'] in ("46'",'46′') else entry['base']
    derived=exit_['base']-begin;method='REGULATION_CLOCK_DIFFERENCE_EXCLUDING_ADDED_TIME;HALFTIME_46_NORMALIZED_TO45';precision='WHOLE_NOMINAL_MINUTES_NOT_ELAPSED_SECONDS'
   else:issues.append('ADDED_TIME_OR_EXTRA_TIME_NUMERIC_CONVENTION_UNRESOLVED')
  reported=stats.get(target)
  if reported is not None and derived is not None and reported!=derived:issues.append('REPORTED_DERIVED_CONVENTION_DIFFERENCE')
  ev=dict(provider='Torlytics',upstream='Sportmonks',independent_group='SPORTMONKS',url=r['url'],path=r['path'],sha256=r['sha256'],acquired_at=r['request_timestamp'],http_status=r['http_status'],location='SportsEvent identity; .lineup-col .lineup-list; .event-list; .werte-table',provider_player_url=target)
  output.append(dict(player_id=pid,match_id=key,match_date=row['match_date'],participation_status=status,reported_minutes=reported,derived_minutes=derived,minute_method=method,minute_precision=precision,minute_evidence=dict(fulltime=fulltime,regulation=90,extra_time=False,entry=entries.get(target),exit=exits.get(target),validation_errors=errors),source=ev,rosters=rosters,events=events,event_validation=sets,issues=issues,eligible=True))
 return output

def main():
 baseline=json.loads((OUT/'baseline_matches.json').read_text());logs=[json.loads(x) for x in (OUT/'http_requests.jsonl').read_text().splitlines()];records=[]
 for r in logs:
  if r['provider']=='Torlytics' and r['purpose'] in ('match','target_match') and r['http_status']==200:records.extend(parse(r,baseline))
 write(OUT/'torlytics_evidence.json',records)
 for r in records:print(r.get('player_id'),r.get('match_date'),r.get('participation_status'),r.get('reported_minutes'),r.get('derived_minutes'),r.get('issues'))
if __name__=='__main__':main()
