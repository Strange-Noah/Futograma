"""Independent national fixture fallback. Name similarity never authorizes a player merge."""
import re,html,json
from functools import lru_cache
from urllib.parse import urljoin,urlsplit
from datetime import datetime,timedelta
from .live_parse import catalogs
from .international_parse import parse_sky,namekey
from .international_fotmob import instant,numeric
from .identity_registry import lookup
from .identity_pipeline import variants
from .international_store import team,evidence,put_match,put_appearance,complete_missing_minutes,encoded
from .international_retry import observe

def competition_aliases(fixture):
    key=str(fixture['competition_key']);label=fixture.get('competition_label','')
    if key in ('9806','9807','9808','9809') and label.startswith('UEFA Nations League'):return ('UEFA Nations League',)
    if key=='114' and label=='Friendlies':return ('International Match','International Friendly')
    if key=='287' and label.startswith('EURO U19'):return ('European Under-19 Championship',)
    return ()

def month_links(raw,url,months):
    out=[]
    for href in re.findall(r'href=["\']([^"\']+)',raw):
        target=urljoin(url,html.unescape(href));u=urlsplit(target);m=re.search(r'/(\d{4}-\d{2})-01$',u.path)
        if u.scheme=='https' and u.netloc=='www.skysports.com' and not u.query and m and m[1] in months and ('scores-fixtures/' in u.path or 'results/' in u.path):out.append(target)
    return sorted(set(out))

@lru_cache(maxsize=256)
def directory_team_labels(raw):
    rows={}
    for value in re.findall(r'data-state="([^"<>]*&quot;matchURL&quot;[^"<>]*)"',raw):
        d=json.loads(html.unescape(value));key=str(d['id'])
        try:
            record={side:dict(provider_team_id=str(d['teams'][side]['id']),full_name=d['teams'][side]['name']['full'],short_name=d['teams'][side]['name'].get('short')) for side in ('home','away')}
            if any(not r['provider_team_id'].isdigit() or int(r['provider_team_id'])<=0 for r in record.values()):raise ValueError('DIRECTORY_TEAM_ID_MISSING')
            if record['home']['provider_team_id']==record['away']['provider_team_id']:raise ValueError('DIRECTORY_DUPLICATE_TEAM_ID')
        except (KeyError,TypeError,ValueError) as exc:record=dict(_error=type(exc).__name__+':'+str(exc))
        if key in rows and rows[key]!=record:record=dict(_error='DIRECTORY_TEAM_LABEL_IDENTITY_CONFLICT')
        rows[key]=record
    return rows

def directory_candidates(raw,fixture,asof,source):
    out=[];aliases=competition_aliases(fixture)
    for c in catalogs(raw,'Sky',None,asof):
        if not c['catalog_completed'] or c['catalog_competition'] not in aliases:continue
        if (namekey(c['catalog_home']),namekey(c['catalog_away']))!=(namekey(fixture['home']['name']),namekey(fixture['away']['name'])):continue
        if abs((datetime.fromisoformat(c['date']).date()-instant(fixture['kickoff']).date()).days)>1:continue
        labels=directory_team_labels(raw)[c['catalog_provider_id']]
        if '_error' in labels:raise ValueError(labels['_error'])
        c['team_label_certificates']={side:dict(label,provider='Sky',provider_match_id=c['catalog_provider_id'],side=side,directory_source_sha256=source['sha256'],directory_source_url=source['url'],source_locator='HTML.data-state[match.id='+c['catalog_provider_id']+'].teams.'+side) for side,label in labels.items()};out.append(c)
    return out

