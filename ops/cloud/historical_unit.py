"""Resume retained-source work for one historical unit using SourceService.

This command owns archive replay checkpoints and observed-union evaluation, not
source collection, canonical adjudication, or production. Missing downstream
routing remains explicit; a successful process exit is not period acceptance.
"""
import argparse
from contextlib import contextmanager
from collections import Counter
from dataclasses import asdict
from datetime import datetime
import fcntl
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from squad_pipeline.core import ROOT
from squad_pipeline.canonical_fixture import digest, instant
from squad_pipeline.quality_fixture import export_accepted_catalogue
from squad_pipeline.roster_initialization import PeriodUnit, initialize
from squad_pipeline.identity_registry import lookup
from squad_pipeline.source_interface import EvidenceStore, SourceService, SourceRequest, RawSourceArtifact
from squad_pipeline.source_parsing import PARSER_VERSION


def within(path):
    p=Path(path).resolve()
    if not p.is_relative_to(ROOT.resolve()):
        raise ValueError('AUTHORIZED_PROJECT_BOUNDARY_REQUIRED')
    return p


def artifact(record):
    row=dict(record.get('artifact',record));request=dict(row.pop('request'))
    request['parameters']=tuple(tuple(x) for x in request.get('parameters',()))
    if request.get('policy','ARCHIVE_ONLY')!='ARCHIVE_ONLY':
        raise ValueError('ARCHIVE_ONLY_COMMAND_NO_NEW_COLLECTION')
    return RawSourceArtifact(request=SourceRequest(**request),**row)


