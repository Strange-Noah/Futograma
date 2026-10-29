"""Read-only player database -> playing affiliation -> half-season comparison.

This module deliberately never calls collect/build/normalize/LoadIndex and never
updates individual appearances, pre-match loads, daily loads or core tables.
"""
from __future__ import annotations
import argparse,json,os,sqlite3,shutil
from collections import defaultdict
from datetime import date,timedelta,datetime
from .common import ROOT,UTC,DataError,safe,read_json,read_csv,write_json,write_csv,checksum,instant,iso,day_start,day_end,stable_id
from .analytics import percentile

def protected_snapshot(baseline):
    return {name:checksum(safe(ROOT/name).read_bytes()) for name in baseline['files']}

def verify_unchanged(baseline):
    rows=[]
    for name,old in baseline['files'].items():
        p=safe(ROOT/name);after=checksum(p.read_bytes()) if p.exists() else None
        rows.append(dict(path=name,before_sha256=old['sha256'],after_sha256=after,identical=old['sha256']==after))
    for name,before in baseline.get('protected_core_sources',{}).items():
        p=safe(ROOT/name);after=checksum(p.read_bytes()) if p.exists() else None
        rows.append(dict(path=name,before_sha256=before,after_sha256=after,identical=before==after))
    if any(not r['identical'] for r in rows):raise DataError('Protected personal data/core source changed: '+str([r['path'] for r in rows if not r['identical']]))
    return rows

def read_personal(config):
    root=safe(ROOT/config['personal_outputs']);manifest=read_json(root/'manifest.json')
    db=safe(root/'player_load.sqlite')
    con=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True);con.row_factory=sqlite3.Row
    names=['players','teams','matches','registrations','player_appearances','player_availability','daily_load','player_summary']
    tables={n:[dict(r) for r in con.execute('SELECT * FROM "'+n+'"')] for n in names}
    con.close();return tables,manifest

def period_for(day,periods):
    return next((p for p in periods if p['start']<=day<p['end_exclusive']),None)

def actual_affiliations(players,config):
    """Explicit loans override ownership; academy membership never implies first-team selection."""
    rows=[];invalid=[];parent_ids=set(config['academy_parent_team_ids']);reserve_ids=set(config.get('reserve_parent_team_ids',[]))
    for player in players:
        pid=player['fotmob_player_id'];profile=read_json(ROOT/f'raw/player_load/profiles/{pid}.json')
        for group,items in profile.get('careerHistory',{}).get('careerItems',{}).items():
            if group not in ('senior','youth'):continue
            for entry in items.get('teamEntries') or []:
                tid=entry.get('teamId');start=entry.get('startDate');end=entry.get('endDate')
                if not tid or int(tid)<=0 or not start:
                    invalid.append(dict(fotmob_player_id=pid,group=group,source_team_id=tid,team_name=entry.get('team'),reason='Missing valid team ID or start; never merge -1 or infer start from first match'))
                    continue
                if group=='youth' and int(tid) not in parent_ids:continue
                parent=group=='youth' or int(tid) in reserve_ids
                typ=entry.get('transferType') or {};typ=typ.get('localizationKey') or typ.get('text') or '' if isinstance(typ,dict) else str(typ)
                loan='on_loan' in typ or typ=='on loan'
                # Calendar date semantics: reported transfer dates, no invented exact-time precision.
                start=instant(start).date().isoformat();end=instant(end).date().isoformat() if end else None
                if end and end<=start:continue
                rows.append(dict(player_id=player['player_id'],fotmob_player_id=pid,team_id=config['team_id'] if parent else int(tid),source_team_id=int(tid),team_name=config['team_name'] if parent else entry['team'],valid_from=start,valid_to=end,kind='academy_parent' if group=='youth' else 'reserve_parent' if int(tid) in reserve_ids else 'loan' if loan else 'loan_return' if 'back_from_loan' in typ or 'back from loan' in typ else 'senior_playing',priority=1 if parent else 4 if loan else 3,evidence_confidence='reported_playing_transfer_dates',source=f'https://www.fotmob.com/players/{pid}',raw_source=f'raw/player_load/profiles/{pid}.json',note='Playing-career entry; not contract ownership or formal competition registration. Academy parent only qualifies comparison after match-squad evidence in that half.'))
    pmap={p['fotmob_player_id']:p for p in players}
    for r in read_csv(ROOT/'config/playing_affiliation_overrides.csv'):
        pid=int(r['fotmob_player_id'])
        if pid not in pmap:raise DataError('Affiliation evidence player missing from frozen database')
        rows.append(dict(player_id=pmap[pid]['player_id'],fotmob_player_id=pid,team_id=int(r['fotmob_team_id']),source_team_id=int(r['fotmob_team_id']),team_name=r['team_name'],valid_from=r['valid_from'],valid_to=r['valid_to'] or None,kind=r['kind'],priority=10,evidence_confidence=r['confidence'],source=r['source'],raw_source='config/playing_affiliation_overrides.csv',note=r['note']))
    return rows,invalid

