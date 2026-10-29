"""Parsers for the three actual user-supplied excerpts. Never scrape club sites."""
import json,re
from collections import Counter
from .core import *
EXPECTED={'psg':{'goalkeeper':3,'defender':8,'midfielder':7,'forward':6},'bayern':{'goalkeeper':3,'defender':8,'midfielder':8,'forward':5},'barcelona':{'goalkeeper':3,'defender':8,'midfielder':8,'forward':8}}

def parse_simple(text,club):
    if club not in ('psg','bayern'):raise ValueError('Unsupported simple excerpt')
    rows=[]
    for line_no,line in enumerate(text.splitlines(),1):
        m=re.fullmatch(r'\s*(\d+|NULL)\s*\|\s*(.+?)\s*\|\s*(goalkeeper|defender|midfielder|forward)\s*',line)
        if not m:continue
        number=None if m[1]=='NULL' else str(int(m[1]))
        if number is not None and not 1<=int(number)<=99:raise ValueError('Invalid shirt number')
        rows.append(dict(name=m[2],parsed_name=m[2],candidate_full_name=None,raw_asset_label=None,shirt_number=number,position=m[3],nationality=None,country_code=None,date_of_birth=None,official_source_player_id=None,source_line=line_no,identity_input_kind='official_player_name'))
    validate(rows,club);return rows

def parse_barcelona(text):
    rows=[]
    for line_no,line in enumerate(text.splitlines(),1):
        fields=[x.strip() for x in line.split('|')]
        if len(fields)!=5 or fields[3] not in EXPECTED['barcelona']:continue
        label,parsed,candidate,position,number=fields
        prefix=re.match(r'^(\d{2})-',label);from_asset=str(int(prefix[1])) if prefix and int(prefix[1]) else None
        declared=None if number=='NULL' else str(int(number))
        if declared!=from_asset:raise ValueError('Supplied shirt number disagrees with asset-prefix convention')
        if re.search(r'Hansi[_ ]Flick',label,re.I):continue
        clean=re.sub(r'^(\d{2})-','',label);clean=re.sub(r'-(?:BLUE|RED|blau|grana)$','',clean,flags=re.I).replace('_',' ')
        if clean.casefold()!=parsed.casefold():raise ValueError('Parsed asset name disagrees with original label')
        rows.append(dict(name=parsed,parsed_name=parsed,candidate_full_name=candidate,raw_asset_label=label,shirt_number=declared,position=position,nationality=None,country_code=None,date_of_birth=None,official_source_player_id=None,source_line=line_no,identity_input_kind='image_asset_label_candidate_not_authoritative_full_name'))
    validate(rows,'barcelona');return rows

def validate(rows,club):
    if dict(Counter(r['position'] for r in rows))!=EXPECTED[club]:raise ValueError('Parsed roster position/total count mismatch')
    if len({r['name'] for r in rows})!=len(rows):raise ValueError('Duplicate source member names')
    numbers=[r['shirt_number'] for r in rows if r['shirt_number'] is not None]
    if len(numbers)!=len(set(numbers)):raise ValueError('Duplicate known shirts in supplied excerpt; review source')

def import_excerpt(db,adapter,folder,out,club):
    path=folder/'squad.txt';manifest=read(folder/'source.json');raw=path.read_bytes()
    if digest(raw)!=manifest['content_sha256']:raise ValueError('Original user roster transcription hash mismatch')
    rows=parse_barcelona(raw.decode('utf-8')) if club=='barcelona' else parse_simple(raw.decode('utf-8'),club)
    if club=='psg' and 'EFFECTIF 2026-27' not in raw.decode():raise ValueError('PSG explicit season label missing')
    a=db.execute('SELECT * FROM raw_artifacts WHERE source_id=? AND sha256=? AND kind=?',(manifest['source_id'],digest(raw),'manual')).fetchall()
    if len(a)!=1:raise ValueError('Expected one registered manual raw artifact')
    snapshot=db.execute('SELECT * FROM official_snapshots WHERE source_id=? AND artifact_id=? AND period_id=?',(manifest['source_id'],a[0]['artifact_id'],'2026-27_H1')).fetchone()
    if not snapshot:raise ValueError('Registered manual snapshot not found')
    sid=snapshot['snapshot_id']
    db.execute("CREATE TABLE IF NOT EXISTS official_source_attributes(snapshot_id TEXT,official_member_id TEXT,raw_asset_label TEXT,parsed_name TEXT,candidate_full_name TEXT,source_artifact_sha256 TEXT,source_line INTEGER,PRIMARY KEY(snapshot_id,official_member_id),FOREIGN KEY(snapshot_id,official_member_id) REFERENCES official_membership(snapshot_id,official_player_id))");db.commit()
    with db:
        for i,row in enumerate(rows,1):
            row['official_member_id']='local:'+sid[:16]+':'+str(i).zfill(3)
            provenance=json.dumps(dict(origin=manifest['evidence_origin'],source_artifact_sha256=digest(raw),raw_asset_label=row['raw_asset_label'],source_line=row['source_line'],captured_at=None,source_season_label=manifest['season_displayed'],shirt_number_is_identity_key=False),ensure_ascii=False)
            old=db.execute('SELECT * FROM official_membership WHERE snapshot_id=? AND official_player_id=?',(sid,row['official_member_id'])).fetchone()
            if old and (old['source_name']!=row['name'] or old['shirt_number']!=row['shirt_number']):raise ValueError('Immutable official membership changed')
            db.execute('INSERT OR IGNORE INTO official_membership VALUES(?,?,?,?,?,?,?)',(sid,row['official_member_id'],row['name'],None,row['shirt_number'],None,provenance))
            db.execute('INSERT OR IGNORE INTO official_membership_details VALUES(?,?,?,?,?,?,?,?,?)',(sid,row['official_member_id'],i,row['position'],None,None,None,row['source_line'],row['source_line']))
            values=(sid,row['official_member_id'],row['raw_asset_label'],row['parsed_name'],row['candidate_full_name'],digest(raw),row['source_line'])
            old_attrs=db.execute('SELECT * FROM official_source_attributes WHERE snapshot_id=? AND official_member_id=?',(sid,row['official_member_id'])).fetchone()
            if old_attrs and tuple(old_attrs)!=values:raise ValueError('Immutable source-specific attributes changed')
            db.execute('INSERT OR IGNORE INTO official_source_attributes VALUES(?,?,?,?,?,?,?)',values)
        db.execute("UPDATE official_snapshots SET parse_status='PARSED_PENDING_IDENTITY' WHERE snapshot_id=? AND parse_status NOT IN ('VERIFIED','PARTIALLY_RESOLVED')",(sid,))
    table(out/'parsed_official_roster.csv',rows);write(out/'parsed_official_roster.json',rows)
    return rows,dict(snapshot),dict(input_path=str(path.relative_to(BASE)),input_sha256=digest(raw),raw_artifact_id=a[0]['artifact_id'],raw_sha256=digest(raw),parsing=club+'_manual_roster',season_displayed=manifest['season_displayed'],original_official_html=False)

def psg_manual_roster(db,adapter,folder,out):return import_excerpt(db,adapter,folder,out,'psg')
def bayern_manual_roster(db,adapter,folder,out):return import_excerpt(db,adapter,folder,out,'bayern')
def barcelona_manual_roster(db,adapter,folder,out):return import_excerpt(db,adapter,folder,out,'barcelona')
