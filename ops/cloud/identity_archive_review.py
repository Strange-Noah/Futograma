"""Read-only identity review. No task mutations, source access or retries."""
import sqlite3,json,tarfile,hashlib,argparse
from pathlib import Path
from collections import Counter
from squad_pipeline.source_identity_review import routing_review,shared_routes

def review(snapshot,archives):
 before=hashlib.sha256(snapshot.read_bytes()).hexdigest();bodies={};hashes={}
 for path in archives:
  hashes[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
  with tarfile.open(path) as tar:
   captures=[]
   for m in tar.getmembers():
    if not m.isfile() or not m.name.endswith('.json'):continue
    try:d=json.load(tar.extractfile(m))
    except (ValueError,UnicodeDecodeError):continue
    if isinstance(d,dict) and isinstance(d.get('captures'),list):captures.extend(d['captures'])
    if isinstance(d,dict) and d.get('url') and d.get('path') and d.get('sha256'):captures.append(d)
   for r in captures:
    try:raw=tar.extractfile(r['path']).read()
    except (KeyError,TypeError):continue
    if hashlib.sha256(raw).hexdigest()!=r.get('reused_body_sha256',r.get('sha256')):raise ValueError('ARCHIVE_HASH_MISMATCH')
    bodies.setdefault(r['url'],(r,raw))
 db=sqlite3.connect('file:'+str(snapshot.resolve())+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;rows=[];routes=[];classes=Counter();categories=Counter()
 for row in db.execute("select * from international_acquisition_queue where state!='RESOLVED' and failure_classification='SOURCE_MATCH_ID_MISMATCH'"):
  t=dict(row);expected=json.loads(t['payload_json'])['fixture']['provider_match_id'];mapped=db.execute('select match_id from international_match_provider_ids where provider=? and provider_match_id=?',(t['provider'],expected)).fetchone();mid=mapped[0] if mapped else None
  fact=bool(mid and t['player_id'] and db.execute('select 1 from international_appearances where match_id=? and player_id=?',(mid,t['player_id'])).fetchone())
  category='EXISTING_PLAYER_FACT_VERIFICATION_DEBT' if fact else 'EXISTING_FIXTURE_PLAYER_FACT_UNVERIFIED' if mid and t['player_id'] else 'EXISTING_FIXTURE_VERIFICATION_DEBT' if mid else 'NO_CANONICAL_SOURCE_UNVERIFIED';categories[category]+=1;diag=None
  if t['url'] in bodies:
   source,raw=bodies[t['url']];r=routing_review(raw.decode('utf8','replace'),expected,source);routes.append(r);diag=r['diagnostic']
  classification=diag['classification'] if diag else 'ARCHIVE_UNAVAILABLE';classes[classification]+=1;rows.append(dict(task_key=t['task_key'],entity_type=t['entity_type'],expected_id=expected,canonical_match_id=mid,player_fact_exists=fact,category=category,classification=classification,diagnostic=diag))
 integrity=db.execute('pragma integrity_check').fetchone()[0];fk=db.execute('pragma foreign_key_check').fetchall();national=db.execute('select count(*) from international_appearances').fetchone()[0];db.close();assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==before
 return dict(snapshot_sha256=before,integrity=integrity,foreign_key_errors=len(fk),national_fact_count=national,archive_hashes=hashes,tasks=len(rows),response_classes=dict(classes),fact_categories=dict(categories),records=rows,routes=shared_routes(routes),resolved_tasks=0,facts_changed=0)

def main():
 p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--archive',type=Path,action='append',default=[]);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 with a.output.open('x') as f:json.dump(review(a.snapshot,a.archive),f,indent=2)
if __name__=='__main__':main()
