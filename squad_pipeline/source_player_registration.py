"""New source entities retain their real source namespace; never name-merge or grant management."""
import re,hashlib
from urllib.parse import urlsplit
from .six_club_experiment import addplayer
from .identity_pipeline import Record,persist_verified_alias

def register(db,club_id,player,source):
    key=player.get('provider_key','');u=urlsplit(key)
    if not (u.scheme=='https' and u.hostname=='torlytics.de' and re.fullmatch(r'/spieler/[a-zA-Z0-9-]+',u.path)) and not re.fullmatch(r'fotmob:[1-9][0-9]*',key):raise ValueError('NEW_SOURCE_PLAYER_REQUIRES_REAL_STABLE_PROVIDER_KEY')
    if not source.get('sha256') or not source.get('url'):raise ValueError('HASHED_VALIDATED_SHEET_REQUIRED')
    provider='FotMob' if key.startswith('fotmob:') else 'Torlytics';value=key.split(':',1)[1] if provider=='FotMob' else key
    pid=int(value) if provider=='FotMob' else -int(hashlib.sha256(key.encode()).hexdigest()[:15],16)
    if db.execute('SELECT 1 FROM players WHERE player_id=?',(pid,)).fetchone():raise ValueError('NEW_SOURCE_ID_COLLISION;NO_NAME_MERGE')
    addplayer(db,dict(player_id=pid,display_name=player['name'],identity_status='VERIFIED_SOURCE_ENTITY;CROSS_PROVIDER_NOT_ASSUMED',provider_keys=[key],source_names=[player['name']],fotmob_id=int(value) if provider=='FotMob' else None))
    record=Record(source['url'],source['sha256'],player.get('json_path','validated_complete_club_sheet'),provider,[player['name']],provider_ids={provider:value},official_clubs=[],scope='SOURCE_IDENTITY_ONLY',raw_fields={'matched_club_id':club_id,'no_management_assertion':True})
    result=dict(identity_status='VERIFIED',player_id=str(pid),identity_proof=[dict(method='NEW_STABLE_SOURCE_ENTITY;NOT_A_CROSS_PROVIDER_MERGE',provider=provider,provider_id=value)],source_record=record.evidence())
    persist_verified_alias(db,record,result,source.get('acquired_at') or source.get('response_timestamp'));return pid
