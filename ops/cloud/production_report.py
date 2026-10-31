"""Durable production receipts on a report-only Git branch; never advance the DB authority."""
import argparse,base64,hashlib,json,os,re,sqlite3,subprocess
from pathlib import Path
from datetime import datetime,timezone
from squad_pipeline.core import ROOT,BASE
from squad_pipeline.cloud.hosted_guard import require_hosted
from squad_pipeline.cloud.github_store import GitHubStore
REPORT_REF='production-reports'
def utc():return datetime.now(timezone.utc).isoformat()
def read(path,default=None):return json.loads(path.read_text()) if path.is_file() else default

def match_state(path):
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
    try:
        result={}
        for r in db.execute('SELECT club_id,match_id,player_id,participation_status,minutes FROM player_match_minutes ORDER BY club_id,match_id,player_id'):
            result.setdefault(('CLUB',str(r[1])),[]).append(list(r))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='international_appearances'").fetchone():
            for r in db.execute('SELECT match_id,player_id,represented_team_key,participation_status,minutes FROM international_appearances ORDER BY match_id,player_id'):
                result.setdefault(('NATIONAL',str(r[0])),[]).append(list(r))
        return {k:hashlib.sha256(json.dumps(v,sort_keys=True).encode()).hexdigest() for k,v in result.items()}
    finally:db.close()

def delta(before,after):
    removed=before.keys()-after.keys()
    if removed:raise ValueError('ACCEPTED_MATCH_REMOVED')
    out={}
    for kind in ('CLUB','NATIONAL'):
        old={k:v for k,v in before.items() if k[0]==kind};new={k:v for k,v in after.items() if k[0]==kind}
        out[kind]=dict(new=sum(k not in old for k in new),updated=sum(k in old and v!=old[k] for k,v in new.items()),unchanged=sum(k in old and v==old[k] for k,v in new.items()))
    out['TOTAL']={k:sum(out[t][k] for t in ('CLUB','NATIONAL')) for k in ('new','updated','unchanged')}
    return out

