"""Read-only dated roster query. Unknown membership never silently becomes a zero-minute absence."""
import argparse,sqlite3,json
from .core import BASE
from .eligibility import active,absence_semantics

def query(club_id,on,view='all'):
 db=sqlite3.connect('file:'+str(BASE/'state/live.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;rows=[];unknown=[]
 for pid, in db.execute('SELECT DISTINCT player_id FROM player_eligibility WHERE club_id=?',(club_id,)):
  r=active(db,club_id,pid,on);r=dict(r);r['player_id']=str(pid);r['name']=db.execute('SELECT display_name FROM players WHERE player_id=?',(pid,)).fetchone()[0];r['absence_semantics']=absence_semantics(db,club_id,pid,on)
  if r['verification_status']!='VERIFIED':unknown.append(r['player_id'])
  if view=='regular' and not (r['regular_member']==1 and r['verification_status']=='VERIFIED'):continue
  if 'source_json' in r:r['sources']=json.loads(r.pop('source_json'))
  rows.append(r)
 db.close();return dict(club_id=club_id,as_of=on,view=view,population_complete=not unknown,unverified_player_ids=unknown,warning='Unverified candidates must be resolved before cohort-rate comparisons' if unknown else None,players=rows)
def main():
 p=argparse.ArgumentParser();p.add_argument('--club',type=int,required=True);p.add_argument('--date',required=True);p.add_argument('--view',choices=['all','regular'],default='all');a=p.parse_args();print(json.dumps(query(a.club,a.date,a.view),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
