"""Atalanta user-supplied first-team text parser; never a generic website parser."""
from __future__ import annotations
import argparse,json,re,unicodedata
from collections import Counter,defaultdict
from .core import *
from .acquisition import seed_sources
from .manual import register
from .fotmob import extract
COUNTRIES={'意大利':'ITA','科特迪瓦':'CIV','瑞典':'SWE','波黑':'BIH','丹麦':'DEN','斯洛文尼亚':'SVN','克罗地亚':'CRO','塞尔维亚':'SRB','巴西':'BRA','波兰':'POL','北马其顿':'MKD','加纳':'GHA','英格兰':'ENG','比利时':'BEL','黑山':'MNE'}
POSITIONS={'门将':0,'后卫':1,'中场':2,'前锋':3}
EXPECTED={'门将':3,'后卫':9,'中场':7,'前锋':6}

def normalized(name):
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in unicodedata.normalize('NFKD',name).casefold() if not unicodedata.combining(c)).split())

def parse_text(text,expected_counts=None):
    expected_counts=expected_counts or EXPECTED;rows=[];position=None;pending=[];declared={};stopped=False
    for lineno,line in enumerate(text.splitlines(),1):
        value=line.strip()
        if not value:continue
        heading=re.fullmatch(r'###\s*(门将|后卫|中场|前锋)[（(](\d+)人[）)]',value)
        if heading:
            if pending:raise ValueError('Incomplete player record before next position')
            position=heading[1]
            if position in declared:raise ValueError('Duplicate position group')
            declared[position]=int(heading[2]);continue
        if value.startswith(('主教练','教练组','医疗团队','工作人员','### STAFF','合计')):
            if pending:raise ValueError('Incomplete player record before staff/total')
            stopped=True;continue
        if value.startswith('#') or value in {'**号码**','**球员**','**国籍**'} or re.fullmatch(r'\|[ \-&;#x0-9a-z]*\|',value):continue
        if not position:continue
        if stopped:continue
        pending.append((lineno,value))
        if len(pending)==3:
            (start,number),(_,name),(end,nation)=pending
            if not number.isdecimal() or not 1<=int(number)<=99:raise ValueError('Invalid/missing shirt number on line '+str(start))
            if nation not in COUNTRIES:raise ValueError('Unknown nationality on line '+str(end))
            if not re.search(r'[^\W\d_]',name,re.U):raise ValueError('Missing player name')
            rows.append(dict(ordinal=len(rows)+1,shirt_number=int(number),full_name=name,position=position,nationality=nation,country_code=COUNTRIES[nation],line_start=start,line_end=end))
            pending=[]
    if pending:raise ValueError('Truncated final record')
    counts=Counter(r['position'] for r in rows)
    if dict(counts)!=dict(expected_counts) or declared!=dict(expected_counts):raise ValueError('Roster/position count mismatch: '+str(dict(counts)))
    if len({normalized(r['full_name']) for r in rows})!=len(rows):raise ValueError('Duplicate player names; review required')
    if len({r['shirt_number'] for r in rows})!=len(rows):raise ValueError('Duplicate shirt numbers; review required')
    return rows

