"""Match-specific availability resolution; absence is never injury evidence."""
def resolve(played,role,history,entry,detail_available):
    if played:return 'available','appeared','observed_appearance','match_appearance'
    if role=='subs' or (history and history.get('onBench') is True):
        return 'available','unused_substitute','named_in_match_squad','match_squad'
    if role=='unavailable':
        typ=str((entry['lineup'].get('unavailability') or {}).get('type','')).lower()
        status='injured' if typ in ('injury','illness') else 'suspended' if 'suspen' in typ else 'unknown'
        return status,'not_in_squad',typ or 'unspecified','match_specific_unavailable'
    if detail_available:return 'unknown','not_in_squad','absence_reason_unknown','lineup_absence_not_medical'
    return 'unknown','unknown','match_squad_not_available','missing_match_detail'
