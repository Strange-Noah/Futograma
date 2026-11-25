"""Bind supplemental duration proof to exact retained match bytes."""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import unicodedata
from urllib.parse import urlsplit

from squad_pipeline.international_parse import namekey
from squad_pipeline.live_parse import Tree, uk_datetime
from squad_pipeline.source_interface import EvidenceStore, RawSourceArtifact, SourceRequest, SourceService

OFFICIAL_HOSTS = {'www.cafonline.com', 'www.fifa.com', 'inside.fifa.com', 'www.uefa.com'}


def canonical_text(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def bind(certificates, archive, work, window, completed):
    store = EvidenceStore(work / 'duration-sources')
    service = SourceService(store, 'historical-duration-proof', OFFICIAL_HOSTS,
                            max_calls=len(certificates) * 2, max_bytes=24_000_000)
    verified, unresolved, captures = {}, [], []
    blocked = set()
    for item in certificates:
        key = sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        if key in completed:
            continue
        source, official = item['source_url'], item['official_url']
        owner = urlsplit(official)
        if (owner.scheme != 'https' or owner.hostname not in OFFICIAL_HOSTS
                or owner.username or owner.password or owner.port is not None
                or len(item['teams']) != 2 or item['duration_minutes'] != 120
                or not item['official_terms'] or not all(canonical_text(x) for x in item['official_terms'])):
            raise ValueError('INVALID_HISTORICAL_DURATION_CERTIFICATE')
        issue = dict(type='DURATION_CERTIFICATE_UNRESOLVED',source=source,official=official)
        try:
            parent, row = archive[source]
            record = dict(row['artifact'])
            original = RawSourceArtifact(request=SourceRequest(**record.pop('request')), **record)
            raw = EvidenceStore(parent, archives=[work.parent.parent]).read(original)
            if original.request.url != source:
                raise ValueError('RETAINED_SOURCE_URL_MISMATCH')
            root = Tree(raw.decode()).root
            header = root.one('sdc-site-match-header')
            teams = [x.txt().strip() for x in header.all('sdc-site-match-header__team-name-block-target')]
            canonical = [x.attrs.get('href') for x in root.all(tag='link') if x.attrs.get('rel') == 'canonical']
            kickoff = uk_datetime(header.one('sdc-site-match-header__detail-time').txt())
            score = '-'.join(x.txt().strip() for x in header.all('sdc-site-match-header__team-score-block'))
            if (canonical != [source] or header.attrs.get('data-status') != '8' or
                    list(map(namekey, teams)) != list(map(namekey, item['teams'])) or
                    score != item['ordered_score'] or not window['start'] <= kickoff[:10] < window['end_exclusive']):
                raise ValueError('EXACT_RETAINED_DURATION_SCOPE_MISMATCH')
            if owner.hostname in blocked:
                raise ValueError('OFFICIAL_SOURCE_ALREADY_STOPPED')
            request = SourceRequest(item['authority'], 'match', official, 'Official extra time evidence', policy='LIVE_ALLOWED')
            result = service.acquire(request)
            captures.append(dict(url=official,outcome=result.outcome,reason=result.reason,
                                 artifact=asdict(result.artifact) if result.artifact else None))
            if result.outcome in {'ACCESS_RESTRICTED','RATE_LIMITED','ROBOTS_DISALLOWED'}:
                blocked.add(owner.hostname)
            if result.outcome != 'FOUND':
                raise ValueError(result.outcome + ':' + str(result.reason))
            body = store.read(result.artifact)
            document = canonical_text(Tree(body.decode()).root.txt())
            if any(canonical_text(term) not in document for term in item['official_terms']):
                raise ValueError('OFFICIAL_DURATION_TERMS_NOT_VERIFIED')
            verified[source] = dict(teams=teams,score=score,kickoff=kickoff,
                                    match_sha256=original.content_sha256,official_url=official,
                                    official_sha256=result.artifact.content_sha256,certificate_key=key)
        except (KeyError, ValueError, OSError, AssertionError) as exc:
            unresolved.append(dict(issue,reason=str(exc),required_input='Exact retained match and official duration proof'))
    if captures:
        (work / 'duration-source-receipts.json').write_text(json.dumps(captures, ensure_ascii=False, indent=2)+'\n')
    return verified, unresolved
