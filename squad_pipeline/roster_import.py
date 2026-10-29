"""One-command, offline official roster import; source-specific parsers are adapters."""
from __future__ import annotations
import csv,json,re,sqlite3
from datetime import date
from pathlib import Path
from .core import *
from .atalanta_text import normalized,raw_identity_pool

class ImportBlocked(RuntimeError):pass

def import_schema(db):
    db.executescript((BASE/'import_schema.sql').read_text())
    # Confirmed individual mappings may be used even while other snapshot members are unresolved.
    db.executescript("""DROP VIEW IF EXISTS verified_baseline_membership;
    CREATE VIEW verified_baseline_membership AS SELECT s.club_id,s.period_id,b.fotmob_player_id player_id,s.snapshot_id FROM official_membership b JOIN official_snapshots s USING(snapshot_id) WHERE b.fotmob_player_id IS NOT NULL AND EXISTS(SELECT 1 FROM provider_identities i WHERE i.source_id=s.source_id AND i.official_player_id=b.official_player_id AND i.fotmob_player_id=b.fotmob_player_id AND i.review_status='VERIFIED');""")

def season_key(value):
    m=re.fullmatch(r'(20\d{2})[-/](\d{2}|20\d{2})',value)
    if not m:raise ImportBlocked('Season must be YYYY-YY, YYYY/YY or YYYY/YYYY')
    y=int(m[1]);end=int(m[2]) if len(m[2])==4 else 2000+int(m[2])
    if end!=y+1:raise ImportBlocked('Invalid consecutive season years')
    return f'{y}/{end%100:02d}'

def checkpoint(db,job,stage,payload):
    sha=digest(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode())
    db.execute('INSERT OR IGNORE INTO roster_import_steps VALUES(?,?,?,?)',(job,stage,sha,now()))
    db.execute('UPDATE roster_import_jobs SET last_stage=?,status=?,reason=NULL,updated_at=? WHERE job_id=?',(stage,'RUNNING',now(),job));db.commit()

def reuse_atalanta(db,adapter,folder,out):
    path=within(BASE/adapter['parsed_path'])
    if not path.exists():
        from .atalanta_text import load_baseline
        load_baseline(db,folder,out/'parsed');path=out/'parsed/official_roster.csv'
    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    if not rows:raise ImportBlocked('Parsed official roster is empty')
    sid=rows[0]['snapshot_id'];snapshot=db.execute('SELECT * FROM official_snapshots WHERE snapshot_id=?',(sid,)).fetchone()
    if not snapshot:raise ImportBlocked('Parsed source snapshot missing from database')
    raw=db.execute('SELECT * FROM raw_artifacts WHERE artifact_id=?',(snapshot['artifact_id'],)).fetchone()
    if not raw or digest((ROOT/raw['path']).read_bytes())!=raw['sha256']:raise ImportBlocked('Original roster artifact missing/corrupt')
    text=(ROOT/raw['path']).read_text().splitlines();native=[]
    for r in rows:
        if r['snapshot_id']!=sid:raise ImportBlocked('Mixed snapshots in parsed input')
        start,end=int(r['line_start']),int(r['line_end'])
        if [v.strip() for v in text[start-1:end]]!=[str(r['shirt_number']),r['full_name'],r['nationality']]:raise ImportBlocked('Parsed roster no longer agrees with immutable source lines')
        stored=db.execute('SELECT * FROM official_membership WHERE snapshot_id=? AND official_player_id=?',(sid,r['local_member_id'])).fetchone()
        if not stored or stored['source_name']!=r['full_name'] or stored['shirt_number']!=str(r['shirt_number']):raise ImportBlocked('Frozen official member mismatch')
        native.append(dict(official_member_id=r['local_member_id'],name=r['full_name'],shirt_number=str(r['shirt_number']),position=r['position'],nationality=r['nationality'],country_code=r['country_code'],date_of_birth=r.get('date_of_birth') or None,official_source_player_id=r.get('official_source_player_id') or None))
    if len({r['official_member_id'] for r in native})!=len(native):raise ImportBlocked('Duplicate official member IDs')
    return native,dict(snapshot),dict(input_path=str(path.relative_to(BASE)),input_sha256=digest(path.read_bytes()),raw_artifact_id=raw['artifact_id'],raw_sha256=raw['sha256'],parsing='REUSED_EXISTING_PARSED_ROSTER_WITH_SOURCE_LINE_VALIDATION')

from .three_club_parsers import psg_manual_roster,bayern_manual_roster,barcelona_manual_roster
from .html_rosters import import_html
PARSERS={'manchester_city_html':import_html,'inter_html':import_html,'existing_atalanta_text':reuse_atalanta,'psg_manual_roster':psg_manual_roster,'bayern_manual_roster':bayern_manual_roster,'barcelona_manual_roster':barcelona_manual_roster}

