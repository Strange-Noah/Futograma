from __future__ import annotations
import json,shutil
from .core import *
from .acquisition import detect_type
EXTENSIONS={'.html','.htm','.pdf','.csv','.json','.txt'}

def prepare(sources):
    for s in sources:
        if not s.get('preferred'):continue
        for half in ('H1','H2'):
            folder=within(BASE/'manual_inputs'/s['club_key']/s['season'].replace('/','-')/half);folder.mkdir(parents=True,exist_ok=True)
            manifest=folder/'source.json'
            if not manifest.exists():write(manifest,dict(source_id=s['source_id'],source_url=s['url'],season=s['season'],half=half,captured_at=None,season_displayed=None,effective_from=None,effective_to=None,permission_evidence=None,instructions='Place acquired HTML/PDF/CSV/JSON here. Complete known metadata. Do not obtain restricted files without permission. No parser runs at this stage.'))

def register(db,folder,sources):
    folder=within(folder);m=read(folder/'source.json');s=next((x for x in sources if x['source_id']==m['source_id']),None)
    if not s:raise ValueError('Unknown source_id')
    period=m['season'].replace('/','-')+'_'+m['half']
    expected=within(BASE/'manual_inputs'/s['club_key']/m['season'].replace('/','-')/m['half'])
    if folder!=expected or m['source_url']!=s['url']:raise ValueError('Folder/source/club/period mismatch')
    if not db.execute('SELECT 1 FROM periods WHERE period_id=?',(period,)).fetchone():raise ValueError('Unconfigured period')
    if m['captured_at'] is not None:
        d=datetime.fromisoformat(m['captured_at'].replace('Z','+00:00'))
        if d.tzinfo is None:raise ValueError('captured_at requires explicit timezone')
    evidence=m.get('permission_evidence')
    # Register authenticated user-supplied content offline; this grants no website access or reuse licence.
    supplied=False
    if m.get('acquisition_method')=='user_supplied_attachment_offline' and m.get('evidence_origin')=='USER_SUPPLIED_ROSTER_TRANSCRIPTION_NOT_ORIGINAL_WEB_BYTES':
        proof=(ROOT/m.get('user_request_path','')).resolve()
        supplied=proof.is_relative_to(BASE) and proof.is_file() and digest(proof.read_bytes())==m.get('user_request_sha256')
        if not supplied:raise ValueError('User-supplied request artifact missing or hash mismatch')
        content=folder/'squad.txt'
        if not content.is_file() or digest(content.read_bytes())!=m.get('content_sha256'):raise ValueError('Immutable user transcription missing or hash mismatch')
    if s['policy']=='RESTRICTED' and not supplied and not (evidence and within(BASE/evidence).is_file()):return [dict(source_id=s['source_id'],status='TERMS_RESTRICTED',reason='Manual files do not bypass source restrictions; attach actual owner permission/permitted export evidence')]
    out=[]
    for src in sorted(folder.iterdir()):
        if src.name=='source.json' or src.suffix.lower() not in EXTENSIONS:continue
        src=within(src);raw=src.read_bytes()
        if not raw:out.append(dict(path=str(src),status='CONTENT_INCOMPLETE'));continue
        mime,ext=detect_type(raw,filename=src.name);sha=digest(raw)
        dest=within(BASE/'raw/manual'/s['source_id']/sha/('response'+ext));dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():dest.write_bytes(raw)
        aid=artifact(db,dest,'manual',s['source_id'],m['captured_at'],period);sid=digest((s['source_id']+period+aid).encode())
        db.execute('INSERT OR IGNORE INTO official_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)',(sid,s['source_id'],s['club_id'],period,aid,m['captured_at'],m.get('effective_from'),m.get('effective_to'),'ACQUIRED','AWAITING_PROVIDER_PARSER','MANUAL_FILE_REGISTERED_ROSTER_UNVERIFIED'))
        meta=dest.parent/('metadata_'+digest(json.dumps(m,sort_keys=True).encode())+'.json')
        if not meta.exists():write(meta,dict(manifest=m,original_path=str(src.relative_to(BASE)),sha256=sha,content_type=mime,registered_at=now(),raw_acquisition_complete=True,roster_parsed=False))
        actual_parse=db.execute('SELECT parse_status FROM official_snapshots WHERE snapshot_id=?',(sid,)).fetchone()[0]
        out.append(dict(source_id=s['source_id'],status='ACQUIRED',snapshot_id=sid,artifact_id=aid,parse_status=actual_parse))
    db.commit();return out

def scan(db,sources):
    results=[]
    for manifest in sorted((BASE/'manual_inputs').glob('*/*/*/source.json')):
        try:results.extend(register(db,manifest.parent,sources))
        except (ValueError,KeyError,OSError) as e:results.append(dict(path=str(manifest),status='CONTENT_INCOMPLETE',reason=str(e)))
    write(BASE/'reports/manual_ingestion.json',results);return results
