from __future__ import annotations
import csv,hashlib,json,sqlite3,os
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BASE=Path(__file__).resolve().parent
UTC=timezone.utc

def now(): return datetime.now(UTC).isoformat()
def digest(raw): return hashlib.sha256(raw).hexdigest()
def read(path): return json.loads(Path(path).read_text())
def within(path,root=BASE):
    p=Path(path).resolve()
    if not p.is_relative_to(root.resolve()): raise ValueError('Path outside isolated pipeline: '+str(p))
    return p

def write(path,value):
    path=within(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False));temp.replace(path)
def table(path,rows,fields=None):
    path=within(path);path.parent.mkdir(parents=True,exist_ok=True)
    fields=fields or list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def config(): return read(BASE/'config/pipeline.json')
def connect(path=None):
    path=within(path or BASE/'state/pipeline.sqlite');path.parent.mkdir(exist_ok=True,parents=True)
    c=sqlite3.connect(path);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
    c.executescript((BASE/'schema.sql').read_text());return c

def artifact(db,path,kind,source_id=None,acquired_at=None,effective=None,external=False):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT): raise ValueError('Evidence outside Futbol')
    raw=path.read_bytes();sha=digest(raw);aid=digest((str(path.relative_to(ROOT))+':'+sha).encode())
    db.execute('INSERT OR IGNORE INTO raw_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(aid,source_id,str(path.relative_to(ROOT)),sha,len(raw),kind,acquired_at,now(),effective))
    return aid

def seed(db,cfg):
    for c in cfg['clubs']:db.execute('INSERT OR IGNORE INTO clubs VALUES(?,?,?,?)',(c['id'],c['key'],c['name'],c['country']))
    for p in cfg['periods']:
        value=(p['id'],p['season'],p['half'],p['start'],p['end_exclusive'],cfg['timezone'])
        old=db.execute('SELECT * FROM periods WHERE period_id=?',(p['id'],)).fetchone()
        if old and tuple(old)!=value:raise ValueError('Period configuration changed; explicit migration required')
        db.execute('INSERT OR IGNORE INTO periods VALUES(?,?,?,?,?,?)',value)
    db.commit()
