"""Shared bounded minute selection. Status evidence precedes numerical tolerance."""
import json,hashlib
PLAYED={'STARTER','SUBSTITUTE_USED'}
NOT_PLAYED={'UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD'}
PRIORITY={'OFFICIAL_REPORTED':0,'RETAINED_ACCEPTED':5,'FotMob':10,'Torlytics':20,'Sky':30,'DERIVED_CLOCK':90}
VERSION='FIVE_MINUTE_SOURCE_PRIORITY_V1'
def select(observations):
    original=[dict(o) for o in observations];known=[o for o in original if o.get('status') in PLAYED|NOT_PLAYED]
    kinds={o['status'] in PLAYED for o in known}
    if len(kinds)>1:return dict(minutes=None,accepted=False,reason='APPEARANCE_STATUS_CONTRADICTION',observations=original)
    values=[o for o in known if type(o.get('minutes')) in (int,float) and 0<=o['minutes']<=150]
    if any(o['status'] in NOT_PLAYED and o['minutes']!=0 for o in values):return dict(minutes=None,accepted=False,reason='NONAPPEARANCE_POSITIVE_MINUTES',observations=original)
    if not values:return dict(minutes=None,accepted=False,reason='NO_VALID_NUMERICAL_MINUTES',observations=original)
    if max(o['minutes'] for o in values)-min(o['minutes'] for o in values)>5:return dict(minutes=None,accepted=False,reason='MATERIAL_MINUTE_SOURCE_CONFLICT',observations=original)
    # Stable provider/source/value ordering, never arrival order or averaging.
    chosen=min(values,key=lambda o:(PRIORITY.get(o.get('provider'),50),o.get('provider',''),o.get('source_url',''),o['minutes']))
    return dict(minutes=chosen['minutes'],accepted=True,reason=VERSION,selected_source=dict(chosen),observations=original)
def audit(db,domain,match_id,player_id,result):
    db.execute('CREATE TABLE IF NOT EXISTS minute_policy_decisions(decision_id TEXT PRIMARY KEY,domain TEXT,match_id TEXT,player_id INTEGER,policy TEXT,payload_json TEXT)')
    payload=json.dumps(result,ensure_ascii=False,sort_keys=True);key=hashlib.sha256(json.dumps([domain,str(match_id),player_id,payload],ensure_ascii=False).encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO minute_policy_decisions VALUES(?,?,?,?,?,?)',(key,domain,str(match_id),player_id,VERSION,payload));return key
