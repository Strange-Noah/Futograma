"""Required same-source CI check. No dispatch or publication on any failure."""
import json,os,urllib.request
from squad_pipeline.cloud.release_gate import require_ci

def main():
    sha=os.environ['EXPECTED_CODE_SHA']
    url='https://api.github.com/repos/Strange-Noah/Futograma/actions/runs?head_sha='+sha+'&per_page=100'
    request=urllib.request.Request(url,headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request,timeout=30) as r:runs=json.load(r)['workflow_runs']
    run=require_ci(runs,sha)
    print(json.dumps({'software':'PASS','source_commit':sha,'required_ci_run':run}))
if __name__=='__main__':main()