def publish(s,record,ref=REPORT_REF):
    if ref=='main' or not (ref==REPORT_REF or ref.startswith('acceptance/reports-')):raise ValueError('REPORT_ONLY_REF_REQUIRED')
    rid=str(record['run_id']);attempt=str(record.get('run_attempt','1'))
    if not re.fullmatch('[0-9]+',rid) or not re.fullmatch('[0-9]+',attempt):raise ValueError('NUMERIC_GITHUB_RUN_REQUIRED')
    existing=s.api('GET',s.base+'/git/ref/heads/'+ref,allow404=True);head=existing['object']['sha'] if existing else None
    text=(json.dumps(record,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    paths={'reports/production/latest.json':text,'reports/production/runs/'+rid+'.json':text,'reports/production/attempts/'+rid+'-'+attempt+'.json':text}
    if not head:paths['README.md']=b'# Production reports only\n\nAuthoritative SQLite is on main, never this branch. Durable reports/production/latest.json and runs/<run-id>.json describe actual operations. Historical attempts are retained. No cleanup is scheduled here.\n'
    entries=[]
    for path,body in paths.items():
        blob=s.api('POST',s.base+'/git/blobs',dict(content=base64.b64encode(body).decode(),encoding='base64'));entries.append(dict(path=path,mode='100644',type='blob',sha=blob['sha']))
    tree_data=dict(tree=entries)
    if head:tree_data['base_tree']=s.api('GET',s.base+'/git/commits/'+head)['tree']['sha']
    tree=s.api('POST',s.base+'/git/trees',tree_data);commit=s.api('POST',s.base+'/git/commits',dict(message='report: '+rid+' attempt '+attempt+' '+record['status'],tree=tree['sha'],parents=[head] if head else []))
    if head:s.api('PATCH',s.base+'/git/refs/heads/'+ref,dict(sha=commit['sha'],force=False))
    else:s.api('POST',s.base+'/git/refs',dict(ref='refs/heads/'+ref,sha=commit['sha']))
    for path,body in paths.items():
        item=s.api('GET',s.base+'/git/blobs/'+next(e['sha'] for e in entries if e['path']==path))
        if base64.b64decode(item['content'])!=body:raise RuntimeError('DURABLE_REPORT_READBACK_FAILED')
    return dict(ref=ref,commit=commit['sha'],latest='reports/production/latest.json',run='reports/production/runs/'+rid+'.json')

def main():
    require_hosted();a=argparse.ArgumentParser();a.add_argument('--capture',action='store_true');a.add_argument('--finalize',action='store_true');a.add_argument('--job-conclusion',default='failure');a.add_argument('--mode',required=True);args=a.parse_args()
    s=GitHubStore('Strange-Noah/Futograma-data',os.environ['GITHUB_TOKEN']);run=os.environ['GITHUB_RUN_ID'];attempt=os.environ['GITHUB_RUN_ATTEMPT'];folder=ROOT/'.cloud'/('receipt-'+run+'-'+attempt);folder.mkdir(parents=True,exist_ok=True);context=folder/'start.json'
    head,pointer=s.head()
    if args.capture:
        with context.open('x') as f:json.dump(dict(started_at=utc(),head=head,current=pointer['current']),f)
        return
    if args.finalize:
        item=s.api('GET',s.base+'/contents/reports/production/runs/'+run+'.json?ref='+REPORT_REF,allow404=True)
        prior=json.loads(base64.b64decode(item['content'])) if item and item.get('content') else {}
        if prior.get('run_attempt')!=attempt:prior={}
        if not prior:
            prior=dict(schema_version=1,run_id=run,run_attempt=attempt,mode=args.mode,started_at=os.environ.get('WORKFLOW_STARTED_AT'),status='FAILED_OR_INCOMPLETE',collection_completed=False,formal_ingestion=False,match_counts=None,unresolved_source_issues=[],unresolved_state='Unavailable because controller receipt was not produced',snapshot_publication=dict(published=None,result='UNCONFIRMED'),recovery_or_retry_actions=[],application_code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),reporting_code_commit=os.environ.get('REPORTING_CODE_SHA'))
        prior.update(finished_at=utc(),workflow_conclusion=args.job_conclusion,data_commit=head,after=pointer['current'],workflow_url='https://github.com/Strange-Noah/Futograma-data/actions/runs/'+run,finalized_by_independent_job=True,next_scheduled_update_enabled=os.environ.get('SCHEDULER_ENABLED')=='true' and os.environ.get('FUTBOL_CLOUD_ENABLED')=='true' and os.environ.get('FUTBOL_AUTHORITY_HANDOVER')=='CONFIRMED')
        if args.job_conclusion!='success':prior.update(status='FAILED_OR_INCOMPLETE',collection_completed=False,formal_ingestion=False)
        receipt=publish(s,prior)
        if s.head()[0]!=head:raise RuntimeError('REPORT_CHANGED_DATABASE_AUTHORITY')
        print(json.dumps(dict(status=prior['status'],durable=receipt),indent=2));return
    start=read(context,{})
    work=ROOT/'.cloud'/('hosted-'+run+'-'+attempt+'-'+args.mode);operation=read(work/'result.json',{});detail=read(work/'update/result.json',{});club=read(BASE/'audit/live_deployment/latest_run.json',{})
    # No result is completion evidence when the process died, timed out, or failed a version gate.
    waiting=operation.get('status')=='WAITING_FOR_FIRST_FORMAL_RUN'
    published=bool(detail.get('publish_site')) and head!=start.get('head')
    complete=args.mode=='update' and args.job_conclusion=='success' and bool(operation.get('formal_ingestion')) and published
    counts=None;old=work/'update/restored.sqlite';new=work/'update/candidate.sqlite'
    if published and old.is_file() and new.is_file():counts=delta(match_state(old),match_state(new))
    unresolved=[];retry=[];candidate=work/'update/working.sqlite'
    if candidate.is_file():
        db=sqlite3.connect('file:'+str(candidate)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        for table,statecol in [('match_retry_queue','state'),('international_acquisition_queue','state')]:
            if db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
                for row in db.execute('SELECT * FROM '+table+" WHERE "+statecol+"<>'RESOLVED'"):
                    item={k:row[k] for k in row.keys() if k in ('match_key','task_key','club_id','player_id','provider','provider_match_id','canonical_match_id','match_id','url','match_date','state','last_attempt_at','attempt_count','next_retry_at','failure_classification','escalation_status','last_error')};unresolved.append(dict(queue=table,**item))
        db.close()
    status='WAITING_FOR_FIRST_FORMAL_RUN' if waiting else ('COMPLETED_WITH_SOURCE_ISSUES' if detail.get('network_exit') else 'COMPLETED') if complete else 'OPERATION_SUCCEEDED' if args.job_conclusion=='success' and operation else 'FAILED_OR_INCOMPLETE'
    report=dict(schema_version=1,run_id=run,run_attempt=attempt,workflow_url='https://github.com/Strange-Noah/Futograma-data/actions/runs/'+run,event=os.environ.get('GITHUB_EVENT_NAME'),mode=args.mode,started_at=start.get('started_at'),finished_at=utc(),workflow_conclusion=args.job_conclusion,status=status,collection_completed=complete,formal_ingestion=complete,application_code_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),reporting_code_commit=os.environ.get('REPORTING_CODE_SHA'),before=start.get('current'),after=pointer['current'],data_commit=head,match_counts=counts,unchanged_definition='Existing accepted matches with identical facts; separately see actually checked club status counts',checked_club_status_counts=club.get('status_counts',{}) if args.mode=='update' else {},catalog_results=club.get('catalogs',[]) if args.mode=='update' else [],unresolved_source_issues=unresolved,remaining_unresolved_count=len(unresolved),snapshot_publication=dict(published=published,result=detail.get('status'),version=pointer['current']['version'],sha256=pointer['current']['database']['sha256']),recovery_or_retry_actions=dict(policy='72-hour lookback + checkpoint catchup + durable due retries; quarantines not silently retried',missed_window_catchup=club.get('missed_window_catchup',[]),source_results=club.get('results',[]) if args.mode=='update' else []),next_scheduled_update_enabled=os.environ.get('SCHEDULER_ENABLED')=='true' and os.environ.get('FUTBOL_CLOUD_ENABLED')=='true' and os.environ.get('FUTBOL_AUTHORITY_HANDOVER')=='CONFIRMED',schedule='0 8 * * *',first_formal_run=os.environ.get('FUTOGRAMA_FIRST_FORMAL_RUN') or json.loads((ROOT/'ops/cloud/runtime.json').read_text())['first_formal_run'],site_publication=operation.get('site'),failure_phase='No successful cloud controller result' if not operation else None)
    receipt=publish(s,report)
    if s.head()[0]!=head:raise RuntimeError('REPORT_CHANGED_DATABASE_AUTHORITY')
    print(json.dumps(dict(report=report,durable=receipt),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
