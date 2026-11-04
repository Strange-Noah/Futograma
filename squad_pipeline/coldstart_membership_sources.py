"""Captured-source roster adapter: no hand-curated player correspondences.

Provider squad membership alone stays pending; independently acquired official
roster+profile supplies current (observed-date only) membership after the shared
identity verifier accepts a unique provider candidate.
"""
import json,re,hashlib
from urllib.parse import urlsplit
from .identity_pipeline import Record,Candidate,verify_identity

def source_ref(source,provider):
 return dict(provider=provider,source_url=source.get('url') or source.get('source_url'),sha256=source['sha256'],**({'evidence_id':source['evidence_id']} if source.get('evidence_id') else {}))
def checked(raw,source):
 body=raw if isinstance(raw,bytes) else raw.encode()
 if hashlib.sha256(body).hexdigest()!=source['sha256']:raise ValueError('CAPTURE_BODY_HASH_MISMATCH')
 return body.decode('utf8')
def script(raw,name):
 matches=re.findall(r'<script\b[^>]*id=["\']'+re.escape(name)+r'["\'][^>]*>(.*?)</script>',raw,re.S)
 if len(matches)!=1:raise ValueError('EXACT_EMBEDDED_STATE_REQUIRED')
 return json.loads(matches[0])
def nodes(value,key):
 if isinstance(value,dict):
  for k,v in value.items():
   if k==key:yield v
   yield from nodes(v,key)
 elif isinstance(value,list):
  for v in value:yield from nodes(v,key)
def cms_identity(path):
 # CMS localized content copies retain the exact sports/team/player resource
 # suffix; locale is presentation, not a different stable person identity.
 return re.sub(r'(/content/dam/portals/[^/]+/)[a-z]{2}-[a-z]{2}(/sports/)',r'\1{locale}\2',str(path))
def fullname(p):return ' '.join(str(p.get(k) or '').strip() for k in ['name','surnames']).strip()

