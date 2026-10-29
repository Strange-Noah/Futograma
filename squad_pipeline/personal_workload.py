"""Independent canonical player-match facts and calendar-day personal workload."""
import json,sqlite3,argparse
from pathlib import Path
from collections import defaultdict,Counter
from datetime import date,timedelta
from zoneinfo import ZoneInfo
from .core import BASE,ROOT,within,read,write,table,digest
from .player_history import Context,cached_history,raw_observations
from futbol.common import instant
DB=BASE/'state/player_workload.sqlite'

def connect(path=DB):
    path=within(path);path.parent.mkdir(exist_ok=True,parents=True);db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript((BASE/'workload_schema.sql').read_text());return db

def artifact(db,e):
    db.execute('INSERT OR IGNORE INTO workload_artifacts VALUES(?,?,?,?,?,?)',tuple(e[k] for k in ['artifact_id','path','sha256','kind','source_url','acquired_at']))

def discovery_id(o):return digest(json.dumps([o['player_id'],o['match_id'],o['evidence']['artifact_id'],o['evidence']['json_path']]).encode())
def order(o):return (0 if o['evidence']['kind']=='MATCH_DETAIL' else 1 if o['authoritative_minutes'] else 2,o['evidence']['path'],o['evidence']['json_path'])

def resolve(observations,context):
    """Multiple sources collapse to one fact; conflicting claims become NULL, never arbitrary sums."""
    observations=sorted(observations,key=order);trusted=[o for o in observations if o['authoritative_minutes']];claims=trusted or observations;base=claims[0];conflicts=[]
    if any(o['rejected'] for o in claims):conflicts+=['REJECTED_SOURCE: '+x for o in claims for x in o['rejected']]
    parent=base['competition_id'];allowed={parent}|{o['source_competition_id'] for o in claims if o['evidence']['kind']=='MATCH_DETAIL' and o['competition_id']==parent}
    for o in claims:
        if (o['team_id'],o['opponent_team_id'])!=(base['team_id'],base['opponent_team_id']):conflicts.append('REPRESENTED_TEAM_OR_OPPONENT_CONFLICT')
        if o['kickoff'] and base['kickoff'] and instant(o['kickoff'])!=instant(base['kickoff']):conflicts.append('KICKOFF_CONFLICT')
        if o['competition_id'] not in allowed:conflicts.append('COMPETITION_CONFLICT')
    eligibility,category,reason,teamtype,basis=context.classify(parent,base['team_id'],base['competition_name'])
    if not base['kickoff']:conflicts.append('MISSING_KICKOFF')
    if conflicts:eligibility='REVIEW';reason='METADATA_OR_SOURCE_CONFLICT'
    values={o['minutes'] for o in trusted if not o['rejected'] and o['minutes'] is not None};played={o['played'] for o in trusted if not o['rejected'] and o['played'] is not None}
    if len(values)>1:conflicts.append('CONFLICTING_SOURCE_MINUTES')
    if len(played)>1:conflicts.append('CONFLICTING_PARTICIPATION')
    if conflicts:minutes=None;status='CONFLICT';ms='CONFLICT';verification='REVIEW_REQUIRED'
    else:
        minutes=next(iter(values)) if values else None;p=next(iter(played)) if played else None
        if p is True:status='CONFIRMED_APPEARANCE';ms='SOURCE_REPORTED_ZERO' if minutes==0 else ('SOURCE_REPORTED_MINUTES' if minutes is not None else 'MISSING_MINUTES')
        elif p is False:
            status='CONFIRMED_NONAPPEARANCE';ms='SOURCE_REPORTED_DNP_ZERO' if minutes==0 else 'EXPLICIT_DNP_ZERO';minutes=0
        else:status='UNKNOWN_APPEARANCE';ms='UNKNOWN_NOT_ZERO';minutes=None
        verification='SOURCE_CONSISTENT' if status!='UNKNOWN_APPEARANCE' and minutes is not None else 'INSUFFICIENT_EVIDENCE'
    minute_source=next((o for o in claims if o['authoritative_minutes'] and not o['rejected'] and (o['played'] is False if status=='CONFIRMED_NONAPPEARANCE' else o['minutes']==minutes)),base);ev=base['evidence'];me=minute_source['evidence'];day=instant(base['kickoff']).astimezone(ZoneInfo('Europe/Madrid')).date().isoformat() if base['kickoff'] else None
    idx=dict(player_id=base['player_id'],match_id=base['match_id'],match_date=day,kickoff=base['kickoff'],competition_id=parent,competition_name=base['competition_name'],competition_category=category,represented_team_id=base['team_id'],represented_team_type=teamtype,opponent_team_id=base['opponent_team_id'],match_status=base['match_status'],eligibility_status=eligibility,eligibility_reason=reason+'; '+','.join(basis),discovery_source=ev['kind'],source_artifact_id=ev['artifact_id'],source_hash=ev['sha256'],metadata_status='CONFLICT' if any('CONFLICT' in c or 'REJECTED_SOURCE' in c for c in conflicts) else 'CONSISTENT_AVAILABLE_SOURCES')
    app=dict(player_id=base['player_id'],match_id=base['match_id'],played_status=status,minutes=minutes,minutes_status=ms,minutes_source=me['kind'] if minutes is not None else None,source_artifact_id=me['artifact_id'],source_hash=me['sha256'],verification_status=verification,detail_status='DETAIL_PRESENT' if any(o['evidence']['kind']=='MATCH_DETAIL' for o in observations) else 'DETAIL_MISSING_HISTORY_MAY_REPORT_MINUTES',conflicts_json=json.dumps(sorted(set(conflicts))),evidence_ids_json=json.dumps(sorted(discovery_id(o) for o in observations)))
    return idx,app

