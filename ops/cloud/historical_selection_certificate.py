"""Resolve independently documented matchday identities for retained lineups."""
import datetime
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from ops.cloud.historical_duration import canonical_text
from squad_pipeline.international_parse import namekey
from squad_pipeline.live_parse import Tree
from squad_pipeline.source_interface import EvidenceStore, RawSourceArtifact, SourceRequest, SourceService

SELECTION_HOSTS = {'fcbayern.com','www.fcbayern.com','www.psg.fr','www.ussoccer.com','www.ussoccerhistory.org',
                   'www.cafonline.com','www.fifa.com','www.uefa.com','english.ahram.org.eg'}


def prepare(certificates, archived, work, completed):
    store = EvidenceStore(work / 'selection-certificate-sources')
    service = SourceService(store, 'historical-selection-certificate', SELECTION_HOSTS,
                            max_calls=len(certificates)*2, max_bytes=30_000_000)
    bindings, errors, blocked = {}, [], set()
    captures = {}
    receipt_path = work / 'selection-certificate-receipts.json'
    retained = {item['url']:item for item in json.loads(receipt_path.read_text())} if receipt_path.exists() else {}
    for cert in certificates:
        token = sha256(json.dumps(cert,sort_keys=True).encode()).hexdigest()
        if token in completed:
            continue
        url, official = cert['source_url'], cert['evidence_url']
        host = urlsplit(official).hostname
        issue = dict(type='MATCHDAY_IDENTITY_EVIDENCE',source=url,evidence=official)
        if url not in archived or host in blocked or host not in SELECTION_HOSTS or not cert['terms']:
            errors.append(dict(issue,reason='ARCHIVED_MATCH_OR_EVIDENCE_SOURCE_UNAVAILABLE'))
            continue
        if official not in captures:
            request = SourceRequest(cert['authority'],'match',official,
                                    'Independent exact matchday statement',policy='CACHE_FIRST')
            old = retained.get(official, {})
            if old.get('outcome') in {'ACCESS_RESTRICTED','RATE_LIMITED','ROBOTS_DISALLOWED'}:
                errors.append(dict(issue,reason='PREVIOUS_SOURCE_ACCESS_RESTRICTION',outcome=old['outcome']))
                continue
            if old.get('outcome')=='FOUND' and old.get('artifact'):
                data = dict(old['artifact'])
                old_request = SourceRequest(**data.pop('request'))
                result = service.acquire(request, RawSourceArtifact(request=old_request, **data))
            else:
                result = service.acquire(request)
            captures[official] = (result,None)
            if result.outcome in {'ACCESS_RESTRICTED','RATE_LIMITED','ROBOTS_DISALLOWED'}:
                blocked.add(host)
            if result.outcome=='FOUND':
                try:
                    body = canonical_text(Tree(store.read(result.artifact).decode()).root.txt())
                    captures[official] = (result,body)
                except (OSError, ValueError):
                    pass
        result, text = captures[official]
        if not text or any(canonical_text(token) not in text for token in cert['terms']):
            errors.append(dict(issue,reason='EXACT_TEXT_NOT_VERIFIED',outcome=result.outcome))
            continue
        captured = archived[url][1]['artifact']
        bindings.setdefault(url, []).append(dict(cert,certificate_key=token,raw_sha256=captured['content_sha256'],
                                            source_sha256=result.artifact.content_sha256))
    if captures:
        receipts = [dict(url=url,outcome=result.outcome,reason=result.reason,
                         artifact=asdict(result.artifact) if result.artifact else None)
                    for url,(result,_) in captures.items()]
        retained.update((item['url'],item) for item in receipts)
        receipt_path.write_text(json.dumps(list(retained.values()),ensure_ascii=False,indent=2)+'\n')
    return bindings, errors