def resolve_affiliation(rows,day):
    active=[r for r in rows if r['valid_from']<=day and (not r['valid_to'] or day<r['valid_to'])]
    if not active:return None,'unknown'
    # Active playing loans take precedence even over official parent-club ownership/transfer evidence.
    loans=[r for r in active if r['kind']=='loan']
    if loans:active=loans
    rank=max(r['priority'] for r in active);active=[r for r in active if r['priority']==rank]
    if len({r['team_id'] for r in active})>1:return None,'conflicting_playing_clubs'
    return max(active,key=lambda r:r['valid_from']),'resolved'

def squad_records(matches,config):
    """Only target-side starters/subs are S_j; unavailable never enters S_j."""
    from .normalization import load_detail
    club=stable_id('teams',config['team_id']);rows=[];coverage=[]
    for m in matches:
        if club not in (m['home_team_id'],m['away_team_id']):continue
        if not period_for(m['date_madrid'],config['periods']):continue
        d=load_detail(m['fotmob_match_id']);team=None
        if d:
            team=next((v for v in ((d.get('content') or {}).get('lineup') or {}).values() if isinstance(v,dict) and int(v.get('id') or -1)==config['team_id']),None)
        starters=(team or {}).get('starters') or [];subs=(team or {}).get('subs') or []
        ids=[int(p['id']) for p in [*starters,*subs]]
        complete=bool(team) and len(starters)==11 and 'subs' in team and isinstance(team['subs'],list) and len(ids)==len(set(ids))
        period=period_for(m['date_madrid'],config['periods'])
        path=next((p for p in [ROOT/f"raw/matches/{m['fotmob_match_id']}.json",ROOT/f"match_{m['fotmob_match_id']}.json",ROOT/f"match_{m['fotmob_match_id']}_api.json"] if p.exists()),None)
        coverage.append(dict(period_id=period['id'],match_id=m['match_id'],fotmob_match_id=m['fotmob_match_id'],date_madrid=m['date_madrid'],competition=m['competition'],home_team=m['home_team_name'],away_team=m['away_team_name'],starters=len(starters),substitutes=len(subs),squad_size=len(set(ids)),source_squad_complete=complete,coverage_definition='FotMob target-side 11 starters + explicit substitute array + unique IDs; not independent official census.',raw_source=str(path.relative_to(ROOT)) if path else None,raw_sha256=checksum(path.read_bytes()) if path else None,source=m['source']))
        for role,entries in [('starter',starters),('substitute',subs)]:
            for p in entries:
                rows.append(dict(period_id=period['id'],match_id=m['match_id'],date_madrid=m['date_madrid'],fotmob_player_id=int(p['id']),player_id=stable_id('players',int(p['id'])),player_name=p['name'],shirt_number=p.get('shirtNumber'),squad_role=role,source_squad_complete=complete,source=m['source'],raw_source=str(path.relative_to(ROOT)) if path else None))
    return rows,coverage

def eligible_days(personal_days,affiliations,team_id,academy_eligible):
    """Select observation dates only; never filter/recompute the rolling source events."""
    result=[];unresolved=0
    for row in personal_days:
        relation,status=resolve_affiliation(affiliations,row['date'])
        if status!='resolved':unresolved+=1;continue
        if relation['team_id']!=team_id:continue
        if relation['kind'] in ('academy_parent','reserve_parent') and not academy_eligible:continue
        result.append((row,relation))
    return result,unresolved

def membership_intervals(selected,period,player):
    groups=[]
    for row,rel in selected:
        day=row['date'];key=(rel['raw_source'],rel['valid_from'],rel['valid_to'],rel['kind'],rel['source'])
        if groups and groups[-1]['_key']==key and date.fromisoformat(day)==date.fromisoformat(groups[-1]['last_observation_date'])+timedelta(days=1):
            groups[-1]['last_observation_date']=day;groups[-1]['observation_days']+=1
        else:groups.append(dict(period_id=period['id'],season=period['season'],half=period['half'],player_id=player['player_id'],fotmob_player_id=player['fotmob_player_id'],player_name=player['player_name'],first_observation_date=day,last_observation_date=day,observation_days=1,playing_valid_from=rel['valid_from'],playing_valid_to_exclusive=rel['valid_to'],affiliation_kind=rel['kind'],evidence_confidence=rel['evidence_confidence'],source=rel['source'],raw_source=rel['raw_source'],_key=key))
    for r in groups:r.pop('_key')
    return groups

