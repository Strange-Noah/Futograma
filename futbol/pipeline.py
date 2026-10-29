from __future__ import annotations
import os,json,sqlite3,shutil
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .common import *
from .collection import FotMob,fetch_players,profile_teams
from .discovery import collect_catalogs,collect_club_details
from .normalization import normalize,player_key
from .roster import cohort_ids,roster_audit
from .analytics import LoadIndex,prematch_table,daily_table,ucl_rotation,national_returns
from .aggregation import summary_table,attendance_table
from .storage import validate,export
from .reports import team_summary,stage_summary,acceptance

def configuration(path='config/player_load.json',as_of=None):
    c=read_json(ROOT/path);c['research']['as_of']=as_of or c['research'].get('as_of') or datetime.now(ZoneInfo(c['research']['timezone'])).date().isoformat()
    if c['research']['as_of']<c['research']['start']:raise DataError('as-of precedes research start')
    return c

def bounds(c):return day_start(c['research']['start'],c['research']['timezone'])-timedelta(days=max(c['windows_days'])),day_end(c['research']['as_of'],c['research']['timezone'])

def cached_inputs(c):
    profiles={};histories={}
    for pid in cohort_ids(c):
        pp=ROOT/f'raw/player_load/profiles/{pid}.json';hp=ROOT/f'data/collected_histories/{pid}.json'
        if not pp.exists() or not hp.exists():raise DataError(f'Missing collected player {pid}; run collect first')
        profiles[pid]=read_json(pp);histories[pid]=read_json(hp)
    return profiles,histories,read_json(ROOT/'data/club_national_schedule.json')

def collect(c,offline=False,refresh=False):
    client=FotMob(c,offline=offline,refresh=refresh);lo,hi=bounds(c)
    initial=fetch_players(client,cohort_ids(c),lo,hi)
    profiles={pid:v[0] for pid,v in initial.items()}
    if not profiles:raise DataError('No seed profiles; import source-verified IDs into registration_evidence.csv')
    schedule=collect_catalogs(client,profiles,lo,hi,c['club']['fotmob_team_id'])
    _,candidates=collect_club_details(client,schedule,c['club']['fotmob_team_id'],hi)
    extra=fetch_players(client,set(candidates)-set(initial),lo,hi);initial.update(extra)
    if extra:schedule=collect_catalogs(client,{pid:v[0] for pid,v in initial.items()},lo,hi,c['club']['fotmob_team_id'])
    needed={int(m['id']) for m in schedule['matches']}
    for profile,hist in initial.values():
        clubs,nts=profile_teams(profile)
        for r in hist['records']:
            national=int(r['teamId']) in nts
            friendly=int(r['leagueId']) in c['exclude_club_friendly_ids'] or ('friendly' in r['leagueName'].lower() and not national) or (national and int(r['leagueId']) in c['national_friendly_competition_ids'] and not c['include_national_friendlies'])
            if not friendly:needed.add(int(r['id']))
    cutoff=hi-timedelta(days=c['update']['revisit_days']);errors=[]
    # Threaded requests share a global start-rate limiter; bounded concurrency.
    from concurrent.futures import ThreadPoolExecutor,as_completed
    def fetch(mid):
        existing=None
        try:
            from .normalization import load_detail
            existing=load_detail(mid)
            mutable=bool(existing and instant(existing['general']['matchTimeUTCDate'])>=cutoff)
            return client.match(mid,mutable=mutable)
        except Exception as exc:return {'error':str(exc),'id':mid}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for i,future in enumerate(as_completed([pool.submit(fetch,mid) for mid in sorted(needed)]),1):
            result=future.result()
            if result.get('error'):errors.append(result)
            if i%40==0 or i==len(needed):print(f'DETAILS {i}/{len(needed)} errors={len(errors)}',flush=True)
    report=dict(as_of=c['research']['as_of'],players=len(initial),details_requested=len(needed),detail_errors=errors,catalog_errors=schedule['errors'],player_errors=client.errors,sources=client.sources)
    write_json(ROOT/'data/collection_report.json',report)
    if errors or schedule['errors'] or client.errors:raise DataError('Collection incomplete; see data/collection_report.json. Last successful exports preserved.')
    return report