def upsert(db,t,row,keys):
    columns=list(row);updates=[k+'=excluded.'+k for k in columns if k not in keys]
    db.execute('INSERT INTO '+t+'('+','.join(columns)+') VALUES('+','.join('?' for k in columns)+') ON CONFLICT('+','.join(keys)+') DO UPDATE SET '+','.join(updates),tuple(row.values()))

def build_cache(player_ids,path=DB):
    ids=sorted(set(int(p) for p in player_ids))
    if not ids or any(p<=0 for p in ids):raise ValueError('Positive player IDs required')
    context=Context();raw=raw_observations(ids);db=connect(path);summaries=[]
    with db:
        for e in context.artifacts.values():artifact(db,e)
        for typ,groups in [('TEAM_TYPE',context.teams),('COMPETITION_TYPE',context.competitions),('COMPETITION_METADATA',context.catalogs)]:
            for eid,claims in sorted(groups.items()):
                for claim in claims:
                    e=claim['artifact'];attrs={k:v for k,v in claim.items() if k not in ('artifact','json_path')};cid=digest(json.dumps([typ,eid,e['artifact_id'],claim['json_path']],sort_keys=True).encode());db.execute('INSERT OR IGNORE INTO workload_context VALUES(?,?,?,?,?,?)',(cid,typ,eid,json.dumps(attrs,sort_keys=True,ensure_ascii=False),e['artifact_id'],claim['json_path']))
        for pid in ids:
            profile,history,coverage=cached_history(pid);obs=history+raw.get(pid,[]);name=profile['name'] if profile else next((o.get('player_name') for o in raw.get(pid,[]) if o.get('player_name')),None)
            db.execute('INSERT INTO workload_players VALUES(?,?) ON CONFLICT(player_id) DO UPDATE SET display_name=COALESCE(workload_players.display_name,excluded.display_name)',(pid,name))
            for o in obs:
                ev=o['evidence'];artifact(db,ev);did=discovery_id(o);db.execute('INSERT OR IGNORE INTO player_match_discoveries VALUES(?,?,?,?,?,?,?,?)',(did,pid,o['match_id'],ev['kind'],ev['artifact_id'],ev['sha256'],ev['json_path'],json.dumps(o,ensure_ascii=False,sort_keys=True)))
            # Preserve immutable old source versions and flag contradictions rather than discarding history.
            grouped=defaultdict(list)
            for r in db.execute('SELECT observation_json FROM player_match_discoveries WHERE player_id=? ORDER BY discovery_id',(pid,)):grouped[json.loads(r[0])['match_id']].append(json.loads(r[0]))
            for mid,observations in sorted(grouped.items()):
                idx,app=resolve(observations,context);upsert(db,'player_match_index',idx,['player_id','match_id']);upsert(db,'player_match_appearances',app,['player_id','match_id'])
            upsert(db,'player_history_coverage',dict(player_id=pid,**coverage),['player_id'])
            summaries.append(dict(player_id=pid,name=name,discoveries=sum(len(o) for o in grouped.values()),canonical_matches=len(grouped),profile_history_present=bool(profile),coverage_status=coverage['coverage_status']))
        if list(db.execute('PRAGMA foreign_key_check')):raise ValueError('Workload foreign key violation')
    db.close();return summaries