def independent_aliases(specs,club_key):
    """Read only previously captured, checksum-pinned identity content, never roster membership."""
    records=[]
    def walk(v,path='$'):
        if isinstance(v,dict):
            yield path,v
            for k,x in v.items():yield from walk(x,path+'.'+k)
        elif isinstance(v,list):
            for i,x in enumerate(v):yield from walk(x,f'{path}[{i}]')
    for spec in specs:
        if spec.get('provider')!='seriea':continue
        p=ROOT/spec['path']
        if not p.is_file() or digest(p.read_bytes())!=spec['sha256']:raise ImportBlocked('Required independent identity cache missing/corrupt: '+spec['path'])
        for si,script in enumerate(re.findall(r'<script[^>]*>(.*?)</script>',p.read_text(),re.S)):
            if not script.startswith('self.__next_f.push('):continue
            try:frame=json.loads(script[len('self.__next_f.push('):-1]);content=frame[1] if len(frame)>1 and isinstance(frame[1],str) else ''
            except ValueError:continue
            for li,line in enumerate(content.splitlines()):
                try:obj=json.loads(line.split(':',1)[1])
                except (ValueError,IndexError):continue
                for path,v in walk(obj):
                    if v.get('teamSlug')!=club_key or not v.get('playerId') or not v.get('mediaFirstName'):continue
                    full=(v['mediaFirstName']+' '+v.get('mediaLastName','')).strip()
                    records.append(dict(full_name=full,aliases=sorted({x for x in (v.get('shortName'),v.get('shirtName')) if x}),country_code=v.get('nationalityIsoCode'),date_of_birth=v.get('dateOfBirth'),source_player_id=v['playerId'],source_url=spec['url'],raw_path=spec['path'],sha256=spec['sha256'],field_path=f'script[{si}]/frame_line[{li}]/{path}'))
    return records

def ordered_tokens(short,full):
    tokens=normalized(short).split();long=iter(normalized(full).split())
    return len(tokens)>=3 and all(any(x==wanted for x in long) for wanted in tokens)

def resolve_identity(row,pool,aliases=()):
    candidates=[];target=normalized(row['name'])
    for pid,observations in sorted(pool.items()):
        observations=sorted(observations,key=lambda e:(e.get('date',''),e.get('match_id',0),e.get('json_path','')))
        exact=[e for e in observations if target in {normalized(e['name']),normalized(' '.join(v for v in (e.get('first_name'),e.get('last_name')) if v))}]
        bridges=[a for a in aliases if (target==normalized(a['full_name']) or ordered_tokens(row['name'],a['full_name'])) and a['country_code']==row['country_code'] and any(normalized(e['name']) in {normalized(n) for n in a['aliases']} for e in observations)]
        # Bare name resemblance is a review candidate only, never automatic confirmation.
        weak=[e for e in observations if len(normalized(e['name']).split())==1 and target.startswith(normalized(e['name'])+' ')]
        if not exact and not bridges and not weak:continue
        evidence=exact or [e for e in observations if any(normalized(e['name']) in {normalized(n) for n in a['aliases']} for a in bridges)] or weak
        nations={e.get('country_code') for e in evidence if e.get('country_code')};nation_ok=bool(nations) and nations=={row['country_code']}
        birthdays={e['date_of_birth'][:10] for e in evidence if e.get('date_of_birth')};dob_conflict=bool(row.get('date_of_birth') and birthdays and birthdays!={row['date_of_birth'][:10]})
        ages_checked=0;age_conflict=False
        for a in bridges:
            if row.get('date_of_birth') and a.get('date_of_birth') and row['date_of_birth'][:10]!=a['date_of_birth'][:10]:dob_conflict=True
            if not a.get('date_of_birth'):continue
            born=date.fromisoformat(a['date_of_birth'][:10])
            for e in evidence:
                if e.get('age') is None:continue
                played=date.fromisoformat(e['date']);age=played.year-born.year-((played.month,played.day)<(born.month,born.day));ages_checked+=1
                if int(e['age'])!=age:age_conflict=True
        reliable_alias=bool(bridges and ages_checked and not age_conflict and all(e.get('opta_id') for e in evidence))
        conflicts=[]
        if not nation_ok:conflicts.append('NATIONALITY_MISSING_OR_CONFLICT')
        if dob_conflict:conflicts.append('DATE_OF_BIRTH_CONFLICT')
        if age_conflict:conflicts.append('INDEPENDENT_DOB_VS_FOTMOB_AGE_CONFLICT')
        strong=nation_ok and not conflicts and bool(exact or reliable_alias)
        candidates.append(dict(fotmob_player_id=int(pid),display_name=evidence[-1]['name'],rule='EXACT_FULL_NAME_NATIONALITY_CLUB_MATCH' if exact else ('DOCUMENTED_ALIAS_STABLE_IDS_NATIONALITY_AGE' if reliable_alias else 'WEAK_NAME_SIMILARITY'),strong=strong,conflicts=conflicts,raw_match_evidence=evidence,independent_alias_evidence=bridges,age_checks=ages_checked))
    if len(candidates)>1:status,reason='UNRESOLVED','MULTIPLE_PLAUSIBLE_IDS'
    elif not candidates:status,reason='UNRESOLVED','NO_RELIABLE_FOTMOB_IDENTITY'
    elif not candidates[0]['strong']:status,reason='UNRESOLVED',(';'.join(candidates[0]['conflicts']) or 'WEAK_NAME_SIMILARITY_ONLY')
    else:status,reason='CONFIRMED_AUTOMATIC',candidates[0]['rule']
    return dict(status=status,reason=reason,player_id=candidates[0]['fotmob_player_id'] if status=='CONFIRMED_AUTOMATIC' else None,name_fotmob=candidates[0]['display_name'] if status=='CONFIRMED_AUTOMATIC' else None,evidence=dict(policy='roster_identity_v1',official_attributes=row,candidates=candidates,position_used_as_constraint=False,current_appearance_required=False,shirt_number_used_as_identifier=False,scope='Operational identity confirmation; not independent matchday membership certification'))

