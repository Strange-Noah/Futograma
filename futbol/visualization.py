"""Four standard scientific figures; CSVs retain the underlying values."""
import os
from datetime import date
from collections import defaultdict
from .common import ROOT,safe

def render(data,config,out):
    os.environ['MPLCONFIGDIR']=str(ROOT/'.cache/matplotlib')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False,'savefig.dpi':150})
    out=safe(out);out.mkdir(parents=True,exist_ok=True)
    focus=[p for fid in config['focus_fotmob_player_ids'] for p in data['players'] if p['fotmob_player_id']==fid]
    names={p['player_id']:p['player_name'] for p in data['players']};m={r['match_id']:r for r in data['matches']}
    days=defaultdict(list)
    for r in data['daily_load']:days[r['player_id']].append(r)
    def save(fig,name):
        fig.savefig(out/(name+'.png'),bbox_inches='tight');fig.savefig(out/(name+'.svg'),bbox_inches='tight');plt.close(fig)
    fig,axes=plt.subplots(len(focus),3,figsize=(17,3*len(focus)),squeeze=False)
    for i,p in enumerate(focus):
        dd=days[p['player_id']];x=[date.fromisoformat(r['date']) for r in dd]
        for j,w in enumerate(config['windows_days']):
            ax=axes[i,j];club=np.array([r[f'club_load_{w}d'] if r[f'club_load_{w}d'] is not None else np.nan for r in dd]);nt=np.array([r[f'load_{w}d_International'] if r[f'load_{w}d_International'] is not None else np.nan for r in dd]);ax.stackplot(x,club,nt,labels=['Club','National'],colors=['#287c9d','#efad49'],alpha=.85)
            av=[a for a in data['player_availability'] if a['player_id']==p['player_id'] and config['research']['start']<=m[a['match_id']]['date_madrid']<=config['research']['as_of']]
            ucl=[date.fromisoformat(m[a['match_id']]['date_madrid']) for a in av if m[a['match_id']]['competition_type']=='UCL']
            injured=[date.fromisoformat(m[a['match_id']]['date_madrid']) for a in av if a['status']=='injured']
            ax.scatter(ucl,[0]*len(ucl),marker='|',s=50,color='#572e8b',label='UCL fixture',zorder=4);ax.scatter(injured,[0]*len(injured),marker='x',s=17,color='#bc3048',label='Injury evidence at match',zorder=5)
            ax.set_title(f"{p['player_name']} | {w} days");ax.set_ylabel('Minutes');ax.tick_params(axis='x',rotation=25)
    axes[0,0].legend(fontsize=7,ncol=2);fig.suptitle('Daily competitive load | Madrid calendar | injury points are not inferred intervals',y=1.005);fig.tight_layout();save(fig,'01_load_timeseries')
    summary=sorted([r for r in data['player_summary'] if r['period']=='All'],key=lambda r:r['load_28d_max'] if r['load_28d_max'] is not None else -1)
    fig,axes=plt.subplots(1,3,figsize=(18,max(9,len(summary)*.24)),sharey=True)
    for ax,col,title in zip(axes,['load_28d_max','longest_observed_club_starts','longest_observed_club_75min'],['Maximum 28-day minutes','Consecutive club starts (observed)','Consecutive club >=75 min (observed)']):
        ax.barh(range(len(summary)),[r[col] if r[col] is not None else np.nan for r in summary],color=['#dd8452' if any(p['player_id']==r['player_id'] for p in focus) else '#3986a2' for r in summary]);ax.set_title(title);ax.grid(axis='x',alpha=.2)
        for y,r in enumerate(summary):
            if r[col] is None:ax.text(0,y,'NA',va='center',fontsize=7,color='#666666')
    axes[0].set_yticks(range(len(summary)),[r['player_name'] for r in summary],fontsize=7);fig.suptitle('Player comparison | cohort includes unconfirmed registrations; see sequence completeness in CSV');fig.tight_layout();save(fig,'02_load_comparison')
    rows=data['ucl_rotation'];dates=sorted(set(r['ucl_datetime'] for r in rows));fig,axes=plt.subplots(7,1,figsize=(max(15,len(dates)*.4),18),squeeze=False)
    for ax,col,title in zip(axes[:,0],['pre_match_7d','pre_match_14d','pre_match_28d','club_pre_match_7d','club_pre_match_14d','club_pre_match_28d','previous_domestic_minutes'],['Total pre-match 7 days','Total pre-match 14 days','Total pre-match 28 days','Club pre-match 7 days','Club pre-match 14 days','Club pre-match 28 days','Minutes in previous domestic fixture']):
        lookup={(r['player_id'],r['ucl_datetime']):r.get(col) for r in rows};array=np.array([[lookup.get((p['player_id'],d)) if lookup.get((p['player_id'],d)) is not None else np.nan for d in dates] for p in focus]);im=ax.imshow(array,aspect='auto',cmap='YlOrRd');ax.set_yticks(range(len(focus)),[p['player_name'] for p in focus]);ax.set_xticks(range(len(dates)),[d[:10] for d in dates],rotation=70,fontsize=7);ax.set_title(title);fig.colorbar(im,ax=ax,label='Minutes',fraction=.018)
    fig.suptitle('Before UCL | missing values blank | stage and Club Load retained in ucl_rotation.csv',y=1.01);fig.tight_layout();save(fig,'03_pre_ucl_rotation')
    fig,axes=plt.subplots(1,3,figsize=(17,max(5,len(data['players'])*.22)),sharey=True)
    allp=sorted(data['players'],key=lambda p:p['player_name']);types=['Domestic','UCL','International'];lookup={(r['player_id'],r['competition_type']):r for r in data['attendance']}
    for ax,col,title in zip(axes,['appearance_rate','start_rate','mean_minutes_per_appearance'],['Appearance rate','Start rate','Mean minutes / appearance']):
        ar=np.array([[lookup.get((p['player_id'],t),{}).get(col) if lookup.get((p['player_id'],t),{}).get(col) is not None else np.nan for t in types] for p in allp]);im=ax.imshow(ar,aspect='auto',cmap='Blues',vmin=0,vmax=1 if 'rate' in col else None);ax.set_xticks(range(3),types);ax.set_title(title);fig.colorbar(im,ax=ax,fraction=.025)
    axes[0].set_yticks(range(len(allp)),[p['player_name'] for p in allp],fontsize=7);fig.suptitle('Attendance | Domestic/UCL = target club; NT = known affiliation fixtures, not call-up eligibility; missing = blank');fig.tight_layout();save(fig,'04_competition_attendance')
