from __future__ import annotations
import csv,json,hashlib
from datetime import datetime,date,time,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
UTC=timezone.utc

class DataError(RuntimeError):pass

def safe(path):
    p=Path(path)
    if not p.resolve().is_relative_to(ROOT.resolve()):
        raise DataError("Path outside Futbol: "+str(p))
    return p

def read_json(path):return json.loads(safe(path).read_text(encoding="utf-8"))
def write_json(path,data):
    p=safe(path);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=safe(p.with_suffix(p.suffix+".tmp"))
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    tmp.replace(p)

def read_csv(path):
    p=safe(path)
    if not p.exists():return []
    with p.open(encoding="utf-8-sig",newline="") as h:return list(csv.DictReader(h))

def write_csv(path,rows,fields=None):
    p=safe(path);p.parent.mkdir(parents=True,exist_ok=True)
    if fields is None:
        fields=list(dict.fromkeys(k for row in rows for k in row))
    with p.open("w",encoding="utf-8-sig",newline="") as h:
        w=csv.DictWriter(h,fieldnames=fields,extrasaction="raise")
        w.writeheader();w.writerows(rows)

def instant(value):
    if isinstance(value,(int,float)):return datetime.fromtimestamp(value/(1000 if abs(value)>100_000_000_000 else 1),UTC)
    result=value if isinstance(value,datetime) else datetime.fromisoformat(value.replace("Z","+00:00"))
    return (result.replace(tzinfo=UTC) if result.tzinfo is None else result).astimezone(UTC)

def iso(value):return instant(value).isoformat().replace("+00:00","Z")
def day_start(day,tz):
    return datetime.combine(date.fromisoformat(str(day)),time.min,ZoneInfo(tz)).astimezone(UTC)
def day_end(day,tz):
    return datetime.combine(date.fromisoformat(str(day))+timedelta(days=1),time.min,ZoneInfo(tz)).astimezone(UTC)
def stable_id(kind,external):return str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL,f"https://www.fotmob.com/{kind}/{external}"))
def checksum(data):return hashlib.sha256(data).hexdigest()
def as_number(value):
    if value in (None,"","undefined","null"):return None
    try:
        result=float(value)
        return int(result) if result.is_integer() else result
    except (ValueError,TypeError):return None
