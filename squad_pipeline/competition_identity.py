"""Central competition identities; aliases are explicit provider assertions, not fuzzy names."""
BASE_LABELS = {'Premier League': 47, 'Bundesliga': 54, 'La Liga': 87, 'LaLiga': 87, 'Serie A': 55, 'Italian Serie A': 55, 'Ligue 1': 53, 'UEFA Champions League': 42, 'Champions League': 42, 'Carabao Cup': 133, 'EFL Cup': 133, 'FA Cup': 132, 'The FA Cup': 132, 'DFB Pokal': 209, 'DFB-Pokal': 209, 'German DFB Cup': 209, 'Copa del Rey': 138, 'Coupe de France': 141, 'Coppa Italia': 137, 'Community Shield': 247, 'UEFA Super Cup': 74, 'FIFA Club World Cup': 78, 'Trophée des Champions': 207, 'DFL Supercup': 'DFL_SUPERCUP'}
PROVIDER_LABELS = {'Sky': {'German Bundesliga':54,'Spanish La Liga':87,'Spanish Copa del Rey':138}}
# Provider labels are verified in archived directory+header/fixture replay.
# Their actual body hashes and canonical fixture decisions are persisted in registry.
COMP = BASE_LABELS | {label:cid for labels in PROVIDER_LABELS.values() for label,cid in labels.items()}
