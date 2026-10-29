from collections import defaultdict
from datetime import timedelta
from .common import instant,day_start,day_end,stable_id,write_json

def team_summary(data,config):
    matches={m['match_id']:m for m in data['matches']};groups=defaultdict(list)
    for a in data['player_availability']:
        m=matches[a['match_id']]
        if not config['research']['start']<=m['date_madrid']<=config['research']['as_of']:continue
        groups[(a['team_id'],m['research_season'],m['competition_type'])].append(a)
    return [dict(team_id=tid,season=season,competition_type=typ,cohort_player_matches=len(rows),cohort_players=len({r['player_id'] for r in rows}),appearances=sum(r['squad_status']=='appeared' for r in rows),known_minutes=sum(r['minutes_played'] or 0 for r in rows),unknown_status=sum(r['status']=='unknown' for r in rows),formal_registration_confirmed=sum(r['registration_confirmed'] for r in rows),interpretation='Observed/affiliated player cohort only; not complete official registered team') for (tid,season,typ),rows in sorted(groups.items())]

def stage_summary(rotation):
    groups=defaultdict(list)
    for r in rotation:groups[(r['player_id'],r['ucl_stage'])].append(r)
    rows=[]
    for (pid,stage),rr in groups.items():
        out=dict(player_id=pid,ucl_stage=stage,known_matches=len(rr),ucl_registration_confirmed_matches=sum(r['ucl_registration_confirmed'] for r in rr),previous_domestic_available_matches=sum(r['previous_domestic_available']=='available' for r in rr),previous_domestic_unknown_matches=sum(r['previous_domestic_available']=='unknown' for r in rr))
        for col in ['B_90_minus_previous_minutes','previous_domestic_minutes','pre_match_7d','pre_match_14d','pre_match_28d','club_pre_match_7d','club_pre_match_14d','club_pre_match_28d']:
            vv=[r[col] for r in rr if r.get(col) is not None];out[col+'_mean']=sum(vv)/len(vv) if vv else None;out[col+'_valid_matches']=len(vv)
        rows.append(out)
    return rows

def acceptance(data,histories,index,config):
    matches={m['match_id']:m for m in data['matches']};teams={t['team_id']:t for t in data['teams']};rows=[]
    for player in data['players']:
        if player['fotmob_player_id'] not in config['focus_fotmob_player_ids']:continue
        pid=player['player_id'];apps=[a for a in data['player_appearances'] if a['player_id']==pid];pm=[r for r in data['prematch_load'] if r['player_id']==pid]
        checks=0;failures=[]
        # Independent direct sum for every focus pre-match window, including edge exclusion.
        for r in pm:
            upper=instant(r['datetime'])
            for w in config['windows_days']:
                lower=upper-timedelta(days=w);actual=sum(a['minutes_played'] or 0 for a in apps if lower<=instant(matches[a['match_id']]['datetime'])<upper)
                checks+=1
                if r[f'known_pre_match_{w}d']!=actual:failures.append((r['match_id'],w))
        for r in (r for r in data['daily_load'] if r['player_id']==pid):
            upper=day_end(r['date'],config['research']['timezone'])
            from datetime import date
            for w in config['windows_days']:
                lower=day_start(date.fromisoformat(r['date'])-timedelta(days=w-1),config['research']['timezone']);actual=sum(a['minutes_played'] or 0 for a in apps if lower<=instant(matches[a['match_id']]['datetime'])<upper);checks+=1
                if r[f'known_load_{w}d']!=actual:failures.append((r['date'],w))
        rr=[r for r in data['player_availability'] if r['player_id']==pid]
        rows.append(dict(player_id=pid,name=player['player_name'],fotmob_player_id=player['fotmob_player_id'],history_reached_start=histories[pid]['source_history_reached_start'],source_history_errors=histories[pid]['errors'],club_teams=sorted({teams[a['team_id']]['team_name'] for a in apps if not matches[a['match_id']]['is_national']}),national_teams=sorted({teams[a['team_id']]['team_name'] for a in apps if matches[a['match_id']]['is_national']}),appearances=len(apps),missing_minutes=sum(a['minutes_played'] is None for a in apps),missing_start_information=sum(a['started'] is None for a in apps),window_checks=checks,window_failures=failures,injured_match_evidence=sum(r['status']=='injured' for r in rr),unknown_match_status=sum(r['status']=='unknown' for r in rr),confirmed_healthy_rotation=sum(r['healthy_rotation_confirmed'] for r in rr)))
    return {'focus_players':rows,'passed_computation_checks':all(not r['window_failures'] for r in rows),'official_registration_acceptance':'INCOMPLETE: phase-specific official rosters unverified; never use this report as proof of full registration census.','history_acceptance':'Paginated source complete to requested start; independent census of all official appearances not certified.','rotation_interpretation':'Minutes and B are descriptive; intentional healthy rotation requires explicit match-specific source.'}