def streaks_for_player(player,team_matches,appmap,affiliations,coverage,config):
    """Complete observed PSG runs stay continuous across half boundaries."""
    runs=[];pid=player['player_id'];cov={r['match_id']:r for r in coverage}
    for measure in ['starts','75min']:
        run=[]
        def flush():
            if run:
                runs.append(dict(player_id=pid,fotmob_player_id=player['fotmob_player_id'],measure=measure,match_ids=[r['match_id'] for r in run],start_date=run[0]['date_madrid'],end_date=run[-1]['date_madrid'],full_observed_count=len(run),left_censored=run[0]['match_id']==team_matches[0]['match_id']))
        for m in team_matches:
            rel,_=resolve_affiliation(affiliations,m['date_madrid']);a=appmap.get((pid,m['match_id']))
            ok=bool(rel and rel['team_id']==config['team_id'] and cov[m['match_id']]['source_squad_complete'] and a and (a['started']==1 if measure=='starts' else a['minutes_played'] is not None and a['minutes_played']>=75 and not a['minute_conflict']))
            if ok:run.append(m)
            else:flush();run=[]
        flush()
    return runs

def half_statistics(player,period,selected,team_matches,apps,appmap,runs,config,availability=None):
    dd=[r for r,rel in selected];allowed={r['date'] for r in dd};pid=player['player_id']
    # Actual target-team minutes never include earlier clubs from a rolling window.
    aa=[a for a in apps if a['player_id']==pid and a['team_id']==stable_id('teams',config['team_id']) and a['_match']['date_madrid'] in allowed]
    matchdays={m['match_id'] for m in team_matches if period['start']<=m['date_madrid']<period['end_exclusive'] and m['date_madrid'] in allowed}
    base=dict(period_id=period['id'],team=config['team_name'],season=period['season'],half=period['half'],data_cutoff=config['as_of'],player_id=pid,fotmob_player_id=player['fotmob_player_id'],player_name=player['player_name'],cohort_method='Playing affiliation at observation date; loans excluded; academy needs this-half match squad',eligible_observation_days=len(dd),first_eligible_date=dd[0]['date'] if dd else None,last_eligible_date=dd[-1]['date'] if dd else None,target_team_matches_during_affiliation=len(matchdays),target_team_appearances=len(aa),target_team_starts=sum(a['started']==1 for a in aa),target_team_minutes=sum(a['minutes_played'] for a in aa) if all(a['minutes_played'] is not None and not a['minute_conflict'] for a in aa) else None)
    for measure in ['starts','75min']:
        rr=[r for r in runs if r['measure']==measure and any(mid in matchdays for mid in r['match_ids'])]
        base['longest_'+measure+'_within_half']=max((sum(mid in matchdays for mid in r['match_ids']) for r in rr),default=0)
        base['full_observed_'+measure+'_run_overlapping_half']=max((r['full_observed_count'] for r in rr),default=0)
        base[measure+'_run_crosses_half_boundary']=any(sum(mid in matchdays for mid in r['match_ids'])<r['full_observed_count'] for r in rr)
        base[measure+'_full_record_left_censored']=any(r['left_censored'] for r in rr)
    av=[a for a in availability or [] if a['player_id']==pid and a['team_id']==stable_id('teams',config['team_id']) and a['_match']['date_madrid'] in allowed]
    for typ in ['Domestic','UCL']:
        eligible=[a for a in av if a['_match']['competition_type']==typ and a['status']=='available']
        base[typ+'_available_matches']=len(eligible)
        vv=[a['minutes_played'] for a in eligible]
        base[typ+'_available_usage']=sum(vv)/(90*len(vv)) if vv and all(x is not None for x in vv) and not any(a.get('minute_conflict') for a in eligible) else None
        base[typ+'_available_start_rate']=sum(a['started']==1 for a in eligible)/len(eligible) if eligible and all(a['started'] is not None for a in eligible) else None
        base[typ+'_available_appearance_rate']=sum(a['squad_status']=='appeared' for a in eligible)/len(eligible) if eligible else None
    for status in ['injured','suspended','unknown']:base[status+'_target_matches']=sum(a['status']==status for a in av)
    output=[]
    for scope,prefix in [('Total','load'),('Club','club_load')]:
        result=dict(base,load_scope=scope,club_scope_definition='Club = all clubs in existing personal rolling window; target_team_minutes = PSG only')
        for w in (7,14,28):
            col=f'{prefix}_{w}d';values=[d[col] for d in dd if d[col] is not None]
            result[f'load_{w}d_valid_days']=len(values);result[f'load_{w}d_coverage']=len(values)/len(dd) if dd else None
            threshold=config.get('thresholds_minutes',{'7':180,'14':360,'28':720})[str(w)]
            result[f'load_{w}d_threshold']=threshold;result[f'load_{w}d_fraction_above_threshold']=sum(v>threshold for v in values)/len(values) if values else None
            for name,func in [('mean',lambda v:sum(v)/len(v)),('p50',lambda v:percentile(v,.5)),('p90',lambda v:percentile(v,.9)),('max',max)]:result[f'load_{w}d_{name}']=func(values) if values else None
            peak=next((d for d in dd if values and d[col]==max(values)),None)
            result[f'peak_{w}d_date']=peak['date'] if peak else None
        output.append(result)
    return output

