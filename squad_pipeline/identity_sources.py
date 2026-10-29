"""Raw official/provider parsers. No player-specific names, IDs, decisions or DOB answers."""
import json,re,html
from urllib.parse import urlsplit,urljoin,unquote
from .live_parse import Tree
from .html_rosters import payload,POSITIONS
from .identity_pipeline import Record,variants
UEFA_CLUBS={'52919':8456,'52280':9825,'50037':9823,'52747':9847,'50080':8634,'50138':8636}

def key(s):return variants(s)['forms'].get('LATIN_TRANSLITERATION_JOINED','')
def meta(raw):
    return {n.attrs.get('itemprop') or n.attrs.get('property') or n.attrs.get('name'):n.attrs.get('content','') for n in Tree(raw).root.all(tag='meta')}

def jsonld(raw):
    rows=[]
    for s in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',raw,re.S|re.I):
        try:
            d=json.loads(s);rows.extend(d if isinstance(d,list) else [d])
        except (ValueError,TypeError):continue
    return rows

def next_data(raw):
    matches=re.findall(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',raw,re.S)
    if len(matches)!=1:raise ValueError('EXACTLY_ONE_NEXT_PAYLOAD_REQUIRED')
    return json.loads(matches[0])['props']['pageProps']

def base(capture,locator,provider,names,**kw):
    return Record(capture['url'],capture.get('sha256',''),locator,provider,names,observed_on=capture.get('response_timestamp','')[:10] or None,source_status=capture['status'],retrieved=capture['status']=='SUCCESS',**kw)

def fotmob(raw,capture):
    d=next_data(raw)['data'];pid=re.search(r'/players/(\d+)',capture['url'])
    if not pid or str(d['id'])!=pid[1] or d.get('isCoach') or d.get('gender')!='male':raise ValueError('FOTMOB_PROFILE_ID_OR_SCOPE_MISMATCH')
    team=d.get('primaryTeam') or {};career=d.get('careerHistory',{}).get('careerItems',{});person=d.get('meta',{}).get('personJSONLD',{});names=list(dict.fromkeys(x for x in [d.get('name'),person.get('name'),person.get('alternateName')] if isinstance(x,str) and x))
    record=base(capture,'$.props.pageProps.data','FotMob',names,provider_ids={'FotMob':str(d['id'])},birth_date=(d.get('birthDate') or {}).get('utcTime','')[:10] or None,official_clubs=[str(team['teamId'])] if team.get('teamId') else [],raw_fields={'primary_team':team,'career':career,'history':d.get('initialMatchesPage'),'recent_matches':d.get('recentMatches'),'matches_url':d.get('matchesUrl'),'match_filters':d.get('matchFilters'),'seo_path':d.get('meta',{}).get('seopath'),'related_links':d.get('relatedLinksData') or {},'career_clubs':[str(t['teamId']) for t in career.get('senior',{}).get('teamEntries',[]) if t.get('teamId') and t.get('participantId')==d['id']]})
    return [record]

def uefa_profile(raw,capture):
    m=meta(raw);match=re.search(r'/players/(\d+)--',capture['url']);uid=match[1] if match else None
    canonical=m.get('og:url') or m.get('url','')
    if not uid or '/players/'+uid+'--' not in canonical or not m.get('name') or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',m.get('birthdate','')):raise ValueError('UEFA_STABLE_ID_NAME_DOB_REQUIRED')
    clubs=[]
    for n in Tree(raw).root.all(tag='meta'):
        if n.attrs.get('itemprop')=='url':
            cid=re.search(r'/clubs/(\d+)--',n.attrs.get('content',''))
            if cid and cid[1] in UEFA_CLUBS:clubs.append(str(UEFA_CLUBS[cid[1]]))
    return [base(capture,'meta[itemprop=name,birthdate,url]; official club identifier','UEFA',[m['name']],provider_ids={'UEFA':uid},birth_date=m['birthdate'],official_clubs=list(set(clubs)),authority='OFFICIAL')]

def uefa_squad(raw,capture):
    cid=re.search(r'/clubs/(\d+)--',capture['url']);club=UEFA_CLUBS.get(cid[1]) if cid else None
    if not club or '2026/27' not in meta(raw).get('og:title',''):raise ValueError('CURRENT_UEFA_CLUB_SCOPE_REQUIRED')
    result=[]
    for i,row in enumerate(Tree(raw).root.all(tag='pk-table-row')):
        links=[a for a in row.all(tag='a') if re.search(r'/clubs/players/\d+--',a.attrs.get('href',''))]
        if not links:continue
        if len(links)!=1:raise ValueError('UEFA_ROW_PLAYER_ID_AMBIGUOUS')
        a=links[0];pid=re.search(r'/players/(\d+)--',a.attrs['href'])[1];name=a.attrs.get('title','').strip();scope='UEFA_LIST_B' if '*' in a.txt() else 'UEFA_SENIOR'
        if not name:raise ValueError('UEFA_PLAYER_NAME_MISSING')
        result.append(base(capture,f'pk-table-row[{i}]/player-link','UEFA',[name],provider_ids={'UEFA':pid},official_clubs=[str(club)],scope=scope,authority='OFFICIAL',raw_fields={'profile_url':urljoin(capture['url'],a.attrs['href']),'list_b_marker':'*' in a.txt()}))
    if not result:raise ValueError('UEFA_ROSTER_EMPTY_IS_NOT_NO_PLAYERS')
    return result

def official_roster(raw,capture,club):
    result=[]
    if club==8456:
        data=next_data(raw);field='playersListData' if 'playersListData' in data else 'playersData';d=data[field]
        if d.get('cityTeamInfo',{}).get('category')!='mens':raise ValueError('CITY_FIRST_TEAM_SCOPE_REQUIRED')
        for gi,group in enumerate(d['body']):
            if group.get('name') not in ('goalkeeper','defender','midfielder','forward'):continue
            for pi,p in enumerate(group.get('players',[])):
                if not p.get('isMensTeam') or p.get('isCoach') or p.get('isManager'):continue
                result.append(base(capture,f'$.props.pageProps.{field}.body[{gi}].players[{pi}]','OfficialClub',[p['name']],provider_ids={'City':str(p['id'])},birth_date=(p.get('dateOfBirth') or '')[:10] or None,official_clubs=[str(club)],scope='FIRST_TEAM_CARD',authority='OFFICIAL',assertion='LOAN_OUT' if p.get('onLoan') is True else None,raw_fields={'profile_url':urljoin(capture['url'],'/players'+p.get('urlName','')),'on_loan':p.get('onLoan')}))
        return result
    if club==8636:
        d=next_data(raw);page=d['page']
        if page.get('slug')!='first-team' or page.get('youthTeam') or page.get('type')!='Men':raise ValueError('INTER_FIRST_TEAM_SCOPE_REQUIRED')
        for i,p in enumerate(d['players']):
            if p.get('team',{}).get('slug')!='first-team' or p.get('role',{}).get('name') not in POSITIONS:continue
            name=' '.join(x for x in [p.get('firstName'),p.get('lastName')] if x)
            result.append(base(capture,f'$.props.pageProps.players[{i}]','OfficialClub',[name],provider_ids={'Inter':p['sys']['id']},official_clubs=[str(club)],scope='FIRST_TEAM_CARD',authority='OFFICIAL',raw_fields={'profile_url':'https://www.inter.it/en/teams/first-team/player/'+p['slug']}))
        return result
    root=Tree(raw).root
    for i,a in enumerate(root.all(tag='a')):
        href=a.attrs.get('href','');names=[];provider_ids={}
        if club==8634 and re.search(r'/football/first-team/players/\d+/',href):
            given=a.one('team-person__first-name').txt();family=a.one('team-person__last-name').txt();name=' '.join(x for x in [given,family] if x);provider_ids={'Barcelona':re.search(r'/players/(\d+)/',href)[1]}
            if name:names=[name]
        elif club==9847 and re.fullmatch(r'/(?:joueurs|players)/[^/?]+',href):
            # Dedicated card contains shirt number and a single player-name heading.
            hs=[n.txt() for tag in ('h2','h3','h4') for n in a.all(tag=tag) if n.txt()]
            names=[' '.join(n for n in hs if not n.isdigit())] if hs else [re.sub(r'^\s*\d+\s*','',a.txt()).strip()];provider_ids={'PSGProfile':urljoin(capture['url'],href)}
        elif club==9825 and re.match(r'/fixtures/men/players/[^/?]+',href):
            hs=[n.txt() for tag in ('h2','h3','h4') for n in a.all(tag=tag) if n.txt()]
            title=a.attrs.get('aria-label') or a.attrs.get('title');names=hs or ([title] if title else [re.sub(r'^\s*\d+\s*','',a.txt()).strip()]);provider_ids={'ArsenalProfile':urljoin(capture['url'],href)}
        if not names or not names[0]:continue
        result.append(base(capture,f'player-card-anchor[{i}]','OfficialClub',names,provider_ids=provider_ids,official_clubs=[str(club)],scope='FIRST_TEAM_CARD',authority='OFFICIAL',raw_fields={'profile_url':urljoin(capture['url'],href)}))
    unique={tuple(sorted(r.provider_ids.items())):r for r in result}
    if not unique:raise ValueError('OFFICIAL_PLAYER_CARD_SCHEMA_UNRECOGNIZED')
    return list(unique.values())

def registration(raw,capture,club_name,club_id):
    headings=list(re.finditer(r'<h5\b[^>]*>(.*?)</h5>',raw,re.S|re.I));result=[]
    for i,h in enumerate(headings):
        if Tree(h[1]).root.txt().strip()!=club_name:continue
        segment=raw[h.end():headings[i+1].start() if i+1<len(headings) else len(raw)]
        parts=re.split(r'U(?:nder)?[- ]?21(?:\s+players)?',segment,maxsplit=1,flags=re.I)
        for idx,part in enumerate(parts):
            for li,value in enumerate(re.split(r'<br\s*/?>|</p>',part,flags=re.I)):
                name=Tree(value).root.txt().replace('*','').strip()
                if ',' not in name:continue
                result.append(base(capture,f'club={club_name};'+('senior' if idx==0 else 'U21')+f';name[{li}]','PremierLeague',[name],official_clubs=[str(club_id)],scope='SENIOR_REGISTRATION' if idx==0 else 'U21_REGISTRATION',authority='OFFICIAL',raw_fields={'registered_name':name}))
        break
    return result

def academy_biographies(raw,capture,club_id):
    # DOB-bounded biography headings, not all names mentioned in a news page.
    headings=list(re.finditer(r'<h2\b[^>]*>(.*?)</h2>',raw,re.S|re.I));result=[]
    for i,h in enumerate(headings):
        name=Tree(h[1]).root.txt().strip();body=Tree(raw[h.end():headings[i+1].start() if i+1<len(headings) else len(raw)]).root.txt();m=re.search(r'Born:\s*(\d{2})/(\d{2})/(\d{2,4})',body)
        if not m or len(name.split())>6:continue
        day,month,year=m.groups();year=('20'+year) if len(year)==2 and int(year)<30 else ('19'+year) if len(year)==2 else year
        result.append(base(capture,'h2 biography: '+name+'; Born field','OfficialClub',[name],birth_date=f'{year}-{month}-{day}',official_clubs=[str(club_id)],scope='ACADEMY_AFFILIATION',authority='OFFICIAL',raw_fields={'biography_excerpt':body[:500]}))
    return result

def person_profile(raw,capture,club_id=None):
    owned={8456:{'www.mancity.com'},9825:{'www.arsenal.com'},9823:{'fcbayern.com','app.fcbayern.com'},9847:{'www.psg.fr'},8634:{'www.fcbarcelona.com','www.fcbarcelona.es'},8636:{'www.inter.it'}}
    if urlsplit(capture['url']).hostname not in owned.get(club_id,set()):return []
    rows=[]
    for i,d in enumerate(jsonld(raw)):
        if d.get('@type')=='Person' and d.get('name') and d.get('birthDate'):
            names=[d['name']]+([d['alternateName']] if isinstance(d.get('alternateName'),str) else [])
            rows.append(base(capture,f'JSONLD.Person[{i}]','OfficialClub',names,birth_date=d['birthDate'][:10],official_clubs=[str(club_id)] if club_id else [],authority='OFFICIAL',raw_fields={'biography':d.get('description',''),'profile_url':d.get('url')}))
    return rows


def documented_alias_articles(raw,capture,candidates,club_id=None):
    """Extract a full-name/common-name bridge stated in the same official article."""
    m=meta(raw);headline=m.get('og:title') or m.get('twitter:title') or '';intro=m.get('og:description') or m.get('description') or '';result=[]
    # Only a grammatical signed-player introduction; no arbitrary string of capitalized words.
    long=re.search(r'(?:contracted|signed)\s+(?:(?:the|a)\s+)?(?:midfielder|defender|forward|goalkeeper|striker|winger)\s+([^().,]+)\s*\((?:\d{1,2})\)',intro,re.I)
    if not long:return result
    legal=long[1].strip()
    for c in candidates:
        short=next((name for name in c.names if len(name.split())>=2 and key(name) in key(headline) and set(variants(name)['forms']['LATIN_TRANSLITERATION'].split())<=set(variants(legal)['forms']['LATIN_TRANSLITERATION'].split())),None)
        if not short:continue
        result.append(base(capture,'og:title + og:description signed-player introduction','OfficialArticle',[short,legal],official_clubs=[str(club_id)] if club_id else [],authority='OFFICIAL',raw_fields={'documented_alias_bridge':{'common_name':short,'legal_name':legal,'intro':intro},'career_club':str(club_id) if club_id else None}))
    return result

def departure_articles(raw,capture,candidates,club_ids):
    """Official completed-transfer language + raw career identity; announcement is a known-by bound."""
    result=[];m=meta(raw);articles=[d for d in jsonld(raw) if d.get('@type') in ('NewsArticle','Article')]
    for d in articles:
        headline=d.get('headline') or m.get('og:title') or '';intro=d.get('description') or m.get('og:description') or '';body=Tree(d.get('articleBody') or '').root.txt();claim=' '.join([headline,intro,body[:2000]])
        # Explicit completed wording only. Rumours, targets and potential moves are not management evidence.
        if re.search(r'(?i)\b(could|might|rumour|rumor|interested|targeting|reportedly)\b',headline):continue
        loan=bool(re.search(r'(?i)\b(?:arrives on loan|loan signing|on a season.long loan|prêt)\b',claim))
        completed=loan or bool(re.search(r'(?i)(complete .* signing|has (?:joined|signed)|transfert définitif|as a [^.!]+ player|a rejoint)',claim))
        if not completed:continue
        published=(d.get('datePublished') or '')[:10]
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',published):continue
        for c in candidates:
            subject=next((n for n in c.names if len(n.split())>=2 and key(n) in key(headline)),None)
            if not subject:continue
            profiles=[r for r in c.records if r.provider=='FotMob' and r.raw_fields.get('primary_team')]
            for profile in profiles:
                current=profile.raw_fields['primary_team'];current_id=str(current.get('teamId'));career=profile.raw_fields.get('career',{}).get('senior',{}).get('teamEntries',[])
                for oldclub in club_ids.get(c.player_id,[]):
                    if str(oldclub)==current_id:continue
                    history=[x for x in career if str(x.get('teamId'))==str(oldclub)]
                    if not history:continue
                    publisher=d.get('publisher') or {};publisher=publisher.get('name','') if isinstance(publisher,dict) else ''
                    # Source must identify departure/receiving side, beyond just a matching headline.
                    team_name=current.get('teamName','');old_name=history[0].get('team','')
                    normalized=key(' '.join([claim,publisher]))
                    if not ((key(team_name) and key(team_name).replace('fc','') in normalized) or (key(old_name) and key(old_name) in normalized)):continue
                    result.append(base(capture,'JSONLD.NewsArticle headline/description/datePublished + acquired provider career','OfficialArticle',[subject],official_clubs=[str(oldclub)],authority='OFFICIAL',assertion='LOAN_OUT' if loan else 'TRANSFERRED_OUT',effective_from=published,date_precision='ANNOUNCED_COMPLETED_BY_DAY_NOT_REGULATORY_START',raw_fields={'announcement':{'headline':headline,'description':intro,'published_at':d.get('datePublished'),'publisher':publisher},'career_bridge':{'fotmob_id':profile.provider_ids.get('FotMob'),'profile_url':profile.source_url,'profile_sha256':profile.source_sha256,'old_team':history[0],'current_team':current}}))
    return result


def torly_profile(raw,capture):
    from .live_sources import CLUB_FEEDS
    known={slug:str(cid) for cid,(slug,_) in CLUB_FEEDS.items()};records=[]
    for i,d in enumerate(jsonld(raw)):
        if d.get('@type')!='Person' or not d.get('name') or not d.get('birthDate'):continue
        url=d.get('url','').rstrip('/')
        if url!=capture['url'].rstrip('/'):raise ValueError('TORLY_PERSON_PROFILE_URL_MISMATCH')
        membership=d.get('memberOf') or {};slug=membership.get('url','').rstrip('/').rsplit('/',1)[-1];club=known.get(slug)
        records.append(base(capture,f'JSONLD.Person[{i}]','Torlytics',[d['name']],provider_ids={'Torlytics':url},birth_date=d['birthDate'][:10],official_clubs=[club] if club else [],raw_fields={'membership':membership,'profile_url':url}))
    if not records:raise ValueError('TORLY_PERSON_BIRTHDATE_SCHEMA_UNAVAILABLE')
    return records


def current_official_profile(raw,capture,club_id):
    rows=person_profile(raw,capture,club_id)
    if rows:
        for r in rows:r.scope='FIRST_TEAM_CARD'
        return rows
    # Only an acquired dedicated men's player profile, not an academy news/camp or a name in page navigation.
    if club_id!=9825 or not re.fullmatch(r'https://www\.arsenal\.com/fixtures/men/players/[^/?#]+',capture['url']):return []
    root=Tree(raw).root;headings=[n.txt().strip() for n in root.all(tag='h1') if n.txt().strip()];m=meta(raw)
    names=[n for n in headings if len(n.split()) in range(2,6) and not re.search(r'(?i)players|fixtures|arsenal|news|tickets',n)]
    if not names:
        title=(m.get('og:title') or '').split('|')[0].strip()
        if len(title.split()) in range(2,6) and not re.search(r'(?i)players|fixtures|arsenal|news|tickets',title):names=[title]
    if not names:return []
    return [base(capture,'Dedicated men/player profile h1 or og:title','OfficialClub',names,provider_ids={'ArsenalProfile':capture['url']},official_clubs=[str(club_id)],scope='FIRST_TEAM_CARD',authority='OFFICIAL',raw_fields={'profile_url':capture['url'],'current_mens_player_profile':True})]


def dfb_roster(raw,capture,club_id):
    result=[]
    if urlsplit(capture['url']).hostname!='datencenter.dfb.de':raise ValueError('DFB_OFFICIAL_HOST_REQUIRED')
    for ti,table in enumerate(Tree(raw).root.all(tag='table')):
        if table.all(tag='table'):continue
        heading=' '.join(c.txt() for c in table.children if getattr(c,'tag',None)=='thead')
        if re.search(r'(?i)trainer|staff|betreuer',heading):continue
        for ri,row in enumerate(table.all(tag='tr')):
            cells=[c for c in row.children if getattr(c,'tag',None)=='td']
            if len(cells)!=3:continue
            links=[a for a in cells[1].all(tag='a') if re.search(r'/(?:profil/\d+|(?:personen|persons)/[^/]+-\d+/spieler)',a.attrs.get('href',''))]
            if not links:continue
            birthday=re.fullmatch(r'(\d{2})\.(\d{2})\.(\d{4})',cells[2].txt().strip())
            if not birthday:continue
            a=links[0];match=re.search(r'/profil/(\d+)|-(\d+)/spieler',a.attrs['href']);pid=next(x for x in match.groups() if x);name=a.txt().strip()
            result.append(base(capture,f'DFB leaf roster table[{ti}] role={heading} row[{ri}]','DFB',[name],provider_ids={'DFB':pid},birth_date=f'{birthday[3]}-{birthday[2]}-{birthday[1]}',official_clubs=[str(club_id)],scope='COMPETITION_REGISTRATION',authority='OFFICIAL',raw_fields={'profile_url':urljoin(capture['url'],a.attrs['href']),'source_position_group':heading}))
    if not result:raise ValueError('DFB_REGISTERED_PLAYER_DOB_ROWS_UNAVAILABLE')
    return result


def dfb_profile(raw,capture):
    if urlsplit(capture['url']).hostname!='datencenter.dfb.de':return []
    pid=re.search(r'/(?:personen|persons)/[^/]+-(\d+)/spieler',capture['url'])
    if not pid:return []
    root=Tree(raw).root;text=root.txt();dob=re.search(r'Geburtstag:\s*(\d{2})\.(\d{2})\.(\d{4})',text);name=re.search(r'Name:\s*(.*?)\s*Geburtstag:',text,re.S)
    if not dob or not name:return []
    club=[]
    if any('/bayern-muenchen' in a.attrs.get('href','') for a in root.all(tag='a')):club=['9823']
    return [base(capture,'DFB person name/birthday + evidenced club-history links','DFB',[name[1].strip()],provider_ids={'DFB':pid[1]},birth_date=f'{dob[3]}-{dob[2]}-{dob[1]}',official_clubs=club,scope='ACADEMY_AFFILIATION' if club else 'IDENTITY_ONLY',authority='OFFICIAL',date_precision='OBSERVED_CAREER_ASSOCIATION_NOT_CURRENT_MANAGEMENT')]


def laliga_profile(raw,capture,candidate_names=()):
    if urlsplit(capture['url']).hostname!='www.laliga.com' or '/player/' not in capture['url']:return []
    root=Tree(raw).root;headings=root.all(tag='h1');text=root.txt();birthday=re.search(r'DATE\s+OF\s+BIRTH\s*(\d{2})-(\d{2})-(\d{4})',text,re.I)
    if not headings or not birthday:return []
    names=[headings[-1].txt().strip()]
    # Only distinct, complete displayed name fields; no first-name/surname token inference.
    for n in root.all(tag='p')+root.all(tag='h2')+root.all(tag='span'):
        value=n.txt().strip()
        if any(key(value)==key(c) for c in candidate_names if c) and value not in names:names.append(value)
    from .live_parse import canon,CLUBS
    labels=[n.txt() for n in root.all(tag='h2')];clubs=[str(cid) for cid,(_,slug) in CLUBS.items() if any(canon(label)==slug for label in labels)]
    return [base(capture,'LaLiga h1 legal name; complete displayed common-name field; DATE OF BIRTH; club h2','LaLiga',names,provider_ids={'LaLigaProfile':capture['url']},birth_date=f'{birthday[3]}-{birthday[2]}-{birthday[1]}',official_clubs=clubs,scope='IDENTITY_ONLY',authority='OFFICIAL')]
