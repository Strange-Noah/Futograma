"""Read-only evidence inventory for legacy acceptance, never fabricate new certificates.

Legacy fact retention is not equivalent to source availability or independent
R1/R2/R5 coverage. Inventory exposes those preconditions before a real audit.
"""
import json
from pathlib import Path
from collections import Counter
from .archive import Archive


def inventory(db, archive_root):
    archive = Archive(archive_root)
    clubs = [dict(r) for r in db.execute('SELECT * FROM clubs ORDER BY club_id')]
    output = []
    for c in clubs:
        cid=c['club_id']
        sheets=list(db.execute('SELECT * FROM validation_match_sheets WHERE club_id=?',(cid,)))
        available=Counter()
        paths=[]
        for sheet in sheets:
            p=json.loads(sheet['payload_json']);source=p.get('source') or {}
            path=source.get('path');sha=source.get('sha256')
            if not path or not sha:
                available['MISSING_LOCATOR_OR_HASH']+=1
            elif archive.body(path,sha) is None:
                available['RAW_BODY_UNAVAILABLE']+=1;paths.append(path)
            else:
                available['HASH_VERIFIED_BODY_AVAILABLE']+=1
        membership=Counter()
        for row in db.execute('SELECT * FROM club_management_events WHERE club_id=?',(cid,)):
            for source in json.loads(row['source_json'] or '[]'):
                path=source.get('path');sha=source.get('sha256') or source.get('body_sha256')
                if not path or not sha:membership['MISSING_LOCATOR_OR_HASH']+=1
                elif archive.body(path,sha) is None:membership['RAW_BODY_UNAVAILABLE']+=1
                else:membership['HASH_VERIFIED_BODY_AVAILABLE']+=1
        output.append(dict(club_id=cid,club_name=c['name'],squad_sheets=len(sheets),
            squad_body_coverage=dict(available),membership_evidence_coverage=dict(membership),
            accepted_player_records=db.execute('SELECT count(*) FROM player_match_minutes WHERE club_id=?',(cid,)).fetchone()[0],
            missing_paths=sorted(set(paths)),
            status='SOURCE_REVALIDATION_REQUIRED;NO_NEW_CERTIFICATES'))
    return output
