"""Phase-two acceptance only: no production pointer, membership or match fact writes."""
import json,os,sqlite3,gzip,hashlib,re,subprocess,copy,shutil
from pathlib import Path
from datetime import datetime,timezone,timedelta
from collections import Counter
from urllib.parse import urlsplit,unquote
from squad_pipeline.core import ROOT,BASE
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
from squad_pipeline.cloud.quality import inspect,snapshot,pack,sha
from squad_pipeline.cloud.browser_export import export_browser
from squad_pipeline.managed_roster import migrate as management_migrate,add_event,import_verified_history,decision
from squad_pipeline import pending_matches as retry
from squad_pipeline.live_sources import Client,HOSTS
from squad_pipeline.live_parse import Tree,canon
from squad_pipeline.html_rosters import parse_city,parse_inter,payload,POSITIONS
from squad_pipeline.multi_source_dataset import norm
from squad_pipeline.sky_german import parse as german_parse

REF='acceptance/phase2-data'
ROSTER_SOURCES={8456:'https://www.mancity.com/players/mens',9825:'https://www.arsenal.com/men/players',9823:'https://fcbayern.com/en/teams/first-team',9847:'https://www.psg.fr/football-masculin/effectif',8634:'https://www.fcbarcelona.com/en/football/first-team/players',8636:'https://www.inter.it/en/teams/first-team'}
def write(path,obj):
 Path(path).parent.mkdir(parents=True,exist_ok=True)
 with Path(path).open('x') as f:json.dump(obj,f,ensure_ascii=False,indent=2)
def store(ref='main'):return GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN'],ref)
def ensure_ref(s,ref,base):
 if not s.api('GET',s.base+'/git/ref/heads/'+ref,allow404=True):s.api('POST',s.base+'/git/refs',dict(ref='refs/heads/'+ref,sha=base))
def tokens(s):
 import unicodedata
 return set(re.findall('[a-z]+',unicodedata.normalize('NFKD',s).encode('ascii','ignore').decode().lower()))
def identities(db,tid):
 result={}
 for r in db.execute('SELECT DISTINCT p.* FROM players p JOIN validation_roster_membership v USING(player_id) WHERE v.club_id=?',(tid,)):
  names={r['display_name']}
  for a in db.execute('SELECT identity_json FROM validation_player_aliases WHERE player_id=?',(r['player_id'],)):
   names.update(json.loads(a[0]).get('names') or [])
  result[r['player_id']]=dict(name=r['display_name'],names=names)
 return result

def roster_rows(tid,raw):
 if tid==8456:return parse_city(raw)[0]
 if tid==8636:
  # Explicit first-team application array is the authoritative category; do not scrape unrelated news/profile links.
  d=payload(raw);p=d['page']
  if p.get('slug')!='first-team' or p.get('youthTeam') or p.get('type')!='Men':raise ValueError('INTER_NOT_MENS_FIRST_TEAM')
  return [dict(name=' '.join(x for x in [a.get('firstName'),a.get('lastName')] if x),profile=a['slug']) for a in d['players'] if a.get('team',{}).get('slug')=='first-team' and a.get('role',{}).get('name') in POSITIONS]
 prefixes={9823:('/en/teams/first-team/',),9847:('/joueurs/','/players/'),8634:('/en/football/first-team/players/',),9825:('/men/players/',)}[tid];rows=[]
 for a in Tree(raw).root.all(tag='a'):
  href=a.attrs.get('href','')
  if not any(x in href for x in prefixes) or any(x in href.lower() for x in ['/coach/','/coaches/','/staff/']):continue
  value=a.txt();alts=' '.join(n.attrs.get('alt','') for n in a.all(tag='img'));rows.append(dict(name=value,profile=href,alt=alts))
 if not rows:raise ValueError('OFFICIAL_PLAYER_CARDS_NOT_AVAILABLE')
 return rows

def link_rows(identities,rows):
 matched={};ambiguous=[]
 for row in rows:
  profile=unquote(row.get('official_profile_url') or row.get('profile') or '').rstrip('/').rsplit('/',1)[-1];hay=norm(row['name']+' '+row.get('alt',''));profile_norm=norm(profile);hits=[]
  for pid,p in identities.items():
   names={norm(x) for x in p['names'] if x}
   # Exact structured name or complete existing alias in the dedicated profile card, never jersey matching.
   if norm(row['name']) in names or profile_norm in names or any(len(n)>=6 and n in hay for n in names):hits.append(pid)
  if len(hits)==1:matched[hits[0]]=row
  elif len(hits)>1:ambiguous.append(dict(card=row,candidates=[str(x) for x in hits]))
 return matched,ambiguous

