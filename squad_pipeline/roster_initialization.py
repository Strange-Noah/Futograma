"""Roster input strategies only; acquisition, identity, facts and audits stay shared.

Historical inputs are admitted club fixtures and identity-bound squad observations
from the existing source/canonical mechanisms. No database or network writes.
"""
from dataclasses import dataclass
from datetime import date
import re
from .international_store import digest

@dataclass(frozen=True)
class PeriodUnit:
    club_id: int
    season: str
    half: str
    start: str
    end_exclusive: str
    def __post_init__(self):
        if self.half not in {'H1','H2'} or date.fromisoformat(self.start)>=date.fromisoformat(self.end_exclusive):raise ValueError('EXPLICIT_VALID_REPORTING_PERIOD_REQUIRED')
    @property
    def key(self):return f'{self.club_id}:{self.season}:{self.half}'


def initialize(strategy,unit,*,catalogue=(),squads=(),catalogue_complete=False,accepted_current=()):
    """Return one roster-input contract, never a contractual-membership assertion.

    Catalogue rows and squads are already fixture-admitted, provider-identity
    checked inputs. Canonical player IDs must come from existing identity
    reconciliation; unresolved source entities remain explicit coverage debt.
    """
    if strategy not in {'ACCEPTED_CURRENT','HISTORICAL_SQUAD_UNION'}:raise ValueError('UNKNOWN_ROSTER_INITIALIZATION_STRATEGY')
    if strategy=='HISTORICAL_SQUAD_UNION' and accepted_current:raise ValueError('CROSS_PERIOD_ROSTER_SEED_FORBIDDEN')
    evidence={};players={};unresolved=set();covered=set();issues=[]
    fixtures={f['match_id']:f for f in catalogue if unit.start<=f['on']<unit.end_exclusive}
    required={mid for mid,f in fixtures.items() if f['state']=='COMPLETED'}
    if strategy=='ACCEPTED_CURRENT':
        for row in accepted_current:
            if row['included']:players[int(row['player_id'])]=dict(classification=row['classification'],observed_dates=[],source_refs=list(row.get('source_refs',[])))
        complete=True;scope='EXISTING_ACCEPTED_CURRENT_DECISIONS'
    else:
        for sheet in squads:
            mid=sheet['match_id'];fixture=fixtures.get(mid)
            if not fixture or fixture['state']!='COMPLETED' or sheet['club_id']!=unit.club_id:continue
            if sheet['on']!=fixture['on'] or sheet.get('admission')!='EXISTING_VERIFIED_FIXTURE_AND_SOURCE':
                issues.append(dict(match_id=mid,reason='SOURCE_FIXTURE_BINDING_REQUIRED'));continue
            ref=sheet['source'];key=ref.get('sha256')
            if not isinstance(key,str) or not re.fullmatch('[a-fA-F0-9]{64}',key) or not ref.get('url','').startswith('https://'):
                issues.append(dict(match_id=mid,reason='RETAINED_PROVENANCE_REQUIRED'));continue
            source_players=sheet['players'];ids=[p['provider_key'] for p in source_players]
            if len(ids)!=len(set(ids)):
                issues.append(dict(match_id=mid,reason='DUPLICATE_NATIVE_PLAYER'));continue
            mapped=[p.get('canonical_player_id') for p in source_players if p.get('canonical_player_id') is not None]
            if any(type(pid) is not int for pid in mapped) or len(mapped)!=len(set(mapped)):
                issues.append(dict(match_id=mid,reason='CANONICAL_IDENTITY_COLLISION_OR_TYPE'));continue
            if sheet['complete_squad']:covered.add(mid)
            for p in source_players:
                if p['role'] not in {'starters','bench'}:continue
                pid=p.get('canonical_player_id')
                if pid is None:unresolved.add((mid,p['provider_key']));continue
                entry=players.setdefault(int(pid),dict(classification='OBSERVED_SQUAD_MEMBER',observed_dates=[],source_refs=[]))
                entry['observed_dates'].append(sheet['on']);entry['source_refs'].append(ref)
            evidence[(mid,key)]=ref
        complete=bool(catalogue_complete and required and required<=covered and not unresolved and not issues and all(f['state'] in {'COMPLETED','POSTPONED','CANCELLED','SCHEDULED'} for f in fixtures.values()))
        scope='OBSERVED_MATCHDAY_SQUAD_UNION;NOT_CONTRACTUAL_POPULATION'
    for p in players.values():
        p['observed_dates']=sorted(set(p['observed_dates']));p['source_refs']=list({digest(x):x for x in p['source_refs']}.values())
    result=dict(unit=unit.__dict__,unit_key=unit.key,strategy=strategy,scope=scope,player_ids=sorted(players),players={str(k):players[k] for k in sorted(players)},catalogue_fixtures=len(fixtures),required_squads=len(required),verified_full_squads=len(required&covered),missing_squads=sorted(required-covered),unresolved_source_entities=len(unresolved),issues=issues,observed_population_accepted=complete,management_intervals_inferred=False,unobserved_player_searches=0,canonical_writes=0)
    result['freeze_sha256']=digest(result);return result


def cross_period_findings(previous,current):
    """Post-freeze R1 triggers; no transfer/termination inferred from disappearance."""
    for result in [previous,current]:
        material={k:v for k,v in result.items() if k!='freeze_sha256'}
        if digest(material)!=result['freeze_sha256']:raise ValueError('IMMUTABLE_PERIOD_FREEZE_REQUIRED')
    if not previous['observed_population_accepted'] or not current['observed_population_accepted']:raise ValueError('COMPLETE_OBSERVED_UNIONS_REQUIRED_FOR_PLAYER_DIFFERENCE_AUDIT')
    if previous['unit']['club_id']!=current['unit']['club_id'] or previous['unit']['end_exclusive']!=current['unit']['start']:raise ValueError('ADJACENT_SAME_CLUB_PERIODS_REQUIRED')
    a,b=set(previous['player_ids']),set(current['player_ids']);out=[]
    changed={p for p in a&b if previous['players'][str(p)]['classification']!=current['players'][str(p)]['classification']}
    for pid in sorted((a^b)|changed):
        direction='ADDED' if pid in b-a else 'DISAPPEARED' if pid in a-b else 'CLASSIFICATION_CHANGED'
        out.append(dict(trigger='R1',player=str(pid),team=str(current['unit']['club_id']),domain='CLUB',canonical=None,interval=[current['unit']['start'],current['unit']['end_exclusive']],opportunities=[],change=direction,claim='Explain material difference between independently frozen observed populations',confidence_status='NOT_CHECKED',human_pending=False,canonical_correction_authorized=False,initialization_population_change_authorized=False,accepted_explanation_types=['TRANSFER','LOAN','RETURN','YOUTH_CHANGE','DOCUMENTED_WHOLE_PERIOD_INJURY_OR_PROLONGED_ABSENCE'],source_freezes=[previous['freeze_sha256'],current['freeze_sha256']]))
    return out