def build_observations(fotmob_html,fotmob_source,official_html,official_source,details,club_id,on):
 page=script(checked(fotmob_html,fotmob_source),'__NEXT_DATA__')['props']['pageProps'];team=(page.get('fallback') or {}).get('team-'+str(club_id))
 if not isinstance(team,dict):raise ValueError('EXACT_PROVIDER_TEAM_PAYLOAD_REQUIRED')
 groups=(team.get('squad') or {}).get('squad') or [];players={}
 for group in groups:
  if group.get('title')=='coach':continue
  for p in group.get('members',[]):
   if not isinstance(p.get('id'),int) or not p.get('name'):continue
   if p['id'] in players and players[p['id']]!=p:raise ValueError('CONFLICTING_PROVIDER_PLAYER')
   players[p['id']]=p
 roster_state=script(checked(official_html,official_source),'ng-state');official={}
 for season in nodes(roster_state,'seasonList'):
  items=season.get('items',[]) if isinstance(season,dict) else []
  # Multiple season snapshots need explicit selection; do not silently union eras.
  if len(items)!=1:raise ValueError('OFFICIAL_SEASON_SELECTION_REQUIRED')
  for p in (items[0].get('squad') or {}).get('players',[]):
   if p.get('slug'):official[p['slug']]=p
 host=urlsplit(official_source.get('url') or official_source.get('source_url')).netloc
 profiles={}
 for source,raw in details:
  if urlsplit(source.get('url') or source.get('source_url')).netloc!=host:raise ValueError('OFFICIAL_PROFILE_HOST_MISMATCH')
  state=script(checked(raw,source),'ng-state')
  for listing in nodes(state,'playerList'):
   for p in listing.get('items',[]):
    if p.get('slug') in official and urlsplit(source.get('url') or source.get('source_url')).path.rstrip('/').split('/')[-1]==p['slug']:profiles[p['slug']]=(p,source)
 candidates=[];records={}
 for pid,p in sorted(players.items()):
  rec=Record(source_url=fotmob_source['url'],source_sha256=fotmob_source['sha256'],locator='team-'+str(club_id)+'.squad.members.id='+str(pid),provider='FotMob',names=[p['name']],provider_ids={'FotMob':str(pid)},birth_date=p.get('dateOfBirth'),official_clubs=[str(club_id)],scope='PROVIDER_SQUAD',observed_on=on)
  records[pid]=rec;candidates.append(Candidate(pid,[p['name']],[rec]))
 observations=[];matches=[];unresolved=[];matched=set();certificates=[]
 for slug,card in sorted(official.items()):
  if slug not in profiles:unresolved.append(dict(slug=slug,reason='OFFICIAL_PROFILE_NOT_ACQUIRED'));continue
  profile,source=profiles[slug]
  if (card.get('_path') and profile.get('_path') and cms_identity(card['_path'])!=cms_identity(profile['_path'])) or (fullname(card) and fullname(card)!=fullname(profile)):
   unresolved.append(dict(slug=slug,reason='OFFICIAL_CARD_PROFILE_IDENTITY_CONFLICT'));continue
  rec=Record(source_url=source['url'],source_sha256=source['sha256'],locator='playerList.items.slug='+slug,provider='OfficialClub',names=list(dict.fromkeys([fullname(profile),str(profile.get('nickname') or '').strip()])),birth_date=profile.get('birthDate'),official_clubs=[str(club_id)],scope='OFFICIAL_FIRST_TEAM',observed_on=on,assertion='FIRST_TEAM',authority='OFFICIAL')
  verified=[(candidate,verify_identity(rec,candidate)) for candidate in candidates if verify_identity(rec,candidate)['verified']]
  if len(verified)!=1:unresolved.append(dict(slug=slug,reason='IDENTITY_BRIDGE_NOT_UNIQUE' if verified else 'IDENTITY_BRIDGE_INSUFFICIENT',provider_candidate_ids=[c.player_id for c,_ in verified]));continue
  candidate,proof=verified[0];pid=candidate.player_id
  if pid in matched:unresolved.append(dict(slug=slug,reason='DUPLICATE_OFFICIAL_PROVIDER_BINDING'));continue
  certificates.append(dict(player_id=pid,provider_record=records[pid].evidence(),official_record=rec.evidence()))
  matched.add(pid);evidence=[source_ref(fotmob_source,'FotMob'),source_ref(official_source,'OfficialClub'),source_ref(source,'OfficialClub')]
  observations.append(dict(player_id=pid,club_id=club_id,assertion='FIRST_TEAM',identity_status='VERIFIED',verification_status='VERIFIED',source_scope='OFFICIAL_ROSTER',effective_from=None,observed_on=on,evidence=evidence,date_precision='OBSERVED_SNAPSHOT',identity_proof=proof,official_slug=slug))
  matches.append(dict(player_id=pid,official_slug=slug,proof=proof))
 for pid,p in sorted(players.items()):
  if pid not in matched:observations.append(dict(player_id=pid,club_id=club_id,assertion='FIRST_TEAM_INJURED' if p.get('injury') else 'FIRST_TEAM',identity_status='VERIFIED',verification_status='PROVIDER_ASSERTED',source_scope='PROVIDER_SQUAD',effective_from=None,observed_on=on,evidence=[source_ref(fotmob_source,'FotMob')]))
 # Transfer observations are preserved but never used as independent official proof.
 transfers=[]
 for direction,items in (team.get('transfers',{}).get('data') or {}).items():
  if not isinstance(items,list):continue
  for p in items:
   pid=p.get('playerId')
   if not isinstance(pid,int):continue
   assertion=('LOAN_OUT' if p.get('onLoan') else 'TRANSFERRED_OUT') if direction=='Players out' else ('LOAN_IN' if p.get('onLoan') else 'FIRST_TEAM') if direction=='Players in' else None
   item=dict(player_id=pid,direction=direction,assertion=assertion,on_loan=p.get('onLoan'),provider_transfer_type=p.get('transferType'),provider_contract_from=p.get('fromDate'),provider_contract_until=p.get('toDate'),name=p.get('name'),effective_from=(p.get('transferDate') or '')[:10] or None,from_club_id=p.get('fromClubId'),to_club_id=p.get('toClubId'),verification_status='PROVIDER_ASSERTED',evidence=[source_ref(fotmob_source,'FotMob')]);transfers.append(item)
 return dict(identity_certificates=certificates,players=[dict(player_id=pid,name=p['name'],birth_date=p.get('dateOfBirth'),provider='FotMob') for pid,p in sorted(players.items())],observations=observations,identity_matches=matches,official_expected_count=len(official),official_profile_count=len(profiles),provider_squad_count=len(players),verified_membership_count=len(matched),unresolved_official_members=unresolved,provider_transfer_claims=transfers,historical_membership_start_verified=False)

