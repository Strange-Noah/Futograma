"""Six-club consumer of accepted typed source interfaces; no expected rosters.

This adapter is deliberately incomplete when historical management evidence is
not independently recoverable. It reports coverage exclusions rather than using
appearance unions as proof of continuous management or national membership.
"""
from datetime import timedelta
from dataclasses import asdict
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from .engine import Evidence, Membership, Fixture, SquadStatus, day, digest, status_agreement
from ..source_interface import EvidenceStore, SourceService, SourceRequest, RawSourceArtifact
from ..canonical_fixture import instant
from ..source_interface import VERSION
from ..source_parsing import PARSER_VERSION
from ..roster_policy import roster_decision, latest


class RetainedAdapter:
    def __init__(self, db, archive_root, club_id, calendars=(), source_service=None):
        self.db = db
        self.dossiers = latest(db)
        self.service = source_service or SourceService(EvidenceStore(Path.cwd()/'.cloud/reconstruction-sources',archives=[archive_root]),'reconstruction-offline',set(),max_calls=0)
        self.club_id = club_id
        self.calendars = calendars
        self.coverage = {}
        self.version = digest(['RETAINED_OBLIGATION_V2',VERSION,PARSER_VERSION,club_id,[asdict(w) for w in calendars]])
        self.parsed = {}
        self.source_cache = {}
        self.details = {}
        self.history_cache = {}
        self.team_cache = {}
        self.discovered_national = set()
        self.candidate_packets={};self.retained_artifacts={}

    def source(self, source):
        key=digest(source)
        if key not in self.source_cache:
            self.source_cache[key]=self._source(source)
        return self.source_cache[key]

    def baseline_receipt(self,kind,payload):
        """Reference retained acceptance, without fabricating a source response."""
        key=digest([kind,payload])
        return Evidence('Accepted baseline','baseline:'+key,key,kind,
            'NOT_A_NEW_SOURCE_OBSERVATION','RETAINED_CANONICAL_AUTHORITY','RETAINED_RECORD','ACCEPTED_BASELINE')

    def _source(self, source):
        provider=source.get('provider') or urlsplit(source.get('url') or source.get('source_url') or '').hostname or 'UNKNOWN'
        acquired=self.service.store.normalize(source,provider)
        if acquired.outcome=='MANUAL_POLICY':
            record=acquired.metadata['record'];sha=acquired.metadata['record_hash']
            e=Evidence(record.get('source_type','MANUAL_POLICY'),'policy:'+sha,sha,
                'preserved-authorization-record',record['authorization_date'],
                record.get('authority','Human project owner'),'DAY','MANUAL_POLICY')
            return e if e.valid() else None
        if acquired.outcome!='FOUND':return None
        artifact=acquired.artifact;metadata=artifact.metadata
        observed=metadata.get('response_timestamp') or metadata.get('observed_on')
        if not observed:return None
        precision='DAY' if len(observed)==10 else 'TIMESTAMP'
        host=urlsplit(artifact.request.target_url).hostname
        origin='Sky' if host in {'www.skysports.com','sport.sky.de'} else 'FotMob' if host in {'www.fotmob.com','fotmob.com'} else host
        e=Evidence(artifact.request.provider,artifact.request.target_url,artifact.content_sha256,
            metadata.get('locator') or 'artifact:'+artifact.key,observed,
            origin,precision)
        return e if e.valid() else None

    def discover(self, start, cutoff, mode):
        """Reuse accepted on-date policy/facts; output audit scope, never new roster facts.

        Replay gaps annotate integration work and cannot revoke acceptance.
        Explicit interval boundaries still constrain population. Concrete source
        contradictions are emitted separately; this adapter has no fact writer.
        """
        members, fixtures, statuses = [], [], []
        self.parsed={}
        self.details={}
        self.candidate_packets={}
        keys={r[0] for r in self.db.execute('SELECT DISTINCT player_id FROM club_management_events WHERE club_id=?',(self.club_id,))}
        self.coverage = dict(managed_days=0,uncertified_managed_days=0,unknown_membership_days=0,
            accepted_baseline_managed_days=0,source_contract_replay_gap_days=0,
            raw_sheets=0,verified_source_sheets=0,national_membership='NO_VERIFIED_CALLUP_INTERVALS;NOT_INFERRED_FROM_CAREER_NATIONALITY_OR_APPEARANCES',
            broad_discovery='RETAINED_CLUB_SHEETS_ONLY;PLAYER_TEAM_NATIONAL_LIVE_ADAPTERS_REQUIRED')
        d=day(start)
        while d<=day(cutoff):
            on=d.isoformat()
            for pid in sorted(keys):
                decision=roster_decision(self.db,self.club_id,pid,on,self.dossiers)
                if not decision.get('included'):
                    if decision.get('cohort')=='UNKNOWN':self.coverage['unknown_membership_days']+=1
                    continue
                if decision.get('effective_from') and on<decision['effective_from']:continue
                if decision.get('end_exclusive') and on>=decision['end_exclusive']:continue
                # A retained event's observation DAY can support its assertion.
                # It is never represented as an invented HTTP retrieval timestamp.
                observations=[r['observed_on'] for r in self.db.execute(
                    'SELECT * FROM club_management_events WHERE club_id=? AND player_id=? AND status=? AND verification_status=?',
                    (self.club_id,pid,decision['status'],'VERIFIED'))
                    if r['effective_from']==decision.get('effective_from') and json.loads(r['source_json'])==decision.get('sources',[])]
                event_day=max(observations,default=None)
                sources=tuple(e for s in decision.get('sources',[]) if isinstance(s,dict)
                    and (e:=self.source(dict(s,observed_on=s.get('observed_on') or event_day))))
                if not sources:self.coverage['source_contract_replay_gap_days']+=1
                receipt=self.baseline_receipt('roster_policy.roster_decision',dict(club_id=self.club_id,player_id=pid,decision=decision))
                sources=(receipt,)
                self.coverage['accepted_baseline_managed_days']+=1
                self.coverage['managed_days']+=1
                members.append(Membership(str(pid),str(self.club_id),'CLUB','SENIOR',on,
                    (d+timedelta(days=1)).isoformat(),decision['status'] if decision['status'] in {'FIRST_TEAM','FIRST_TEAM_INJURED','LOAN_IN'} else 'AUTHORIZED_EXCEPTION',sources,
                    precision='ACCEPTED_POLICY_AUDIT_DAY;'+str(decision.get('date_precision') or 'RETAINED_SCOPE')+';NOT_NEW_HISTORICAL_BOUNDARY'))
            d+=timedelta(days=1)
        source_rows=[dict(r) for r in self.db.execute('SELECT * FROM validation_match_sheets WHERE club_id=?',(self.club_id,))]
        seen_sources={(r['match_id'],json.loads(r['payload_json']).get('source',{}).get('sha256')) for r in source_rows}
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'player_match_minutes' in tables:
            for match_row in self.db.execute('SELECT DISTINCT m.* FROM matches m JOIN player_match_minutes p USING(match_id) WHERE p.club_id=? AND m.local_date BETWEEN ? AND ?',(self.club_id,start,cutoff)):
                for source in json.loads(match_row['metadata_json'] or '{}').get('source_evidence',[]):
                    key=(match_row['match_id'],source.get('sha256'))
                    if key in seen_sources:continue
                    seen_sources.add(key)
                    source_rows.append(dict(match_id=match_row['match_id'],provider=source.get('provider') or 'UNKNOWN',payload_json=json.dumps(dict(source=source))))
            for source in self.service.store.records:
                mid=source.get('canonical_match_id')
                if not mid or (mid,source.get('sha256')) in seen_sources:continue
                match_row=self.db.execute('SELECT * FROM matches WHERE match_id=? AND (home_id=? OR away_id=?)',(mid,self.club_id,self.club_id)).fetchone()
                if match_row:
                    seen_sources.add((mid,source.get('sha256')))
                    source_rows.append(dict(match_id=mid,provider=source.get('provider') or 'UNKNOWN',payload_json=json.dumps(dict(source=source))))
        self.coverage['fixture_source_inputs']=len(source_rows)
        for row in source_rows:
            sheet=json.loads(row['payload_json']);source=sheet.get('source') or {}
            self.coverage['raw_sheets']+=1
            match=self.db.execute('SELECT * FROM matches WHERE match_id=?',(row['match_id'],)).fetchone()
            if not match:continue
            acquired=self.service.store.normalize(source,row['provider'],'match')
            if acquired.outcome!='FOUND':
                key='source_'+acquired.outcome
                self.coverage[key]=self.coverage.get(key,0)+1;continue
            a=acquired.artifact
            if row['provider']=='UNKNOWN':
                host=urlsplit(a.request.url or '').hostname
                row['provider']={'www.fotmob.com':'FotMob','fotmob.com':'FotMob','torlytics.de':'Torlytics','www.skysports.com':'Sky','sport.sky.de':'Sky'}.get(host,'UNKNOWN')
            expected=dict(source.get('expected_ids') or {})
            if row['provider']=='FotMob' and not expected:
                bindings=self.db.execute('SELECT provider_match_id FROM canonical_fixture_sources WHERE domain=? AND provider=? AND match_id=?',('CLUB','FotMob',row['match_id'])).fetchall()
                if not bindings and 'validation_match_aliases' in tables:
                    bindings=self.db.execute("SELECT DISTINCT substr(alias,8) FROM validation_match_aliases WHERE match_id=? AND alias LIKE 'fotmob:%'",(row['match_id'],)).fetchall()
                if len(bindings)==1:expected['match']=str(bindings[0][0])
            if row['provider']=='Sky':
                bindings=self.db.execute('SELECT provider_match_id FROM canonical_fixture_sources WHERE domain=? AND provider=? AND match_id=?',('CLUB','Sky',row['match_id'])).fetchall()
                if len(bindings)==1:expected['match']=str(bindings[0][0])
                elif source.get('directory_match_id'):expected['match']=str(source['directory_match_id'])
                else:
                    retained=json.loads(match['metadata_json'] or '{}').get('source_evidence',[])
                    if any(s.get('url')==a.request.url for s in retained):
                        token=urlsplit(a.request.url).path.rstrip('/').rsplit('/',1)[-1]
                        if token.isdigit():expected['match']=token
            context=dict(
                home_id=match['home_id'],away_id=match['away_id'],kickoff=match['kickoff'],
                competition_id=match['source_competition_id'] or match['competition_id'])
            if row['provider']=='Sky' and any(s.get('url')==a.request.url for s in json.loads(match['metadata_json'] or '{}').get('source_evidence',[])):
                context['accepted_source_url']=a.request.url
            request=SourceRequest(row['provider'],'match',a.request.url,'fixture-detail',expected_ids=expected,context=context)
            a=RawSourceArtifact(request,a.content_sha256,a.locator,a.metadata,a.authority)
            _,observation=self.service.observe(request,a)
            key='parse_'+observation.outcome
            self.coverage[key]=self.coverage.get(key,0)+1
            native=observation.native
            if not observation.completeness.get('source_identity'):continue
            # Parsing is native only. Bind to the retained canonical fixture using
            # independent ordered teams, kickoff and competition before statuses.
            if row['provider']=='FotMob':
                pid=native['provider_match_id']
                existing=self.db.execute('SELECT match_id FROM canonical_fixture_sources WHERE domain=? AND provider=? AND provider_match_id=?',('CLUB',row['provider'],pid)).fetchone()
                if existing and existing[0]!=row['match_id']:continue
            elif row['provider'] in {'Torlytics','Sky'}:
                f=native['fixture'];mapping=native['legacy_mapping_context']
                if instant(f['kickoff'])!=instant(match['kickoff']):continue
                if str(mapping['competition_id'])!=str(match['competition_id']):continue
                from ..live_parse import canon
                labels=f.get('source_team_names') or {};meta=json.loads(match['metadata_json'] or '{}')
                if any(canon(labels.get(side,''))!=canon(meta.get(side+'_team','')) for side in ('home','away')):continue
                if any(mapping[side+'_club_id'] is not None and mapping[side+'_club_id']!=match[side+'_id'] for side in ('home','away')):continue
                pid=native['provider_fixture_id'] or expected.get('match')
            else:continue
            e=self.source(dict(source,provider=row['provider']))
            if e is None:continue
            own=next((s for s in native.get('sheets',[]) if str(s.get('provider_team_id') or s.get('configured_club_id'))==str(self.club_id)),None)
            if own is None:continue
            opponent=match['away_id'] if match['home_id']==self.club_id else match['home_id']
            f=Fixture(row['match_id'],pid,pid,str(self.club_id),str(opponent),'CLUB','SENIOR',str(match['competition_id']),match['local_date'],(e,))
            fixtures.append(f)
            self.details.setdefault(f.canonical,[]).append((a,e))
            normalized=[]
            for player in own['players']:
                provider_key=player.get('provider_key') or 'fotmob:'+str(player.get('provider_player_id'))
                identities=self.db.execute('SELECT DISTINCT player_id FROM validation_player_aliases WHERE provider_key=?',(provider_key,)).fetchall()
                if len(identities)!=1:
                    self.coverage['unbound_source_players']=self.coverage.get('unbound_source_players',0)+1;continue
                ident=identities[0][0]
                state=player.get('participation_status') or player.get('status');minutes=player.get('reported_minutes')
                if minutes is None and own.get('complete_events') and not own.get('issues'):minutes=player.get('derived_minutes')
                if type(minutes) is float and minutes.is_integer():minutes=int(minutes)
                # Only explicit provider unused status supports confirmed zero.
                if state=='UNUSED_SUBSTITUTE' and minutes is None:minutes=player.get('derived_minutes')
                normalized.append(dict(player_id=ident,status=state,reported_minutes=minutes))
                status=SquadStatus(str(ident),f.canonical,f.team,state,minutes,(e,))
                if status.reliable():
                    statuses.append(status)
                    self.keep_candidate('CLUB',f.canonical,f.team,ident,player,a,state,minutes)
            complete=own.get('complete_squad') and len(normalized)==len(own['players'])
            self.parsed.setdefault(f.canonical,[]).append((dict(players=normalized,complete_squad=bool(complete),
                issues=() if complete else ('INCOMPLETE_OWN_SQUAD_IDENTITY',),native_players=own['players'],
                native_complete_squad=bool(own.get('complete_squad')),provider=row['provider']),e))
            self.coverage['verified_source_sheets']+=1
        accepted_fixtures,accepted_statuses=self.accepted_club_facts(start,cutoff)
        for retained in accepted_statuses:
            if retained.status not in {'STARTER','SUBSTITUTE_USED','UNUSED_SUBSTITUTE'}:continue
            for sheet,e in self.parsed.get(retained.canonical,[]):
                if sheet.get('complete_squad') and not sheet.get('issues') and not any(str(p['player_id'])==retained.player for p in sheet['players']):
                    statuses.append(SquadStatus(retained.player,retained.canonical,retained.team,'ABSENT_COMPLETE_SQUAD',None,(e,),True))
        fixtures=accepted_fixtures+fixtures;statuses=accepted_statuses+statuses
        self.discover_teams(keys,start,cutoff)
        fixtures.extend(self.national_fixtures(keys,start,cutoff))
        national_members,national_fixtures,national_statuses=self.accepted_national_facts(keys,start,cutoff)
        members.extend(national_members);fixtures=national_fixtures+fixtures;statuses=national_statuses+statuses
        # Coalesce only already-proven consecutive audit days of the same policy,
        # never extrapolate a snapshot backwards or bridge an unknown interval.
        joined=[]
        for m in sorted(members,key=lambda x:(x.player,x.start)):
            if joined and (joined[-1].player,joined[-1].team,joined[-1].domain,joined[-1].level,joined[-1].end,joined[-1].status,joined[-1].evidence)==(m.player,m.team,m.domain,m.level,m.start,m.status,m.evidence):
                from dataclasses import replace
                joined[-1]=replace(joined[-1],end=m.end)
            else:joined.append(m)
        self.version=digest(['RETAINED_OBLIGATION_V3',VERSION,PARSER_VERSION,self.club_id,self.coverage,[e.sha256 for m in joined for e in m.evidence]])
        return joined,fixtures,statuses,self.calendars

    def accepted_club_facts(self,start,cutoff):
        """Accepted facts enter audits without re-certification or any fact write."""
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'player_match_minutes' not in tables:return [],[]
        fixtures=[];statuses=[];seen=set()
        for row in self.db.execute('SELECT p.*,m.home_id,m.away_id,m.local_date,m.competition_id FROM player_match_minutes p JOIN matches m USING(match_id) WHERE p.club_id=? AND m.local_date BETWEEN ? AND ?',(self.club_id,start,cutoff)):
            value=dict(row);mid=row['match_id'];receipt=self.baseline_receipt('player_match_minutes+matches',value)
            if mid not in seen:
                seen.add(mid);opponent=row['away_id'] if row['home_id']==self.club_id else row['home_id']
                fixtures.append(Fixture(mid,'','',str(self.club_id),str(opponent),'CLUB','SENIOR',str(row['competition_id']),row['local_date'],(receipt,),identity_basis='ACCEPTED_CANONICAL'))
            minutes=row['minutes']
            if type(minutes) is float and minutes.is_integer():minutes=int(minutes)
            statuses.append(SquadStatus(str(row['player_id']),mid,str(self.club_id),row['participation_status'],minutes,(receipt,),authority='ACCEPTED_BASELINE'))
        self.coverage['accepted_club_fixtures']=len(fixtures);self.coverage['accepted_club_statuses']=len(statuses)
        return fixtures,statuses

    def keep_candidate(self,domain,mid,team,pid,player,artifact,state,minutes):
        """Stage verified native material; canonical persistence remains elsewhere."""
        from dataclasses import asdict
        if state not in {'STARTER','SUBSTITUTE_USED','UNUSED_SUBSTITUTE'} or type(minutes) is not int:return
        if artifact.key not in self.retained_artifacts:
            self.retained_artifacts[artifact.key]=self.service.store.import_body(artifact.request,self.service.store.read(artifact),artifact.metadata,artifact.authority)
        a=self.retained_artifacts[artifact.key]
        key=player.get('provider_key') or 'fotmob:'+str(player.get('provider_player_id'))
        typed=dict(player,provider_key=key,provider_player_id=player.get('provider_player_id') or key,participation_status=state,minutes=minutes,
            source_path=player.get('source_path') or player.get('json_path'),reported_minutes=player.get('reported_minutes'),derived_minutes=player.get('derived_minutes'))
        source=dict(provider=a.request.provider,url=a.request.target_url,sha256=a.content_sha256,artifact_key=a.key,storage_locator=a.locator,
            response_timestamp=a.metadata.get('response_timestamp'),acquired_at=a.metadata.get('response_timestamp'),size=len(self.service.store.read(a)))
        packet=dict(domain=domain,match_id=mid,team=team,managing_club_id=self.club_id,player_id=int(pid),player=typed,source=source,artifact=asdict(a))
        self.candidate_packets[digest([domain,mid,team,pid,state,minutes,a.key])]=packet

    def accepted_national_facts(self,players,start,cutoff):
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'international_appearances','international_matches','football_teams'}<=tables:return [],[],[]
        members=[];fixtures=[];statuses=[];seen=set()
        for row in self.db.execute('SELECT a.*,m.madrid_date,m.competition_key,m.home_team_key,m.away_team_key,t.level FROM international_appearances a JOIN international_matches m USING(match_id) JOIN football_teams t ON t.team_key=a.represented_team_key WHERE m.madrid_date BETWEEN ? AND ?',(start,cutoff)):
            pid=row['player_id'];on=row['madrid_date']
            if pid not in players or not roster_decision(self.db,self.club_id,pid,on,self.dossiers).get('included'):continue
            receipt=self.baseline_receipt('international_appearances+matches',dict(row));team=row['represented_team_key'];mid=row['match_id']
            if (mid,team) not in seen:
                seen.add((mid,team));opponent=row['away_team_key'] if row['home_team_key']==team else row['home_team_key']
                fixtures.append(Fixture(mid,'','',team,opponent,'NATIONAL',row['level'],row['competition_key'],on,(receipt,),identity_basis='ACCEPTED_CANONICAL'))
            members.append(Membership(str(pid),team,'NATIONAL',row['level'],on,(day(on)+timedelta(days=1)).isoformat(),
                'MATCHDAY_REPRESENTATIVE',(receipt,),precision='ACCEPTED_MATCHDAY_ONLY;NO_CONTINUOUS_CALLUP_INFERRED'))
            minutes=row['minutes']
            if type(minutes) is float and minutes.is_integer():minutes=int(minutes)
            statuses.append(SquadStatus(str(pid),mid,team,row['participation_status'],minutes,(receipt,),authority='ACCEPTED_BASELINE'))
        self.coverage['accepted_national_fixtures']=len(fixtures);self.coverage['accepted_national_statuses']=len(statuses)
        self.coverage['national_membership']='ACCEPTED_MATCHDAY_SCOPE_REUSED;NO_CONTINUOUS_CALLUP_INFERRED'
        return members,fixtures,statuses

    def national_fixtures(self, players, start, cutoff):
        """R2/R5 retain verified national fixtures without inventing call-up spans."""
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'international_matches','international_match_provider_ids','international_appearances'}<=tables:return []
        result=[];relevant={r[0] for r in self.db.execute('SELECT DISTINCT match_id,player_id FROM international_appearances') if r[1] in players}
        for provider_id in self.discovered_national:
            relevant.update(r[0] for r in self.db.execute('SELECT match_id FROM international_match_provider_ids WHERE provider=? AND provider_match_id=?',('FotMob',provider_id)))
        for row in self.db.execute('SELECT * FROM international_matches WHERE madrid_date BETWEEN ? AND ?',(start,cutoff)):
            if row['match_id'] not in relevant:continue
            self.coverage['national_fixture_candidates']=self.coverage.get('national_fixture_candidates',0)+1
            binding=self.db.execute('SELECT * FROM international_match_provider_ids WHERE provider=? AND match_id=?',('FotMob',row['match_id'])).fetchone()
            if binding is None:continue
            retained=self.db.execute('SELECT * FROM persistent_source_evidence WHERE evidence_id=?',(binding['evidence_id'],)).fetchone()
            if retained is None:continue
            source=dict(retained)
            provider='Sky' if urlsplit(source.get('source_url') or '').hostname=='www.skysports.com' else 'FotMob'
            if provider=='Sky':
                sky_binding=self.db.execute('SELECT * FROM international_match_provider_ids WHERE provider=? AND match_id=?',('Sky',row['match_id'])).fetchone()
                if sky_binding is None:
                    self.coverage['national_sky_binding_missing']=self.coverage.get('national_sky_binding_missing',0)+1;continue
                binding=sky_binding
            acquired=self.service.store.normalize(source,provider,'match')
            if acquired.outcome!='FOUND':
                key='national_'+acquired.outcome;self.coverage[key]=self.coverage.get(key,0)+1;continue
            teams=[];context=None
            if provider=='Sky':
                payload=json.loads(source.get('payload_json') or '{}');original=payload.get('verified_fixture_context') or {}
                fixture={k:original[k] for k in ('kickoff','competition_key','level') if k in original}
                for side in ('home','away'):
                    declared=original.get(side) or {};bindings=self.db.execute('SELECT provider_team_id FROM football_team_provider_ids WHERE provider=? AND team_key=?',('FotMob',row[side+'_team_key'])).fetchall()
                    if not isinstance(declared,dict) or len(bindings)!=1 or str(declared.get('id'))!=str(bindings[0][0]):break
                    fixture[side]=declared.get('name');fixture[side+'_team_key']=row[side+'_team_key']
                if not fixture.get('home') or not fixture.get('away') or str(fixture.get('competition_key'))!=str(row['competition_key']):
                    self.coverage['national_sky_context_conflict']=self.coverage.get('national_sky_context_conflict',0)+1;continue
                from ..international_sky_fallback import competition_aliases
                aliases=competition_aliases(original)
                if not aliases:continue
                fixture['competition_label']=aliases[0];fixture['competition_labels']=list(aliases)
                fixture['evidence']={k:original.get('source',{}).get(k) for k in ('url','sha256')}
                fixture['team_label_certificates']=payload.get('team_label_certificates') or {}
                if not fixture.get('kickoff') or instant(fixture['kickoff'])!=instant(row['kickoff_utc']):continue
                levels=[r[0] for r in self.db.execute('SELECT level FROM football_teams WHERE team_key IN (?,?)',(row['home_team_key'],row['away_team_key']))]
                if len(levels)!=2 or any(level!=fixture.get('level') for level in levels):continue
                context=dict(domain='NATIONAL',fixture=fixture)
                teams=[dict(level=fixture['level']),dict(level=fixture['level'])]
            for side in ('home','away'):
                if provider=='Sky':break
                team_key=row[side+'_team_key']
                native=self.db.execute('SELECT p.provider_team_id,t.level FROM football_team_provider_ids p JOIN football_teams t USING(team_key) WHERE p.provider=? AND p.team_key=?',('FotMob',team_key)).fetchall()
                if len(native)!=1:break
                teams.append(native[0])
            if len(teams)!=2 or teams[0]['level']!=teams[1]['level']:continue
            a=acquired.artifact
            request=SourceRequest(provider,'match',a.request.url,'national-fixture-detail',expected_ids=dict(match=binding['provider_match_id']),context=context or dict(
                home_id=teams[0]['provider_team_id'],away_id=teams[1]['provider_team_id'],kickoff=row['kickoff_utc'],
                competition_id=row['competition_key'],domain='NATIONAL',level=teams[0]['level']))
            a=RawSourceArtifact(request,a.content_sha256,a.locator,a.metadata,a.authority)
            _,observation=self.service.observe(request,a)
            key='national_parse_'+observation.outcome;self.coverage[key]=self.coverage.get(key,0)+1
            if not observation.completeness.get('source_identity'):continue
            e=self.source(source)
            if e is None:continue
            self.details.setdefault(row['match_id'],[]).append((a,e))
            for sheet in observation.native.get('sheets',[]):
                represented=sheet.get('represented_team_key')
                if represented is None:
                    mapping=self.db.execute('SELECT team_key FROM football_team_provider_ids WHERE provider=? AND provider_team_id=?',('FotMob',str(sheet.get('provider_team_id')))).fetchall()
                    if len(mapping)!=1:continue
                    represented=mapping[0][0]
                for player in sheet.get('players',[]):
                    key=player.get('provider_key') or 'fotmob:'+str(player.get('provider_player_id'))
                    aliases=self.db.execute('SELECT player_id FROM validation_player_aliases WHERE provider_key=?',(key,)).fetchall()
                    if len(aliases)!=1 or aliases[0][0] not in players:continue
                    minutes=player.get('reported_minutes')
                    if minutes is None:minutes=player.get('derived_minutes')
                    if type(minutes) is float and minutes.is_integer():minutes=int(minutes)
                    self.keep_candidate('NATIONAL',row['match_id'],represented,aliases[0][0],player,a,player.get('participation_status') or player.get('status'),minutes)
            for side,other in [('home','away'),('away','home')]:
                result.append(Fixture(row['match_id'],binding['provider_match_id'],binding['provider_match_id'],row[side+'_team_key'],row[other+'_team_key'],
                    'NATIONAL',teams[0]['level'],row['competition_key'],row['madrid_date'],(e,)))
            self.coverage['verified_national_fixtures']=self.coverage.get('verified_national_fixtures',0)+1
        return result

    def team_capture(self,team_id,level,start,cutoff):
        key=(team_id,level,start,cutoff)
        if key not in self.team_cache:
            request=SourceRequest('FotMob','team_history','https://www.fotmob.com/teams/'+team_id+'/fixtures','team-fixtures',
                expected_ids=dict(team=team_id),context=dict(start=start,cutoff=cutoff,level=level))
            self.team_cache[key]=self.service.observe(request)
        return self.team_cache[key]

    def discover_teams(self,players,start,cutoff):
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.discovered_national=set()
        if not {'national_affiliation_events','football_team_provider_ids','football_teams'}<=tables:return
        teams=set()
        for row in self.db.execute("SELECT a.player_id,p.provider_team_id,t.level FROM national_affiliation_events a JOIN football_teams t USING(team_key) JOIN football_team_provider_ids p USING(team_key) WHERE p.provider='FotMob'"):
            if row['player_id'] in players:teams.add((row['provider_team_id'],row['level']))
        self.coverage['team_history_requests']=len(teams)
        for tid,level in sorted(teams):
            acquired,parsed=self.team_capture(tid,level,start,cutoff)
            outcome=parsed.outcome if parsed else acquired.outcome
            key='team_history_'+outcome;self.coverage[key]=self.coverage.get(key,0)+1
            if parsed and parsed.outcome=='PARSED':
                self.discovered_national.update(str(m['provider_match_id']) for m in parsed.native['matches'])
        self.coverage['team_fixture_native_hints']=len(self.discovered_national)
        self.coverage['broad_discovery']='RETAINED_TEAM_AND_PLAYER_HISTORY;NEW_FIXTURES_REQUIRE_DETAIL_AND_CANONICAL_ADJUDICATION'

    def resolve(self,path,finding):
        from .engine import Resolution
        if path=='player_history':return self.player_history(finding)
        if path=='team_history':
            if finding['domain']!='NATIONAL':return Resolution(path,'NOT_REQUESTED','No certified club-team request binding/capture configured')
            native=self.db.execute('SELECT p.provider_team_id,t.level FROM football_team_provider_ids p JOIN football_teams t USING(team_key) WHERE p.provider=? AND p.team_key=?',('FotMob',finding['team'])).fetchall()
            if len(native)!=1:return Resolution(path,'NOT_REQUESTED','Unique verified national team provider binding required')
            acquired,parsed=self.team_capture(native[0]['provider_team_id'],native[0]['level'],finding['interval'][0],(day(finding['interval'][1])-timedelta(days=1)).isoformat())
            if acquired.outcome!='FOUND':return Resolution(path,acquired.outcome,'Retained exact-team response: '+str(acquired.reason))
            a=acquired.artifact;e=self.source(dict(url=a.request.url,path=a.locator,sha256=a.content_sha256,response_timestamp=a.metadata.get('response_timestamp')))
            if e and parsed and parsed.outcome=='PARSED':return Resolution(path,'NO_MATCH','Exact team schedule parsed; schedule cannot certify player selection',(e,))
            return Resolution(path,'NOT_REQUESTED','Team capture requires identity/format remediation before claim use')
        # Active exact-fixture lookup before an explicit unavailable outcome.
        if path=='fixture_details':
            pairs=self.parsed.get(finding.get('canonical'),[])
            if pairs:
                candidates=[];evidence=[];listed_unknown=False
                for sheet,e in pairs:
                    evidence.append(e)
                    player=next((p for p in sheet['players'] if str(p.get('player_id'))==finding['player']),None)
                    if player is None and ((sheet.get('complete_squad') and not sheet.get('issues')) or self.exact_native_exclusion(sheet,finding['player'])):
                        candidates.append(SquadStatus(finding['player'],finding['canonical'],finding['team'],'ABSENT_COMPLETE_SQUAD',None,(e,),True))
                    elif player is not None:
                        status=SquadStatus(finding['player'],finding['canonical'],finding['team'],player['status'],player['reported_minutes'],(e,))
                        if status.reliable():candidates.append(status)
                        else:listed_unknown=True
                evidence=tuple(dict.fromkeys(evidence))
                agreement=status_agreement(candidates)
                if (candidates and not agreement['accepted']) or (listed_unknown and any(s.status=='ABSENT_COMPLETE_SQUAD' for s in candidates)):
                    return Resolution(path,'CONFLICT','Conflicting exact-source squad claims; no last-source-wins',evidence)
                if candidates:
                    selected=next(s for s in candidates if s.minutes==agreement.get('minutes'))
                    return Resolution(path,'FOUND','Retained typed exact-fixture claim; existing minute policy applied; not a new independent search',evidence,selected)
                return Resolution(path,'NO_MATCH','Retained typed sheets checked; no certified status',evidence)
        return Resolution(path,'NOT_REQUESTED','No source request mapped for this path; not a website availability result')

    def exact_native_exclusion(self,sheet,pid):
        """Target-specific negative check; unrelated unmapped peers do not erase a full sheet.

        Requires an accepted provider alias or existing shared identity certificate. It grants
        an audit inference only, never a canonical writer or an absence cause.
        """
        if not sheet.get('native_complete_squad'):return False
        if 'identity_json' not in {r[1] for r in self.db.execute('PRAGMA table_info(validation_player_aliases)')}:return False
        provider=sheet.get('provider');prefix={'FotMob':'fotmob:','Torlytics':'https://torlytics.de/spieler/','Sky':'sky:'}.get(provider)
        if not prefix:return False
        rows=self.db.execute('SELECT provider_key,identity_json FROM validation_player_aliases WHERE player_id=?',(pid,)).fetchall()
        own=[r for r in rows if r['provider_key'].startswith(prefix)]
        from ..identity_registry import forms,lookup
        names=set();keys={r['provider_key'] for r in own}
        for row in rows:
            for name in json.loads(row['identity_json'] or '{}').get('names',[]):
                if isinstance(name,str):names.update(forms(name))
        if not names:return False
        if not own:
            # Reuse existing cross-provider identity certificates, not a new name join.
            certified=False
            for row in rows:
                for name in json.loads(row['identity_json'] or '{}').get('names',[]):
                    if isinstance(name,str) and len(name.split())>1:
                        try:certified=lookup(self.db,self.club_id,dict(provider_key='',name=name))==int(pid)
                        except ValueError:return False
                        if certified:break
                if certified:break
            if not certified:return False
        for player in sheet.get('native_players',[]):
            key=player.get('provider_key') or 'fotmob:'+str(player.get('provider_player_id'))
            if key in keys or names&forms(player['name']):return False
        return True

    def player_history(self,finding):
        """Check actual retained personal-history captures through SourceService."""
        from .engine import Resolution
        from dataclasses import replace
        tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'verified_identity_links','persistent_source_evidence'}<=tables:
            return Resolution('player_history','NOT_REQUESTED','Verified profile identity/evidence index not configured')
        ids=[str(r[0]) for r in self.db.execute('SELECT provider_player_id FROM verified_identity_links WHERE provider=? AND player_id=?',('FotMob',finding['player']))]
        if not ids and 'validation_player_aliases' in tables:
            ids=[r[0].split(':',1)[1] for r in self.db.execute("SELECT provider_key FROM validation_player_aliases WHERE player_id=? AND provider_key LIKE 'fotmob:%'",(finding['player'],))]
        if len(set(ids))!=1:return Resolution('player_history','NOT_REQUESTED','Unique retained or independently verified profile ID required; no numeric canonical-ID guess')
        fid=ids[0];left,right=finding['interval'];right=(day(right)-timedelta(days=1)).isoformat()
        cache_key=(fid,left,right)
        if cache_key not in self.history_cache:
            captures=[];seen=set();unresolved=[]
            for row in self.db.execute('SELECT * FROM persistent_source_evidence ORDER BY fetched_at DESC,evidence_id'):
                url=row['source_url'] or ''
                match=re.search(r'/players/(\d+)(?:/|$)',url)
                if not match or match[1]!=fid or row['body_sha256'] in seen:continue
                seen.add(row['body_sha256'])
                source=dict(row);acquired=self.service.store.normalize(source,'FotMob','player_history')
                if acquired.outcome!='FOUND':unresolved.append(acquired.outcome);continue
                a=acquired.artifact;request=SourceRequest('FotMob','player_history',url,'personal-history',expected_ids=dict(player=fid),context=dict(start=left,cutoff=right,include_club=True))
                a=RawSourceArtifact(request,a.content_sha256,a.locator,a.metadata,a.authority)
                _,observation=self.service.observe(request,a);e=self.source(source)
                if e and observation.outcome=='PARSED':captures.append((observation,e))
                else:unresolved.append(observation.outcome)
            self.history_cache[cache_key]=(captures,unresolved)
        captures,unresolved=self.history_cache[cache_key]
        if not captures:
            return Resolution('player_history','ARCHIVE_LOCATOR_UNRESOLVED' if unresolved else 'NOT_REQUESTED',
                'No usable exact-profile capture; source availability not inferred')
        evidence=tuple(dict.fromkeys(e for _,e in captures));candidates=[]
        if finding.get('canonical'):
            for artifact,detail_evidence in self.details.get(finding['canonical'],[]):
                if artifact.request.provider!='FotMob':continue
                mid=artifact.request.expected_ids.get('match')
                if mid is None:
                    from ..source_parsing import returned_match_id
                    mid=returned_match_id(artifact.request.target_url)
                claims=[m for observation,_ in captures for m in observation.native.get('matches',[]) if str(m['provider_match_id'])==str(mid)]
                if not claims:continue
                request=replace(artifact.request,context=dict(artifact.request.context,history_claims=claims))
                a=replace(artifact,request=request);_,observation=self.service.observe(request,a)
                if observation.outcome not in {'PARSED','INCOMPLETE'}:
                    return Resolution('player_history','CONFLICT','Profile claim and exact detail fail joint identity/minute validation',evidence+(detail_evidence,))
                for sheet in observation.native.get('sheets',[]):
                    team=finding['team']
                    if finding.get('domain')=='NATIONAL':
                        native_ids=self.db.execute('SELECT provider_team_id FROM football_team_provider_ids WHERE provider=? AND team_key=?',('FotMob',team)).fetchall()
                        if len(native_ids)!=1:continue
                        team=str(native_ids[0][0])
                    if str(sheet.get('provider_team_id'))!=team:continue
                    for p in sheet['players']:
                        if p['provider_player_id']!=fid:continue
                        s=SquadStatus(finding['player'],finding['canonical'],finding['team'],p['participation_status'],p['reported_minutes'],evidence+(detail_evidence,))
                        if s.reliable():
                            candidates.append(s)
                            self.keep_candidate(finding.get('domain','CLUB'),finding['canonical'],finding['team'],finding['player'],p,a,s.status,s.minutes)
        agreement=status_agreement(candidates)
        if candidates and not agreement['accepted']:
            return Resolution('player_history','CONFLICT','Retained history/detail claims disagree',tuple(dict.fromkeys(e for s in candidates for e in s.evidence)))
        if candidates:
            selected=next(s for s in candidates if s.minutes==agreement.get('minutes'))
            return Resolution('player_history','FOUND','Exact retained profile plus lineup; same publisher, not independent corroboration',selected.evidence,selected)
        return Resolution('player_history','NO_MATCH','All configured distinct exact-profile captures parsed; hints do not certify the unresolved squad claim',evidence)
