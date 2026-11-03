"""Reproducible, offline team onboarding from captured public source responses.

The reference is copied, not modified. No curated football facts are inputs.
The same coordinator handles onboarding, historical replay and incremental replay.
"""
import argparse,json,sqlite3,hashlib,shutil,subprocess,re
from pathlib import Path
from collections import Counter
from urllib.parse import urlsplit
from datetime import datetime
from zoneinfo import ZoneInfo
from squad_pipeline.core import ROOT
from squad_pipeline import quality_pipeline as pipeline,canonical_fixture as canonical
from squad_pipeline.coldstart_membership_sources import build_observations,script,checked
from squad_pipeline.coldstart_fixture_sources import directory_fixture,parse_completed_club
from squad_pipeline.cloud.all_match_export import export_all_matches
from squad_pipeline.cloud.quality import inspect

def load_body(source):
 path=ROOT/(source.get('reused_body_path') or source['path']);body=path.read_bytes();expected=source.get('reused_body_sha256') or source['sha256']
 if hashlib.sha256(body).hexdigest()!=expected:raise ValueError('CAPTURE_HASH_MISMATCH')
 return body,dict(source,sha256=expected,provider='OfficialClub' if urlsplit(source['url']).netloc=='www.realmadrid.com' else 'FotMob')

def snapshot_rows(db):
 return {name:[tuple(r) for r in db.execute('SELECT * FROM "'+name+'"')] for name, in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}

def preserve(before,db):
 losses={};derived={}
 for name,rows in before.items():
  after=Counter(tuple(r) for r in db.execute('SELECT * FROM "'+name+'"'));remaining=Counter(rows)-after
  if name=='canonical_field_values' and remaining:
   semantic=Counter(tuple(r)[:4] for r in db.execute('SELECT * FROM canonical_field_values'));lost_semantics=Counter(r[:4] for r in rows)-semantic
   if lost_semantics:losses[name]=sum(lost_semantics.values())
   derived[name]=sum(remaining.values())
  elif remaining:losses[name]=sum(remaining.values())
 return losses,derived


def canonical_duplicates(db):
 query="SELECT c.domain,c.home_key,c.away_key,c.competition_key,k.value_json,COUNT(*) FROM canonical_fixtures c JOIN canonical_field_values k ON k.match_id=c.match_id AND k.field='kickoff' GROUP BY c.domain,c.home_key,c.away_key,c.competition_key,k.value_json HAVING COUNT(*)>1"
 return [list(r) for r in db.execute(query)]

def record_source(db,source):
 aid='quality_raw_'+source['sha256'];db.execute('INSERT OR IGNORE INTO raw_artifacts VALUES(?,?,?,?,?,?,?,?,?)',(aid,None,source.get('reused_body_path') or source.get('path'),source['sha256'],source.get('size'), 'RETAINED_PUBLIC_RESPONSE',source.get('response_timestamp'),source.get('response_timestamp'),None));return aid