def build_academy_observations(provider_profiles,academy_rosters,official_profiles,club_id,on):
 """Identity bridge + official academy membership, not age or bench inference."""
 from .identity_sources import fotmob
 cards={};official_hosts=set()
 for source,raw in academy_rosters:
  url=source.get('url') or source.get('source_url')
  if '/football/academy/' not in urlsplit(url).path:raise ValueError('EXPLICIT_ACADEMY_ROSTER_SCOPE_REQUIRED')
  official_hosts.add(urlsplit(url).netloc)
  for seasons in nodes(script(checked(raw,source),'ng-state'),'seasonList'):
   for season in seasons.get('items',[]):
    for p in season.get('squad',{}).get('players',[]):
     if p.get('slug'):cards.setdefault(p['slug'],(p,source))
 official=[]
 for source,raw in official_profiles:
  url=source.get('url') or source.get('source_url');parts=urlsplit(url)
  if parts.netloc not in official_hosts or '/football/academy/' not in parts.path:raise ValueError('OFFICIAL_ACADEMY_PROFILE_SCOPE_MISMATCH')
  for listing in nodes(script(checked(raw,source),'ng-state'),'playerList'):
   for p in listing.get('items',[]):
    slug=p.get('slug')
    if slug not in cards or parts.path.rstrip('/').split('/')[-1]!=slug:continue
    card,roster_source=cards[slug]
    if card.get('_path') and p.get('_path') and cms_identity(card['_path'])!=cms_identity(p['_path']):continue
    rec=Record(source_url=url,source_sha256=source['sha256'],locator='playerList.items.slug='+slug,provider='OfficialClub',names=list(dict.fromkeys([fullname(p),p.get('nickname','').strip()])),birth_date=p.get('birthDate'),official_clubs=[str(club_id)],scope='OFFICIAL_ACADEMY',observed_on=on,authority='OFFICIAL')
    official.append((rec,source,roster_source,slug))
 observations=[];unresolved=[];matches=[];certificates=[]
 for source,raw in provider_profiles:
  text=checked(raw,source);capture=dict(source,url=source.get('url') or source.get('source_url'),status=source.get('status','SUCCESS'))
  provider=fotmob(text,capture)[0];pid=int(provider.provider_ids['FotMob']);verified=[]
  for rec,detail_source,roster_source,slug in official:
   proof=verify_identity(provider,Candidate(pid,rec.names,[rec]))
   if proof['verified']:verified.append((rec,detail_source,roster_source,slug,proof))
  # Duplicated raw official observations of the same CMS identity are harmless.
  unique={x[3]:x for x in verified}
  if len(unique)!=1:
   unresolved.append(dict(player_id=pid,reason='IDENTITY_BRIDGE_NOT_UNIQUE' if unique else 'ACADEMY_IDENTITY_BRIDGE_INSUFFICIENT',provider_identity=dict(names=provider.names,birth_date=provider.birth_date,provider_ids=provider.provider_ids,primary_team=provider.raw_fields.get('primary_team'),career_club_ids=provider.raw_fields.get('career_clubs')),official_profile_obligations=[dict(slug=slug,names=rec.names,birth_date=rec.birth_date,verification=verify_identity(provider,Candidate(pid,rec.names,[rec])),source=source_ref(detail_source,'OfficialClub')) for rec,detail_source,roster_source,slug in official if rec.birth_date==provider.birth_date]));continue
  rec,detail_source,roster_source,slug,proof=next(iter(unique.values()))
  certificates.append(dict(player_id=pid,provider_record=provider.evidence(),official_record=rec.evidence()))
  evidence=[source_ref(source,'FotMob'),source_ref(roster_source,'OfficialClub'),source_ref(detail_source,'OfficialClub')]
  observations.append(dict(player_id=pid,club_id=club_id,assertion='YOUTH',identity_status='VERIFIED',verification_status='VERIFIED',source_scope='OFFICIAL_ROSTER',effective_from=None,observed_on=on,evidence=evidence,date_precision='OBSERVED_SNAPSHOT',identity_proof=proof,official_slug=slug))
  matches.append(dict(player_id=pid,official_slug=slug,proof=proof))
 return dict(identity_certificates=certificates,observations=observations,identity_matches=matches,provider_profile_count=len(provider_profiles),official_profile_count=len(official),verified_youth_count=len(observations),unresolved=unresolved,historical_membership_start_verified=False)