def peak_detail(player,peak_day,kind,scope,daily,apps,matches,affiliations,config):
    if not peak_day:return None,[]
    key='load_28d' if scope=='Total' else 'club_load_28d'
    row=next(r for r in daily if r['date']==peak_day);hi=day_end(peak_day,config['timezone']);lo=day_start(date.fromisoformat(peak_day)-timedelta(days=27),config['timezone']);rel,status=resolve_affiliation(affiliations,peak_day)
    audit_id=f"{player['fotmob_player_id']}:{kind}:{scope}:{peak_day}"
    details=[]
    for a in apps:
        if a['player_id']!=player['player_id']:continue
        m=matches[a['match_id']]
        if lo<=instant(m['datetime'])<hi and (scope=='Total' or not m['is_national']):
            details.append(dict(audit_id=audit_id,audit_origin=kind,load_scope=scope,fotmob_player_id=player['fotmob_player_id'],player_name=player['player_name'],match_id=a['match_id'],datetime=m['datetime'],date_madrid=m['date_madrid'],team_id=a['team_id'],team_name=a['_team_name'],home_team=m['home_team_name'],away_team=m['away_team_name'],competition=m['competition'],competition_type=m['competition_type'],minutes_played=a['minutes_played'],minute_conflict=a['minute_conflict'],source=a['source']))
    details.sort(key=lambda r:r['datetime'])
    peak=dict(audit_id=audit_id,audit_origin=kind,load_scope=scope,fotmob_player_id=player['fotmob_player_id'],player_name=player['player_name'],peak_date=peak_day,window_start_inclusive=iso(lo),window_end_exclusive=iso(hi),existing_rolling_minutes=row[key],detail_minutes_sum=sum(a['minutes_played'] or 0 for a in details),playing_club_at_peak=rel['team_name'] if rel else status,peak_during_target_affiliation=bool(rel and rel['team_id']==config['team_id']),appearance_matches=len(details),interpretation='Old global peak is personal history; only phase-affiliated observation dates enter team comparisons.')
    if row[key] is not None and peak['detail_minutes_sum']!=row[key]:raise DataError('Peak trace does not reconcile frozen rolling value')
    return peak,details