def legal_senior(raw,club):
 block=re.search(r'<h5[^>]*>\s*'+re.escape(club)+r'\s*</h5>(.*?)(?=<h5\b|\Z)',raw,re.S|re.I)
 if not block:return []
 text=block[1];senior=re.split(r'U(?:nder)?[- ]?21',text,flags=re.I)[0];return [re.sub('<[^>]*>',' ',x).replace('*','').strip() for x in re.split(r'<br\s*/?>|</p>',senior,flags=re.I) if ',' in re.sub('<[^>]*>',' ',x)]
def add_current_memberships(db,captures,on):
 audits=[]
 for club in db.execute('SELECT * FROM clubs ORDER BY club_id').fetchall():
  tid=club['club_id'];known=identities(db,tid);r=captures.get('roster_'+str(tid));matched={};ambiguous=[];error=None;rows=[]
  if r and r['status']=='SUCCESS':
   try:rows=roster_rows(tid,(ROOT/r['path']).read_text());matched,ambiguous=link_rows(known,rows)
   except Exception as e:error=type(e).__name__+':'+str(e)
  registration=captures.get('registration')
  if tid in (8456,9825) and registration and registration['status']=='SUCCESS':
   legal=legal_senior((ROOT/registration['path']).read_text(),'Manchester City' if tid==8456 else 'Arsenal')
   for name in legal:
    hits=[pid for pid,p in known.items() if any(len(tokens(n))>=2 and tokens(n)<=tokens(name) for n in p['names'])]
    if len(hits)==1 and hits[0] not in matched:matched[hits[0]]=dict(name=name,registration=True)
  for pid,row in matched.items():
   prior=[x[0] for x in db.execute('SELECT classification FROM player_eligibility WHERE club_id=? AND player_id=?',(tid,pid))];development=any(x.startswith('ACADEMY') for x in prior);status='CLUB_MANAGED_DEVELOPMENT' if development else 'FIRST_TEAM_INJURED' if 'FIRST_TEAM_INJURED' in prior else 'FIRST_TEAM';source=registration if row.get('registration') else r
   proof={k:source.get(k) for k in ['url','path','sha256','response_timestamp','http_status']};proof['identity_match']=row['name'];proof['method']='OWN_CLUB_SENIOR_REGISTER_WITH_KNOWN_ALIASES' if row.get('registration') else 'CURRENT_OFFICIAL_FIRST_TEAM_CARD_WITH_KNOWN_ALIASES';add_event(db,tid,pid,status,'2026-09-03' if row.get('registration') else None,on,[proof],'Official current first-team scope or own senior registration; no membership inferred from match frequency or ownership',precision='REGISTERED_BY_DAY_NOT_CONTRACT_START' if row.get('registration') else 'OBSERVED_CURRENT_SNAPSHOT')
  audits.append(dict(club_id=tid,club=club['name'],official_url=ROSTER_SOURCES[tid],capture_status=r.get('status') if r else None,parse_error=error,linked_current_cards=len(matched),ambiguous_cards=ambiguous,unmapped_source_cards=[x for x in rows if x not in matched.values()]))
 return audits

def retain(s,head,work,report,client):
 # Minimal audit JSON plus explicit acquired bodies; private, unresolved evidence is not a regular expiring log.
 import tarfile
 write(work/'report.json',report);archive=work/'phase2-evidence.tar.gz'
 with tarfile.open(archive,'w:gz') as tar:
  tar.add(work/'report.json',arcname='report.json')
  for r in client.records:
   if r.get('path') and (ROOT/r['path']).is_file():tar.add(ROOT/r['path'],arcname=r['path'],recursive=False)
 release=s.api('POST',s.base+'/releases',dict(tag_name='futograma-phase2-evidence-'+os.environ['GITHUB_RUN_ID']+'-'+os.environ['FUTOGRAMA_PHASE'],target_commitish=head,name='Phase 2 audit evidence',body='Persistent unresolved roster/source audit evidence. Never delete with snapshot or routine temporary retention.',draft=True));asset=s.upload(release['id'],archive);s.download(asset['id'],work/'evidence-readback.gz')
 if sha(archive)!=sha(work/'evidence-readback.gz'):raise RuntimeError('PRIVATE_EVIDENCE_READBACK_FAILED')
 s.api('PATCH',s.base+'/releases/'+str(release['id']),dict(draft=False));return dict(asset_id=asset['id'],release_id=release['id'],sha256=sha(archive))

