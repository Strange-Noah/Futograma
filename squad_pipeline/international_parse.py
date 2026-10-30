"""National match adapter uses verified fixture context, never citizenship or club IDs."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .live_parse import Tree,ParseError,Pending,uk_datetime,canon
from .six_player_extract import clock
from .identity_pipeline import variants

def namekey(name):return variants(name)['forms'].get('LATIN_TRANSLITERATION_JOINED','')
def nominal(c,duration):
    if not c:return None
    if c['base']>duration:return None
    if c['base']==46 and not c.get('added') and c.get('raw') in ("46'",'46′'):return 45
    if c['base']==106 and duration==120 and not c.get('added'):return 105
    return c['base']

def minutes_from_events(players,events,duration):
    if duration not in (90,120):raise Pending('MATCH_DURATION_NOT_VERIFIED')
    keys=[p['provider_key'] for p in players]
    if len(keys)!=len(set(keys)):raise ParseError('DUPLICATE_PLAYER_ID')
    if sum(p['role']=='starters' for p in players)!=11 or not any(p['role']=='bench' for p in players):raise Pending('XI_OR_FULL_BENCH_UNAVAILABLE')
    incoming={};outgoing={};reds={};bench={p['provider_key'] for p in players if p['role']=='bench'}
    for e in events:
        k=e['provider_key'];kind=e['type'];c=e.get('clock')
        if kind not in ('sub-on','sub-off','red','yellow-red','second-yellow','red-card','yellow-red-card'):continue
        if not c:raise Pending('EVENT_CLOCK_MISSING')
        if k not in keys:raise ParseError('EVENT_PLAYER_NOT_IN_SQUAD')
        if kind=='sub-on':
            if k not in bench or k in incoming:raise ParseError('INVALID_INCOMING_PLAYER')
            incoming[k]=c
        elif kind=='sub-off':
            if k in outgoing:raise ParseError('DUPLICATE_OUTGOING_PLAYER')
            outgoing[k]=c
        else:
            if k in reds:raise ParseError('DUPLICATE_RED_CARD')
            reds[k]=c
    if sorted((c['base'],c.get('added',0)) for c in incoming.values())!=sorted((c['base'],c.get('added',0)) for c in outgoing.values()):raise ParseError('SUBSTITUTION_CLOCK_DISAGREEMENT')
    for p in players:
        key=p['provider_key'];entry=incoming.get(key);exit_=outgoing.get(key) or reds.get(key)
        if p['role']=='bench' and key not in incoming and (key in outgoing or key in reds):raise ParseError('BENCH_EVENT_WITHOUT_ON_PITCH_PROOF')
        begin=0 if p['role']=='starters' else nominal(entry,duration);end=nominal(exit_,duration) if exit_ else duration
        if begin is not None and end is not None and end<begin:raise ParseError('NEGATIVE_PLAYING_INTERVAL')
        status='STARTER' if p['role']=='starters' else 'SUBSTITUTE_USED' if entry else 'UNUSED_SUBSTITUTE'
        minutes=0 if status=='UNUSED_SUBSTITUTE' else end-begin if begin is not None and end is not None else None
        p.update(participation_status=status,derived_minutes=minutes,entry=entry,exit=exit_,minute_precision='NOMINAL_EXCLUDING_ADDED_TIME' if minutes is not None else 'AMBIGUOUS_EVENT_CLOCK')
    return players

def sky_duration(header,fixture):
    # Sky emits all FT/AET/LIVE labels into hidden template spans. Never scan header text.
    verified=fixture.get('verified_duration_minutes')
    if verified is not None:
        proof=fixture.get('duration_evidence') or {}
        if verified not in (90,120) or proof.get('scope')!='EXACT_MATCH_DURATION' or not proof.get('sha256'):raise ValueError('EXTERNAL_DURATION_EVIDENCE_REQUIRED')
        return verified
    if header.attrs.get('data-status')=='4':return 90
    raise Pending('SKY_MATCH_DURATION_NOT_VERIFIED')

def certified_sky_team_label(observed,expected,side,fixture,source):
    """Reuse only a source-declared full/short label bound to this exact provider match/team."""
    if canon(observed)==canon(expected):return None
    from urllib.parse import urlsplit
    import re
    certs=fixture.get('team_label_certificates') or {};cert=certs.get(side) or {};other=certs.get('away' if side=='home' else 'home') or {};url=urlsplit(source.get('url',''));directory=urlsplit(cert.get('directory_source_url',''))
    valid=cert.get('provider')=='Sky' and cert.get('side')==side and url.scheme=='https' and url.hostname=='www.skysports.com' and str(cert.get('provider_match_id'))==url.path.rstrip('/').rsplit('/',1)[-1]
    valid=valid and str(cert.get('provider_team_id','')).isdigit() and int(cert['provider_team_id'])>0 and str(cert['provider_team_id'])!=str(other.get('provider_team_id'))
    valid=valid and directory.scheme=='https' and directory.hostname=='www.skysports.com' and re.fullmatch('[0-9a-f]{64}',cert.get('directory_source_sha256','')) and cert.get('source_locator')
    valid=valid and namekey(cert.get('full_name',''))==namekey(expected) and namekey(cert.get('short_name',''))==namekey(observed)
    if not valid:raise ParseError('SKY_ROSTER_SIDE_MISMATCH;NO_EXACT_SOURCE_TEAM_LABEL_CERTIFICATE')
    return cert

def parse_sky(raw,source,fixture):
    # Context must come from verified history/schedule evidence, not a guessed country/competition.
    required=('home','away','home_team_key','away_team_key','kickoff','competition_label','competition_key','level','evidence')
    if any(not fixture.get(k) for k in required):raise ValueError('VERIFIED_NATIONAL_FIXTURE_CONTEXT_REQUIRED')
    root=Tree(raw).root;header=root.one('sdc-site-match-header')
    if header.attrs.get('data-status')!='4':raise Pending('SKY_NOT_FULL_TIME')
    names=header.all('sdc-site-match-header__team-name-block-target')
    if len(names)!=2 or [canon(n.txt()) for n in names]!=[canon(fixture['home']),canon(fixture['away'])]:raise ParseError('NATIONAL_FIXTURE_TEAM_MISMATCH')
    kickoff=uk_datetime(header.one('sdc-site-match-header__detail-time').txt())
    if datetime.fromisoformat(kickoff.replace('Z','+00:00'))!=datetime.fromisoformat(fixture['kickoff'].replace('Z','+00:00')):raise ParseError('NATIONAL_FIXTURE_KICKOFF_MISMATCH')
    label=header.one('sdc-site-match-header__detail-fixture').txt()
    if fixture['competition_label'].casefold() not in label.casefold():raise ParseError('NATIONAL_COMPETITION_UNVERIFIED')
    duration=sky_duration(header,fixture)
    sheets=[];both=[];cols=root.all('sdc-site-team-lineup__col')
    if len(cols)!=2:raise Pending('SKY_TWO_LINEUP_COLUMNS_REQUIRED')
    for side,col in zip(('home','away'),cols):
        observed_team_label=col.one('sdc-site-team-lineup__team-name').txt();label_certificate=certified_sky_team_label(observed_team_label,fixture[side],side,fixture,source)
        players=[];events=[]
        for group in col.all('sdc-site-team-lineup__players'):
            role='bench' if 'sdc-site-team-lineup__players--subs' in group.attrs.get('class','') else 'starters'
            nums=group.all('sdc-site-team-lineup__player-number');names=group.all('sdc-site-team-lineup__player-name')
            if len(nums)!=len(names):raise ParseError('SKY_PLAYER_PAIRING_MISMATCH')
            for number,node in zip(nums,names):
                name=(node.one('sdc-site-team-lineup__player-initial').attrs.get('title','')+' '+node.one('sdc-site-team-lineup__player-surname').txt()).strip()
                if not name:raise ParseError('PLAYER_NAME_MISSING')
                key='sky-name:'+fixture[side+'_team_key']+':'+namekey(name)
                players.append(dict(name=name,provider_key=key,role=role,shirt=number.txt(),source_path='sdc-site-team-lineup__col/'+side+'/'+role))
                for event in node.all('sdc-site-team-lineup__event'):
                    types=[c.split('icon--',1)[1] for c in event.one('sdc-site-team-lineup__item-event-icon').attrs.get('class','').split() if 'icon--' in c]
                    if len(types)!=1:raise ParseError('SKY_UNTYPED_EVENT')
                    events.append(dict(type=types[0],provider_key=key,clock=clock(event.one('sdc-site-team-lineup__event_time').txt())))
        both.append({namekey(p['name']) for p in players});sheets.append(dict(represented_team_key=fixture[side+'_team_key'],side=side,observed_team_label=observed_team_label,team_label_certificate=label_certificate,players=minutes_from_events(players,events,duration),events=events,source=source,complete_squad=True))
    if both[0]&both[1]:raise ParseError('PLAYER_ON_BOTH_SIDES')
    return dict(fixture=fixture,kickoff_utc=kickoff,madrid_date=datetime.fromisoformat(kickoff.replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Madrid')).date().isoformat(),duration_minutes=duration,sheets=sheets,shootout_minutes=0)