def compare(config_path='config/team_comparison.json',plots=True):
    config=read_json(ROOT/config_path);baseline=read_json(ROOT/config['baseline']);before=verify_unchanged(baseline)
    tables,manifest=read_personal(config);config['as_of']=manifest['as_of'];config['thresholds_minutes']=manifest['config']['thresholds_minutes'];periods=config['periods']
    for a,b in zip(periods,periods[1:]):
        if a['end_exclusive']!=b['start']:raise DataError('Half periods overlap or have a gap')
    squad,coverage=squad_records(tables['matches'],config)
    target=stable_id('teams',config['team_id']);matches={r['match_id']:r for r in tables['matches']};team_matches=sorted([matches[r['match_id']] for r in coverage],key=lambda m:m['datetime'])
    names={r['team_id']:r['team_name'] for r in tables['teams']};players={r['player_id']:r for r in tables['players']};pids={r['fotmob_player_id']:r for r in players.values()}
    if any(r['fotmob_player_id'] not in pids for r in squad):raise DataError('New squad player absent from frozen personal DB; report separately, do not silently collect/mutate')
    affiliations,invalid=actual_affiliations(tables['players'],config);aff_by=defaultdict(list)
    for r in affiliations:aff_by[r['player_id']].append(r)
    days=defaultdict(list)
    for r in tables['daily_load']:days[r['player_id']].append(r)
    for dd in days.values():dd.sort(key=lambda r:r['date'])
    apps=tables['player_appearances'];appmap={(r['player_id'],r['match_id']):r for r in apps}
    for a in apps:a['_match']=matches[a['match_id']];a['_team_name']=names[a['team_id']]
    for a in tables['player_availability']:a['_match']=matches[a['match_id']]
    squad_by=defaultdict(list)
    for r in squad:squad_by[(r['period_id'],r['player_id'])].append(r)
    memberships=[];rosters=[];squad_rosters=[];stats=[];allruns=[];unresolved=[];period_summaries=[]
    for p in players.values():allruns.extend(streaks_for_player(p,team_matches,appmap,aff_by[p['player_id']],coverage,config))
    for period in periods:
        if period['start']>config['as_of']:continue
        for p in sorted(players.values(),key=lambda p:p['player_name']):
            pid=p['player_id'];ss=squad_by.get((period['id'],pid),[])
            if ss:squad_rosters.append(dict(affiliation_consistency='conflict_or_unknown' if any((resolve_affiliation(aff_by[pid],x['date_madrid'])[0] or {}).get('team_id')!=config['team_id'] for x in ss) else 'consistent',period_id=period['id'],season=period['season'],half=period['half'],player_id=pid,fotmob_player_id=p['fotmob_player_id'],player_name=p['player_name'],squad_matches=len(ss),starter_squad_matches=sum(r['squad_role']=='starter' for r in ss),substitute_squad_matches=sum(r['squad_role']=='substitute' for r in ss),first_squad_date=min(r['date_madrid'] for r in ss),last_squad_date=max(r['date_madrid'] for r in ss),definition='Union of PSG-side starters and substitutes only, not unavailable or other-club squads'))
            subset=[r for r in days[pid] if period['start']<=r['date']<period['end_exclusive'] and r['date']<=config['as_of']]
            selected,missing=eligible_days(subset,aff_by[pid],config['team_id'],bool(ss))
            if selected:
                segments=membership_intervals(selected,period,p);memberships.extend(segments)
                rosters.append(dict(period_id=period['id'],season=period['season'],half=period['half'],player_id=pid,fotmob_player_id=p['fotmob_player_id'],player_name=p['player_name'],squad_matches=len(ss),playing_days=len(selected),first_playing_observation=selected[0][0]['date'],last_playing_observation=selected[-1][0]['date'],observation_intervals=json.dumps([[r['first_observation_date'],r['last_observation_date']] for r in segments]),entry_evidence='playing_affiliation_and_match_squad' if ss else 'playing_affiliation_only_no_this_half_match_squad',evidence_confidence=';'.join(sorted({r['evidence_confidence'] for d,r in selected})),definition='Final refinement playing-affiliation cohort; membership is time-varying, not available for every match'))
                stats.extend(half_statistics(p,period,selected,team_matches,apps,appmap,[r for r in allruns if r['player_id']==pid],config,tables['player_availability']))
            if ss:
                for s in ss:
                    rel,status=resolve_affiliation(aff_by[pid],s['date_madrid'])
                    if not rel or rel['team_id']!=config['team_id']:
                        unresolved.append(dict(period_id=period['id'],fotmob_player_id=p['fotmob_player_id'],player_name=p['player_name'],match_id=s['match_id'],date_madrid=s['date_madrid'],issue='PSG match-squad identity contradicts or lacks playing affiliation',resolved_team=rel['team_name'] if rel else status,raw_source=s['raw_source'],action='Retain source squad evidence; exclude unproven PSG observation days; no change to personal records'))
        cov=[r for r in coverage if r['period_id']==period['id']];rr=[r for r in rosters if r['period_id']==period['id']];sr=[r for r in squad_rosters if r['period_id']==period['id']]
        period_summaries.append(dict(period_id=period['id'],season=period['season'],half=period['half'],start=period['start'],end_exclusive=period['end_exclusive'],data_cutoff=config['as_of'],last_observation_date=min(config['as_of'],(date.fromisoformat(period['end_exclusive'])-timedelta(days=1)).isoformat()),target_matches=len(cov),complete_squad_matches=sum(r['source_squad_complete'] for r in cov),squad_fetch_completeness=sum(r['source_squad_complete'] for r in cov)/len(cov) if cov else None,match_squad_cohort_players=len(sr),playing_affiliation_cohort_players=len(rr),squad_players_with_playing_days=sum(r['squad_matches']>0 for r in rr),playing_only_no_match_squad=sum(r['squad_matches']==0 for r in rr),definition='Default: final playing-affiliation rule; separate match-squad union retained for original P_h definition'))
    peaks=[];peak_matches=[];audit_squads=[r for r in squad if r['fotmob_player_id'] in config['audit_player_ids']]
    for fid in config['audit_player_ids']:
        p=pids[fid];old=next(r for r in tables['player_summary'] if r['player_id']==p['player_id'] and r['period']=='All')
        peak,detail=peak_detail(p,old['peak_28d_date'],'OLD_PERSONAL_ALL','Total',days[p['player_id']],apps,matches,aff_by[p['player_id']],config);peaks.append(peak);peak_matches.extend(detail)
        for s in [r for r in stats if r['player_id']==p['player_id']]:
            peak,detail=peak_detail(p,s['peak_28d_date'],s['period_id'],s['load_scope'],days[p['player_id']],apps,matches,aff_by[p['player_id']],config)
            if peak:peaks.append(peak);peak_matches.extend(detail)
    out=safe(ROOT/config['outputs']);stage=safe(out.with_name(out.name+'.building'))
    if stage.exists():shutil.rmtree(stage)
    stage.mkdir(parents=True)
    outputs={'period_summary':period_summaries,'half_rosters':rosters,'half_match_squad_rosters':squad_rosters,'team_half_membership_intervals':memberships,'playing_affiliation_evidence':affiliations,'match_squads':squad,'squad_completeness':coverage,'comparison_stats':stats,'streak_records':[{**r,'match_ids':json.dumps(r['match_ids'])} for r in allruns],'audit_peak_windows':peaks,'audit_peak_window_appearances':peak_matches,'audit_psg_match_squads':audit_squads,'affiliation_conflicts':unresolved,'invalid_career_team_entries':invalid}
    for name,rows in outputs.items():write_csv(stage/(name+'.csv'),rows or [],None if rows else ['no_records'])
    if plots:render_comparison(stats,period_summaries,config,stage/'figures')
    consistency=verify_unchanged(baseline);write_csv(stage/'personal_data_consistency.csv',consistency)
    write_json(stage/'consistency_report.json',dict(passed=True,identical_files=len(consistency),personal_outputs_files=len(baseline['files']),protected_core_sources=len(baseline.get('protected_core_sources',{})),personal_matches_changed=False,prematch_load_changed=False,daily_load_changed=False,check='Byte-for-byte SHA256 before/after, including both SQLite copies and all pre-existing CSVs/figures/manifests'))
    write_json(stage/'manifest.json',dict(built_at=datetime.now(UTC).isoformat(),as_of=config['as_of'],config=config,periods=period_summaries,affiliation_conflicts=len(unresolved),personal_data_unchanged=True,notes=['Final playing-affiliation definition supersedes pure match-squad union as default; both are exported separately.','Career playing spells are source-reported, not formal registration; explicit external loans take precedence.','No personal rolling load has been recomputed.','Completeness is source-structural; independent official match-squad census not certified.'],exports={str(p.relative_to(stage)):checksum(p.read_bytes()) for p in stage.rglob('*') if p.is_file()}))
    report(stage,period_summaries,rosters,squad_rosters,peaks,config,unresolved,consistency)
    generated_manifest=read_json(stage/'manifest.json');generated_manifest['exports']['验收报告.md']=checksum((stage/'验收报告.md').read_bytes());write_json(stage/'manifest.json',generated_manifest)
    if out.exists():
        backup=safe(ROOT/'archive/previous_team_comparison')
        if backup.exists():shutil.rmtree(backup)
        backup.parent.mkdir(parents=True,exist_ok=True);out.rename(backup)
    stage.rename(out)
    print(json.dumps(dict(output=str(out),periods=period_summaries,personal_data_unchanged=True,affiliation_conflicts=len(unresolved)),ensure_ascii=False,indent=2))
    return outputs

