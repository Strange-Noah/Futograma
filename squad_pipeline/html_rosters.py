"""Source-specific parsers for already acquired official HTML. No acquisition here."""
import json,re
from collections import Counter
from .core import *

POSITIONS={'goalkeeper':'goalkeeper','defender':'defender','midfielder':'midfielder','forward':'forward','Goalkeepers':'goalkeeper','Defenders':'defender','Midfielders':'midfielder','Strikers':'forward'}

def payload(text):
    scripts=re.findall(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',text,re.S)
    if len(scripts)!=1:raise ValueError('Expected exactly one source application payload')
    return json.loads(scripts[0])['props']['pageProps']

def parse_city(text):
    d=payload(text)['playersData']
    if d['cityTeamInfo']['category']!='mens':raise ValueError('Not the mens first team')
    rows=[];excluded=[];seen={};cards=0
    for gi,group in enumerate(d['body']):
        for pi,p in enumerate(group.get('players') or []):
            path=f'$.props.pageProps.playersData.body[{gi}].players[{pi}]'
            if group['name'] not in POSITIONS or not p.get('isMensTeam') or p.get('isManager') or p.get('isCoach') or p.get('isEdsTeam') or p.get('isAcademyTeam'):
                excluded.append(dict(path=path,name=p.get('name'),reason='NON_FIRST_TEAM_PLAYER_CATEGORY_OR_STAFF'));continue
            cards+=1
            if p['id'] in seen:
                if p!=seen[p['id']]:raise ValueError('Conflicting duplicate City player card')
                continue
            seen[p['id']]=p
            rows.append(dict(name=p['name'],shirt_number=str(p['shirtNumber']) if p.get('shirtNumber') else None,position=p.get('position') or group['name'],nationality=p.get('nationality') or p.get('country'),country_code=None,date_of_birth=p.get('dateOfBirth'),official_source_player_id=p['id'],official_profile_url='https://www.mancity.com/players'+p['urlName'],official_opta_id=str(p['optaId']) if p.get('optaId') else None,source_json_path=path))
    return checked(rows,cards,excluded)

def parse_inter(text):
    d=payload(text);page=d['page']
    if page.get('slug')!='first-team' or page.get('youthTeam') or page.get('type')!='Men':raise ValueError('Not Inter mens first team')
    rows=[];seen={};excluded=[];cards=0
    for i,p in enumerate(d['players']):
        path=f'$.props.pageProps.players[{i}]';role=(p.get('role') or {}).get('name');team=(p.get('team') or {}).get('slug')
        if team!='first-team' or role not in POSITIONS:
            excluded.append(dict(path=path,name=p.get('lastName'),reason='NON_FIRST_TEAM_PLAYER_CATEGORY_OR_STAFF'));continue
        cards+=1;key=p['sys']['id']
        if key in seen:
            if p!=seen[key]:raise ValueError('Conflicting duplicate Inter card')
            continue
        seen[key]=p
        rows.append(dict(name=' '.join(x for x in [p.get('firstName'),p.get('lastName')] if x),shirt_number=str(p['number']) if p.get('number') else None,position=role,nationality=None,country_code=None,date_of_birth=None,official_source_player_id=key,official_profile_url='https://www.inter.it/en/teams/first-team/player/'+p['slug'],official_opta_id=None,source_json_path=path))
    # Server-rendered player cards must agree with the dedicated application player array.
    rendered=set(re.findall(r'href="/en/teams/first-team/player/([^"/?]+)"',text))
    parsed={r['official_profile_url'].rsplit('/',1)[1] for r in rows}
    if rendered!=parsed:raise ValueError('Rendered Inter player cards and application roster differ')
    return checked(rows,cards,excluded)

def checked(rows,cards,excluded):
    if not rows or len({r['official_source_player_id'] for r in rows})!=len(rows):raise ValueError('Empty or duplicate official roster')
    return rows,dict(included_player_card_occurrences=cards,unique_included_player_cards=len(rows),parsed_players=len(rows),duplicate_card_occurrences=cards-len(rows),every_unique_card_once=True,excluded_cards=excluded,position_counts=dict(Counter(r['position'] for r in rows)),expected_roster_size_invented=False)

def import_html(db,adapter,folder,out):
    s=db.execute('SELECT * FROM official_snapshots WHERE source_id=? AND period_id=?',(adapter['provider'],adapter['period'])).fetchall()
    if len(s)!=1:raise ValueError('Expected one pinned registered HTML snapshot')
    snapshot=dict(s[0]);a=dict(db.execute('SELECT * FROM raw_artifacts WHERE artifact_id=?',(snapshot['artifact_id'],)).fetchone());p=ROOT/a['path']
    if a['kind']!='official_html' and not p.suffix=='.html':raise ValueError('Not registered official HTML')
    raw=p.read_bytes()
    if digest(raw)!=a['sha256']:raise ValueError('Registered HTML hash mismatch')
    rows,qc=(parse_city if adapter['parser']=='manchester_city_html' else parse_inter)(raw.decode())
    sid=snapshot['snapshot_id']
    with db:
        for i,r in enumerate(rows,1):
            r['official_member_id']=r['official_source_player_id'];r['identity_input_kind']='official_html_player_card';r['source_line']=None;r['candidate_full_name']=None;r['raw_asset_label']=None;r['parsed_name']=r['name']
            evidence=json.dumps(dict(origin='ACQUIRED_OFFICIAL_HTML',source_artifact_sha256=a['sha256'],source_json_path=r['source_json_path'],official_profile_url=r['official_profile_url'],official_opta_id=r['official_opta_id']),ensure_ascii=False)
            old=db.execute('SELECT * FROM official_membership WHERE snapshot_id=? AND official_player_id=?',(sid,r['official_member_id'])).fetchone()
            if old and (old['source_name']!=r['name'] or old['shirt_number']!=r['shirt_number']):raise ValueError('Immutable HTML membership changed')
            db.execute('INSERT OR IGNORE INTO official_membership VALUES(?,?,?,?,?,?,?)',(sid,r['official_member_id'],r['name'],r['date_of_birth'],r['shirt_number'],None,evidence))
            db.execute('INSERT OR IGNORE INTO official_membership_details VALUES(?,?,?,?,?,?,?,?,?)',(sid,r['official_member_id'],i,r['position'],r['nationality'],r['country_code'],r['official_source_player_id'],None,None))
    db.execute("UPDATE official_snapshots SET content_review_status='ALL_INCLUDED_FIRST_TEAM_PLAYER_CARDS_PARSED_ONCE' WHERE snapshot_id=?",(sid,));db.commit()
    table(out/'parsed_official_roster.csv',rows);write(out/'parsed_official_roster.json',rows);write(out/'parser_validation.json',qc)
    manifest=dict(season_displayed=None,season_assignment_basis='USER_ASSIGNED_ARCHIVE_PERIOD_NOT_HTML_SEASON_LABEL',membership_source='official_baseline_acquired_html')
    return rows,snapshot,dict(input_path=str(p.relative_to(BASE)),input_sha256=a['sha256'],raw_artifact_id=a['artifact_id'],raw_sha256=a['sha256'],parsing=adapter['parser'],original_official_html=True,source_manifest=manifest,parser_validation=qc,captured_at=snapshot['captured_at'])