def ensure_schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS official_membership_details(snapshot_id TEXT,local_member_id TEXT,ordinal INTEGER,position TEXT,nationality TEXT,country_code TEXT,official_source_player_id TEXT,line_start INTEGER,line_end INTEGER,PRIMARY KEY(snapshot_id,local_member_id),FOREIGN KEY(snapshot_id,local_member_id) REFERENCES official_membership(snapshot_id,official_player_id));
    CREATE TABLE IF NOT EXISTS official_identity_candidates(snapshot_id TEXT,local_member_id TEXT,fotmob_player_id INTEGER,method TEXT,review_status TEXT,evidence_json TEXT,PRIMARY KEY(snapshot_id,local_member_id,fotmob_player_id));
    """)

def raw_identity_pool(db,club_id):
    pool=defaultdict(list)
    for r in db.execute('SELECT DISTINCT m.match_id,m.local_date,m.period_id,a.path,a.sha256,a.artifact_id FROM squad_observations o JOIN matches m USING(match_id) JOIN raw_artifacts a ON o.artifact_id=a.artifact_id WHERE o.club_id=?',(club_id,)):
        path=ROOT/r['path'];raw=path.read_bytes()
        if digest(raw)!=r['sha256']:raise ValueError('Raw match evidence hash changed')
        detail=json.loads(raw);entries=extract(detail,club_id)
        for x in entries:
            m=re.fullmatch(r'\$\.content\.lineup\.(homeTeam|awayTeam)\.(starters|subs)\[(\d+)\]',x['json_path'])
            player=detail['content']['lineup'][m[1]][m[2]][int(m[3])]
            pool[x['player_id']].append(dict(match_id=r['match_id'],date=r['local_date'],period=r['period_id'],artifact_id=r['artifact_id'],sha256=r['sha256'],path=r['path'],json_path=x['json_path'],name=player['name'],first_name=player.get('firstName'),last_name=player.get('lastName'),country_code=player.get('countryCode'),position_id=player.get('usualPlayingPositionId'),shirt_number=player.get('shirtNumber'),age=player.get('age'),opta_id=((detail.get('content') or {}).get('playerStats') or {}).get(str(x['player_id']),{}).get('optaId')))
    return pool

def candidates(row,pool,period):
    result=[];name=normalized(row['full_name'])
    for pid,evidence in pool.items():
        exact=[e for e in evidence if name in {normalized(e['name']),normalized(' '.join(x for x in (e['first_name'],e['last_name']) if x))}]
        alias=[e for e in evidence if len(normalized(e['name']).split())==1 and name.startswith(normalized(e['name'])+' ') and row['country_code']==e['country_code'] and POSITIONS[row['position']]==e['position_id']]
        if not exact and not alias:continue
        names=exact or alias;country=any(e['country_code']==row['country_code'] for e in names);position=any(e['position_id']==POSITIONS[row['position']] for e in names);current=any(e['period']==period for e in names)
        status='NATIONALITY_CONFLICT_REVIEW_REQUIRED' if not country else ('POSITION_CLASSIFICATION_REVIEW_REQUIRED' if not position else ('ALIAS_REVIEW_REQUIRED' if not exact else ('SUPPORTED_CANDIDATE_REVIEW_REQUIRED' if current else 'HISTORICAL_CANDIDATE_REVIEW_REQUIRED')))
        result.append(dict(fotmob_player_id=pid,method='NORMALIZED_FULL_NAME_AND_RAW_CLUB_CONTEXT' if exact else 'SINGLE_NAME_ALIAS_CANDIDATE_ONLY',status=status,country_agrees=country,position_agrees=position,observed_current_half=current,evidence=names))
    if len(result)>1:
        for c in result:c['status']='AMBIGUOUS_MULTIPLE_IDS'
    return result

def load_baseline(db,folder,out):
    folder=within(folder);out=within(out);out.mkdir(exist_ok=True,parents=True)
    sources=read(BASE/'config/official_sources.json');seed_sources(db,sources);ensure_schema(db)
    manifest=read(folder/'source.json')
    if manifest['source_id']!='atalanta_club' or manifest['season']!='2026/27' or manifest['half']!='H1':raise ValueError('Dedicated parser only accepts Atalanta 2026/27 H1')
    registrations=register(db,folder,sources);src=folder/'squad.txt';raw=src.read_bytes();rows=parse_text(raw.decode('utf-8-sig'))
    rawpath=BASE/'raw/manual/atalanta_club'/digest(raw)/'response.txt'
    reg=next((r for r in registrations if r.get('artifact_id') and db.execute('SELECT path FROM raw_artifacts WHERE artifact_id=?',(r['artifact_id'],)).fetchone()[0]==str(rawpath.relative_to(ROOT))),None)
    if not reg:raise ValueError('squad.txt was not registered')
    sid=reg['snapshot_id'];pool=raw_identity_pool(db,8524);structured=[];matching=[];review=[]
    with db:
        for row in rows:
            local='local:'+sid[:16]+':'+str(row['ordinal']).zfill(3)
            provenance=json.dumps(dict(evidence_origin=manifest['evidence_origin'],artifact_id=reg['artifact_id'],line_start=row['line_start'],line_end=row['line_end'],official_provider_id_available=False,season_label_present=False,captured_at=manifest['captured_at']),ensure_ascii=False)
            old=db.execute('SELECT source_name,shirt_number FROM official_membership WHERE snapshot_id=? AND official_player_id=?',(sid,local)).fetchone()
            if old and (old['source_name']!=row['full_name'] or str(old['shirt_number'])!=str(row['shirt_number'])):raise ValueError('Frozen baseline record changed')
            db.execute('INSERT OR IGNORE INTO official_membership VALUES(?,?,?,?,?,?,?)',(sid,local,row['full_name'],None,str(row['shirt_number']),None,provenance))
            db.execute('INSERT OR IGNORE INTO official_membership_details VALUES(?,?,?,?,?,?,?,?,?)',(sid,local,row['ordinal'],row['position'],row['nationality'],row['country_code'],None,row['line_start'],row['line_end']))
            cand=candidates(row,pool,'2026-27_H1')
            for c in cand:db.execute('INSERT INTO official_identity_candidates VALUES(?,?,?,?,?,?) ON CONFLICT(snapshot_id,local_member_id,fotmob_player_id) DO UPDATE SET method=excluded.method,review_status=excluded.review_status,evidence_json=excluded.evidence_json',(sid,local,c['fotmob_player_id'],c['method'],c['status'],json.dumps(c,ensure_ascii=False)))
            item=dict(snapshot_id=sid,local_member_id=local,official_source_player_id=None,**row,date_of_birth=None,fotmob_player_id=None,identity_status='NOT_VERIFIED',captured_at=manifest['captured_at'],season='2026/27',half='H1',season_label_present=False,season_assignment='USER_ASSIGNED_PENDING_CAPTURE_TIME')
            structured.append(item)
            match=dict(local_member_id=local,official_name=row['full_name'],candidate_count=len(cand),candidate_fotmob_ids=';'.join(str(c['fotmob_player_id']) for c in cand),status=cand[0]['status'] if len(cand)==1 else ('UNMATCHED' if not cand else 'AMBIGUOUS_MULTIPLE_IDS'),country_agrees=cand[0]['country_agrees'] if len(cand)==1 else None,position_agrees=cand[0]['position_agrees'] if len(cand)==1 else None,observed_current_half=cand[0]['observed_current_half'] if len(cand)==1 else None,confirmed_fotmob_id=None,evidence_json=json.dumps(cand,ensure_ascii=False))
            matching.append(match);review.append(dict(official_name=row['full_name'],candidate_ids=match['candidate_fotmob_ids'],status=match['status'],next_action='Verify full identity against a stable official profile or DOB; do not confirm from name/shirt alone' if match['status']!='UNMATCHED' else 'Provide official player identity/profile evidence; no speculative ID assignment'))
        db.execute("UPDATE official_snapshots SET parse_status='PARSED_PENDING_IDENTITY',content_review_status='USER_ACCEPTED_FIRST_TEAM_SEASON_AND_CAPTURE_TIME_UNCONFIRMED' WHERE snapshot_id=? AND parse_status!='VERIFIED'",(sid,))
        db.execute('UPDATE manual_actions SET status=?,last_step=?,next_action=? WHERE source_id=?',('ACQUIRED','User-provided 25-player text registered and parsed','No further page export needed. Supply actual copy time and review identity candidates.','atalanta_club'))
    table(out/'official_roster.csv',structured);write(out/'official_roster.json',structured);table(out/'identity_matches.csv',matching);table(out/'identity_review.csv',review)
    summary=dict(snapshot_id=sid,artifact_id=reg['artifact_id'],records=len(rows),position_counts=dict(Counter(r['position'] for r in rows)),source_origin=manifest['evidence_origin'],captured_at=manifest['captured_at'],received_date=manifest['received_date'],source_season_label=None,assigned_period='2026-27_H1',canonical_mapping_confirmed=0,unique_candidate_records=sum(r['candidate_count']==1 for r in matching),unmatched=sum(r['candidate_count']==0 for r in matching),ambiguous=sum(r['candidate_count']>1 for r in matching),candidate_status_counts=dict(Counter(r['status'] for r in matching)),baseline_content='ACCEPTED_USER_TRANSCRIPTION',baseline_canonical_identity='PENDING',original_webpage_bytes_available=False)
    write(out/'summary.json',summary)
    return summary

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--folder',default=str(BASE/'manual_inputs/atalanta/2026-27/H1'));args=ap.parse_args();db=connect();seed(db,config());print(json.dumps(load_baseline(db,args.folder,BASE/'atalanta_2026_27_h1'),ensure_ascii=False,indent=2));db.close()
if __name__=='__main__':main()
