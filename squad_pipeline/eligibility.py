"""Evidence-backed dated affiliation and regular membership, independent of match facts."""
import sqlite3,json,re
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from .core import ROOT,BASE,read,write,now,digest
from .six_club_validation import CLUBS
from .six_club_experiment import encoded
from .six_player_extract import text
from .live_parse import Tree
OUT=BASE/'audit/live_deployment'
TYPES={'FIRST_TEAM','LOAN_IN','LOAN_OUT','ACADEMY','ACADEMY_CALLED_UP','TRANSFERRED_OUT','UNVERIFIED'}
DDL='''
CREATE TABLE IF NOT EXISTS player_affiliations(affiliation_id TEXT PRIMARY KEY,player_id INTEGER REFERENCES players,club_id INTEGER,relationship_type TEXT CHECK(relationship_type IN ('FIRST_TEAM','LOAN_IN','LOAN_OUT','ACADEMY','ACADEMY_CALLED_UP','TRANSFERRED_OUT','UNVERIFIED')),start_date TEXT,end_date TEXT,verified_as_of TEXT,verification_status TEXT,source_json TEXT,reason TEXT,date_precision TEXT);
CREATE TABLE IF NOT EXISTS player_eligibility(eligibility_id TEXT PRIMARY KEY,player_id INTEGER REFERENCES players,club_id INTEGER,classification TEXT,regular_member INTEGER,start_date TEXT,end_date TEXT,verified_as_of TEXT,verification_status TEXT,source_json TEXT,reason TEXT);
CREATE TABLE IF NOT EXISTS affiliation_changes(change_id TEXT PRIMARY KEY,club_id INTEGER,player_id INTEGER,before_json TEXT,after_json TEXT,effective_date TEXT,source_json TEXT,reason TEXT,recorded_at TEXT);
CREATE TABLE IF NOT EXISTS player_registration(player_id INTEGER,club_id INTEGER,registration_type TEXT,as_of TEXT,source_json TEXT,PRIMARY KEY(player_id,club_id,registration_type,as_of));
'''
def active(db,tid,pid,on):
 # Unknown interval endpoints are snapshot observations, not permission to backdate an assertion.
 rows=[dict(x) for x in db.execute('SELECT * FROM player_eligibility WHERE player_id=? AND club_id=?',(pid,tid))]
 rows=[r for r in rows if (r['start_date'] is not None and r['start_date']<=on or r['start_date'] is None and on>=r['verified_as_of']) and (r['end_date'] is None or on<=r['end_date'])]
 if not rows:return dict(classification='UNVERIFIED',regular_member=None,verification_status='UNVERIFIED')
 rows.sort(key=lambda r:(r['verification_status']=='VERIFIED',r['start_date'] or r['verified_as_of']),reverse=True);return rows[0]
def absence_semantics(db,tid,pid,on):
 a=active(db,tid,pid,on);c=a['classification']
 if c in ['ACADEMY_NOT_CALLED_UP','ACADEMY_CALLED_UP']:return 'ACADEMY_NOT_SELECTED;NO_INJURY_OR_COACH_REJECTION_INFERENCE'
 if c in ['LOAN_OUT','TRANSFERRED_OUT']:return 'OUTSIDE_PARENT_CLUB_ACTIVE_ROSTER'
 return 'ABSENT_REASON_UNKNOWN;INJURY_CANNOT_BE_INFERRED'
