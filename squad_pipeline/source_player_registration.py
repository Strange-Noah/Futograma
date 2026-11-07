"""New source entities retain their real source namespace; never name-merge or grant management."""
import re,hashlib
from urllib.parse import urlsplit
from .six_club_experiment import addplayer
from .identity_pipeline import Record,persist_verified_alias

def register(db,club_id,player,source):
    key=player.get('provider_key','');u=urlsplit(key)
    torly=u.scheme=='https' and u.hostname=='torlytics.de' and re.fullmatch(r'/spieler/[a-zA-Z0-9-]+',u.path)
    fotmob=re.fullmatch(r'fotmob:[1-9][0-9]*',key)
    psg=re.fullmatch(r'PSG_STATS_PERFORM:([a-z0-9]{1,64})',key)
    if not torly and not fotmob and not psg:raise ValueError('NEW_SOURCE_PLAYER_REQUIRES_REAL_STABLE_PROVIDER_KEY')
    if not source.get('sha256') or not source.get('url'):raise ValueError('HASHED_VALIDATED_SHEET_REQUIRED')
    provider='PSG_STATS_PERFORM' if psg else 'FotMob' if fotmob else 'Torlytics';value=psg[1] if psg else key.split(':',1)[1] if fotmob else key
    if psg:
        endpoint=urlsplit(source['url'])
        if club_id!=9847 or source.get('club_id')!=9847 or source.get('provider')!='PSG' or source.get('complete_squad') is not True or source.get('source_identity_verified') is not True or not source.get('provider_match_id') or source.get('native_player_id')!=value or player.get('provider_player_id')!=value or player.get('provider_id_namespace')!='PSG_STATS_PERFORM':raise ValueError('PSG_VALIDATED_NATIVE_SQUAD_PROOF_REQUIRED')
        if endpoint.scheme!='https' or endpoint.hostname!='www.psg.fr' or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or not re.fullmatch(r'/en/matches/mens-football/20\d{6}/[^/]+',endpoint.path) or not re.fullmatch(r'[a-f0-9]{64}',source['sha256']):raise ValueError('PSG_OFFICIAL_MATCH_SOURCE_REQUIRED')
    # A new provider URL may be a renamed URL for an existing canonical person.
    # Similarity can block duplicate creation, but can never authorize a merge.
    from .identity_pipeline import Candidate,ranked_candidates
    candidates={r['player_id']:Candidate(r['player_id'],[r['display_name']]) for r in db.execute('SELECT player_id,display_name FROM players')}
    for row in db.execute('SELECT player_id,identity_json FROM validation_player_aliases'):
        import json
        if row['player_id'] in candidates:candidates[row['player_id']].names+=json.loads(row['identity_json']).get('names',[])
    probe=Record(source['url'],source['sha256'],'new-provider-entity',provider,[player['name']])
    possible=[r for r in ranked_candidates(probe,list(candidates.values())) if r['score']>=.82]
    if possible:raise ValueError('POSSIBLE_EXISTING_CANONICAL_IDENTITY;VERIFY_PROVIDER_BRIDGE_BEFORE_CREATE')

    pid=int(value) if provider=='FotMob' else -int(hashlib.sha256(key.encode()).hexdigest()[:15],16)
    if db.execute('SELECT 1 FROM players WHERE player_id=?',(pid,)).fetchone():raise ValueError('NEW_SOURCE_ID_COLLISION;NO_NAME_MERGE')
    addplayer(db,dict(player_id=pid,display_name=player['name'],identity_status='VERIFIED_SOURCE_ENTITY;CROSS_PROVIDER_NOT_ASSUMED',provider_keys=[key],source_names=[player['name']],fotmob_id=int(value) if provider=='FotMob' else None))
    record=Record(source['url'],source['sha256'],player.get('json_path','validated_complete_club_sheet'),provider,[player['name']],provider_ids={provider:value},official_clubs=[],scope='SOURCE_IDENTITY_ONLY',raw_fields={'matched_club_id':club_id,'no_management_assertion':True})
    result=dict(identity_status='VERIFIED',player_id=str(pid),identity_proof=[dict(method='NEW_STABLE_SOURCE_ENTITY;NOT_A_CROSS_PROVIDER_MERGE',provider=provider,provider_id=value)],source_record=record.evidence())
    persist_verified_alias(db,record,result,source.get('acquired_at') or source.get('response_timestamp'));return pid
