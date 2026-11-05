"""R1 -> R2 -> R5 -> R3/R4 -> R6; never infer a football fact from a gap.

Adapters supply independently verified evidence. Findings and explanations are
separate from accepted appearances. The caller owns the transaction and export.
"""
from dataclasses import dataclass, asdict
from datetime import date, timedelta
import hashlib
import json
import re

MODES = {'ONBOARD', 'BACKFILL', 'INCREMENTAL', 'DELAYED_RECONCILIATION'}
KNOWN = {'STARTER', 'SUBSTITUTE_USED', 'UNUSED_SUBSTITUTE', 'NOT_IN_MATCHDAY_SQUAD', 'ABSENT_COMPLETE_SQUAD', 'OUT_OF_SCOPE'}
PATHS = ('player_history', 'team_history', 'fixture_details', 'official_squad',
         'substitutions', 'callups_withdrawals', 'transfer_academy',
         'injury_availability', 'disciplinary', 'announcements',
         'press_conference', 'match_reports', 'identity_archives')


def encoded(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(x):
    return hashlib.sha256(encoded(x).encode()).hexdigest()


def day(value):
    return date.fromisoformat(value)


@dataclass(frozen=True)
class Evidence:
    provider: str
    url: str
    sha256: str
    locator: str
    observed_at: str
    origin: str  # underlying publisher/claim identity, not syndication outlet
    time_precision: str = 'TIMESTAMP'
    authority: str = 'SOURCE_RESPONSE'

    def valid(self):
        from datetime import datetime
        try:
            if self.authority=='ACCEPTED_BASELINE' and self.time_precision=='RETAINED_RECORD':
                time_valid=True  # receipt of retained authority, not a new HTTP observation
            elif self.time_precision=='DAY':
                day(self.observed_at);time_valid=True
            else:
                observed = datetime.fromisoformat(self.observed_at.replace('Z', '+00:00'))
                time_valid=observed.tzinfo is not None
        except (ValueError, TypeError):
            return False
        origin_valid=(self.url.startswith('baseline:') if self.authority=='ACCEPTED_BASELINE' else self.url.startswith('policy:') if self.authority=='MANUAL_POLICY' else self.url.startswith('https://'))
        return bool(self.provider and origin_valid and self.locator
                    and self.origin and re.fullmatch('[a-fA-F0-9]{64}', self.sha256)
                    and time_valid)


@dataclass(frozen=True)
class Membership:
    player: str
    team: str
    domain: str
    level: str
    start: str
    end: str  # exclusive, only audited interval; not an invented joining date
    status: str
    evidence: tuple[Evidence, ...]
    precision: str = 'VERIFIED_INTERVAL'
    region: str = 'UNKNOWN'
    coverage_evidence: tuple[Evidence, ...] = ()

    def covers(self, fixture):
        return (self.team == fixture.team and self.domain == fixture.domain
                and self.level == fixture.level and self.start <= fixture.on < self.end)

    def managed(self):
        return self.status in {'FIRST_TEAM', 'FIRST_TEAM_INJURED', 'LOAN_IN', 'AUTHORIZED_EXCEPTION', 'MATCHDAY_REPRESENTATIVE'}


@dataclass(frozen=True)
class Fixture:
    canonical: str
    provider_id: str
    returned_id: str
    team: str
    opponent: str
    domain: str
    level: str
    competition: str
    on: str
    evidence: tuple[Evidence, ...]
    provider_bindings: tuple = ()
    identity_basis: str = 'SOURCE_VERIFIED'


@dataclass(frozen=True)
class SquadStatus:
    player: str
    canonical: str
    team: str
    status: str
    minutes: int | None
    evidence: tuple[Evidence, ...]
    complete_squad: bool = False
    authority: str = 'SOURCE_OBSERVATION'

    def matchday_known(self):
        return bool(self.evidence and all(e.valid() for e in self.evidence) and
            (self.reliable() or self.status in {'STARTER','SUBSTITUTE_USED','BENCH_PARTICIPATION_UNKNOWN'}))

    def reliable(self):
        if not self.evidence or not all(e.valid() for e in self.evidence):
            return False
        if self.status not in KNOWN:
            return False
        if self.status == 'ABSENT_COMPLETE_SQUAD':
            return self.complete_squad and self.minutes is None
        if self.status == 'OUT_OF_SCOPE':
            return self.minutes is None
        numeric = type(self.minutes) is int and 0 <= self.minutes <= 150
        return numeric and (self.minutes == 0 if self.status in {'UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD'} else True)


@dataclass(frozen=True)
class Window:
    kind: str
    start: str
    end: str  # exclusive
    competition: str
    team: str | None
    level: str
    uncertainty: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Resolution:
    path: str
    outcome: str  # FOUND, NO_MATCH, UNAVAILABLE, CONFLICT
    coverage: str
    evidence: tuple[Evidence, ...] = ()
    status: SquadStatus | None = None
    explanation: str | None = None
    explanation_start: str | None = None
    explanation_end: str | None = None
    retry_on: str | None = None


def migrate(db):
    for sql in (
        'CREATE TABLE IF NOT EXISTS reconstruction_findings(claim_key TEXT PRIMARY KEY, dependency TEXT NOT NULL, payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS reconstruction_attempts(attempt_key TEXT PRIMARY KEY, claim_key TEXT NOT NULL, payload TEXT NOT NULL)',
        'CREATE TABLE IF NOT EXISTS reconstruction_transitions(transition_key TEXT PRIMARY KEY,claim_key TEXT NOT NULL,payload TEXT NOT NULL)',
    ):
        db.execute(sql)


def windows(fixtures, calendars):
    """Official periods and observed fixture spans stay distinct; no appearance input."""
    result = []
    for w in calendars:
        if w.kind != 'OFFICIAL' or not w.evidence or not all(e.valid() for e in w.evidence):
            raise ValueError('OFFICIAL_CALENDAR_EVIDENCE_REQUIRED')
        if day(w.end) <= day(w.start):
            raise ValueError('INVALID_CALENDAR_INTERVAL')
        result.append(w)
    groups = {}
    for f in fixtures:
        if f.domain == 'NATIONAL':
            groups.setdefault((f.team, f.level, f.competition), []).append(f)
    for (team, level, competition), rows in sorted(groups.items()):
        # A span covers observed fixtures only; never calls itself an official window.
        rows = sorted(rows, key=lambda f: f.on)
        clusters = []
        for f in rows:
            if not clusters or (day(f.on) - day(clusters[-1][-1].on)).days > 14:
                clusters.append([])
            clusters[-1].append(f)
        for cluster in clusters:
            result.append(Window('OBSERVED', cluster[0].on,
                (day(cluster[-1].on) + timedelta(days=1)).isoformat(), competition,
                team, level, 'OBSERVED_FIXTURE_SPAN_ONLY',
                tuple(e for f in cluster for e in f.evidence)))
    return result


def validate_inputs(members, fixtures, statuses, start, cutoff):
    if day(cutoff) < day(start):
        raise ValueError('INVALID_AUDIT_INTERVAL')
    for m in members:
        if m.domain not in {'CLUB', 'NATIONAL'} or day(m.end) <= day(m.start):
            raise ValueError('INVALID_MEMBERSHIP_INTERVAL')
        if not m.evidence or not all(e.valid() for e in m.evidence):
            raise ValueError('MEMBERSHIP_EVIDENCE_REQUIRED')
    indexed = {}
    for f in fixtures:
        day(f.on)
        baseline=(f.identity_basis=='ACCEPTED_CANONICAL' and any(e.authority=='ACCEPTED_BASELINE' for e in f.evidence))
        if (not f.canonical or not (f.provider_id or baseline) or f.provider_id != f.returned_id
                or not f.team or not f.opponent or f.team == f.opponent
                or not f.competition or f.domain not in {'CLUB', 'NATIONAL'}
                or not f.evidence or not all(e.valid() for e in f.evidence)):
            raise ValueError('STRICT_FIXTURE_EVIDENCE_REQUIRED')
        key = (f.canonical, f.team)
        if key in indexed and indexed[key] != f:
            raise ValueError('CONFLICTING_CANONICAL_FIXTURE')
        indexed[key] = f
    for s in statuses:
        if (s.canonical, s.team) not in indexed:
            raise ValueError('STATUS_REQUIRES_EXACT_FIXTURE_TEAM')
    return indexed


def coalesce_fixtures(fixtures):
    """Keep independently bound provider observations of one canonical fixture."""
    from dataclasses import replace
    grouped={}
    fields=('canonical','team','opponent','domain','level','competition','on')
    for fixture in fixtures:
        if fixture.provider_id!=fixture.returned_id:raise ValueError('STRICT_FIXTURE_EVIDENCE_REQUIRED')
        key=(fixture.canonical,fixture.team)
        baseline=fixture.identity_basis=='ACCEPTED_CANONICAL'
        bindings=fixture.provider_bindings or ((('ACCEPTED_CANONICAL',fixture.canonical,fixture.canonical),) if baseline else tuple((e.provider,fixture.provider_id,fixture.returned_id) for e in fixture.evidence))
        if any(not provider or not asked or asked!=returned for provider,asked,returned in bindings):
            raise ValueError('STRICT_FIXTURE_EVIDENCE_REQUIRED')
        if key not in grouped:
            grouped[key]=replace(fixture,provider_bindings=bindings);continue
        previous=grouped[key]
        if any(getattr(previous,k)!=getattr(fixture,k) for k in fields):
            raise ValueError('CONFLICTING_CANONICAL_FIXTURE')
        grouped[key]=replace(previous,evidence=tuple(dict.fromkeys(previous.evidence+fixture.evidence)),
            provider_bindings=tuple(dict.fromkeys(previous.provider_bindings+bindings)))
    return list(grouped.values())


def preferred_statuses(statuses):
    reliable=[s for s in statuses if s.reliable()]
    accepted=[s for s in reliable if s.authority=='ACCEPTED_BASELINE' and any(e.authority=='ACCEPTED_BASELINE' for e in s.evidence)]
    return accepted or reliable


def status_agreement(statuses):
    """Assess discrepancies with the existing policy; never write a fact."""
    if not statuses:return dict(accepted=False,reason='NO_STATUS_EVIDENCE')
    if len({s.status for s in statuses})>1:return dict(accepted=False,reason='PARTICIPATION_OR_ROLE_CONTRADICTION')
    if len({s.minutes for s in statuses})==1:return dict(accepted=True,minutes=statuses[0].minutes,reason='EXACT_STATUS_AGREEMENT')
    from ..minute_policy import select
    return select([dict(status=s.status,minutes=s.minutes,provider='RETAINED_ACCEPTED' if s.authority=='ACCEPTED_BASELINE' else s.evidence[0].provider,
        source_url=s.evidence[0].url) for s in statuses])


def opportunities(members, fixtures, statuses, start, cutoff):
    """R4 detects obligations using scoped baseline eligibility and verified peers.

    Returns findings/assessments only. Accepted statuses remain authoritative;
    materially incompatible observations become challenges, not corrections.
    """
    findings = []
    for m in members:
        if not m.managed():
            continue
        for f in fixtures:
            if not start <= f.on <= cutoff or not m.covers(f):
                continue
            scoped=[s for s in statuses if s.canonical==f.canonical and s.team==f.team]
            peer_statuses=[s for pid in {s.player for s in scoped} for s in preferred_statuses([s for s in scoped if s.player==pid])]
            peers = {s.player for s in peer_statuses if s.canonical == f.canonical
                     and s.team == f.team and s.player != m.player and s.reliable()
                     and s.status in {'STARTER', 'SUBSTITUTE_USED'}
                     and any(p.player == s.player and p.managed() and p.covers(f) for p in members)}
            if not peers:
                continue
            target = [s for s in statuses if s.player == m.player
                      and s.canonical == f.canonical and s.team == f.team and s.reliable()]
            retained=[s for s in scoped if s.player==m.player and s.authority=='ACCEPTED_BASELINE']
            authoritative=preferred_statuses(target)
            semantic = {(s.status, s.minutes) for s in authoritative}
            state = 'CONFLICT' if len(semantic) > 1 else authoritative[0].status if authoritative else 'INSUFFICIENT_COVERAGE'
            accepted=any(s.authority=='ACCEPTED_BASELINE' for s in authoritative)
            agreement=status_agreement(target)
            variation=accepted and any((s.status,s.minutes) not in semantic for s in target if s.authority!='ACCEPTED_BASELINE')
            challenge=bool(variation and not agreement['accepted'])
            relaxed = (f.domain == 'NATIONAL' and m.region not in {'UEFA', 'CONMEBOL', 'UNKNOWN'}
                       and bool(m.coverage_evidence) and all(e.valid() for e in m.coverage_evidence))
            findings.append(dict(player=m.player, canonical=f.canonical, team=f.team,
                domain=f.domain, level=f.level, on=f.on, state=state,
                teammates=sorted(peers), trigger='R4', relaxed=relaxed,
                limitation='INDEPENDENT_COVERAGE_MAY_BE_WEAKER' if relaxed else None,
                required_evidence='Exact identity-bound squad/participation evidence or complete squad exclusion',
                interval=[f.on, (day(f.on)+timedelta(days=1)).isoformat()],
                accepted_state_retained=state if accepted else retained[0].status if retained else None,
                investigation_scope='MATERIAL_CONTRADICTION' if challenge else 'MINUTES_OR_PARTICIPATION_DETAIL' if retained and not authoritative else 'MATCHDAY_STATUS' if not authoritative else None,
                challenge='MATERIAL_SOURCE_CONTRADICTION;ACCEPTED_FACT_UNCHANGED' if challenge else None,
                compatible_source_variation=bool(variation and agreement['accepted']),comparison_policy=agreement['reason'],
                variation_assessment=dict(claim='The numerical source variation is a material contradiction',
                    evidence=[dict(status=s.status,minutes=s.minutes,authority=s.authority,references=[asdict(e) for e in s.evidence]) for s in target],
                    direction='REFUTES',confidence_status='REFUTED',limitations='Only the existing same-role minute-policy tolerance is applied; participation or role changes are not waived',
                    next_action='Preserve accepted minutes and retain source observations') if variation and agreement['accepted'] else None,
                confidence_status='CONFLICTED' if challenge or state=='CONFLICT' else 'CONFIRMED' if accepted else 'SUPPORTED_BUT_UNCONFIRMED' if authoritative else 'INDETERMINATE' if retained else 'NOT_CHECKED',
                assessment=dict(claim='The target has sufficient matchday-status evidence for this exact opportunity',
                    evidence=[asdict(e) for s in retained+target for e in s.evidence],
                    direction='CONFLICTS' if challenge or state=='CONFLICT' else 'SUPPORTS' if authoritative else 'SUPPORTS_RETAINED_SCOPE_ONLY' if retained else 'NOT_YET_ASSESSED',
                    confidence_status='CONFLICTED' if challenge or state=='CONFLICT' else 'CONFIRMED' if accepted else 'SUPPORTED_BUT_UNCONFIRMED' if authoritative else 'INDETERMINATE' if retained else 'NOT_CHECKED',
                    limitations='Accepted scope retained; new observations do not authorize a correction' if accepted else 'Source observations are not canonical adjudications',
                    next_action='Investigate the concrete contradiction without changing accepted data' if challenge else 'Preserve accepted result; no blanket revalidation' if accepted else 'Assess relevant available evidence paths')))
    return findings


def gaps(members, fixtures, statuses, opportunities_, start, cutoff):
    """R3 warnings require a qualifying gap and actual peer opportunity.

    A warning is neither a nonselection fact nor an absence explanation.
    Accepted matchday evidence remains an anchor despite incomplete new replay.
    """
    result = []
    for m in members:
        if not m.managed():
            continue
        left = max(day(start), day(m.start))
        right = min(day(cutoff) + timedelta(days=1), day(m.end))
        if right <= left:
            continue
        anchors = sorted({day(f.on) for f in fixtures if m.covers(f) and left <= day(f.on) < right
                          and any(s.player == m.player and s.canonical == f.canonical
                                  and s.team == f.team and s.matchday_known() for s in statuses)
                          and (any(s.authority=='ACCEPTED_BASELINE' and s.matchday_known() for s in statuses if s.player==m.player and s.canonical==f.canonical and s.team==f.team)
                               or len({s.status for s in statuses if s.player==m.player and s.canonical==f.canonical and s.team==f.team and s.matchday_known()})==1)})
        cursor = left
        for anchor in anchors + [right]:
            # Reliable evidence days are not counted as missing days.
            if (anchor - cursor).days >= 5:
                peers = [o for o in opportunities_ if o['player'] == m.player
                         and o['team'] == m.team and o['domain'] == m.domain
                         and cursor.isoformat() <= o['on'] < anchor.isoformat()]
                if peers:
                    result.append(dict(player=m.player, team=m.team, domain=m.domain,
                        trigger='R3', interval=[cursor.isoformat(), anchor.isoformat()],
                        canonical=None, state='COMPLETENESS_WARNING',
                        opportunities=sorted({o['canonical'] for o in peers}),
                        required_evidence='Matchday evidence within gap; no absence or injury inferred'))
            cursor = anchor + timedelta(days=1)
    return result


def active_resolution(db, finding, adapter, dependency, cutoff, budget):
    """Assess relevant evidence with limits; persist only the audit ledger.

    Results preserve direction, limitations and retained authority. Technical
    retry/source-work states are separate from human Pending. No returned
    resolution authorizes a canonical correction; existing adjudication owns it.
    """
    claim = {k: finding.get(k) for k in ('player', 'team', 'domain', 'canonical', 'trigger', 'interval')}
    key = digest(claim)
    old = db.execute('SELECT dependency,payload FROM reconstruction_findings WHERE claim_key=?', (key,)).fetchone()
    if old and old[0] == dependency:
        prior = json.loads(old[1])
        if not prior.get('next_retry_on') or cutoff < prior['next_retry_on']:
            return dict(prior, reused=True, new_attempts=0)
    attempts, candidates, explanations = [], [], []
    plan = PATHS if finding['domain'] == 'NATIONAL' else tuple(p for p in PATHS if p != 'callups_withdrawals')
    exhausted = False
    for path in plan:
        if budget[0] <= 0:
            exhausted = True
            break
        result = adapter.resolve(path, finding)
        if not isinstance(result, Resolution) or result.path != path or result.outcome not in {'FOUND', 'NO_MATCH', 'UNAVAILABLE', 'CONFLICT', 'NOT_REQUESTED', 'ARCHIVE_LOCATOR_UNRESOLVED', 'PARSER_NOT_IMPLEMENTED', 'TEMPORARILY_FAILED', 'ACCESS_RESTRICTED', 'RATE_LIMITED'}:
            raise ValueError('INVALID_RESOLUTION_RESPONSE')
        if not result.coverage:
            raise ValueError('RESOLUTION_COVERAGE_REQUIRED')
        if result.outcome not in {'NOT_REQUESTED','PARSER_NOT_IMPLEMENTED','ARCHIVE_LOCATOR_UNRESOLVED'}:
            budget[0] -= 1
        if result.outcome in {'FOUND','NO_MATCH','CONFLICT'} and (not result.evidence or not all(e.valid() for e in result.evidence)):
            raise ValueError('RESOLUTION_EVIDENCE_REQUIRED')
        if result.retry_on:
            day(result.retry_on)
            if result.retry_on <= cutoff:
                raise ValueError('RETRY_MUST_BE_AFTER_CUTOFF')
        attempts.append(asdict(result))
        if result.status:
            s = result.status
            if (s.player != finding['player'] or s.team != finding['team']
                    or s.canonical != finding['canonical'] or not s.reliable()):
                raise ValueError('RESOLUTION_IDENTITY_OR_STATUS_MISMATCH')
            candidates.append(s)
        if result.explanation:
            if (not result.evidence or not result.explanation_start or not result.explanation_end
                    or result.explanation_start > finding['interval'][0]
                    or result.explanation_end < finding['interval'][1]):
                raise ValueError('EXPLANATION_DOES_NOT_COVER_CLAIM_INTERVAL')
            explanations.append(dict(cause=result.explanation, evidence=asdict(result)))
        # Continue all relevant paths to detect conflict; no first-source-wins.
    agreement=status_agreement(candidates)
    if candidates and agreement['accepted']:
        candidates.sort(key=lambda s:(s.minutes!=agreement.get('minutes'),s.evidence[0].provider,s.evidence[0].url))
    conflict = (finding.get('state') == 'CONFLICT' or bool(finding.get('challenge')) or bool(candidates and not agreement['accepted'])
                or any(a['outcome'] == 'CONFLICT' for a in attempts))
    unperformed=any(a['outcome'] in {'NOT_REQUESTED','PARSER_NOT_IMPLEMENTED','ARCHIVE_LOCATOR_UNRESOLVED'} for a in attempts)
    state = ('CONFLICT' if conflict else 'RESOLVED' if candidates else 'SOURCE_WORK_REQUIRED' if unperformed and not exhausted else 'PENDING')
    unavailable = len(attempts) == len(plan) and all(a['outcome']=='UNAVAILABLE' for a in attempts)
    # Transport unavailability is explicit, never an invented performed search.
    result = dict(finding, claim_key=key, state=state, attempts=attempts,
        new_attempts=sum(a['outcome'] not in {'NOT_REQUESTED','PARSER_NOT_IMPLEMENTED','ARCHIVE_LOCATOR_UNRESOLVED'} for a in attempts), reused=False, explanations=explanations,
        resolution_basis='STATUS_EVIDENCE' if candidates else 'RESOURCE_LIMIT' if exhausted else 'SOURCE_ADAPTER_WORK_REQUIRED' if unperformed else 'PATHS_UNAVAILABLE' if unavailable else 'PLAN_EXHAUSTED',
        why_unresolved=None if state=='RESOLVED' else 'No uncontested identity-bound matchday status recovered',
        resolved_status=asdict(candidates[0]) if candidates and not conflict else None,
        next_retry_on=min([a['retry_on'] for a in attempts if a['retry_on']] or [(day(cutoff)+timedelta(days=1)).isoformat()]) if state!='RESOLVED' else None,
        retry_conditions='Relevant evidence/version change, new adapter availability or next retry date',
        remaining_paths=list(dict.fromkeys([a['path'] for a in attempts if a['outcome'] in {'NOT_REQUESTED','PARSER_NOT_IMPLEMENTED','ARCHIVE_LOCATOR_UNRESOLVED'}]+list(plan[len(attempts):]))))
    retained_evidence=(finding.get('assessment') or {}).get('evidence',[])
    evidence_inputs=[e for e in retained_evidence+[e for a in attempts for e in a['evidence']] if e.get('authority')!='ACCEPTED_BASELINE']
    groups=[]
    for e in evidence_inputs:
        tokens={'origin:'+e['origin'],'body:'+e['sha256']}
        overlapping=[g for g in groups if g&tokens]
        for group in overlapping:tokens|=group;groups.remove(group)
        groups.append(tokens)
    confidence='CONFLICTED' if conflict else 'CONFIRMED' if candidates else 'NOT_IMPLEMENTED' if any(a['outcome']=='PARSER_NOT_IMPLEMENTED' for a in attempts) else 'NOT_CHECKED' if unperformed or exhausted else 'INDETERMINATE'
    result['assessment']=dict(claim='Recover an exact, defensible status for the audited claim',
        evidence=[dict(path='retained_input',references=retained_evidence)]+[dict(path=a['path'],outcome=a['outcome'],references=a['evidence']) for a in attempts],
        direction='CONFLICTS' if conflict else 'SUPPORTS' if candidates else 'NO_DIRECTION_ESTABLISHED',confidence_status=confidence,
        limitations='Conclusion is scoped to exact verified fixture/player evidence, not an absence cause or authority to change accepted facts; shared origins are not independent corroboration',
        next_action='Independent explicit adjudication before any canonical correction' if candidates else 'Implement or execute remaining relevant paths' if unperformed else 'Resume bounded investigation' if exhausted else 'Reassess on new material evidence')
    result['independent_source_origins']=len(groups)
    result['independence_limitations']='Distinct retained origin/body groups; unknown syndication is not proven independent corroboration'
    result['human_pending']=bool(conflict and not unperformed and not exhausted and len(groups)>1)
    result['canonical_correction_authorized']=False
    for a in attempts:
        db.execute('INSERT OR IGNORE INTO reconstruction_attempts VALUES(?,?,?)', (digest([key, dependency, a]), key, encoded(a)))
    semantic = {k:v for k,v in result.items() if k not in {'reused','new_attempts'}}
    db.execute('INSERT OR IGNORE INTO reconstruction_transitions VALUES(?,?,?)', (digest([key,dependency,semantic]), key, encoded(semantic)))
    db.execute('INSERT INTO reconstruction_findings VALUES(?,?,?) ON CONFLICT(claim_key) DO UPDATE SET dependency=excluded.dependency,payload=excluded.payload', (key, dependency, encoded(result)))
    return result


def run(db, adapter, start, cutoff, mode='INCREMENTAL', max_attempts=200):
    if mode not in MODES or type(max_attempts) is not int or max_attempts < 0:
        raise ValueError('INVALID_MODE_OR_BUDGET')
    migrate(db)
    members, fixtures, statuses, calendars = adapter.discover(start, cutoff, mode)
    fixtures = coalesce_fixtures(fixtures)
    validate_inputs(members, fixtures, statuses, start, cutoff)
    context = windows(fixtures, calendars)  # R5 strictly precedes both completeness checks
    r4 = opportunities(members, fixtures, statuses, start, cutoff)
    for finding in r4:
        finding['window_context'] = [asdict(w) for w in context if finding['domain']=='NATIONAL'
            and w.start <= finding['on'] < w.end and w.level==finding['level']
            and (w.team is None or w.team==finding['team'])]
    r3 = gaps(members, fixtures, statuses, r4, start, cutoff)
    dependency = digest([adapter.version, [asdict(m) for m in members],
        [asdict(f) for f in fixtures], [asdict(s) for s in statuses], [asdict(w) for w in context]])
    budget = [max_attempts]
    resolutions = [active_resolution(db, f, adapter, dependency, cutoff, budget)
                   for f in r4 if f['state'] in {'CONFLICT','INSUFFICIENT_COVERAGE'} or f.get('challenge')]
    recovered = []
    for resolution in resolutions:
        item = resolution.get('resolved_status')
        if resolution['state']=='RESOLVED' and item:
            item = dict(item, evidence=tuple(Evidence(**e) for e in item['evidence']))
            recovered.append(SquadStatus(**item))
    final_statuses = list(statuses) + recovered
    final_r4 = opportunities(members, fixtures, final_statuses, start, cutoff)
    final_gaps = gaps(members, fixtures, final_statuses, final_r4, start, cutoff)
    # Resolving a precise squad status can remove a longitudinal trigger. Do not
    # open obsolete R3 tasks or rerun a full source search for those intervals.
    resolutions += [active_resolution(db, f, adapter, dependency, cutoff, budget) for f in final_gaps]
    return dict(mode=mode, stages=['R1','R2','R5','R3_R4','R6'], windows=[asdict(w) for w in context],
        opportunities=r4, gaps=r3, final_opportunities=final_r4, final_gaps=final_gaps, resolutions=resolutions,
            metrics=dict(opportunities=len(r4),club_opportunities=sum(f['domain']=='CLUB' for f in r4),national_opportunities=sum(f['domain']=='NATIONAL' for f in r4),
            r3_triggers=len(r3), r4_triggers=sum(f['state'] in {'CONFLICT','INSUFFICIENT_COVERAGE'} or bool(f.get('challenge')) for f in r4),
            resolved=sum(r['state']=='RESOLVED' for r in resolutions), final_gap_count=len(final_gaps),
            recovered_status_candidates=len(recovered), recovered_appearance_rows=0, pending=sum(r['state']=='PENDING' for r in resolutions),
            conflicts=sum(r['state']=='CONFLICT' for r in resolutions), attempts=max_attempts-budget[0], reused=sum(r['reused'] for r in resolutions),
            source_work_required=sum(r['state']=='SOURCE_WORK_REQUIRED' for r in resolutions),
            accepted_challenges=sum(bool(f.get('challenge')) for f in r4),human_pending=sum(r.get('human_pending',False) for r in resolutions),
            compatible_source_variations=sum(f.get('compatible_source_variation',False) for f in r4),
            retained_response_checks=sum(a['outcome'] in {'FOUND','NO_MATCH','CONFLICT'} for r in resolutions for a in r['attempts']),
            unperformed_paths=sum(a['outcome']=='NOT_REQUESTED' for r in resolutions for a in r['attempts']),
            supported_absences=sum(f['state']=='ABSENT_COMPLETE_SQUAD' for f in r4)),
        completeness_certified=False, calendar_coverage='OFFICIAL_AND_OBSERVED' if calendars else 'OBSERVED_ONLY;OFFICIAL_CALENDAR_UNAVAILABLE')