def add(db,pid,tid,rel,classification,regular,start,end,verified,source,reason,status='VERIFIED',precision='DAY'):
 assert rel in TYPES
 sources=source if isinstance(source,list) else [source];base=dict(player_id=str(pid),club_id=tid,relationship_type=rel,start_date=start,end_date=end,verified_as_of=verified,verification_status=status,source=sources,reason=reason,date_precision=precision);key=digest(encoded(base).encode());db.execute('INSERT OR IGNORE INTO player_affiliations VALUES(?,?,?,?,?,?,?,?,?,?,?)',(key,pid,tid,rel,start,end,verified,status,encoded(sources),reason,precision));ekey=digest(encoded([base,classification,regular]).encode());db.execute('INSERT OR IGNORE INTO player_eligibility VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ekey,pid,tid,classification,regular,start,end,verified,status,encoded(sources),reason));return dict(**base,classification=classification,regular_member=regular)
def participation(db,tid,pid):return [dict(x) for x in db.execute('SELECT m.match_id,m.local_date,m.kickoff,m.metadata_json,p.participation_status,p.minutes FROM player_match_minutes p JOIN matches m USING(match_id) WHERE p.club_id=? AND p.player_id=? ORDER BY m.kickoff',(tid,pid))]
def evidence(r,terms=()):
 if r.get('status')!='SUCCESS':return False
 p=ROOT/r.get('reused_body_path',r.get('path',''))
 if not p.is_file():return False
 content=text(p.read_text());return all(t.lower() in content.lower() for t in terms)
def case_evidence(j,key,terms):
 r=j[key]
 if not evidence(r,terms):raise RuntimeError('CASE_PRIMARY_EVIDENCE_NOT_CONFIRMED:'+key)
 return dict(url=r['url'],path=r.get('reused_body_path',r['path']),sha256=r.get('reused_body_sha256',r['sha256']),observed_at=r['response_timestamp'],location='HTML body; checked literal terms '+repr(terms),evidence_channel='FRESH_PUBLIC_OFFICIAL_HTTP')
def run():
 db=sqlite3.connect(BASE/'state/live.sqlite');db.row_factory=sqlite3.Row;db.executescript(DDL);j=read(OUT/'case_source_capture.json');b=read(OUT/'boundary_source_capture.json');b.update(read(OUT/'additional_boundary_capture.json'));today=datetime.now(ZoneInfo('Europe/Madrid')).date().isoformat();allchanges=[];cases=[];refs=read(BASE/'audit/six_club/reference_rosters.json');diff=read(BASE/'audit/six_club/roster_differences.json');facts_before=digest(encoded([list(x) for x in db.execute('SELECT * FROM player_match_minutes ORDER BY club_id,match_id,player_id')]).encode());national_before=digest(encoded([list(x) for x in db.execute('SELECT * FROM validation_national_reference ORDER BY player_id,match_id')]).encode())
 def record(pid,tid,rel,cls,reg,start=None,end=None,source=None,reason='',status='VERIFIED',precision='DAY'):
  old=active(db,tid,pid,today);after=add(db,pid,tid,rel,cls,reg,start,end,today,source,reason,status,precision);before=dict(classification='CANDIDATE_SETS_WITHOUT_SEPARATE_ELIGIBILITY' if db.execute('SELECT 1 FROM validation_roster_membership WHERE club_id=? AND player_id=?',(tid,pid)).fetchone() else 'NO_MEMBERSHIP_IN_SIX_CLUB_SEED',membership_rows=[dict(x) for x in db.execute('SELECT * FROM validation_roster_membership WHERE club_id=? AND player_id=?',(tid,pid))],source='unchanged validation_roster_membership',previous_review=old);ch=dict(player_id=str(pid),club_id=tid,before=before,after=after,effective_date=start,source=source,reason=reason);allchanges.append(ch);db.execute('INSERT OR IGNORE INTO affiliation_changes VALUES(?,?,?,?,?,?,?,?,?)',(digest(encoded(ch).encode()),tid,pid,encoded(before),encoded(after),start,encoded(source),reason,now()));return after
 # Four primary-source acceptance gates; no statistics are used to infer promotion.
 a1=case_evidence(j,'araujo_barca',['30 June 2027','10 Aug 26']);a2=case_evidence(j,'araujo_liverpool',['formalities','2026-27','2026-08-10']);db.execute('INSERT OR IGNORE INTO players VALUES(?,?,?)',(921253,'Ronald Araújo','FotMob identity retained from legacy identity registry; official full-name checked'))
 after=record(921253,8634,'LOAN_OUT','LOAN_OUT',0,'2026-08-10','2027-06-30',[a1,a2],'双方公告及接收方已完成手续后首次训练；日精度生效；合同计划到期不自动证明届时回归。')
 cases.append(dict(name='Ronald Araújo',player_id='921253',decision=after,participation=participation(db,8634,921253),history='Legacy Barcelona records preserved; Liverpool club scope is never aggregated as Barcelona',scheduled_end_not_return_proof=True))
 academy=case_evidence(j,'barca_b',['19 Abdelkarim','24 Bisiwu']);sign=case_evidence(j,'bisiwu_signing',['Jesse Bisiwu']);hcontract=case_evidence(j,'hamza_contract',['31 August 2026','2030']);hbuy=case_evidence(j,'hamza_buy',['2029'])
 bs=participation(db,8634,1656591);assert bs and all(x['participation_status']=='NOT_IN_MATCHDAY_SQUAD' for x in bs)
 after=record(1656591,8634,'ACADEMY','ACADEMY_NOT_CALLED_UP',0,source=[academy,sign],reason='当前 Barça B 名单#24；已采集13场完整一线队名单均未入选。官网文章 First Team 栏目及签约不能证明晋升；实际梯队起始日未知。',precision='OBSERVED_AS_OF;START_UNKNOWN')
 cases.append(dict(name='Jesse Bisiwu',player_id='1656591',decision=after,participation=bs,coverage='All acquired Barcelona completed fixtures, not an assertion about unacquired fixtures',absence_semantics='ACADEMY_NOT_SELECTED'))
 hs=participation(db,8634,1708842);after=record(1708842,8634,'ACADEMY','ACADEMY_CALLED_UP',0,source=[academy,hcontract,hbuy],reason='当前 Barça B #19；正式比赛征召独立保留，续约不等于正式晋升。2026年6月买断后不能继续标为租入。',precision='OBSERVED_AS_OF;START_UNKNOWN')
 cases.append(dict(name='Hamza Abdelkarim',player_id='1708842',decision=after,participation=hs,status_counts=dict(Counter(x['participation_status'] for x in hs)),minutes=sum(x['minutes'] or 0 for x in hs)))
 for x in hs:
  if x['participation_status'] not in ['UNKNOWN','NOT_IN_MATCHDAY_SQUAD']:add(db,1708842,8634,'ACADEMY_CALLED_UP','ACADEMY_CALLED_UP',0,x['local_date'],x['local_date'],today,[dict(match_id=x['match_id'],source='retained validated full match sheet; not proof of promotion')],'正式比赛临时调用当日',precision='MATCH_DAY')
 promotion=case_evidence(b,'dowman_promotion',['summer of 2025','Max Dowman was officially promoted']);reg=case_evidence(j,'pl_registration',['Dowman, Max Robert']);ds=participation(db,9825,1635773)
 after=record(1635773,9825,'FIRST_TEAM','FIRST_TEAM',1,source=[promotion,reg],reason='阿森纳署名官方专题明确2025夏正式晋升；英超U21为年龄注册豁免，不能反推不是常规成员。确切晋升日未知，2026本窗口可确认已晋升。',precision='PROMOTION_SUMMER_2025;EXACT_DAY_UNKNOWN')
 # The promotion predates this acquired season; date is a verified latest bound, never a fabricated contract date.
 db.execute("UPDATE player_eligibility SET start_date='2026-08-01' WHERE player_id=1635773 AND club_id=9825 AND classification='FIRST_TEAM'")
 db.execute('INSERT OR IGNORE INTO player_registration VALUES(?,?,?,?,?)',(1635773,9825,'U21_AGE_EXEMPT',today,encoded(reg)))
 cases.append(dict(name='Max Dowman',player_id='1635773',decision=after,effective_scope_start='2026-08-01;STATISTICAL_WINDOW_NOT_PROMOTION_DATE',participation=ds,status_counts=dict(Counter(x['participation_status'] for x in ds)),minutes=sum(x['minutes'] or 0 for x in ds)))

 # Senior PL registration is additional independent evidence, unlike the mixed senior/U21 shirt list.
 plraw=(ROOT/j['pl_registration']['path']).read_text();plroot=Tree(plraw).root;senior={};registration_names={}
 for heading in plroot.all(tag='h5'):
  club=heading.txt()
  tid=next((t for t,v in CLUBS.items() if v[0]==club or club=='Manchester City' and t==8456),None)
  if tid not in [8456,9825]:continue
  # Locate the actual registered senior paragraph after this club's own header.
  marker=re.search(r'<h5[^>]*>.*?'+re.escape(club)+r'.*?</h5>\s*<p>(.*?)</p>',plraw,re.S)
  if marker:senior[tid]=[text(x).replace('*','').strip() for x in re.split(r'<br\s*/?>',marker[1],flags=re.I) if ',' in text(x)]
  youth=re.search(r'<h5[^>]*>.*?'+re.escape(club)+r'.*?</h5>\s*<p>.*?</p>\s*<p>(.*?)</p>',plraw,re.S)
  youths=[text(x).replace('*','').replace('(Loan)','').strip() for x in re.split(r'<br\s*/?>',youth[1],flags=re.I) if ',' in text(x)] if youth else []
  registration_names[tid]=senior.get(tid,[])+youths
 def tokens(name):
  import unicodedata
  return set(re.findall(r'[a-z]+',unicodedata.normalize('NFKD',name).encode('ascii','ignore').decode().lower().replace("'",'')))
 def senior_match(tid,name):
  ts=tokens(name);hits=[n for n in senior.get(tid,[]) if len(ts)>=2 and ts<=tokens(n)]
  return hits[0] if len(hits)==1 else None
 # Every baseline/union player gets an explicit reviewed boundary outcome; unknowns remain unknown.
 webpath=OUT/'bayern_web_tool_response.json'
 if webpath.exists():
  captured=read(webpath);webtext=captured if isinstance(captured,str) else encoded(captured)
  for label,needles in {'Cassiano Kiala':['Cassiano Kiala','Campus','20.08.2026'],'Tim Binder':['Tim Binder','Campus-Talente'],'David Podar-Stiube':['David Podar-Stiube','Campus-Talente'],'Maycon Cardozo':['Maycon Cardozo','Campus-Talente'],'Arijon Ibrahimovic':['01 September 2026','FC Augsburg','end of the season']}.items():
   if all(t in webtext for t in needles):
    old=b.get(label,{});b[label]=dict(status='SUCCESS',url=old.get('url'),path=str(webpath.relative_to(ROOT)),sha256=digest(webpath.read_bytes()),response_timestamp=today,evidence_channel='FRESH_WEB_TOOL_OFFICIAL_NOT_LOCAL_HTTP',local_http_attempt=old)
 special={(8634,921253),(8634,1656591),(8634,1708842),(9825,1635773)};diffmap={(d['club_id'],d['player_id']):d for d in diff};refmap={(r['club_id'],r['player_id']):r for r in refs}
 departures={312765:('TRANSFERRED_OUT',None,'2026-09-03'),839204:('LOAN_OUT','2026-08-27',None),1280132:('TRANSFERRED_OUT',None,'2026-08-31'),1486264:('LOAN_OUT','2026-09-02',None),1327150:('LOAN_OUT','2026-09-01',None),1297165:('TRANSFERRED_OUT','2026-08-31',None),1593857:('TRANSFERRED_OUT',None,today),-71613674150274285:('TRANSFERRED_OUT',None,today)}
 for tid,pid in db.execute('SELECT DISTINCT club_id,player_id FROM validation_roster_membership ORDER BY club_id,player_id').fetchall():
  if (tid,pid) in special:continue
  name=db.execute('SELECT display_name FROM players WHERE player_id=?',(pid,)).fetchone()[0];d=diffmap.get((tid,pid));ref=refmap.get((tid,pid));source=[ref['source']] if ref else [];rel='UNVERIFIED';cls='UNVERIFIED';regular=None;start=None;end=None;status='UNVERIFIED';reason='已有官网名单快照或比赛并集仅作候选，尚无足够独立日期化证据确认常规成员及完整转会区间；不推断受伤或离队。';precision='START_AND_END_UNKNOWN'
  if d:
   capture=b.get(name);source=([dict(**capture)] if capture and capture.get('status')=='SUCCESS' else [dict(url=d['source'],evidence_channel='PRIOR_REVIEW_LEAD;NOT_REVERIFIED',capture_result=capture)])
   reason=d['reason']+' 本轮只在新取得的外部证据支持范围内确认。'
   if capture and evidence(capture):
    if d['category']=='YOUTH_CALLUP':rel='ACADEMY';cls='ACADEMY_CALLED_UP';regular=0;status='VERIFIED';precision='SNAPSHOT_AND_MATCH_DAYS;START_UNKNOWN'
    elif d['category']=='INJURED':rel='FIRST_TEAM';cls='FIRST_TEAM_INJURED';regular=1;status='VERIFIED';precision='OBSERVED_AS_OF;START_UNKNOWN'
    elif pid in departures:rel,start,bound=departures[pid];cls=rel;regular=0;status='VERIFIED';precision='ANNOUNCED_DAY' if start else 'DEPARTED_BY_'+bound;start=start or bound;reason+=' '+('start_date是确认已离队的最迟边界，不是推算合同生效日。' if bound else '')
  registered=senior_match(tid,name)
  if rel=='UNVERIFIED' and ref and registered:
   rel='FIRST_TEAM';cls='FIRST_TEAM';regular=1;status='VERIFIED';start='2026-09-03';precision='REGISTERED_BY_DATE_NOT_CONTRACT_START';reason='官网一线队候选与英超实际提交的成年注册名单独立相符；保留伤缺，不由出场次数推断晋升。9月3日为确认注册状态的时间边界，非合同起始日。';source=[ref['source'],(reg|dict(registered_name=registered,club_section=CLUBS[tid][0],location='Own-club 25 Squad players paragraph, excludes U21 paragraph'))]
  if pid==1254234 and tid==9825:
   capture=b['nwaneri_loan'];assert evidence(capture,['loan','2026']);source=[capture];rel='LOAN_OUT';cls='LOAN_OUT';regular=0;start='2026-09-01';status='VERIFIED';precision='OFFICIAL_ANNOUNCEMENT_DAY';reason='多特官方9月1日确认从阿森纳租借至赛季末；不能将接收方分钟算入阿森纳。具体合同终止日未写明，不硬填6月30日。'
  all_matches=[n for n in registration_names.get(tid,[]) if len(tokens(name))>=2 and tokens(name)<=tokens(n)]
  identity_registered=all_matches[0] if len(all_matches)==1 else registered
  if identity_registered:
   registered=identity_registered
   ordered=registered.split(',',1);legal=' '.join(reversed(ordered)).strip() if len(ordered)==2 else registered
   db.execute('INSERT OR IGNORE INTO validation_player_aliases VALUES(?,?,?)',('pl-registered:'+str(tid)+':'+digest(registered.encode())[:16],pid,encoded(dict(names=[legal,name],source=reg,method='UNIQUE_KNOWN_NAME_TOKENS_IN_LEGAL_SENIOR_REGISTER;NOT_JERSEY'))))
  record(pid,tid,rel,cls,regular,start,end,source,reason,status,precision)
 # Explicit issues include unverified loan-in and promotion status instead of silently claiming all members are clean.
 audit=[]
 for tid,pid in db.execute('SELECT club_id,player_id FROM validation_roster_membership UNION SELECT club_id,player_id FROM player_affiliations ORDER BY club_id,player_id'):
  r=active(db,tid,pid,today);r=dict(r);r['player_id']=str(pid);r['name']=db.execute('SELECT display_name FROM players WHERE player_id=?',(pid,)).fetchone()[0];r['club_id']=tid;r['participation_preserved']=True;r['baseline_candidate']=(tid,pid) in refmap;audit.append(r)
 facts_after=digest(encoded([list(x) for x in db.execute('SELECT * FROM player_match_minutes ORDER BY club_id,match_id,player_id')]).encode());national_after=digest(encoded([list(x) for x in db.execute('SELECT * FROM validation_national_reference ORDER BY player_id,match_id')]).encode());assert facts_before==facts_after and national_before==national_after;db.commit();result=dict(observed_at=now(),cases=cases,all_players=audit,changes=allchanges,classification_counts=dict(Counter(r['classification'] for r in audit)),open_items=[dict(club_id=r['club_id'],player_id=r['player_id'],name=r['name'],issue='DATED_AFFILIATION_OR_REGULAR_STATUS_NOT_INDEPENDENTLY_CONFIRMED') for r in audit if r['verification_status']!='VERIFIED'],facts_preserved=dict(before=facts_before,after=facts_after,national_before=national_before,national_after=national_after),limits=['Current player pages cannot establish exact historical dates','Registration U21 does not by itself mean academy membership','Prior manual and web review records are leads, not independent new evidence','All six clubs audited but unresolved items are not promoted to VERIFIED','Live collector never automatically promotes new observed players']);write(OUT/'affiliation_audit.json',result);db.close();print(json.dumps(dict(cases=4,players=len(audit),counts=result['classification_counts'],open_items=len(result['open_items']),facts_preserved=True),ensure_ascii=False))
if __name__=='__main__':run()
