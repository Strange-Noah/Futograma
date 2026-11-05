"""Safe additive audit summary, separate from public player-match facts."""
import json
from collections import Counter


def summary(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='reconstruction_findings'").fetchone():
        return None
    states, triggers = Counter(), Counter()
    for payload, in db.execute('SELECT payload FROM reconstruction_findings'):
        row=json.loads(payload);states[row['state']]+=1;triggers[row['trigger']]+=1
    return dict(scope='COMPLETENESS_AUDIT;NOT_APPEARANCES_OR_ABSENCE_CAUSES',
        states=dict(states),triggers=dict(triggers),complete=False,
        warning='Counts describe audited claims only; unavailable source coverage prevents completeness certification')