def render_comparison(stats,periods,config,out):
    os.environ['MPLCONFIGDIR']=str(ROOT/'.cache/matplotlib');os.environ['TMPDIR']=str(ROOT/'.tmp')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    out.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.size':9,'axes.spines.top':False,'axes.spines.right':False})
    for period in periods:
        for scope in ['Total','Club']:
            rr=sorted([r for r in stats if r['period_id']==period['period_id'] and r['load_scope']==scope],key=lambda r:r['load_28d_max'] if r['load_28d_max'] is not None else -1)
            if not rr:continue
            fig,axes=plt.subplots(1,3,figsize=(18,max(7,len(rr)*.26)),sharey=True)
            for ax,col,title in zip(axes,['load_28d_max','longest_starts_within_half','longest_75min_within_half'],['Max 28-day minutes on eligible dates','PSG consecutive starts within half','PSG consecutive >=75 min within half']):
                ax.barh(range(len(rr)),[r[col] if r[col] is not None else np.nan for r in rr],color=['#267e9d' if r['target_team_appearances'] else '#abbcc4' for r in rr]);ax.set_title(title);ax.grid(axis='x',alpha=.16)
                for i,r in enumerate(rr):
                    if r[col] is None:ax.text(0,i,'NA',va='center',fontsize=8)
            axes[0].set_yticks(range(len(rr)),[r['player_name'] for r in rr],fontsize=8)
            fig.suptitle(f"{config['team_name']} | {period['season']} {period['half']} | {scope} Load | data through {config['as_of']}",fontsize=13,y=.995)
            fig.text(.5,.955,f"Observation dates: {period['start']} to {period['last_observation_date']} AND playing for PSG | cohort: dated playing affiliations; external loans excluded",ha='center',fontsize=9)
            fig.text(.5,.012,'Rolling windows retain earlier clubs and national teams (Total) / all clubs (Club). Gray: no PSG appearance in this half; not evidence of injury or rest.\nSource-reported playing dates; academy players need PSG match-squad evidence. Full cross-half runs are preserved separately in CSV.',ha='center',fontsize=8)
            fig.tight_layout(rect=(0,.055,1,.93))
            name=f"{period['period_id']}_{scope}";fig.savefig(out/(name+'.png'),dpi=150);fig.savefig(out/(name+'.svg'));plt.close(fig)