def main():
 require_hosted()
 if os.environ['GITHUB_REPOSITORY']!='Strange-Noah/Futograma-data' or os.environ.get('FUTBOL_CLOUD_ENABLED')!='false' or os.environ.get('FUTBOL_AUTHORITY_HANDOVER')=='CONFIRMED':raise RuntimeError('ACCEPTANCE_ONLY_WITH_PRODUCTION_DISABLED')
 phase=os.environ['FUTOGRAMA_PHASE'];s=store();main_head,baseline=s.head();ensure_ref(s,REF,main_head);a=store(REF);work=ROOT/'.cloud'/('phase2-'+phase);work.mkdir(parents=True,exist_ok=False);code=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip();config=json.loads((ROOT/'ops/cloud/management-evidence.json').read_text());on=config['reference_date'];report=dict(phase=phase,code_commit=code,reference_date=on,production_head=main_head,production_facts_written=False,authority_handover=False)
 if phase=='first':
  s.restore(work/'working.sqlite');before=inspect(work/'working.sqlite')['tables'];from squad_pipeline import live_ingest
  db=live_ingest.connect(work/'working.sqlite');management_migrate(db);import_verified_history(db);client=Client('phase2-'+os.environ['GITHUB_RUN_ID'],budget=110);HOSTS.update({'www.newcastleunited.com','next.avfc.co.uk','www.avfc.co.uk','www.vfl.de'});captures={}
  urls={('roster_'+str(tid)):url for tid,url in ROSTER_SOURCES.items()};urls['registration']='https://www.premierleague.com/en/news/4706139/see-all-the-202627-premier-league-squad-lists'
  for i,item in enumerate(config['assertions']):urls['departure_'+str(i)]=item['source_url']
  urls.update(cup='https://sport.sky.de/fussball/osnabrueck-vs-fc-bayern/aufstellung/556658',cup_official='https://www.vfl.de/news/spielbericht-bayern-muenchen')
  for name,url in urls.items():
   try:r,b=client.fetch(url,'phase2_'+name);captures[name]=r
   except Exception as exc:captures[name]=dict(url=url,status='SOURCE_UNAVAILABLE',error=type(exc).__name__+':'+str(exc))
  report['roster_capture']=add_current_memberships(db,captures,on);report['disputed']=[]
  for i,item in enumerate(config['assertions']):
   player=db.execute('SELECT display_name FROM players WHERE player_id=?',(item['player_id'],)).fetchone()
   if not player or norm(player[0])!=norm(item['name']):raise RuntimeError('AUDIT_CANONICAL_PLAYER_ID_NAME_MISMATCH')
   source=dict(item,acquired_via='REVIEWED_OFFICIAL_WEB_EXTRACT',assertion_sha256=hashlib.sha256(json.dumps(item,sort_keys=True).encode()).hexdigest());capture=captures['departure_'+str(i)];source['hosted_capture']={k:capture.get(k) for k in ['url','http_status','sha256','path','status','error']};add_event(db,item['club_id'],item['player_id'],item['status'],item['effective_from'],on,[source],item['finding'],precision=item['precision']);d=decision(db,item['club_id'],item['player_id'],on);facts=[dict(x) for x in db.execute('SELECT p.match_id,p.minutes,p.participation_status,m.local_date FROM player_match_minutes p JOIN matches m USING(match_id) WHERE p.club_id=? AND p.player_id=? ORDER BY m.kickoff',(item['club_id'],item['player_id']))];report['disputed'].append(item|dict(management=d,historical_records=facts,historical_records_preserved=True,after_departure_positive_minutes=[f for f in facts if f['local_date']>=item['effective_from'] and (f['minutes'] or 0)>0]));assert not d['included']
  report['six_club_audit']=[]
  for club in db.execute('SELECT * FROM clubs ORDER BY club_id').fetchall():
   members=[]
   for pid, in db.execute('SELECT player_id FROM validation_roster_membership WHERE club_id=? UNION SELECT player_id FROM player_eligibility WHERE club_id=?',(club['club_id'],club['club_id'])):
    p=db.execute('SELECT display_name FROM players WHERE player_id=?',(pid,)).fetchone();members.append(dict(player_id=str(pid),name=p[0],management=decision(db,club['club_id'],pid,on)))
   report['six_club_audit'].append(dict(club_id=club['club_id'],club=club['name'],counts=dict(Counter(m['management']['state'] for m in members)),players=members))
  cup=captures['cup'];expected=db.execute("SELECT * FROM matches WHERE lower(metadata_json) LIKE '%osnab%' AND competition_id=209").fetchone();report['german_cup']=dict(canonical_match_id=expected['match_id'],competition_id=209,home='osnabruck',away='bayern',kickoff=expected['kickoff'],source_url=urls['cup'],live_capture_status=cup['status'])
  if cup['status']=='SUCCESS':
   f,sheets=german_parse((ROOT/cup['path']).read_text(),cup);own=next(x for x in sheets if x['club_id']==9823);target=[p for p in own['players'] if norm(p['name']).endswith('kim') and p['shirt']=='3'];print('CUP_DIAGNOSTIC',json.dumps(dict(parsed=f,expected=dict(expected),target=target),default=str),flush=True);assert f['match_id']==expected['match_id'] and f['competition_id']==209 and f['home_team']=='osnabruck' and f['away_team']=='bayern';assert len(target)==1 and target[0]['derived_minutes']==90;report['german_cup'].update(status='PASS',home_identity_confirmed=True,away_club_id=9823,own_starting_xi=sum(x['role']=='starters' for x in own['players']),own_squad=len(own['players']),min_jae_kim_minutes=90,provider_match_id='556658',identity_method='RETAINED_CANONICAL_MATCH_AND_PLAYER_BRIDGE;NO_NEW_ID_INVENTED')
  else:report['german_cup']['status']='PENDING_LIVE_SOURCE_ACCESS'
  db.commit();db.close();os.environ['FUTBOL_DATABASE_PATH']=str(work/'working.sqlite');os.environ['FUTBOL_SETTINGS_PATH']=str(ROOT/'ops/cloud/runtime.json');os.environ['FUTBOL_RUN_ORIGIN']='phase2_acceptance_preflight'
  from types import SimpleNamespace
  network_exit=live_ingest.run(SimpleNamespace(preflight=True,recheck=True,budget=90,max_details=6));latest=json.loads((BASE/'audit/live_deployment/latest_run.json').read_text());http_log=BASE/'audit/live_deployment/http_requests.jsonl'
  if http_log.is_file():client.records.extend(json.loads(line) for line in http_log.read_text().splitlines() if json.loads(line).get('run_id')==latest['run_id'])
  report['recent_preflight']=dict(exit=network_exit,results=latest['results'],coverage_limitations=latest['coverage_limitations']);db=live_ingest.connect(work/'working.sqlite')
  # Persist a clearly marked controlled clock probe; it contains no football facts and is never displayed.
  at=datetime.now(timezone.utc)-timedelta(days=4);probe=dict(club_id=9823,provider='Sky',catalog_provider_id='PHASE2_CONTROLLED_CLOCK_PROBE',url='https://example.invalid/phase2-clock-probe',date=at.date().isoformat(),acceptance_test_only=True);retry.observe(db,probe,dict(status='SOURCE_UNAVAILABLE',error='HTTP_503'),'controlled-first-job',at);db.commit();report['pending_states']=[{k:r[k] for k in ['match_key','url','state','failure_classification','attempt_count','next_retry_at']} for r in db.execute('SELECT * FROM match_retry_queue')];db.close();assert before==inspect(work/'working.sqlite')['tables'];report['canonical_football_and_legacy_membership_tables_unchanged']=True
 elif phase=='second':
  a.restore(work/'working.sqlite');before=inspect(work/'working.sqlite')['tables'];db=sqlite3.connect(work/'working.sqlite');db.row_factory=sqlite3.Row;pending=retry.due_candidates(db);probe=next(c for c in pending if c.get('acceptance_test_only'));assert probe['durable_pending_retry'] and probe['alternative_sources_allowed'];retry.observe(db,probe,dict(status='UNCHANGED',match_id=None),'controlled-second-job');retry.observe(db,probe,dict(status='UNCHANGED',match_id=None),'controlled-second-job');row=db.execute('SELECT * FROM match_retry_queue WHERE match_key=?',(retry.key(probe),)).fetchone();assert row['state']=='RESOLVED' and row['attempt_count']==2;report.update(pending_survived_independent_job=True,retry_beyond_72h_controlled_clock=True,duplicate_attempt_idempotent=True,diagnostic_history_preserved=True);db.commit();db.close();assert before==inspect(work/'working.sqlite')['tables']
 else:raise ValueError('EXPLICIT_FIRST_OR_SECOND_PHASE_REQUIRED')
 head,old=a.head();pack(work/'working.sqlite',work/'database.sqlite.gz');new,pointer=a.publish(work/'working.sqlite',work/'database.sqlite.gz',head,code);pointer['dataset_ref']=REF;site=work/'site';site.mkdir();manifest=export_browser(work/'working.sqlite',site,new,pointer,on)
 for filename in ['index.html','app.js','style.css']:shutil.copyfile(ROOT/'web'/filename,site/filename)
 report['accepted_snapshot']=dict(head=new,version=pointer['current']['version'],sqlite_sha256=pointer['current']['database']['sha256'],dataset_ref=REF);report['production_pointer_unchanged']=s.head()==(main_head,baseline);assert report['production_pointer_unchanged'];report['evidence']=retain(s,main_head,work,report,client) if phase=='first' else None;write(work/'result.json',report)
 print(json.dumps({k:report[k] for k in report if k not in ('six_club_audit','disputed','recent_preflight')},ensure_ascii=False,indent=2))
 with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write('## Phase 2 '+phase+'\n```json\n'+json.dumps(report,ensure_ascii=False,indent=2)+'\n```\n')
if __name__=='__main__':main()