def persist_fixture_row(db,packet,mid):
 f=packet['fixture'];row=packet['discovery_row'];home,away=row['home'],row['away'];kick=f.get('kickoff_utc');day=kick[:10] if kick else None;period=canonical.club_period(day)[0] if day else None;season=f['season'];half='H1' if day and int(day[5:7])>=7 else 'H2'
 if day:db.execute('INSERT OR IGNORE INTO periods VALUES(?,?,?,?,?,?)',(period,season,half,day[:4]+'-07-01' if half=='H1' else day[:4]+'-01-01',str(int(day[:4])+1)+'-01-01' if half=='H1' else day[:4]+'-07-01','Europe/Madrid'))
 db.execute('INSERT OR IGNORE INTO competitions VALUES(?,?,?,?)',(f['competition_key'],row['tournament']['name'],'COMPETITIVE',None));meta=dict(home_team=home['name'],away_team=away['name'],source_evidence=[packet['source']],competition=row['tournament']['name'])
 # Existing accepted legacy facts and historical observations are preserved.
 if not db.execute('SELECT 1 FROM matches WHERE match_id=?',(mid,)).fetchone():
  db.execute('INSERT INTO matches(match_id,home_id,away_id,kickoff,local_date,period_id,competition_id,result,scheduled_kickoff,match_state,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(mid,home['id'],away['id'],kick,day,period,f['competition_key'],str(f['home_score'])+' - '+str(f['away_score']) if f.get('home_score') is not None and f.get('away_score') is not None else None,kick,'COMPLETED' if f['status']=='FINISHED' else f['status'],canonical.encoded(meta)))

def apply_appearance(db,club_id,mid,player,source,on,mode):
 from squad_pipeline import quality_lifecycle as lifecycle
 from squad_pipeline.minute_policy import select,audit
 pid=int(player['provider_player_id']);db.execute('INSERT OR IGNORE INTO players VALUES(?,?,?)',(pid,player['name'],'VERIFIED_SOURCE_ENTITY;CROSS_PROVIDER_NOT_ASSUMED'))
 obligations=pipeline.participation_contract(player,source);source_quality=lifecycle.evaluate(obligations,list(obligations),lifecycle.fingerprint([player,source]));status=player['participation_status'];minutes=player['minutes']
 if source_quality['state']=='CONFLICTED':
  pipeline.record_source_issue(db,'CLUB_PARTICIPATION',str(mid)+':'+str(pid),'SOURCE_PARTICIPATION_CONFLICT',dict(source=source,player=player,managing_club_id=club_id),on);return dict(quality=source_quality,state='QUARANTINED',player_id=pid)
 if obligations['minutes']['status']!='SATISFIED':minutes=None
 old=db.execute('SELECT participation_status,minutes FROM player_match_minutes WHERE club_id=? AND match_id=? AND player_id=?',(club_id,mid,pid)).fetchone()
 if old and tuple(old)!=(status,minutes):
  selected=select([dict(status=old[0],minutes=old[1],provider='RETAINED_ACCEPTED'),dict(status=status,minutes=minutes,provider=source.get('provider'))])
  if selected['accepted'] and selected['selected_source']['provider']=='RETAINED_ACCEPTED':
   audit(db,'CLUB',mid,pid,selected);return dict(quality=source_quality,state='RETAINED_ACCEPTED_WITHIN_TOLERANCE',player_id=pid)
  pipeline.record_source_issue(db,'CLUB_PARTICIPATION',str(mid)+':'+str(pid),'EXISTING_PLAYER_FACT_DIFFERENCE_RETAINED',dict(source=source,player=player,retained=dict(status=old[0],minutes=old[1]),managing_club_id=club_id),on);return dict(quality=source_quality,state='RETAINED_ACCEPTED_CONFLICT',player_id=pid)
 q=pipeline.verify_participation(db,mid,player,source,on,mode);aid=record_source(db,source);played=1 if status in ('STARTER','SUBSTITUTE_USED') else 0 if status in ('UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD') else None
 db.execute('INSERT OR IGNORE INTO player_match_minutes(club_id,match_id,player_id,minutes,minutes_status,played,artifact_id,participation_status,reported_minutes,derived_minutes,method,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(club_id,mid,pid,minutes,'RESOLVED' if minutes is not None else 'UNKNOWN',played,aid,status,player.get('reported_minutes'),None,'STRICT_PROVIDER_REPORTED;UNKNOWN_NOT_ZERO',canonical.encoded(dict(source=source,source_path=player.get('source_path'),verification=q))))
 db.execute('INSERT OR IGNORE INTO squad_observations VALUES(?,?,?,?,?,?,?,?,?)',(club_id,mid,pid,player['role'],str(player.get('shirt') or ''),player['name'],player.get('source_path'),aid,on+'T00:00:00+00:00'))
 return dict(quality=q,state='RECORDED',player_id=pid)

def run(reference,seed_manifest,detail_manifest,work,team_id,on,season,academy_manifests=(),history_manifests=(),national_discovery=None,national_captures=None,code_commit=None):
 if code_commit:
  actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
  if not re.fullmatch('[0-9a-f]{40}',code_commit) or code_commit!=actual or subprocess.run(['git','diff','--quiet','HEAD'],cwd=ROOT).returncode:raise ValueError('EXACT_CLEAN_CANDIDATE_COMMIT_REQUIRED')
 work.mkdir(parents=True,exist_ok=False);reference_sha=hashlib.sha256(reference.read_bytes()).hexdigest();dbpath=work/'candidate.sqlite';shutil.copyfile(reference,dbpath);db=sqlite3.connect(dbpath);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON');before=snapshot_rows(db);prior_duplicates=canonical_duplicates(db)
 if db.execute('SELECT 1 FROM clubs WHERE club_id=?',(team_id,)).fetchone():raise ValueError('TEAM_MUST_START_ABSENT_FROM_REFERENCE')
 seeds=json.loads(seed_manifest.read_text());details=json.loads(detail_manifest.read_text());provider_seed=next(s for s in seeds if '/squad/' in s['url'] and s.get('status')=='SUCCESS');official_seed=next(s for s in seeds if urlsplit(s['url']).netloc=='www.realmadrid.com' and s.get('status')=='SUCCESS');provider_raw,provider_source=load_body(provider_seed);official_raw,official_source=load_body(official_seed);official_details=[(load_body(s)[1],load_body(s)[0]) for s in details if urlsplit(s['url']).netloc==urlsplit(official_source['url']).netloc and s.get('status')=='SUCCESS']
 source_on=max(datetime.fromisoformat(src['response_timestamp'].replace('Z','+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() for src in [provider_source,official_source]+[src for src,_ in official_details])
 if source_on>on:raise ValueError('MEMBERSHIP_EVIDENCE_AFTER_AS_OF')
 roster=build_observations(provider_raw,provider_source,official_raw,official_source,official_details,team_id,source_on);page=script(checked(provider_raw,provider_source),'__NEXT_DATA__')['props']['pageProps'];team=page['fallback']['team-'+str(team_id)];club=team['details']
 if club['gender']!='male' or (club['latestSeason'].split('/')[0],club['latestSeason'].split('/')[-1][-2:])!=(season.split('/')[0],season.split('/')[-1][-2:]):raise ValueError('CLUB_SEASON_SCOPE_MISMATCH')
 db.execute('INSERT INTO clubs VALUES(?,?,?,?)',(team_id,'provider_fotmob_'+str(team_id),club['name'],club.get('country')))
 pipeline.migrate(db)
 club_proof=dict(provider='FotMob',source_url=provider_source['url'],url=provider_source['url'],sha256=provider_source['sha256'],official_source=dict(provider='OfficialClub',source_url=official_source['url'],sha256=official_source['sha256']),independent_identity_bridges=roster['verified_membership_count'])
 canonical.entity(db,'TEAM','club:'+str(team_id),club['name']);canonical.assert_alias(db,'TEAM','FotMob',club['name'],'club:'+str(team_id),club_proof,provider_id=str(team_id))
 from squad_pipeline import quality_lifecycle as lifecycle
 club_ob={'club_identity':lifecycle.obligation('SATISFIED' if roster['verified_membership_count'] and roster['official_expected_count']==roster['verified_membership_count'] else 'MISSING',[dict(provider='FotMob',source_url=provider_source['url'],sha256=provider_source['sha256']),dict(provider='OfficialClub',source_url=official_source['url'],sha256=official_source['sha256'])],'EXACT_PROVIDER_TEAM_PAYLOAD_AND_OFFICIAL_ROSTER_IDENTITY_BRIDGES')}
 lifecycle.persist(db,'club:'+str(team_id),lifecycle.evaluate(club_ob,['club_identity'],lifecycle.fingerprint(club_proof)),on+'T00:00:00+00:00')

 verified_ids={m['player_id'] for m in roster['identity_matches']}
 for player in roster['players']:db.execute('INSERT OR IGNORE INTO players VALUES(?,?,?)',(player['player_id'],player['name'],'VERIFIED_PROVIDER_AND_OFFICIAL_IDENTITY' if player['player_id'] in verified_ids else 'VERIFIED_SOURCE_ENTITY;INDEPENDENT_IDENTITY_PENDING'))
 observations=list(roster['observations'])
 academy=None
 if academy_manifests:
  from squad_pipeline.coldstart_membership_sources import build_academy_observations
  academy_sources=[src for manifest_path in academy_manifests for src in json.loads(manifest_path.read_text()) if src.get('status')=='SUCCESS'];academy_pairs=[(load_body(src)[1],load_body(src)[0]) for src in academy_sources];provider_profiles=[(src,raw) for src,raw in academy_pairs if '/players/' in src['url'] and urlsplit(src['url']).netloc=='www.fotmob.com'];academy_rosters=[(src,raw) for src,raw in academy_pairs if src.get('purpose')=='discovered_official_academy_team'];academy_profiles=[(src,raw) for src,raw in academy_pairs if src.get('purpose')=='official_academy_candidate_profile']
  academy=build_academy_observations(provider_profiles,academy_rosters,academy_profiles,team_id,on);observations.extend(academy['observations'])
  from squad_pipeline.identity_sources import fotmob
  for src,raw in provider_profiles:
   person=fotmob(raw.decode(),src)[0];pid=int(person.provider_ids['FotMob']);db.execute('INSERT OR IGNORE INTO players VALUES(?,?,?)',(pid,person.names[0],'VERIFIED_SOURCE_ENTITY;INDEPENDENT_ACADEMY_REVIEW_SEPARATE'))
 history_claims={};history_errors=[]
 if history_manifests:
  from squad_pipeline.coldstart_fixture_sources import extract_club_history
  for manifest_path in history_manifests:
   for src in json.loads(manifest_path.read_text()):
    if src.get('status')!='SUCCESS' or not urlsplit(src['url']).path.startswith('/players/'):continue
    raw,source=load_body(src)
    try:
     for claim in extract_club_history(raw,source,team_id):history_claims.setdefault(claim['provider_match_id'],[]).append(claim)
    except (ValueError,KeyError,TypeError) as exc:history_errors.append(dict(source_url=source['url'],error=type(exc).__name__+':'+str(exc)))

 for claim in roster['provider_transfer_claims']:
  if not claim.get('assertion'):continue
  db.execute('INSERT OR IGNORE INTO players VALUES(?,?,?)',(claim['player_id'],claim.get('name') or str(claim['player_id']),'VERIFIED_SOURCE_ENTITY;MEMBERSHIP_UNVERIFIED'))
  observations.append(dict(claim,club_id=team_id,identity_status='VERIFIED',source_scope='PROVIDER_TRANSFER',observed_on=on))
 membership=pipeline.reconcile_membership(db,team_id,[p['player_id'] for p in roster['players']],on,observations,'ONBOARD')
 rows=[]
 for f in team['fixtures']['allFixtures']['fixtures']:
  if 'friendl' in f.get('tournament',{}).get('name','').lower():continue
  day=f.get('status',{}).get('utcTime','')[:10]
  if not day or season.split('/')[0]+'-07-01'<=day<str(int(season.split('/')[0])+1)+'-07-01':rows.append(f)
 captures={s['url']:s for s in details if s.get('status')=='SUCCESS'};results=[];encountered=set();passes=[];review_inputs={};independent_reviews=[];national_results=[]
 for mode in ('ONBOARD','INCREMENTAL'):
  pass_results=[]
  for row in rows:
   def team_key(tid):return canonical.resolve_entity(db,'TEAM','FotMob',provider_id=str(tid)) or 'club:'+str(tid)
   try:
    packet=directory_fixture(row,provider_source,season,team_id,team_key=team_key);packet['source']=provider_source
   except (ValueError,KeyError,TypeError) as exc:
    reason=type(exc).__name__+':'+str(exc);pipeline.record_source_issue(db,'CLUB_FIXTURE_DISCOVERY',row.get('id'),reason,dict(source=provider_source,discovery_row=row),on);pass_results.append(dict(provider_match_id=str(row.get('id')),status=(row.get('status') or {}),decision='QUARANTINE',match_id=None,detail_error=reason,source_url='https://www.fotmob.com'+row.get('pageUrl',''),appearances=[],quality_state='PENDING_VERIFICATION',skipped_full_revalidation=False));continue
   # A provider binding is reused; no canonical IDs are regenerated.
   binding=db.execute("SELECT match_id FROM canonical_fixture_sources WHERE domain='CLUB' AND provider='FotMob' AND provider_match_id=?",(str(row['id']),)).fetchone()
   if binding:packet=directory_fixture(row,provider_source,season,team_id,match_id=binding[0],team_key=team_key);packet['source']=provider_source
   detail=None;error=None;url='https://www.fotmob.com'+row['pageUrl'];reuse=None
   dependency_fingerprint=canonical.digest(dict(detail_sha=captures.get(url,{}).get('sha256'),directory_sha=provider_source['sha256'],history_claims=[dict(player=c['player_provider_id'],match=c['provider_match_id'],values={k:c['discovery_claim'].get(k) for k in ('teamId','opponentTeamId','isHomeTeam','matchDate','minutesPlayed','playedInMatch','onBench','leagueId')}) for c in history_claims.get(str(row['id']),[])],contract='STRICT_CLUB_LINEUP_AND_HISTORY_V1'))
   if mode=='INCREMENTAL' and row['status'].get('finished') and url in captures:
    reuse=pipeline.reusable_fixture(db,'FotMob',row['id'],captures[url]['sha256'],provider_source['sha256'])
    if reuse and not pipeline.can_reuse_participation(db,reuse['match_id'],dependency_fingerprint):reuse=None
   if row['status'].get('finished') and not reuse:
    if url not in captures:error='COMPLETED_DETAIL_NOT_CAPTURED'
    else:
     try:
      raw,source=load_body(captures[url]);detail=parse_completed_club(raw,source,packet,team_id,history_claims.get(str(row['id']),[]));detail['source']=source
     except (ValueError,KeyError,TypeError) as exc:error=type(exc).__name__+':'+str(exc)
   if error:pipeline.record_source_issue(db,'CLUB_COMPLETED_DETAIL',row['id'],error,dict(source=captures.get(url) or provider_source,source_url=url),on)
   accepted=dict(decision='ACCEPT',match_id=reuse['match_id'],quality=reuse['quality'],skipped_full_revalidation=True) if reuse else pipeline.process_fixture(db,(detail or packet)['fixture'],(detail or packet)['evidence'],on,mode)
   entry=dict(provider_match_id=str(row['id']),status=packet['fixture']['status'],decision=accepted['decision'],match_id=accepted['match_id'],detail_error=error,source_url=url,appearances=[],quality_state=accepted['quality']['state'],skipped_full_revalidation=accepted['skipped_full_revalidation'])
   if accepted['decision']=='ACCEPT':
    persist_fixture_row(db,packet,accepted['match_id'])
    if detail:
     if mode=='ONBOARD':review_inputs[str(row['id'])]=(raw,source,packet,detail)
     for player in detail['appearances']:
      encountered.add(int(player['provider_player_id']));entry['appearances'].append(apply_appearance(db,team_id,accepted['match_id'],player,detail['source'],on,mode))
     if all(a['state'] in {'RECORDED','RETAINED_ACCEPTED_WITHIN_TOLERANCE'} for a in entry['appearances']):pipeline.mark_participation_version(db,accepted['match_id'],dependency_fingerprint)
   pass_results.append(entry)
  if mode=='ONBOARD':
   from squad_pipeline.quality_fixture import export_accepted_catalogue
   from squad_pipeline.quality_fixture_review import review_completed
   exported={str(r['provider_match_id']):r for r in export_accepted_catalogue(db)['accepted']}
   for mid,(raw,source,packet,detail) in review_inputs.items():
    if mid not in exported:continue
    previous=lifecycle.load(db,'fixture-source:FotMob:'+mid)
    review=review_completed(raw,source,packet,exported[mid],previous['fingerprint'],dict(provider_source,raw=provider_raw),packet['fixture']['home']['id'],packet['fixture']['away']['id'])
    audited=pipeline.process_fixture(db,detail['fixture'],detail['evidence'],on,mode,stability_review=review);independent_reviews.append(dict(provider_match_id=mid,state=audited['quality']['state'],scope=review['scope'],directory_body_rehashed=review['directory_body_rehashed']))
  # Newly encountered off-roster identities receive explicit decisions as well.
  membership=pipeline.reconcile_membership(db,team_id,encountered|{p['player_id'] for p in roster['players']},on,observations,mode)
  if national_discovery and national_captures:
   from squad_pipeline.coldstart_national import offlineapply
   national_results.append(offlineapply(db,dict(json.loads(national_discovery.read_text()),managing_club_id=team_id),json.loads(national_captures.read_text()),{p['player_id'] for p in roster['players']},on,mode))
  db.commit();passes.append(dict(mode=mode,facts=db.execute('SELECT COUNT(*) FROM player_match_minutes WHERE club_id=?',(team_id,)).fetchone()[0],fixtures=db.execute('SELECT COUNT(*) FROM quality_fixture_acceptance').fetchone()[0],quality_transitions=db.execute('SELECT COUNT(*) FROM quality_transitions').fetchone()[0],results=pass_results))
 losses,derived_updates=preserve(before,db);duplicates=canonical_duplicates(db)
 if duplicates!=prior_duplicates:raise ValueError('NEW_CANONICAL_DUPLICATES:'+json.dumps(duplicates))
 integrity=db.execute('PRAGMA integrity_check').fetchone()[0];fk=db.execute('PRAGMA foreign_key_check').fetchall();quality_states=dict(db.execute('SELECT json_extract(result_json,\'$.state\'),COUNT(*) FROM quality_records GROUP BY 1').fetchall());db.close()
 if losses:raise ValueError('REFERENCE_ROWS_CHANGED:'+json.dumps(losses))
 if hashlib.sha256(reference.read_bytes()).hexdigest()!=reference_sha:raise ValueError('REFERENCE_MODIFIED')
 info=inspect(dbpath);pointer=dict(current=dict(database=info,version='ISOLATED_COLDSTART_'+on,code_commit=code_commit or 'UNCOMMITTED_CANDIDATE'),dataset_ref='acceptance/coldstart');manifest=export_all_matches(dbpath,work/'site','ISOLATED_REFERENCE_COPY',pointer,on,True);browser=json.loads((work/'site/data/browser.json').read_text());real=next(c for c in browser['clubs'] if c['club_id']==str(team_id));records=[r for rs in browser['records'].values() for r in rs if r.get('club_id')==str(team_id)];fixtures=[f for f in browser.get('fixture_catalogue',[]) if str(team_id) in {str(f.get('home',{}).get('id','')).split(':')[-1],str(f.get('away',{}).get('id','')).split(':')[-1]}]
 summary=dict(team_id=team_id,team_name=club['name'],season=season,as_of=on,reference_sha256=reference_sha,reference_unchanged=True,old_row_losses=losses,derived_provenance_updates=derived_updates,canonical_duplicates_before=prior_duplicates,canonical_duplicates_after=duplicates,integrity=integrity,foreign_key_errors=len(fk),scope='FORMAL_CLUB_FIXTURES_2026-07-01_TO_2027-07-01;PROVIDER_ENUMERATED_EXPECTATION;NOT_INDEPENDENT_COMPLETE_SCHEDULE_CERTIFICATE',expected_source_fixtures=len(rows),completed_source_fixtures=sum(bool(r['status'].get('finished')) for r in rows),official_roster_expected=roster['official_expected_count'],verified_current_members=roster['verified_membership_count'],membership_decisions=membership,encountered_unique_players=len(encountered),passes=passes,quality_states=quality_states,national_results=national_results,independent_fixture_reviews=independent_reviews,repeat_full_fixture_parses_skipped=sum(r['skipped_full_revalidation'] for r in passes[1]['results']),exported_player_records=len(records),known_minutes=sum(r['minute_state']!='UNKNOWN' for r in records),exported_club=real,fixture_export_count=len(fixtures),fixture_export_fields_sample=fixtures[:1],provider_transfer_claims=roster['provider_transfer_claims'],historical_membership_start_verified=False,academy_review=academy,history_profile_errors=history_errors,history_claims_count=sum(map(len,history_claims.values())),manifest=manifest,idempotence=dict(fact_count_equal=passes[0]['facts']==passes[1]['facts'],fixture_count_equal=passes[0]['fixtures']==passes[1]['fixtures'],transition_count_equal=passes[0]['quality_transitions']==passes[1]['quality_transitions']))
 (work/'acceptance.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));return summary

def main():
 p=argparse.ArgumentParser();p.add_argument('--reference',type=Path,required=True);p.add_argument('--seeds',type=Path,required=True);p.add_argument('--details',type=Path,required=True);p.add_argument('--work',type=Path,required=True);p.add_argument('--team-id',type=int,required=True);p.add_argument('--as-of',required=True);p.add_argument('--season',required=True);p.add_argument('--academy',type=Path,action='append',default=[]);p.add_argument('--histories',type=Path,action='append',default=[]);p.add_argument('--national-discovery',type=Path);p.add_argument('--national-captures',type=Path);p.add_argument('--code-commit');a=p.parse_args();s=run(a.reference,a.seeds,a.details,a.work,a.team_id,a.as_of,a.season,a.academy,a.histories,a.national_discovery,a.national_captures,a.code_commit);print(json.dumps({k:v for k,v in s.items() if k not in ['passes','membership_decisions','provider_transfer_claims','fixture_export_fields_sample','exported_club']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