def get_workloads(player_ids,as_of,windows=(7,14,28),path=DB):
    """No club, season, half-season or roster parameter is accepted."""
    date.fromisoformat(as_of);ids=sorted(set(int(p) for p in player_ids))
    if any(p<=0 for p in ids) or any(w not in (7,14,28) for w in windows):raise ValueError('Invalid player ID or window')
    path=within(path);db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) if path.exists() else None
    if db:db.row_factory=sqlite3.Row
    result=[]
    for pid in ids:
        coverage=dict(db.execute('SELECT * FROM player_history_coverage WHERE player_id=?',(pid,)).fetchone() or {}) if db else {}
        for w in windows:
            lower=(date.fromisoformat(as_of)-timedelta(days=w)).isoformat();rows=[dict(r) for r in db.execute('SELECT * FROM personal_match_facts WHERE player_id=? AND match_date>? AND match_date<=? ORDER BY match_date,match_id',(pid,lower,as_of))] if db else []
            eligible=[r for r in rows if r['eligibility_status']=='INCLUDE'];review=[r for r in rows if r['eligibility_status']=='REVIEW'];valid=[r for r in eligible if r['verification_status'] in ('SOURCE_CONSISTENT','MANUAL_ACCEPTED') and r['minutes'] is not None];missing=[r for r in eligible if r not in valid];breakdown=defaultdict(float)
            for r in valid:breakdown[r['competition_category']]+=r['minutes']
            certified=bool(coverage.get('coverage_certified') and coverage.get('certified_start_date') and coverage.get('certified_end_date') and coverage['certified_start_date']<=lower and coverage['certified_end_date']>=as_of)
            complete=certified and not missing and not review;known=sum(r['minutes'] for r in valid)
            result.append(dict(player_id=pid,as_of=as_of,window_days=w,lower_exclusive=lower,upper_inclusive=as_of,timezone='Europe/Madrid',known_minutes=known,complete_minutes=known if complete else None,coverage_status='COMPLETE' if complete else ('INCOMPLETE' if missing or review else 'UNKNOWN'),coverage_certified=certified,known_category_minutes=dict(breakdown),eligible_match_ids=[r['match_id'] for r in eligible],counted_match_ids=[r['match_id'] for r in valid],uncertain_match_ids=[r['match_id'] for r in missing+review],excluded_match_ids=[r['match_id'] for r in rows if r['eligibility_status']=='EXCLUDE'],missing_or_uncertain_minute_records=len(missing),review_eligibility_records=len(review),detail_missing_records=sum(r['detail_status'].startswith('DETAIL_MISSING') for r in eligible),unobserved_matches_possible=not certified,history_status=coverage.get('coverage_status','NOT_BUILT_NO_HISTORY_EVIDENCE')))
    if db:db.close()
    return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['build','query']);ap.add_argument('--player-ids',required=True);ap.add_argument('--as-of',default='2026-10-27');args=ap.parse_args();ids=list(map(int,args.player_ids.split(',')));result=build_cache(ids) if args.command=='build' else get_workloads(ids,args.as_of);print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
