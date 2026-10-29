"""Explicit user-reviewed identity decisions, separate from automatic thresholds."""
from .core import BASE,ROOT,read,digest

def apply_manual(rows,resolutions,snapshot):
    path=BASE/'config/manual_identity_decisions.json'
    if not path.exists():return resolutions
    decisions=read(path)
    for row,x in zip(rows,resolutions):
        applicable=[d for d in decisions if d['snapshot_id']==snapshot['snapshot_id'] and d['official_member_id']==row['official_member_id']]
        if not applicable:continue
        if len(applicable)!=1:raise ValueError('Conflicting manual review records')
        d=applicable[0];proof=(ROOT/d['authorization_path']).resolve()
        if not proof.is_relative_to(BASE) or not proof.is_file() or digest(proof.read_bytes())!=d['authorization_sha256']:raise ValueError('Manual authorization artifact missing/corrupt')
        if d['official_name']!=row['name'] or d['club_id']!=snapshot['club_id'] or d['status']!='CONFIRMED_MANUAL':raise ValueError('Manual decision scope mismatch')
        pid=int(d['fotmob_player_id'])
        if x['player_id'] is not None and x['player_id']!=pid:raise ValueError('Manual decision conflicts with prior automatic identity')
        candidate=next((c for c in x['evidence'].get('candidates',[]) if c['fotmob_player_id']==pid),None)
        if not candidate:raise ValueError('Approved identity absent from actual cached candidates')
        previous=dict(status=x['status'],reason=x['reason'],automatic_threshold_satisfied=x['status']=='CONFIRMED_AUTOMATIC')
        x.update(status='CONFIRMED_MANUAL',reason='EXPLICIT_USER_AUTHORIZED_MANUAL_REVIEW',player_id=pid,name_fotmob=candidate['display_name'])
        x['evidence']=dict(x['evidence'],manual_decision=d,automatic_review_before_manual_decision=previous,manual_decision_is_not_automatic_evidence=True)
    return resolutions