def target_minutes(player,claim,fixture,side,duration):
    role=player['role'];status='STARTER' if role=='starters' else 'SUBSTITUTE_USED' if player.get('entry') else 'BENCH_PARTICIPATION_UNKNOWN';reported=None;derived=player.get('derived_minutes');reason='NO_PERSONAL_TARGET_REPORT;MINUTES_UNCONFIRMED';selected=None
    if claim is not None:
        c=claim['discovery_claim'];own=fixture[side];opponent=fixture['away' if side=='home' else 'home']
        if str(c.get('id'))!=str(fixture['provider_match_id']) or str(c.get('teamId'))!=str(own['id']) or str(c.get('opponentTeamId'))!=str(opponent['id']) or bool(c.get('isHomeTeam'))!=(side=='home') or instant(c['matchDate']['utcTime'])!=instant(fixture['kickoff']):raise ValueError('SKY_TARGET_HISTORY_FIXTURE_IDENTITY_CONFLICT')
        if c.get('minutesPlayed') is not None:
            reported=numeric(c['minutesPlayed'],duration)
            if role=='bench' and c.get('playedInMatch') is True:status='SUBSTITUTE_USED'
            elif role=='bench' and c.get('playedInMatch') is False and c.get('onBench') is True and reported==0:status='UNUSED_SUBSTITUTE'
            if role=='bench' and reported==0 and status=='BENCH_PARTICIPATION_UNKNOWN':reason='ZERO_MINUTE_PARTICIPATION_UNVERIFIED'
            elif derived is None or reported==derived:selected=reported;reason='EXACT_PERSONAL_REPORT_AND_CERTIFIED_LINEUP_NAME'+(';CLOCKS_AGREE' if derived is not None else ';NO_INDEPENDENT_MINUTE_CORROBORATION')
            else:reason='PERSONAL_REPORT_AND_SKY_CLOCKS_DISAGREE;NO_AUTOMATIC_SELECTION'
    return dict(participation_status=status,minutes=selected,reported_minutes=reported,derived_minutes=derived,minutes_precision='SOURCE_REPORTED_INTEGER' if selected is not None else 'UNKNOWN',verification_status='CROSS_PROVIDER_CERTIFIED_NAME_AND_PERSONAL_HISTORY_MATCH' if selected is not None else 'CANONICAL_LINEUP_IDENTITY_VERIFIED_MINUTES_UNKNOWN',evidence_scope=reason)

