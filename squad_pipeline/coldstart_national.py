"""Offline, selected-player-only national acceptance; never runs acquisition.

Existing accepted rows are immutable references. All failures live in additive
quality issues, never the production acquisition queues. Caller owns commit.
"""
import json,hashlib,sqlite3
from pathlib import Path
from urllib.parse import urlsplit
from .international_fotmob import parse_public_match,instant
from .international_store import evidence,team,put_match,put_appearance,encoded,digest
from .canonical_fixture import score_fields,exists,value


def offlineapply(db,discovery,captures,selected_player_ids,on,mode='ONBOARD'):
    from .quality_pipeline import check_mode,verify_participation
    check_mode(mode)
    ids={str(k):int(v) for k,v in selected_player_ids.items()} if isinstance(selected_player_ids,dict) else {str(p):int(p) for p in selected_player_ids}
    if any(not db.execute('SELECT 1 FROM players WHERE player_id=?',(p,)).fetchone() for p in ids.values()):raise ValueError('SELECTED_REGISTERED_PLAYER_REQUIRED')
    db.execute('CREATE TABLE IF NOT EXISTS quality_source_issues(issue_key TEXT PRIMARY KEY,scope TEXT NOT NULL,provider_match_id TEXT,reason TEXT NOT NULL,payload_json TEXT NOT NULL)')
    def issue(mid,reason,details=None):
        record=dict(provider_match_id=str(mid),reason=reason,details=details or {},old_facts_preserved=True,managing_club_id=discovery.get('managing_club_id'))
        from .quality_pipeline import record_source_issue
        record_source_issue(db,'COLDSTART_NATIONAL',mid,reason,record,on)
        return record
    blocked=set(map(str,discovery.get('existing_quarantines_not_retried',[])))
    if exists(db,'international_acquisition_queue'):
        blocked|={str(r[0]) for r in db.execute("SELECT provider_entity_id FROM international_acquisition_queue WHERE entity_type='MATCH' AND provider='FotMob' AND state IN ('QUARANTINED','HUMAN_REVIEW')")}
    profile_cache={}
    caplist=list(captures.values()) if isinstance(captures,dict) else captures
    byid={str(c.get('provider_match_id') or urlsplit(c.get('url','')).fragment):c for c in caplist}
    metrics=dict(fixtures_discovered=len(discovery['fixtures']),selected_players=len(ids),blocked_existing_quarantines=0,accepted_fixtures=0,new_matches=0,new_player_facts=0,existing_player_facts_preserved=0,known_minutes=0,unknown_minutes=0,source_failures=[],parsed=[],old_rows_updated=0)
    for mid,f in sorted(discovery['fixtures'].items()):
        mid=str(mid)
        if mid in blocked:
            metrics['blocked_existing_quarantines']+=1;issue(mid,'EXISTING_QUARANTINE_NOT_RETRIED',dict(source_url=f.get('url'),selected_player_claims=[c.get('player_provider_id') for c in discovery.get('claims',{}).get(mid,[]) if str(c.get('player_provider_id')) in ids]));continue
        cap=byid.get(mid)
        if not cap or cap.get('status')!='SUCCESS':metrics['source_failures'].append(issue(mid,'SOURCE_CAPTURE_UNAVAILABLE'));continue
        counts={k:metrics[k] for k in ['new_matches','new_player_facts','existing_player_facts_preserved','known_minutes','unknown_minutes']}
        db.execute('SAVEPOINT coldstart_national_fixture')
        try:
            source={k:v for k,v in cap.items() if k not in ('headers','conditional_headers','body')};source['provider']='FotMob'
            raw=cap.get('body')
            if raw is None:
                from .core import ROOT
                path=Path(cap['path']);raw=(path if path.is_absolute() else ROOT/path).read_bytes()
            raw=raw.encode() if isinstance(raw,str) else raw
            if hashlib.sha256(raw).hexdigest()!=source.get('sha256'):raise ValueError('SOURCE_BODY_HASH_MISMATCH')
            claims=[c for c in discovery.get('claims',{}).get(mid,[]) if str(c.get('player_provider_id')) in ids]
            # Discovery JSON is an index, not an independent source. Verify
            # each personal-history claim against its retained profile body.
            from .international_discovery import payload as source_payload
            import re
            for claim in claims:
                hs=claim.get('source') or {};sha=hs.get('sha256')
                if sha not in profile_cache:
                    from .core import ROOT
                    hp=Path(hs['path']);hbody=(hp if hp.is_absolute() else ROOT/hp).read_bytes()
                    if hashlib.sha256(hbody).hexdigest()!=sha:raise ValueError('PROFILE_SOURCE_BODY_HASH_MISMATCH')
                    hd=source_payload(hbody.decode('utf8'));profile_cache[sha]=hd
                hd=profile_cache[sha];url=urlsplit(hs.get('url',''));profile_id=re.match(r'^/players/([0-9]+)(?:/|$)',url.path)
                if url.scheme!='https' or url.hostname not in {'www.fotmob.com','fotmob.com'} or not profile_id or str(hd.get('id'))!=str(claim['player_provider_id']) or profile_id[1]!=str(claim['player_provider_id']):raise ValueError('PROFILE_URL_BODY_ID_MISMATCH')
                actual_rows=list(hd.get('recentMatches') or [])+list((hd.get('initialMatchesPage') or {}).get('matches') or [])
                if claim.get('discovery_claim') not in actual_rows:raise ValueError('PERSONAL_HISTORY_CLAIM_NOT_IN_RETAINED_BODY')
            sheet=parse_public_match(raw.decode('utf8'),source,f,claims)
            if set(score_fields(sheet.get('score')))!={'home_score','away_score'}:raise ValueError('COMPLETED_SCORE_INCOMPLETE')
            eid=evidence(db,source,'COLDSTART_NATIONAL_EXACT_MATCH_IDS_SIDES_DATE_AND_LINEUP','RAW_RESPONSE_VERIFIED',0)
            teams={}
            for side in sheet['sheets']:
                prior=db.execute("SELECT p.team_key,t.level FROM football_team_provider_ids p JOIN football_teams t USING(team_key) WHERE p.provider='FotMob' AND p.provider_team_id=?",(str(side['provider_team_id']),)).fetchone()
                if prior:
                    if prior['level']!=sheet['level']:raise ValueError('NATIONAL_TEAM_LEVEL_CONFLICT')
                    teams[side['side']]=prior['team_key']
                else:teams[side['side']]=team(db,side['name'],sheet['level'],eid,'FotMob',side['provider_team_id'])
            binding=db.execute("SELECT match_id FROM international_match_provider_ids WHERE provider='FotMob' AND provider_match_id=?",(mid,)).fetchone()
            accepted=binding[0] if binding else None
            if accepted:
                old=db.execute('SELECT * FROM international_matches WHERE match_id=?',(accepted,)).fetchone()
                if not old or old['home_team_key']!=teams['home'] or old['away_team_key']!=teams['away'] or instant(old['kickoff_utc'])!=instant(sheet['kickoff_utc']) or str(old['competition_key'])!=str(sheet['competition_key']):raise ValueError('EXISTING_MATCH_IDENTITY_CONFLICT')
                if old['score'] is not None and score_fields(old['score'])!=score_fields(sheet['score']):raise ValueError('EXISTING_MATCH_SCORE_CONFLICT')
                if old['score'] is None:issue(mid,'EXISTING_MATCH_SCORE_MISSING_RETAINED_UNCHANGED')
            else:
                # Do not let put_match reselect fields on old derived canonical
                # rows merely because this new provider binding was absent.
                if exists(db,'canonical_fixtures'):
                    for old in db.execute("SELECT * FROM canonical_fixtures WHERE domain='NATIONAL' AND home_key=? AND away_key=? AND competition_key=?",(teams['home'],teams['away'],str(sheet['competition_key']))):
                        kick=value(db,old['match_id'],'kickoff')[0]
                        if kick and instant(kick)==instant(sheet['kickoff_utc']):raise ValueError('EXISTING_CANONICAL_REQUIRES_EXPLICIT_PROVIDER_BINDING')
                match=dict(match_id='nt_fotmob_'+mid,home_team_key=teams['home'],away_team_key=teams['away'],kickoff_utc=sheet['kickoff_utc'],madrid_date=sheet['madrid_date'],competition_key=sheet['competition_key'],stage=None,match_state='FINISHED',score=sheet['score'],duration_minutes=sheet['duration_minutes'],extra_time_status=sheet['extra_time_status'],identity_status='PROVIDER_ID_SIDES_DATE_VERIFIED',metadata_json=encoded(dict(competition=sheet['competition_label'],full_squad_identity_status=sheet['full_squad_identity_status'],identity_issues=sheet['identity_issues'])))
                accepted=put_match(db,match,'FotMob',mid,eid);metrics['new_matches']+=1
            accepted_players=[]
            for side in sheet['sheets']:
                for p in side['players']:
                    pid=ids.get(str(p['provider_player_id']))
                    if pid is None:continue
                    a=dict(match_id=accepted,player_id=pid,represented_team_key=teams[side['side']],participation_status=p['participation_status'],minutes=p['minutes'],reported_minutes=p['reported_minutes'],derived_minutes=p['derived_minutes'],minutes_precision=p['minutes_precision'],verification_status=p['verification_status'],evidence_scope=p['evidence_scope'],revision=1,metadata_json=encoded(dict(source_provider='FotMob',source_path=p['source_path'],provider_player_id=p['provider_player_id'],shirt=p['shirt'],role=p['role'],minute_observations=p.get('minute_observations',[]))))
                    old=db.execute('SELECT * FROM international_appearances WHERE match_id=? AND player_id=?',(accepted,pid)).fetchone();agree=1
                    if old:
                        metrics['existing_player_facts_preserved']+=1
                        if old['represented_team_key']!=a['represented_team_key'] or (a['minutes'] is not None and old['minutes']!=a['minutes']) or old['participation_status']!=a['participation_status']:
                            agree=0;issue(mid,'EXISTING_PLAYER_FACT_DIFFERENCE_RETAINED',dict(player_id=pid,old_minutes=old['minutes'],new_minutes=a['minutes']))
                    else:
                        put_appearance(db,a);metrics['new_player_facts']+=1;verify_participation(db,accepted,p,source,on,mode)
                    db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(accepted,pid,eid,'lineup_and_minutes',encoded(a),agree,'NEW_EXACT_PROVIDER_FACT' if not old else 'EXISTING_FACT_RETAINED'))
                    if p.get('history_source'):
                        history_eid=evidence(db,p['history_source'],'COLDSTART_NATIONAL_PERSONAL_HISTORY_TARGET_MINUTES','PROVIDER_ASSERTION_WITH_LINEUP_ID_CHECK',0)
                        db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(accepted,pid,history_eid,'reported_minutes',encoded(dict(minutes=p['minutes'],scope=p['evidence_scope'])),agree,'PERSONAL_HISTORY_RETAINED_WITH_EXACT_MATCH_AND_PLAYER_CHECKS'))
                    metrics['known_minutes' if (old['minutes'] if old else p['minutes']) is not None else 'unknown_minutes']+=1;accepted_players.append(pid)
            metrics['accepted_fixtures']+=1;metrics['parsed'].append(dict(provider_match_id=mid,canonical_match_id=accepted,selected_players=accepted_players))
            db.execute('RELEASE coldstart_national_fixture')
        except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:
            db.execute('ROLLBACK TO coldstart_national_fixture');db.execute('RELEASE coldstart_national_fixture')
            metrics.update(counts);metrics['source_failures'].append(issue(mid,type(exc).__name__+':'+str(exc)))
    return metrics
