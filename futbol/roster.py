"""Import source-reviewed registration facts; never infer registrations from contracts."""
from .common import ROOT,read_csv,write_csv,stable_id

def roster_audit(config,registrations):
    phases=read_csv(ROOT/'config/registration_phases.csv')
    rows=[]
    for phase in phases:
        evidence=[r for r in registrations if r['competition_scope']==phase['competition_scope'] and r.get('phase')==phase['phase'] and r.get('registration_confirmed')]
        expected=int(phase.get('expected_count') or 0)
        complete=phase['status']=='verified_complete' and bool(phase.get('source')) and expected>0 and len({r['player_id'] for r in evidence})==expected
        rows.append(dict(phase,confirmed_evidence_rows=len(evidence),cohort_complete=complete,
            reason='Full phase roster source and unique player count verified' if complete else 'Future window; not due' if phase['status']=='not_due' else 'Full official phase list not recovered; current snapshots and match squads cannot establish historical registration.'))
    return rows

def cohort_ids(config):
    """Official evidence is the primary cohort; observed squad evidence adds candidates."""
    primary={int(r['fotmob_player_id']) for r in read_csv(ROOT/'config/registration_evidence.csv')
             if int(r['fotmob_team_id'])==config['club']['fotmob_team_id']}
    from .common import read_json
    path=ROOT/'data/observed_club_candidates.json'
    observed={int(r['fotmob_player_id']) for r in read_json(path)} if path.exists() else set()
    return sorted(primary|observed|set(config['focus_fotmob_player_ids']))