@contextmanager
def checkpoint(work,unit):
    work=within(work)
    if not work.is_relative_to((ROOT/'.cloud').resolve()):
        raise ValueError('ISOLATED_CLOUD_WORKSPACE_REQUIRED')
    work.mkdir(parents=True,exist_ok=True)
    with (work/'unit.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db=sqlite3.connect(work/'tasks.sqlite')
        try:
            db.execute('CREATE TABLE IF NOT EXISTS scope(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS replay_tasks(key TEXT PRIMARY KEY,artifact_json TEXT NOT NULL,result_json TEXT NOT NULL)')
            old=db.execute("SELECT value FROM scope WHERE key='unit'").fetchone()
            encoded=json.dumps(asdict(unit),sort_keys=True)
            if old and old[0]!=encoded:raise ValueError('WORKSPACE_BOUND_TO_DIFFERENT_UNIT')
            db.execute("INSERT OR IGNORE INTO scope VALUES('unit',?)",(encoded,));db.commit()
            yield db
        finally:db.close()


def replay(db,service,records,max_tasks):
    if type(max_tasks) is not int or max_tasks<0:raise ValueError('NONNEGATIVE_TASK_BUDGET_REQUIRED')
    candidates={}
    for record in records:
        a=artifact(record)
        key=digest(dict(artifact=asdict(a),parser=PARSER_VERSION))
        candidates[key]=a
    outcomes=[];processed=0;remaining=0
    for key,a in sorted(candidates.items()):
        old=db.execute('SELECT result_json FROM replay_tasks WHERE key=?',(key,)).fetchone()
        if old:
            # Never trust a saved parse when the retained body has disappeared or changed.
            acquired=service.acquire(a.request,a)
            if acquired.outcome=='FOUND':
                outcomes.append(dict(json.loads(old[0]),reused=True));continue
        if processed>=max_tasks:remaining+=1;continue
        acquired,parsed=service.observe(a.request,a)
        result=dict(artifact=asdict(a),acquisition=acquired.outcome,
            observation=asdict(parsed) if parsed else None,reused=False)
        db.execute('INSERT OR REPLACE INTO replay_tasks VALUES(?,?,?)',
            (key,json.dumps(asdict(a),sort_keys=True),json.dumps(result,sort_keys=True)))
        db.commit() # A completed task survives interruption before the next task.
        outcomes.append(result);processed+=1
    return outcomes,dict(unique_tasks=len(candidates),processed=processed,
        reused=sum(x['reused'] for x in outcomes),remaining=remaining)


def observed_union(db,unit,outcomes):
    """Use only the existing accepted fixture contract and verified native IDs.

    Consumes the already supported typed own-team squad structure; never parses
    provider HTML here, creates players, or imports another period's roster.
    """
    admitted=export_accepted_catalogue(db)['accepted']
    catalogue=[];bindings={}
    for f in admitted:
        on=datetime.fromisoformat(f['kickoff_utc'].replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Madrid')).date().isoformat()
        if not unit.start<=on<unit.end_exclusive:continue
        own='club:'+str(unit.club_id)
        if not any(str((f.get(side) or {}).get('id'))==own for side in ('home','away')):continue
        source=db.execute('SELECT evidence_json FROM canonical_fixture_sources WHERE domain=? AND provider=? AND provider_match_id=?',
            (f['domain'],f['provider'],str(f['provider_match_id']))).fetchone()
        labels=(json.loads(source[0]).get('directory_team_labels') or {}) if source else {}
        native_sides={}
        for side in ('home','away'):
            ids={str(r[0]) for r in db.execute('SELECT provider_id FROM canonical_provider_entities WHERE kind=\'TEAM\' AND provider=? AND entity_id=?',
                (f['provider'],str(f[side]['id'])))}
            # The existing accepted source-to-fixture certificate can bind the
            # native side even when no global team alias was separately stored.
            certified=(labels.get(side) or {}).get('provider_team_id')
            if certified is not None:ids.add(str(certified))
            native_sides[side]=ids
        own_ids=set().union(*(native_sides[side] for side in ('home','away') if str(f[side]['id'])==own))
        if not own_ids:continue
        key=(f['provider'],str(f['provider_match_id']))
        bindings[key]=(f,on,own_ids,native_sides)
        catalogue.append(dict(match_id=f['match_id'],on=on,state='COMPLETED' if f['status']=='FINISHED' else f['status']))
    sheets=[];holds=[]
    for result in outcomes:
        o=result['observation']
        if not o or o['category']!='match_squad':continue
        n=o['native'];fixture=n.get('fixture',{})
        kickoff=fixture.get('kickoff')
        if kickoff and not unit.start<=datetime.fromisoformat(kickoff.replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Madrid')).date().isoformat()<unit.end_exclusive:continue
        key=(o['provider'],str(fixture.get('provider_match_id')))
        binding=bindings.get(key)
        if not binding:
            holds.append(dict(reason='EXISTING_ADMITTED_FIXTURE_REQUIRED',artifact_key=o['artifact_key']));continue
        f,on,team_ids,native_sides=binding
        if not fixture.get('kickoff') or instant(fixture['kickoff'])!=instant(f['kickoff_utc']):
            holds.append(dict(reason='SOURCE_KICKOFF_BINDING_CONFLICT',artifact_key=o['artifact_key']));continue
        side_conflict=False
        for side in ('home','away'):
            native_team=(fixture.get('source_teams') or {}).get(side) or {}
            if str(native_team.get('id')) not in native_sides[side]:side_conflict=True
        if side_conflict:
            holds.append(dict(reason='ORDERED_SOURCE_TEAM_BINDING_CONFLICT',artifact_key=o['artifact_key']));continue
        raw=result['artifact'];request=raw['request']
        for s in n.get('sheets',[]):
            if s.get('configured_club_id')!=unit.club_id or str(s.get('provider_team_id')) not in team_ids:continue
            players=[]
            for p in s['players']:
                try:pid=lookup(db,unit.club_id,p)
                except ValueError:
                    pid=None # Ambiguous verified global alias remains source work.
                players.append(dict(provider_key=p['provider_key'],canonical_player_id=int(pid) if pid is not None else None,role=p['role']))
            sheets.append(dict(match_id=f['match_id'],club_id=unit.club_id,on=on,
                admission='EXISTING_VERIFIED_FIXTURE_AND_SOURCE',complete_squad=s['complete_squad'],
                source=dict(url=request['url'],sha256=raw['content_sha256']),players=players))
    # A source page or accepted fixture subset is never a complete-catalogue certificate.
    result=initialize('HISTORICAL_SQUAD_UNION',unit,catalogue=catalogue,squads=sheets,catalogue_complete=False)
    return result,holds


def run(unit,records,reference,archives,work,max_tasks=100):
    reference=within(reference);archives=[within(p) for p in archives];work=within(work)
    before=reference.read_bytes()
    with checkpoint(work,unit) as state:
        service=SourceService(EvidenceStore(work/'sources',archives=archives),'historical-unit-replay',set(),max_calls=0)
        outcomes,counts=replay(state,service,records,max_tasks)
        canonical=sqlite3.connect(reference.as_uri()+'?mode=ro',uri=True);canonical.row_factory=sqlite3.Row
        try:population,holds=observed_union(canonical,unit,outcomes)
        finally:canonical.close()
        assert reference.read_bytes()==before
        report=dict(unit=asdict(unit),status='SOURCE_WORK_INCOMPLETE' if counts['remaining'] else 'ROSTER_INCOMPLETE',
            source_tasks=counts,acquisition_outcomes=dict(Counter(r['acquisition'] for r in outcomes)),
            parser_outcomes=dict(Counter(r['observation']['outcome'] for r in outcomes if r['observation'])),source_boundary=dict(parser_calls=service.parse_calls,network_requests=service.client.calls),
            population=population,holds=holds,catalogue_scope='COMPLETENESS_NOT_ESTABLISHED',
            canonical_writes=0,reference_unchanged=True,human_pending=False,
            downstream='NOT_EXECUTED_REQUIRES_ACCEPTED_ROSTER_AND_ROUTING',
            limitations=['Archive replay and union evaluation only; not full historical lifecycle orchestration',
                'No complete fixture-catalogue certificate or new canonical fact admission is asserted'])
        temp=work/'result.tmp';temp.write_text(json.dumps(report,sort_keys=True)+'\n');temp.replace(work/'result-private.json')
        return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['club-id','season','half','start','end-exclusive','artifacts','reference','work']:
        p.add_argument('--'+name,required=True,type=int if name=='club-id' else str)
    p.add_argument('--archive',action='append',required=True);p.add_argument('--max-tasks',type=int,default=100)
    a=p.parse_args();records=json.loads(within(a.artifacts).read_text())
    result=run(PeriodUnit(a.club_id,a.season,a.half,a.start,a.end_exclusive),records,a.reference,a.archive,a.work,a.max_tasks)
    print(json.dumps({k:v for k,v in result.items() if k not in {'population','holds'}},indent=2))


if __name__=='__main__':main()