def acquire_sky_alternatives(db,fetch,fixtures,claims,ids,managed,clubs,asof,token,asset_ref):
    results=[];errors=[];directories={};responses={};new_rows=0
    pending=[]
    for mid,f in fixtures.items():
        q=db.execute("SELECT 1 FROM international_acquisition_queue WHERE provider='FotMob' AND entity_type='MATCH' AND provider_entity_id=? AND state<>'RESOLVED'",(mid,)).fetchone()
        if q:pending.append(f)
    months={f['kickoff'][:7] for f in pending}
    def directory(url):
        if url not in responses:responses[url]=fetch(url,'independent_national_directory')
        return responses[url]
    # Slugs are discovery candidates only; exact detail date, ordered countries and competition remain mandatory.
    for f in pending:
        for side in ('home','away'):
            name=f[side]['name'];slug=variants(name)['forms'].get('LATIN_TRANSLITERATION','').replace(' ','-')
            if not slug:continue
            url='https://www.skysports.com/'+slug+'-results'
            if url in directories:continue
            directories[url]=[]
            try:
                source,body=directory(url);directories[url].append((source,body))
                for monthly in month_links(body,url,months):
                    if monthly==url:continue
                    sr,br=directory(monthly);directories[url].append((sr,br))
            except Exception as exc:errors.append(dict(url=url,stage='DIRECTORY',error=type(exc).__name__+':'+str(exc)))
    for f in pending:
        mid=f['provider_match_id'];candidates={}
        for pages in directories.values():
            for source,body in pages:
                try:
                    for c in directory_candidates(body,f,asof,source):candidates[c['url']]=(c,source)
                except Exception as exc:errors.append(dict(match_id=mid,url=source['url'],stage='CATALOG',error=type(exc).__name__+':'+str(exc)))
        if not candidates:errors.append(dict(match_id=mid,stage='DISCOVERY',error='NO_EXACT_ORDERED_COUNTRY_COMPETITION_DATE_CANDIDATE;NOT_NO_APPEARANCE'));continue
        accepted=False
        for url,(candidate,directory_source) in candidates.items():
            before=new_rows;db.execute('SAVEPOINT independent_sky_match')
            try:
                source,body=fetch(url,'independent_national_lineup:'+mid);context=dict(home=f['home']['name'],away=f['away']['name'],home_team_key='fotmob-national:'+str(f['home']['id']),away_team_key='fotmob-national:'+str(f['away']['id']),kickoff=f['kickoff'],competition_label=candidate['catalog_competition'],competition_key=f['competition_key'],level=f['level'],evidence=f['source'],team_label_certificates=candidate['team_label_certificates']);sheet=parse_sky(body,source,context);eid=evidence(db,dict(source,retained_asset_ref=asset_ref,verified_fixture_context=f,directory_source=directory_source,team_label_certificates=candidate['team_label_certificates']),'CROSS_PROVIDER_EXACT_ORDERED_COUNTRIES_KICKOFF_COMPETITION','INDEPENDENT_LINEUP_SOURCE',1);teams={s:team(db,f[s]['name'],f['level'],eid,'FotMob',f[s]['id']) for s in ('home','away')}
                for side,certificate in candidate['team_label_certificates'].items():
                    assert team(db,f[side]['name'],f['level'],eid,'Sky',certificate['provider_team_id'])==teams[side],'SKY_TEAM_PROVIDER_ID_CONFLICT'
                match=dict(match_id='nt_fotmob_'+mid,home_team_key=teams['home'],away_team_key=teams['away'],kickoff_utc=sheet['kickoff_utc'],madrid_date=sheet['madrid_date'],competition_key=f['competition_key'],stage=None,match_state='FINISHED',score=None,duration_minutes=sheet['duration_minutes'],extra_time_status='SOURCE_FULL_TIME_90;HIDDEN_AET_TEMPLATE_IGNORED',identity_status='CROSS_PROVIDER_EXACT_FIXTURE_CONTEXT_VERIFIED',metadata_json=encoded(dict(competition=candidate['catalog_competition'],identity_scope='MANAGED_TARGETS_ONLY;OTHER_SQUAD_NAMES_NOT_GLOBALLY_ID_VERIFIED')));canonical=put_match(db,match,'FotMob',mid,eid);assert put_match(db,dict(match,match_id=canonical),'Sky',candidate['catalog_provider_id'],eid)==canonical;targets=[];seen=set()
                for side in sheet['sheets']:
                    for player in side['players']:
                        hits={lookup(db,cid,player) for cid in {c for pid in managed for c in clubs[pid]}}-{None};hits&=managed
                        if not hits:continue
                        if len(hits)!=1:raise ValueError('AMBIGUOUS_CERTIFIED_SKY_TARGET')
                        pid=next(iter(hits))
                        if pid in seen:raise ValueError('DUPLICATE_CANONICAL_PLAYER_ACROSS_SKY_SIDES')
                        seen.add(pid);fids={fid for fid,canonical_pid in ids.items() if canonical_pid==pid};reports=[c for c in claims[mid] if c['player_provider_id'] in fids]
                        if len({encoded(c['discovery_claim']) for c in reports})>1:raise ValueError('CONFLICTING_PERSONAL_REPORTS_FOR_SKY_TARGET')
                        ownkey=teams[side['side']]
                        if not reports and not db.execute('SELECT 1 FROM national_affiliation_events WHERE player_id=? AND team_key=?',(pid,ownkey)).fetchone():continue
                        fact=target_minutes(player,reports[0] if reports else None,f,side['side'],sheet['duration_minutes']);appearance=dict(fact,match_id=canonical,player_id=pid,represented_team_key=ownkey,revision=1,metadata_json=encoded(dict(source_provider='Sky',source_player_key=player['provider_key'],identity_method='EXACT_REUSABLE_VERIFIED_ALIAS;NO_FUZZY_MERGE',personal_provider_ids=sorted(fids),role=player['role'],shirt=player.get('shirt'),event_entry=player.get('entry'),event_exit=player.get('exit'))));old=db.execute('SELECT * FROM international_appearances WHERE match_id=? AND player_id=?',(canonical,pid)).fetchone();agree=1;decision='VERIFIED_INDEPENDENT_TARGET_LINEUP'
                        if old:
                            if old['represented_team_key']!=ownkey:raise ValueError('SKY_TARGET_SIDE_CONFLICT_WITH_CANONICAL_FACT')
                            if old['minutes'] is None and fact['minutes'] is not None and not old['verification_status'].startswith('RETAINED_PRIOR'):complete_missing_minutes(db,appearance,eid)
                            elif fact['minutes'] is not None and old['minutes']!=fact['minutes']:agree=0;decision='RETAIN_PRIOR_KNOWN_MINUTES;EXPLICIT_ADJUDICATION_REQUIRED'
                        else:new_rows+=int(put_appearance(db,appearance))
                        db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(canonical,pid,eid,'independent_lineup_and_minutes',encoded(appearance),int(agree and 'DISAGREE' not in fact['evidence_scope']),decision))
                        if reports:
                            hs=reports[0]['source'];he=evidence(db,dict(hs,retained_asset_ref=asset_ref),'PERSONAL_TARGET_HISTORY_REPORTED_MINUTES','EXACT_PROVIDER_PLAYER_AND_MATCH_ID_REPORT',1);db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(canonical,pid,he,'reported_minutes',encoded(reports[0]['discovery_claim']),agree,decision))
                        numeric_conflict='DISAGREE' in fact['evidence_scope']
                        minute_task=dict(entity_type='PLAYER_MINUTES',provider='Sky',provider_entity_id=candidate['catalog_provider_id']+':'+str(pid),url=url,player_id=pid,fixture=f,date=f['kickoff'][:10])
                        if numeric_conflict:
                            observe(db,minute_task,dict(status='PENDING',error='Conflict:PERSONAL_REPORT_AND_SKY_CLOCKS_DISAGREE',match_id=canonical),token)
                            db.execute('INSERT OR IGNORE INTO international_fact_evidence VALUES(?,?,?,?,?,?,?)',(canonical,pid,eid,'lineup_identity',encoded(dict(canonical_player_id=pid,source_player_key=player['provider_key'],scope='CERTIFIED_ALIAS_AND_VERIFIED_NATIONAL_CONTEXT')),1,'Identity accepted independently of disputed minutes'))
                        elif fact['minutes'] is None and (old is None or old['minutes'] is None):observe(db,minute_task,dict(status='PENDING',error='SOURCE_MINUTES_INCOMPLETE',match_id=canonical),token)
                        elif fact['minutes'] is not None:observe(db,minute_task,dict(status='UNCHANGED',match_id=canonical),token)
                        targets.append(dict(player_id=pid,minutes=fact['minutes'],scope=fact['evidence_scope'],retained_existing=bool(old)))
                observe(db,dict(entity_type='ALTERNATIVE_MATCH',provider='Sky',provider_entity_id=candidate['catalog_provider_id'],url=url,fixture=f),dict(status='DISCOVERED',match_id=canonical),token);db.execute('RELEASE independent_sky_match');results.append(dict(provider_match_id=mid,sky_id=candidate['catalog_provider_id'],url=url,targets=targets,original_fotmob_quarantine_unchanged=True));accepted=True;break
            except Exception as exc:
                db.execute('ROLLBACK TO independent_sky_match');db.execute('RELEASE independent_sky_match');new_rows=before;errors.append(dict(match_id=mid,url=url,stage='LINEUP',error=type(exc).__name__+':'+str(exc)))
        if not accepted:continue
    return dict(status='PARTIAL_SOURCE_COVERAGE',pending_fixture_count=len(pending),independent_matches=results,new_rows=new_rows,errors=errors,source_quarantines_not_cleared=True)