def report(out,periods,rosters,squad_rosters,peaks,config,conflicts,consistency):
    lines=['# PSG 半赛季队列与比较验收','',f"数据截止 {config['as_of']}，Europe/Madrid。个人数据库、全部原有输出及原处理模块 SHA-256 均未改变（{len(consistency)} 项一致）。",'',
    '默认按文档末尾的实际效力关系定义队列；为同时交付原先要求的比赛大名单并集，分别输出两种名单和人数。队列成员不表示每场比赛可用；比较只取该球员实际效力 PSG 的观测日期。外租全程归租入队；租入 PSG 同正式加盟。原俱乐部和国家队分钟仍保留在既有滚动值中。','',
    '半赛季以冬歇期间的 1 月 1 日划界，不等到冬窗关闭。2025 冬窗关闭 2 月 3 日、2026 冬窗关闭 2 月 2 日另存配置；阶段内部继续按个人转会/租借日期切分。2025 世俱杯至 7 月 13 日整体放入 2024/25 H2，2025/26 H1 从 7 月 14 日开始，避免同届赛事被 7 月 1 日截断。','',
    '| 阶段 | PSG正式比赛 | 大名单结构完整率 | 比赛大名单并集人数 | 实际效力队列人数 | 其中本期入过大名单 |','| --- | ---: | ---: | ---: | ---: | ---: |']
    for p in periods:lines.append(f"| {p['season']} {p['half']} | {p['target_matches']} | {p['squad_fetch_completeness']:.1%} | {p['match_squad_cohort_players']} | {p['playing_affiliation_cohort_players']} | {p['squad_players_with_playing_days']} |")
    lines+=['','完整率定义：目标队一侧有 11 名首发、明确替补数组、球员 ID 不重复。它证明 FotMob 数据结构完整，不是对全部官方比赛名单的独立认证。Unavailable 不进入比赛大名单。','',
    '## Škriniar 与 Moscardo','']
    for fid in config['audit_player_ids']:
        old=next(r for r in peaks if r['fotmob_player_id']==fid and r['audit_origin']=='OLD_PERSONAL_ALL')
        lines += [f"### {old['player_name']}",'',f"旧全期 Total 28 天峰值：**{old['existing_rolling_minutes']} 分钟，{old['peak_date']}**。窗口右边界 {old['window_end_exclusive']}；峰值日实际效力球队为 **{old['playing_club_at_peak']}**，是否处于 PSG 实际效力期：**{old['peak_during_target_affiliation']}**。原值原样保留，不当作 PSG 峰值。",'',
        '逐半赛季成员情况：','']
        for period in periods:
            rr=next((r for r in rosters if r['fotmob_player_id']==fid and r['period_id']==period['period_id']),None);sr=next((r for r in squad_rosters if r['fotmob_player_id']==fid and r['period_id']==period['period_id']),None)
            lines.append(f"- {period['season']} {period['half']}：PSG 比赛大名单 {sr['squad_matches'] if sr else 0} 场；"+(f"实际效力观测片段 {'；'.join(a+' 至 '+b for a,b in json.loads(rr['observation_intervals']))}，共 {rr['playing_days']} 日（不把片段间外租日期算入）。" if rr else '无可确认的 PSG 观测日期，排除本阶段比较。'))
        details=[r for r in read_csv(out/'audit_peak_window_appearances.csv') if r['audit_id']==old['audit_id']]
        lines += ['', '旧峰值窗口内的实际出场（来自未修改的个人记录）：','', '| 日期（马德里） | 所属队伍 | 对阵 | 分钟 |','| --- | --- | --- | ---: |']
        for r in details:lines.append(f"| {r['date_madrid']} | {r['team_name']} | {r['home_team']} – {r['away_team']} | {r['minutes_played']} |")
        lines+=['']
    lines+=['Škriniar：TFF 官方贷款合同起日为 2025-01-29，PSG 对外公告为 1 月 30 日、许可证签发 1 月 31 日，本次按官方生效合同起日排除 PSG 观测日期，并保留一日来源差异。2025-07-01 至 7 月 30 日的回归由 FotMob 生涯转会记录报告，标为 source-reported，不冒充正式注册或已参加 PSG 比赛。',
    'Moscardo：Reims 外租起于 2024-08-21；FotMob 报告 2025-06-10 提前归队，PSG 官方世俱杯名单搜索记录可佐证其回归参赛队伍。缓存明确记录其进入 PSG 2025-06-23、2025-07-13 比赛替补席；因此保留 2024/25 H2，不能因其 2 月峰值属于其他球队就永久删除。2025-07-19 后在 Braga、2026-07-08 后在 Espanyol 的外租期间不属于 PSG 观测区间。具体日期置信度逐条列在 playing_affiliation_evidence.csv。','',
    '## 交付及限制','',
    '- `half_rosters.csv`：默认实际效力队列；`half_match_squad_rosters.csv`：严格的逐场首发与替补并集。',
    '- `team_half_membership_intervals.csv`：每人每阶段的有效观测片段和来源，可出现离队/回归产生的多个片段。',
    '- `match_squads.csv`、`squad_completeness.csv`：逐场可追溯名单与完整性；`comparison_stats.csv`：分阶段 Total/Club 统计。',
    '- `audit_psg_match_squads.csv`：两名重点核查球员进入 PSG 名单的全部已抓场次；`audit_peak_windows.csv`、`audit_peak_window_appearances.csv`：旧峰值及修正后各期峰值的边界、效力球队和逐场分钟，可直接对账。',
    '- `streak_records.csv`：完整已观察连续记录不按半赛季切断；阶段统计另列 within_half 与 full_observed_overlapping_half，不相加。',
    '- `figures/`：五个半赛季各一张 Total 和 Club 图，每张标明球队、赛季、半赛季、截止日、口径与队列规则。',
    '- `personal_data_consistency.csv` / `consistency_report.json`：逐文件字节级不变证明，含两个 SQLite 副本。','',
    f"发现 {len(conflicts)} 条比赛名单与生涯实际效力关系不一致/未知的来源记录。保留在 affiliation_conflicts.csv；没有用首次上名单日期补归属，也没有静默修改个人数据。学院球员仅在本半赛季入过 PSG 大名单时，使用独立的学院归属日期作为效力证据，不以首次一队名单日期作为生效日。归属仍来自来源报告的转会日期，未全部独立核验为官方注册。",'',
    '2024/25 H1 原始名单并集有一个来源身份冲突（Younes El Hannach），保留在源名单但不纳入默认 PSG 统计。FFF 官方生涯显示该 ID 对应的 2004-06-04 出生球员自 2023 年起效力卡塔尔。不得将他静默替换成另一名球员；详见 docs/队列修正独立数据问题.md。', '',
    '旧的 `outputs/player_load/figures/02_load_comparison` 保留作不变的个人全期历史结果，**不再作为 PSG 默认球队比较图**。本目录按阶段筛选后的图表是当前比较入口。','',
    '来源：',
    '- [TFF Škriniar 合同记录](https://www.tff.org/Default.aspx?kisiID=2766836&pageId=526)',
    '- [PSG Škriniar 租借公告搜索记录](https://en.psg.fr/teams/first-team/content/milan-skriniar-paris-saint-germain-loaned-to-fenerbahce-sk-until-the-end-of-the-season-2024-2025)',
    '- [PSG 世俱杯名单搜索记录（旧 URL 当前跳转首页）](https://en.psg.fr/teams/first-team/content/the-paris-squad-for-the-club-world-cup-in-the-united-states-paris-saint-germain-20242025)',
    '- [PSG Moscardo 租借 Braga](https://www.psg.fr/es/content/gabriel-moscardo-es-cedido-al-sporting-clube-de-braga)',
    '- [Espanyol Moscardo 租借公告](https://www.rcdespanyol.com/en/new/gabriel-moscardo-joins-rcd-espanyol/20450)','']
    for p in periods:
        lines += [f"## {p['season']} {p['half']} 名单",'', '**比赛大名单并集：** '+ '、'.join(r['player_name'] for r in squad_rosters if r['period_id']==p['period_id']), '', '**默认实际效力队列：** '+ '、'.join(r['player_name'] for r in rosters if r['period_id']==p['period_id']), '']
    (out/'验收报告.md').write_text('\n'.join(lines),encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='config/team_comparison.json');parser.add_argument('--no-plots',action='store_true');args=parser.parse_args()
    try:compare(args.config,plots=not args.no_plots)
    except DataError as exc:raise SystemExit(str(exc))