def match_claims(candidate, prepared, acquisition, bindings):
    """Bind certificates to an exact verified reference match and managed player ID."""
    receipts = {item['url']:item for item in json.loads((acquisition / 'results-private.json').read_text())}
    claims, aliases, issues = {}, {}, []
    with closing(sqlite3.connect(candidate.as_uri()+'?mode=ro',uri=True)) as db, closing(
            sqlite3.connect(prepared.as_uri()+'?mode=ro',uri=True)) as ref:
        for url, certificates in bindings.items():
            actual = receipts[url].get('artifact') or {}
            native = url.rsplit('/',1)[-1]
            for item in certificates:
                scope = item['unit']
                label = item['player']['label']
                def hold(reason):
                    issues.append(dict(source=url,player=label,reason=reason))
                if actual.get('content_sha256') != item['raw_sha256']:
                    hold('EXACT_RETAINED_MATCH_DIGEST_REQUIRED')
                    continue
                matches = ref.execute("""
                  SELECT m.match_id,h.display_name,a.display_name FROM international_matches m
                  JOIN international_match_provider_ids v USING(match_id)
                  JOIN football_teams h ON h.team_key=m.home_team_key
                  JOIN football_teams a ON a.team_key=m.away_team_key
                  WHERE v.provider='Sky' AND v.provider_match_id=? AND m.madrid_date=?
                  """,(native,item['date'])).fetchall()
                expected = [{namekey(team),*(namekey(alias) for alias in item.get('team_aliases', {}).get(team,()))}
                            for team in item['teams']]
                if len(matches)!=1 or any(namekey(actual) not in allowed for actual,allowed in zip(matches[0][1:],expected)):
                    issues.append(dict(source=url,player=label,reason='EXACT_FIXTURE_OR_ORDERED_TEAMS_NOT_CERTIFIED',
                                       matched_rows=[list(row) for row in matches],expected_date=item['date']))
                    continue
                player = item['player']
                params = [scope['club_id'],scope['start'],scope['end_exclusive']]
                if player.get('provider_id'):
                    condition = """EXISTS (SELECT 1 FROM verified_identity_links i WHERE i.player_id=p.player_id
                                  AND i.provider=? AND i.provider_player_id=?
                                  AND json_extract(i.evidence_json,'$.identity_status')='VERIFIED')"""
                    params.extend([player['provider'],player['provider_id']])
                else:
                    condition = 'lower(p.display_name)=lower(?)'
                    params.append(label)
                ids = db.execute('''SELECT DISTINCT p.player_id FROM players p
                    JOIN player_match_minutes a USING(player_id) JOIN matches m USING(match_id)
                    WHERE a.club_id=? AND m.local_date>=? AND m.local_date<? AND '''+condition,params).fetchall()
                if len(ids)!=1:
                    hold('UNIQUE_VERIFIED_CLUB_ID_REQUIRED')
                    continue
                match,pid,country = matches[0][0],ids[0][0],namekey(item['represented_team'])
                if not any(country in allowed and namekey(actual) in allowed for actual,allowed in zip(matches[0][1:],expected)):
                    hold('REPRESENTED_TEAM_OUTSIDE_EXACT_FIXTURE')
                    continue
                claims[(match,pid,country)] = dict(source_url=item['evidence_url'],source_sha256=item['source_sha256'],
                    source_scope='INDEPENDENT_EXACT_MATCHDAY_SELECTION',matchday=item['date'],player_display_name=label)
                certificate = dict(player_id=pid,shirt=str(item['shirt']),source_url=item['evidence_url'],
                                   source_sha256=item['source_sha256'],scope='EXACT_TEAM_MATCH_AND_SHIRT')
                for variant in dict.fromkeys([label,*item.get('source_names',())]):
                    key = match,country,namekey(variant)
                    if key in aliases and aliases[key]['player_id']!=pid:
                        raise ValueError('CONFLICTING_EXACT_MATCHDAY_IDENTITY_PROOFS')
                    aliases[key] = certificate
    return claims,aliases,issues