def build(c,plots=True):
    profiles,histories,schedule=cached_inputs(c)
    lo,hi=bounds(c)
    for pid,h in histories.items():
        if instant(h['requested_start'])>lo or instant(h['requested_end_exclusive'])<hi:
            raise DataError(f'Cached history {pid} does not cover requested interval; run collect')
        # Histories may have been collected for a longer study; clip consistently.
        h['records']=[r for r in h['records'] if lo<=instant(r['matchDate']['utcTime'])<hi]
    schedule['matches']=[r for r in schedule['matches'] if lo<=instant(r['status']['utcTime'])<hi]
    d=normalize(profiles,histories,schedule,c);audit=d.pop('audit');hist_by_id={player_key(pid):h for pid,h in histories.items()}
    index=LoadIndex(d['player_appearances'],d['matches'],hist_by_id,c)
    print('NORMALIZED', {k:len(v) for k,v in d.items()},flush=True)
    d['prematch_load']=prematch_table(d,index,c);d['daily_load']=daily_table(d,index,c)
    d['player_summary']=summary_table(d,d['daily_load'],c);d['ucl_rotation']=ucl_rotation(d,d['prematch_load'],c)
    d['national_returns']=national_returns(d,d['prematch_load'],c);d['attendance']=attendance_table(d,c)
    d['team_summary']=team_summary(d,c);d['ucl_stage_summary']=stage_summary(d['ucl_rotation'])
    checks=validate(d,c)
    if not checks['passed']:raise DataError(str(checks))
    accepted=acceptance(d,hist_by_id,index,c)
    if not accepted['passed_computation_checks']:raise DataError('Focus computation acceptance failed: '+str(accepted))
    phase=roster_audit(c,d['registrations']);out=safe(ROOT/c['outputs']);stage=safe(ROOT/(c['outputs']+'.building'))
    if stage.exists():shutil.rmtree(stage)
    stage.mkdir(parents=True)
    phase_complete=bool(phase) and all(p['cohort_complete'] or p['status']=='not_due' for p in phase)
    if phase_complete:accepted['official_registration_acceptance']='Phase-specific lists verified against imported complete-source counts'
    metadata=dict(schema_version=c['schema_version'],built_at=datetime.now(UTC).isoformat(),as_of=c['research']['as_of'],timezone=c['research']['timezone'],config=c,validation=checks,status='ROSTER_PHASES_VERIFIED' if phase_complete else 'PROVISIONAL_ROSTER_EVIDENCE_INCOMPLETE',source_catalog_errors=schedule['errors'],phase_roster_complete=phase_complete)
    export(d,stage,stage/'player_load.sqlite',metadata)
    write_csv(stage/'registration_phase_audit.csv',phase)
    write_json(stage/'acceptance_report.json',accepted);write_json(stage/'data_quality.json',dict(normalization=audit,catalog_errors=schedule['errors'],catalog_identity_conflicts=schedule.get('fixture_identity_conflicts',[]),season_label_differences=schedule.get('season_label_differences',[]),history_coverage=[dict(player_id=player_key(pid),fotmob_player_id=pid,source_history_reached_start=h.get('source_history_reached_start'),pages=h.get('pages'),requested_start=h.get('requested_start'),requested_end_exclusive=h.get('requested_end_exclusive'),errors=h.get('errors')) for pid,h in histories.items()],invalid_source_team_ids='Nonpositive source team IDs are not merged into a shared team; original career entries retained in raw profiles.'))
    if plots:
        from .visualization import render
        render(d,c,stage/'figures')
    metadata['exports']={str(p.relative_to(stage)):checksum(p.read_bytes()) for p in sorted(stage.rglob('*')) if p.is_file() and p.name!='manifest.json'}
    write_json(stage/'manifest.json',metadata)
    backup=safe(ROOT/'archive/previous_player_load')
    if out.exists():
        if backup.exists():shutil.rmtree(backup)
        backup.parent.mkdir(parents=True,exist_ok=True);out.rename(backup)
    stage.rename(out)
    database=safe(ROOT/c['database']);database.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(out/'player_load.sqlite',database.with_suffix('.sqlite.copying'));database.with_suffix('.sqlite.copying').replace(database)
    print(json.dumps(dict(output=str(out),database=c['database'],validation=checks,acceptance=accepted),ensure_ascii=False,indent=2),flush=True)
    return d


def verify_exports(c):
    out=safe(ROOT/c['outputs']);manifest=read_json(out/'manifest.json');failures=[]
    for name,sha in manifest['exports'].items():
        path=safe(out/name)
        if not path.exists() or checksum(path.read_bytes())!=sha:failures.append(name)
    con=sqlite3.connect('file:'+str(safe(ROOT/c['database']))+'?mode=ro',uri=True)
    result=con.execute('PRAGMA integrity_check').fetchone()[0];fk=con.execute('PRAGMA foreign_key_check').fetchall();con.close()
    if result!='ok' or fk:failures.append('database integrity')
    if checksum((out/'player_load.sqlite').read_bytes())!=checksum((ROOT/c['database']).read_bytes()):failures.append('database/export version mismatch')
    if failures:raise DataError('Export validation failed: '+str(failures))
    print(json.dumps(dict(passed=True,as_of=manifest['as_of'],rows=manifest['validation']['rows'],status=manifest['status']),ensure_ascii=False,indent=2))
