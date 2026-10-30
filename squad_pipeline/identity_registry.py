"""Strict identity lookup after P1 verification; generated name keys are never stable IDs."""
import json
from .identity_pipeline import variants

def forms(name):return set(variants(name)['forms'].values())
def lookup(db,club_id,player):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='verified_identity_links'").fetchone():return None
    provider_key=player.get('provider_key','');stable=None
    if provider_key.startswith('fotmob:'):stable=('FotMob',provider_key.split(':',1)[1])
    elif provider_key.startswith('https://torlytics.de/spieler/'):stable=('Torlytics',provider_key)
    if stable:
        row=db.execute('SELECT player_id FROM verified_identity_links WHERE provider=? AND provider_player_id=?',stable).fetchone()
        if row:return row[0]
    target=forms(player['name']);hits=set()
    for row in db.execute('SELECT player_id,source_name,evidence_json FROM verified_name_aliases'):
        if not target&forms(row['source_name']):continue
        certificate=json.loads(row['evidence_json']);source=certificate.get('source_record',{});proof=certificate.get('identity_proof',[])
        if certificate.get('identity_status')!='VERIFIED' or not proof:continue
        # A newly observed source entity is not a reusable cross-provider name certificate.
        if any(p.get('method','').startswith('NEW_STABLE_SOURCE_ENTITY') for p in proof):continue
        # A full documented mononym may be reused only with official ID/DOB/club evidence, never as a surname shortcut.
        if len(player['name'].split())<2:
            if source.get('authority')!='OFFICIAL' or not source.get('birth_date') or str(club_id) not in source.get('official_clubs',[]) or not source.get('provider_ids'):continue
            if not any(p['method'] in ('INDEPENDENT_DOB_NAME_OFFICIAL_AFFILIATION','SHARED_STABLE_PROVIDER_ID') for p in proof):continue
        hits.add(int(row['player_id']))
    if len(hits)>1:raise ValueError('AMBIGUOUS_VERIFIED_GLOBAL_ALIAS:'+player['name'])
    return next(iter(hits)) if hits else None
