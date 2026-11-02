"""Approved application entrypoint with configured-timezone current-roster publication."""
import json
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from ops.cloud import platform as application
from squad_pipeline.core import ROOT
from squad_pipeline.cloud.all_match_export import export_all_matches

def roster_day(at=None):
    settings=json.loads((ROOT/'ops/cloud/runtime.json').read_text())
    return (at or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings['timezone'])).date().isoformat()

def publish_site(s,work,code_commit):
    work.mkdir(parents=True,exist_ok=False);head,p=s.restore(work/'source.sqlite')
    p=dict(p,dataset_ref=s.ref);m=export_all_matches(work/'source.sqlite',work/'site',head,p,roster_day(),False);application.ensure_ref(s,'site',head)
    files={str(x.relative_to(work/'site')):x.read_bytes() for x in (work/'site').rglob('*') if x.is_file()}
    files['publication.json']=json.dumps(dict(data_commit=head,version=m['version'],code_commit=code_commit,roster_reference_timezone='Asia/Shanghai')).encode()
    commit=application.commit_tree(s,'site',files,'site: '+m['version']['data_version'])
    return dict(commit=commit,version=m['version'])

def main():
    application.publish_site=publish_site
    application.main()
if __name__=='__main__':main()
