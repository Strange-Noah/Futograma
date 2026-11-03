"""Offline review of saved GitHub metadata; no credentials or network required."""
import argparse,json
from pathlib import Path
from squad_pipeline.production_reliability import timeline,freshness

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,required=True);p.add_argument('--now',required=True);p.add_argument('--last-publication');p.add_argument('--active',action='store_true');p.add_argument('--aligned',action='store_true');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 d=json.loads(a.input.read_text());out={'schema_version':1,'timelines':{k:[timeline(r,((8,30),(9,30),(10,30)) if k=='Futograma' else ((8,0),)) for r in v['runs']] for k,v in d.items() if isinstance(v,dict) and 'runs' in v},'freshness':freshness(a.now,a.last_publication,a.active,a.aligned),'read_only':True}
 with a.output.open('x') as f:json.dump(out,f,indent=2)
 if out['freshness']['state'] in ('UNKNOWN','BREACH'):raise SystemExit(2)
if __name__=='__main__':main()