def standardize(rows,resolutions,snapshot,source,season,half,manifest):
    out=[]
    for row,result in zip(rows,resolutions):
        out.append(dict(club_id=snapshot['club_id'],season=season,half_season=half,snapshot_id=snapshot['snapshot_id'],official_member_id=row['official_member_id'],fotmob_player_id=result['player_id'],player_name_official=row['name'],player_name_fotmob=result['name_fotmob'],shirt_number=row['shirt_number'],position_official=row['position'],nationality_official=row['nationality'],source_id=source['source_id'],source_url=source['url'],captured_at=snapshot['captured_at'],effective_from=snapshot['effective_from'],effective_to=snapshot['effective_to'],identity_status=result['status'],identity_evidence=json.dumps(dict(reason=result['reason'],**result['evidence']),ensure_ascii=False,sort_keys=True),membership_source=manifest.get('membership_source','official_baseline_user_supplied_transcription'),season_assignment=manifest.get('season_assignment_basis','USER_ASSIGNED_ARCHIVE_PERIOD'),source_season_label=manifest.get('season_displayed'),official_source_player_id=row.get('official_source_player_id')))
    return out

def commit_roster(db,rows):
    """Atomic commit: mapping, immutable decisions, original-member projection and standard table."""
    if db.in_transaction:raise RuntimeError('Commit requires clean transaction boundary')
    with db:
        for row in rows:
            sid=row['snapshot_id'];member=row['official_member_id'];pid=row['fotmob_player_id'];evidence=row['identity_evidence']
            original=db.execute('SELECT * FROM official_membership WHERE snapshot_id=? AND official_player_id=?',(sid,member)).fetchone()
            if not original:raise sqlite3.IntegrityError('Missing original official membership')
            if original['fotmob_player_id'] is not None and original['fotmob_player_id']!=pid:raise sqlite3.IntegrityError('Refusing replacement/downgrade of confirmed mapping')
            if original['source_name']!=row['player_name_official'] or original['shirt_number']!=row['shirt_number']:raise sqlite3.IntegrityError('Official source attributes changed')
            if pid is not None:
                if not db.execute('SELECT 1 FROM players WHERE player_id=?',(pid,)).fetchone():
                    from .identity_v2 import verify_new_canonical_player
                    if not verify_new_canonical_player(row):raise sqlite3.IntegrityError('Canonical player missing or new-player evidence insufficient')
                    db.execute('INSERT INTO players(player_id,display_name,identity_status) VALUES(?,?,?)',(pid,row['player_name_fotmob'],'operational_identity_from_verified_cached_records'))
                conflicts=db.execute("SELECT fotmob_player_id FROM provider_identities WHERE source_id=? AND official_player_id=? AND season=? AND review_status='VERIFIED'",(row['source_id'],member,row['season'])).fetchall()
                if any(c[0]!=pid for c in conflicts):raise sqlite3.IntegrityError('Conflicting prior verified identity; manual review required')
                key=digest(f"{row['source_id']}:{member}:{pid}:{row['season']}:{row['club_id']}".encode())
                existing=db.execute('SELECT * FROM provider_identities WHERE mapping_id=?',(key,)).fetchone()
                if existing and existing['fotmob_player_id']!=pid:raise sqlite3.IntegrityError('Existing mapping mismatch')
                aid=db.execute('SELECT artifact_id FROM official_snapshots WHERE snapshot_id=?',(sid,)).fetchone()[0]
                if not existing:db.execute('INSERT INTO provider_identities VALUES(?,?,?,?,?,?,?,?,?,?,?)',(key,row['source_id'],member,pid,row['player_name_official'],None,row['club_id'],row['season'],('MANUAL_USER_AUTHORIZED' if row['identity_status']=='CONFIRMED_MANUAL' else 'AUTO_MULTI_ATTRIBUTE'),aid,'VERIFIED'))
                elif existing['review_status']!='VERIFIED':db.execute("UPDATE provider_identities SET review_status='VERIFIED',confidence='AUTO_MULTI_ATTRIBUTE' WHERE mapping_id=?",(key,))
                db.execute('UPDATE official_membership SET fotmob_player_id=? WHERE snapshot_id=? AND official_player_id=?',(pid,sid,member))
            decision=digest(json.dumps([sid,member,pid,row['identity_status'],evidence],ensure_ascii=False).encode())
            db.execute('INSERT OR IGNORE INTO identity_decisions VALUES(?,?,?,?,?,?,?)',(decision,sid,member,pid,row['identity_status'],evidence,now()))
            cols=list(row);values=tuple(row[k] for k in cols)
            old=db.execute('SELECT * FROM standardized_official_roster WHERE snapshot_id=? AND official_member_id=?',(sid,member)).fetchone()
            if old:
                if old['fotmob_player_id'] is not None and old['fotmob_player_id']!=pid:raise sqlite3.IntegrityError('Standard roster identity replacement refused')
                db.execute('UPDATE standardized_official_roster SET '+','.join(k+'=?' for k in cols)+' WHERE snapshot_id=? AND official_member_id=?',(*values,sid,member))
            else:db.execute('INSERT INTO standardized_official_roster('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',values)
        if rows:
            sid=rows[0]['snapshot_id'];unresolved=db.execute('SELECT COUNT(*) FROM standardized_official_roster WHERE snapshot_id=? AND fotmob_player_id IS NULL',(sid,)).fetchone()[0]
            db.execute('UPDATE official_snapshots SET parse_status=? WHERE snapshot_id=?',('PARTIALLY_RESOLVED' if unresolved else 'VERIFIED',sid))
        if list(db.execute('PRAGMA foreign_key_check')):raise sqlite3.IntegrityError('Referential integrity check failed')


def reconcile(db,snapshot,period,coverage=None):
    sid=snapshot['snapshot_id'];club=snapshot['club_id']
    official={r['fotmob_player_id']:dict(r) for r in db.execute('SELECT * FROM standardized_official_roster WHERE snapshot_id=? AND fotmob_player_id IS NOT NULL',(sid,))}
    unresolved=[dict(r) for r in db.execute('SELECT * FROM standardized_official_roster WHERE snapshot_id=? AND fotmob_player_id IS NULL',(sid,))]
    observed=default_observed={}
    for r in db.execute('SELECT o.player_id,o.source_name,o.match_id,m.local_date,o.json_path,o.artifact_id FROM squad_observations o JOIN matches m USING(match_id) WHERE o.club_id=? AND m.period_id=?',(club,period)):
        observed.setdefault(r['player_id'],[]).append(dict(r))
    O=set(official);M=set(observed);valid_matches=len({r['match_id'] for rs in observed.values() for r in rs});known=int(coverage['expected_known_matches']) if coverage else None
    incomplete=known is None or valid_matches<known or not coverage or str(coverage.get('all_official_competitions_complete')).lower()!='true'
    timing_uncertain=not(snapshot['captured_at'] and snapshot['effective_from'] and snapshot['effective_to'])
    combined=[]
    for pid in sorted(O|M):
        provenance='official_and_matchday' if pid in O and pid in M else ('official_only' if pid in O else 'matchday_only')
        match_ids=sorted({e['match_id'] for e in observed.get(pid,[])})
        name=official[pid]['player_name_official'] if pid in O else observed[pid][0]['source_name']
        combined.append(dict(snapshot_id=sid,fotmob_player_id=pid,player_name=name,club_id=club,period_id=period,membership_source=provenance,official_member_id=official[pid]['official_member_id'] if pid in O else None,match_ids=json.dumps(match_ids),comparison_status='PROVISIONAL' if incomplete or timing_uncertain or unresolved else 'KNOWN_COVERAGE_ONLY',timing_uncertain=timing_uncertain,discrepancy_interpretation='No nonparticipation inference' if provenance=='official_only' else ('Possible youth/temporary inclusion, snapshot timing, omission or mapping/parsing issue; not invalid' if provenance=='matchday_only' else 'Observed in both sources')))
    with db:
        # A per-snapshot current projection; immutable raw artifacts and identity decisions remain untouched.
        db.execute('DELETE FROM reconciled_roster WHERE snapshot_id=?',(sid,))
        for r in combined:db.execute('INSERT INTO reconciled_roster VALUES(?,?,?,?,?,?,?,?,?)',tuple(r[k] for k in ['snapshot_id','fotmob_player_id','club_id','period_id','membership_source','official_member_id','match_ids','comparison_status','timing_uncertain']))
    counts=dict(official_records=len(official)+len(unresolved),confirmed_official=len(O),unresolved_official=len(unresolved),known_matches=known,valid_lineup_matches=valid_matches,missing_lineup_matches=max(0,known-valid_matches) if known is not None else None,unique_matchday_players=len(M),official_only=len(O-M),matchday_only=len(M-O),official_and_matchday=len(O&M),combined_unique_fotmob_ids=len(O|M),coverage_provisional=incomplete,comparison_provisional=incomplete or timing_uncertain or bool(unresolved),timing_uncertain=timing_uncertain,source_season_independently_verified=False)
    return combined,counts,unresolved

def coverage_for(club,period):
    path=BASE/'reports/coverage.csv'
    if not path.exists():return None
    with path.open(encoding='utf-8-sig',newline='') as f:return next((r for r in csv.DictReader(f) if int(r['club_id'])==club and r['period']==period),None)

def output_reports(out,roster,resolutions,combined,counts,job,provenance):
    club=provenance.get('club_key','unknown');season=roster[0]['season'];half=roster[0]['half_season'];policy=resolutions[0]['evidence']['policy']
    table(out/'standardized_roster.csv',roster);write(out/'standardized_roster.json',roster)
    table(out/'combined_roster.csv',combined,['snapshot_id','fotmob_player_id','player_name','club_id','period_id','membership_source','official_member_id','match_ids','comparison_status','timing_uncertain','discrepancy_interpretation','possible_unresolved_official_member_ids'])
    review=[dict(official_member_id=r['official_member_id'],official_name=r['player_name_official'],candidate_ids=';'.join(str(c['fotmob_player_id']) for c in x['evidence']['candidates']),reason=x['reason'],missing_evidence='A documented alias/full identity bridge or stable profile record; candidate expansion alone is not authoritative',evidence=r['identity_evidence']) for r,x in zip(roster,resolutions) if r['fotmob_player_id'] is None]
    table(out/'identity_review.csv',review,['official_member_id','official_name','candidate_ids','reason','missing_evidence','evidence'])
    label=roster[0]['source_season_label'];time_note=(f'来源明确显示赛季标签{label}；H1仍为用户指定归档期。' if label else '来源没有已验证的明确赛季标签；赛季及H1为用户指定归档期。')+f"captured_at={roster[0]['captured_at'] or 'NULL'}，effective_from/to均未知，不能推断快照覆盖整个H1。"
    source_description='重用已登记官方原始HTML，验证SHA256后解析专用球员数组；未重新获取网页。' if provenance.get('original_official_html') else '保存的是用户提供转录，不是原始官网HTML。'
    identity=['# '+club+' 身份确认报告','',f'规则版本：{policy}。按证据自动确认操作身份，不把原始比赛结构通过冒充独立身份认证。位置不作硬约束，本季出场不是必要条件，球衣号不作身份键。','', '| 来源姓名 | FotMob ID | 状态/依据 |','|---|---:|---|---|']
    for r,x in zip(roster,resolutions):identity.append(f"| {r['player_name_official']} | {r['fotmob_player_id'] or '待核验'} | {r['identity_status']} / {x['reason']} |")
    identity+=['',f"身份确认{counts['confirmed_official']}（人工{sum(r['identity_status']=='CONFIRMED_MANUAL' for r in roster)}），未解决{counts['unresolved_official']}。全部官方条目保留；未解决ID保持NULL，候选ID和确切缺口见identity_review.csv。",'', '逐人证据见standardized_roster.json的identity_evidence：原始匹配记录SHA256/JSON路径、双方球队和ID校验、名称、国籍、稳定FotMob/Opta ID及可用缓存资料；独立UEFA昵称/全名、稳定来源ID与国籍只供身份，不补入基线成员。源未提供的国籍/生日仍为NULL，不从匹配证据偷偷补成官方属性。','', '对资源标签：candidate_full_name只用于查找候选。唯一姓氏或展开名本身不足以确认；优先要求实际缓存中的明确别名桥、完整名称、多字段一致及稳定身份记录。Rodri、Bisiwu、Hamza不靠号码或名称猜测补全。','',time_note]
    (out/'IDENTITY_RESOLUTION.md').write_text('\n'.join(identity)+'\n')
    rec=['# '+club+' 名单对账','', f"已知比赛{counts['known_matches']}，有效阵容{counts['valid_lineup_matches']}，缺失{counts['missing_lineup_matches']}；观察球员{counts['unique_matchday_players']}。覆盖时点沿用已有缓存配置，不宣称已抓取到执行当天。",f"官方确认{counts['confirmed_official']}，未解决{counts['unresolved_official']}，官方独有{counts['official_only']}，比赛独有{counts['matchday_only']}，交集{counts['official_and_matchday']}，合并唯一FotMob身份{counts['combined_unique_fotmob_ids']}。",'',time_note,'比较保留PROVISIONAL；即使已知比赛全部通过，也不把未认证的全量赛事分母称为完整。官方独有不证明未参赛；比赛独有可能涉及青年/临时一队、快照时间、遗漏或身份问题，不自动认定无效。','', '| 姓名 | ID | 来源 | 比赛ID |','|---|---:|---|---|']
    for r in combined:
        if r['membership_source']!='official_and_matchday':rec.append(f"| {r['player_name']} | {r['fotmob_player_id']} | {r['membership_source']} | {r['match_ids']} |")
    rec+=['','未解决的官方成员另见标准名单与identity_review.csv，不与比赛记录猜测去重。possible_unresolved_official_member_ids标明比赛独有ID可能对应某个未解决官方条目，因此matchday_only不代表该人真的没在转录中。补充差异调查见matchday_only_investigation.csv与combined_member_evidence.json。']
    (out/'ROSTER_RECONCILIATION.md').write_text('\n'.join(rec)+'\n')
    write(out/'import_result.json',dict(status='COMPLETE_WITH_PROVISIONAL_RECONCILIATION',job_id=job,counts=counts,source_provenance=provenance,source_season_label=label,database_commit='COMMITTED',network_requests=0))
    report=f"""# {club} 官方名单端到端导入

命令：`python3 -B -m squad_pipeline roster-import --club {club} --season {season.replace('/','-')} --half {half}`。

已执行：来源登记→来源专用转录解析→共享身份匹配与验证→标准化→事务提交→比赛名单对账→质控报告。{source_description}本轮无官网或FotMob网络请求。

解析{len(roster)}名，确认{counts['confirmed_official']}，未解决{counts['unresolved_official']}；官方独有{counts['official_only']}、比赛独有{counts['matchday_only']}、双方均有{counts['official_and_matchday']}、并集{counts['combined_unique_fotmob_ids']}。有效阵容{counts['valid_lineup_matches']}/已知{counts['known_matches']}，缺失{counts['missing_lineup_matches']}。

{time_note}

官方姓名、原始位置、号码和资源标签保留；Ferran不改写为Ferran Torres。DS和00不是号码，NULL不根据其他名单补造。巴萨raw_asset_label、parsed_name、candidate_full_name分别保存；缺失国籍保持NULL。原始SHA256见source_artifact_sha256和provenance。

数据库事务已提交：official_membership保留全部成员，standardized_official_roster保存标准字段，official_source_attributes保存资源标签等附加属性，provider_identities/identity_decisions保存确认映射和审查证据，current_import_roster可查询并集。未解决成员保留NULL；冲突已确认映射拒绝静默替换；失败回滚。重复执行业务结果应一致，验证见verification.json与联合任务验收。

人工事项：{('无需额外身份核验。' if not counts['unresolved_official'] else '只需处理identity_review.csv列出的未解决身份及具体证据缺口；不要求逐一批准已确认项。')} 复制时间/生效日期如能补充可追加佐证，未知继续NULL。比赛覆盖缺口不阻止名单导入。

本轮原Futbol比赛、分钟、个人库和图表保持只读，亚特兰大25确认和30并集保持。六队验收与限制见reports/SIX_CLUB_ROSTER_STATUS.md及reports/OPERATIONAL_READINESS.md。
"""
    (out/'IMPORT_REPORT.md').write_text(report)

def run_import(db,club_key,season,half,settings=None,out=None,register_inputs=True):
    season=season_key(season);cfg=config();club=next((c for c in cfg['clubs'] if c['key']==club_key),None)
    if not club:raise ImportBlocked('Unknown configured club')
    period=season.replace('/','-')+'_'+half
    if not db.execute('SELECT 1 FROM periods WHERE period_id=?',(period,)).fetchone():raise ImportBlocked('Unknown configured half-season')
    settings=settings or read(BASE/'config/roster_import.json');out=within(out or BASE/'imports'/club_key/season.replace('/','-')/half);out.mkdir(parents=True,exist_ok=True);import_schema(db)
    sources=read(BASE/'config/official_sources.json');source=next((s for s in sources if s['club_key']==club_key and s['preferred']),None)
    registration_problem=None
    if source:
        db.execute('INSERT OR IGNORE INTO acquisition_sources VALUES(?,?,?,?,?)',(source['source_id'],club['id'],source['organization'] if 'organization' in source else club['name'],source['url'],json.dumps(source,ensure_ascii=False)));db.commit()
        adapter=settings['adapters'].get(club_key)
        folder=within(BASE/(adapter['manual_folder'] if adapter else f'manual_inputs/{club_key}/{season.replace("/","-")}/{half}'))
        if register_inputs and not (adapter or {}).get('use_acquired_html') and (folder/'source.json').exists():
            try:
                from .manual import register
                registered=register(db,folder,sources)
                denied=[x for x in registered if x.get('status') not in ('ACQUIRED',)]
                if denied:registration_problem='Manual source registration blocked: '+json.dumps(denied,ensure_ascii=False)
            except (ValueError,KeyError,OSError,sqlite3.IntegrityError) as e:db.rollback();registration_problem=str(e)
    snapshot=db.execute('SELECT * FROM official_snapshots WHERE club_id=? AND period_id=? AND source_id=? ORDER BY captured_at DESC LIMIT 1',(club['id'],period,source['source_id'] if source else '')).fetchone()
    job=digest(f"{club['id']}:{period}:{snapshot['snapshot_id'] if snapshot else 'pending'}".encode());stage='SOURCE_REGISTRATION'
    db.execute('INSERT INTO roster_import_jobs VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,reason=NULL,updated_at=excluded.updated_at',(job,club['id'],period,snapshot['snapshot_id'] if snapshot else None,'RUNNING',None,None,now()));db.commit()
    try:
        if not source:raise ImportBlocked('Official source not configured')
        if registration_problem:raise ImportBlocked(registration_problem)
        if not snapshot:raise ImportBlocked('No registered manual input or acquired snapshot; no network acquisition attempted')
        checkpoint(db,job,stage,dict(snapshot_id=snapshot['snapshot_id'],artifact_id=snapshot['artifact_id']))
        adapter=settings['adapters'].get(club_key)
        if not adapter or adapter['parser'] not in PARSERS or adapter.get('period')!=period:raise ImportBlocked('SOURCE_PARSER_NOT_CONFIGURED_FOR_THIS_CLUB_PERIOD; source registration retained')
        stage='PARSING';folder=within(BASE/adapter['manual_folder']);rows,snapshot,provenance=PARSERS[adapter['parser']](db,adapter,folder,out)
        if snapshot['club_id']!=club['id'] or snapshot['period_id']!=period or snapshot['source_id']!=source['source_id']:raise ImportBlocked('Parsed snapshot scope mismatch')
        provenance['club_key']=club_key
        write(out/'parsed_stage.json',dict(rows=rows,snapshot=snapshot,provenance=provenance));checkpoint(db,job,stage,provenance)
        stage='IDENTITY_VALIDATION';pool=raw_identity_pool(db,club['id'])
        if adapter.get('identity_policy')=='roster_identity_v2':
            from .identity_v2 import cached_authorities,attach_profiles,matching_identity
            pool=attach_profiles(pool);aliases=cached_authorities(settings.get('independent_identity_caches',[]),club_key);resolutions=[matching_identity(r,pool,aliases) for r in rows]
            missing=[r for r,x in zip(rows,resolutions) if not x['evidence']['candidates']]
            if missing:
                from .identity_v2 import local_other_team_evidence
                additional=local_other_team_evidence(missing)
                for pid,observations in additional.items():pool.setdefault(pid,[]).extend(observations)
                resolutions=[matching_identity(r,pool,aliases) for r in rows]
                write(out/'targeted_local_identity_lookup.json',dict(source='Existing local match cache only',positive_fotmob_ids=sorted(additional),rejected_zero_ids=True,new_match_memberships_created=False))
        else:
            aliases=independent_aliases(settings.get('independent_identity_caches',[]),club_key);resolutions=[resolve_identity(r,pool,aliases) for r in rows]
        from .identity_reverse import apply_source_bridges,validated_reverse_pool,resolve_reverse_bridges
        if club_key in ('psg','bayern','barcelona'):
            reverse=validated_reverse_pool()
            extended=dict(pool)
            for pid,obs in reverse.items():extended[pid]=obs
            resolutions=[resolve_reverse_bridges(r,matching_identity(r,extended,aliases),extended,aliases) if x['status']!='CONFIRMED_AUTOMATIC' else x for r,x in zip(rows,resolutions)]
        resolutions=apply_source_bridges(rows,resolutions,pool,aliases,provenance)
        from .manual_identity import apply_manual
        resolutions=apply_manual(rows,resolutions,snapshot)
        write(out/'identity_stage.json',resolutions);checkpoint(db,job,stage,resolutions)
        manifest=provenance.get('source_manifest') or read(folder/'source.json');roster=standardize(rows,resolutions,snapshot,source,season,half,manifest);write(out/'standardized_stage.json',roster)
        stage='DATABASE_COMMIT';commit_roster(db,roster);checkpoint(db,job,stage,roster)
        stage='RECONCILIATION';combined,counts,unresolved=reconcile(db,snapshot,period,coverage_for(club['id'],period))
        if adapter.get('identity_policy')=='roster_identity_v2':
            counts['source_season_label']=manifest.get('season_displayed');counts['source_season_label_present']=bool(manifest.get('season_displayed'))
            for member in combined:
                if member['membership_source']=='matchday_only':
                    possible=[native['official_member_id'] for native,res in zip(rows,resolutions) if res['status']=='UNRESOLVED' and any(c['fotmob_player_id']==member['fotmob_player_id'] for c in res['evidence']['candidates'])]
                    member['possible_unresolved_official_member_ids']=';'.join(possible)
                    if possible:member['discrepancy_interpretation']='May correspond to an unresolved official member; not confirmed absent from supplied roster. Timing and identity review pending.'
        checkpoint(db,job,stage,counts)
        evidence={str(r['fotmob_player_id']):pool.get(r['fotmob_player_id'],[]) for r in combined if r['membership_source']=='matchday_only'};write(out/'combined_member_evidence.json',evidence)
        investigation=[]
        for r in combined:
            if r['membership_source']!='matchday_only':continue
            current=[e for e in pool.get(r['fotmob_player_id'],[]) if e['period']==period];ages=[e['age'] for e in current if e.get('age') is not None]
            investigation.append(dict(fotmob_player_id=r['fotmob_player_id'],player_name=r['player_name'],match_ids=r['match_ids'],source_ages=';'.join(map(str,sorted(set(ages)))),possible_youth_or_temporary_inclusion=bool(ages and max(ages)<=23),youth_promotion_proven=False,snapshot_timing_uncertain=True,invalid_player=False,next_interpretation='Young age in original lineup supports youth/temporary-inclusion hypothesis, not proof; timing/omission/mapping/parsing alternatives remain open',evidence_json=json.dumps(current,ensure_ascii=False)))
        table(out/'matchday_only_investigation.csv',investigation,['fotmob_player_id','player_name','match_ids','source_ages','possible_youth_or_temporary_inclusion','youth_promotion_proven','snapshot_timing_uncertain','invalid_player','next_interpretation','evidence_json'])
        if adapter.get('identity_policy')=='roster_identity_v2':
            roster=[dict(r,raw_asset_label=n.get('raw_asset_label'),parsed_name=n.get('parsed_name'),candidate_full_name=n.get('candidate_full_name'),official_profile_url=n.get('official_profile_url'),official_opta_id=n.get('official_opta_id'),source_json_path=n.get('source_json_path'),source_artifact_sha256=provenance['raw_sha256']) for r,n in zip(roster,rows)]
        output_reports(out,roster,resolutions,combined,counts,job,provenance)
        previous=out/'blocked_result.json'
        if previous.exists():
            archive=out/'previous_failures';archive.mkdir(exist_ok=True);previous.replace(archive/(digest(previous.read_bytes())+'.json'))
        checkpoint(db,job,'COMPLETE',counts);db.execute("UPDATE roster_import_jobs SET status='COMPLETE_WITH_PROVISIONAL_RECONCILIATION' WHERE job_id=?",(job,));db.commit()
        return dict(status='COMPLETE_WITH_PROVISIONAL_RECONCILIATION',job_id=job,counts=counts,output=str(out),commit='COMMITTED')
    except (ImportBlocked,ValueError,KeyError,OSError,sqlite3.IntegrityError) as exc:
        db.rollback();db.execute('UPDATE roster_import_jobs SET status=?,reason=?,updated_at=? WHERE job_id=?',('BLOCKED',str(exc),now(),job));db.commit()
        result=dict(status='BLOCKED',job_id=job,blocked_stage=stage,reason=str(exc),preceding_successful_stages_preserved=True);write(out/'blocked_result.json',result);(out/'IMPORT_REPORT.md').write_text('# 导入受阻\n\n'+json.dumps(result,ensure_ascii=False,indent=2)+'\n');return result
