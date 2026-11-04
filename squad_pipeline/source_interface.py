"""Versioned source contracts over existing acquisition/parsing implementations.

No roster selection, canonical acceptance, or R1–R6 business rules belong here.
Provider-native facts are observations, never canonical football decisions.
"""
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl
from datetime import datetime, timezone
import hashlib
import json
import re
from .live_sources import Client
from .acquisition import detect_type

VERSION = 'source-contract/1'


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def sha(body):
    return hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class SourceRequest:
    provider: str
    category: str
    url: str
    resource: str
    parameters: tuple = ()
    expected_ids: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    purpose: str = 'source_evidence'
    policy: str = 'ARCHIVE_ONLY'
    version: str = VERSION

    @property
    def target_url(self):
        if not self.url and self.policy=='ARCHIVE_ONLY' and self.resource:
            return None
        u=urlsplit(self.url)
        if u.scheme!='https' or not u.hostname or u.username or u.password:
            raise ValueError('ANONYMOUS_HTTPS_REQUIRED')
        if self.policy not in {'ARCHIVE_ONLY','CACHE_FIRST','LIVE_ALLOWED'}:
            raise ValueError('UNKNOWN_RETRIEVAL_POLICY')
        query=urlencode(parse_qsl(u.query,keep_blank_values=True)+list(self.parameters),doseq=True)
        return urlunsplit((u.scheme,u.netloc,u.path,query,u.fragment))

    @property
    def key(self):
        return sha(encode([self.version,self.provider,self.category,self.target_url,
                           self.resource,self.expected_ids,self.context]).encode())


@dataclass(frozen=True)
class RawSourceArtifact:
    request: SourceRequest
    content_sha256: str
    locator: str
    metadata: dict
    authority: str = 'HTTP_CAPTURE'
    version: str = VERSION

    @property
    def key(self):
        return sha(encode([self.request.key,self.content_sha256,self.authority,
            self.metadata.get('request_timestamp'),self.metadata.get('response_timestamp'),self.metadata.get('observed_on')]).encode())


@dataclass(frozen=True)
class ParsedSourceObservation:
    outcome: str
    provider: str
    category: str
    native: dict
    artifact_key: str
    parser_version: str
    completeness: dict
    uncertainties: tuple = ()
    canonical_identity: str | None = None
    version: str = VERSION


@dataclass(frozen=True)
class SourceAcquisitionResult:
    outcome: str
    artifact: RawSourceArtifact | None = None
    reason: str | None = None
    metadata: dict = field(default_factory=dict)
    version: str = VERSION


class EvidenceStore:
    """Explicit relocatable roots plus hash index; no silent cross-boundary lookup."""
    def __init__(self, root, archives=(), index=None, relocations=None, asset_reader=None):
        self.root=Path(root).resolve()
        self.archives=tuple(Path(p).resolve() for p in archives)
        self.index=index or {}
        self.relocations=relocations or {}
        self.asset_reader=asset_reader

    def candidates(self, locator):
        p=Path(locator)
        if p.is_absolute():
            for old,new in self.relocations.items():
                try:p=Path(new)/p.relative_to(old);break
                except ValueError:continue
            for root in (self.root,)+self.archives:
                if p.resolve().is_relative_to(root):return [p.resolve()]
            return []
        return [(root/p).resolve() for root in (self.root,)+self.archives
                if (root/p).resolve().is_relative_to(root)]

    def read(self, artifact):
        for p in self.candidates(artifact.locator):
            if p.is_file():
                body=p.read_bytes()
                if sha(body)!=artifact.content_sha256:raise ValueError('ARTIFACT_HASH_MISMATCH')
                return body
        raise FileNotFoundError('ARCHIVE_LOCATOR_UNRESOLVED')

    def import_body(self, request, body, metadata, authority='HTTP_CAPTURE'):
        digest=sha(body);locator='bodies/'+digest+'.response';p=self.root/locator
        if not p.resolve().is_relative_to(self.root):raise ValueError('SOURCE_STORAGE_BOUNDARY')
        p.parent.mkdir(parents=True,exist_ok=True)
        if p.exists():
            if p.read_bytes()!=body:raise ValueError('IMMUTABLE_CONTENT_COLLISION')
        else:
            with p.open('xb') as f:f.write(body)
        # Capture records get content-derived filenames; logical identity never does.
        safe={k:v for k,v in metadata.items() if k not in {'headers','conditional_headers','authorization','cookie'}}
        record=RawSourceArtifact(request,digest,locator,safe,authority)
        meta=self.root/'artifacts'/(record.key+'.json')
        if not meta.resolve().is_relative_to(self.root):raise ValueError('SOURCE_STORAGE_BOUNDARY')
        meta.parent.mkdir(parents=True,exist_ok=True)
        if not meta.exists():
            with meta.open('x') as f:json.dump(asdict(record),f,ensure_ascii=False,sort_keys=True)
        return record

    def normalize(self, record, provider='UNKNOWN', category='evidence'):
        if record.get('authorization_date') and record.get('instruction'):
            # Manual authority is preserved without inventing HTTP status/body.
            return SourceAcquisitionResult('MANUAL_POLICY',reason='AUTHORIZED_MANUAL_POLICY',
                metadata=dict(authority='MANUAL_POLICY_RECORD',authorization='PRESERVED_PRIOR_ASSERTION;NOT_A_NEW_GRANT',record_hash=sha(encode(record).encode()),record=record))
        digest=(record.get('reused_body_sha256') or record.get('source_sha256')
                or record.get('body_sha256') or record.get('sha256'))
        url=record.get('url') or record.get('source_url')
        if not isinstance(digest,str) or not re.fullmatch('[a-fA-F0-9]{64}',digest):
            return SourceAcquisitionResult('EVIDENCE_CONTRACT_INCOMPLETE',reason='HASH_REQUIRED')
        locator=record.get('reused_body_path') or record.get('path')
        asset=record.get('retained_asset_ref') or (record.get('raw_fields') or {}).get('retained_raw_asset')
        if not locator:
            locator=next((p for p in self.index.get(digest,[]) if any(c.is_file() for c in self.candidates(p))),None)
        if not locator and not asset and self.asset_reader is not None and hasattr(self.asset_reader,'find_asset'):
            asset=self.asset_reader.find_asset(digest)
        if not locator and asset and self.asset_reader is not None:
            try:body=self.asset_reader(asset,digest)
            except PermissionError:return SourceAcquisitionResult('ASSET_ACCESS_RESTRICTED',reason='Authorized asset backend denied access')
            except FileNotFoundError:return SourceAcquisitionResult('ASSET_NOT_FOUND',reason='Asset backend did not find retained body')
            except (OSError,TimeoutError):return SourceAcquisitionResult('ASSET_TEMPORARILY_FAILED',reason='Asset backend retrieval failed')
            if not isinstance(body,bytes) or sha(body)!=digest:
                return SourceAcquisitionResult('INTEGRITY_CONFLICT',reason='Asset body hash mismatch')
            req=SourceRequest(record.get('provider') or provider,category,url,category)
            imported=self.import_body(req,body,dict(asset_ref=asset,retrieval_method='AUTHORIZED_ASSET_BACKEND'),authority='SOURCE_RECORD')
            locator=imported.locator
        if not locator:
            return SourceAcquisitionResult('REMOTE_ASSET_REQUIRED' if asset else 'ARCHIVE_LOCATOR_UNRESOLVED',
                reason='Asset requires authorized backend resolution' if asset else 'Not found in configured indexes; source availability unknown',
                metadata=dict(content_sha256=digest,asset_ref=asset,source_url=url))
        if not url:
            return SourceAcquisitionResult('EVIDENCE_CONTRACT_INCOMPLETE',reason='REQUEST_URL_UNAVAILABLE',metadata=dict(content_sha256=digest,locator=locator))
        request=SourceRequest(record.get('provider') or provider,category,url,category)
        metadata=dict(source_url=url,request_timestamp=record.get('request_timestamp'),
            response_timestamp=record.get('response_timestamp') or record.get('observed_at') or record.get('acquired_at') or record.get('fetched_at'),
            observed_on=record.get('observed_on'),locator=record.get('locator') or record.get('location'),
            http_status=record.get('http_status'),asset_ref=asset,source_status=record.get('source_status') or record.get('status'),
            timestamp_precision='TIMESTAMP' if any(record.get(k) for k in ['response_timestamp','observed_at','acquired_at','fetched_at']) else 'DAY_OR_UNKNOWN')
        artifact=RawSourceArtifact(request,digest,locator,metadata,'SOURCE_RECORD' if 'source_sha256' in record else 'HTTP_CAPTURE')
        try:self.read(artifact)
        except FileNotFoundError:return SourceAcquisitionResult('ARCHIVE_LOCATOR_UNRESOLVED',artifact,reason='Retained body not found in configured roots')
        except ValueError:return SourceAcquisitionResult('INTEGRITY_CONFLICT',artifact,reason='Retained body hash differs')
        return SourceAcquisitionResult('FOUND',artifact)


class SourceService:
    """Opt-in facade; reuses the legacy bounded public Client without business rules."""
    def __init__(self, store, run_id, allowed_hosts, max_calls=20, max_bytes=12000000,
                 transport=None,sleeper=None):
        if type(max_calls) is not int or max_calls<0 or type(max_bytes) is not int or max_bytes<0:raise ValueError('INVALID_SOURCE_BUDGET')
        self.store=store;self.max_bytes=max_bytes;self.bytes=0
        kwargs=dict(storage_root=store.root,allowed_hosts=allowed_hosts,transport=transport)
        if sleeper is not None:kwargs['sleeper']=sleeper
        self.client=Client(run_id,budget=max_calls,**kwargs)

    def acquire(self, request, prior=None):
        if request.version!=VERSION:return SourceAcquisitionResult('CONTRACT_VERSION_UNSUPPORTED')
        request.target_url  # validate even on a replay
        if prior:
            if prior.request.key!=request.key:return SourceAcquisitionResult('IDENTITY_MISMATCH',reason='REPLAY_REQUEST_MISMATCH')
            try:body=self.store.read(prior)
            except FileNotFoundError:return SourceAcquisitionResult('ARCHIVE_LOCATOR_UNRESOLVED',prior)
            except ValueError:return SourceAcquisitionResult('INTEGRITY_CONFLICT',prior)
            return SourceAcquisitionResult('FOUND',prior,metadata=dict(transport='ARCHIVE_REPLAY',bytes=len(body)))
        if request.policy=='ARCHIVE_ONLY':return SourceAcquisitionResult('NOT_REQUESTED',reason='No archived artifact supplied')
        if request.policy=='CACHE_FIRST':
            cached=self.client.cache.get(request.target_url)
            if cached:
                saved=dict(cached,url=request.target_url)
                for p in self.store.candidates(cached['path']):
                    sidecar=p.with_suffix('.metadata.json')
                    if sidecar.is_file():
                        saved.update(json.loads(sidecar.read_text()));break
                result=self.store.normalize(saved,request.provider,request.category)
                if result.outcome=='INTEGRITY_CONFLICT':return result
                if result.outcome=='FOUND':
                    a=result.artifact
                    return SourceAcquisitionResult('FOUND',RawSourceArtifact(request,a.content_sha256,a.locator,a.metadata),metadata=dict(transport='CACHE'))
        if self.bytes>=self.max_bytes:return SourceAcquisitionResult('BUDGET_EXHAUSTED',reason='BYTE_BUDGET')
        try:record,body=self.client.fetch(request.target_url,request.purpose)
        except RuntimeError as e:return SourceAcquisitionResult('BUDGET_EXHAUSTED',reason=str(e))
        self.bytes=sum(r.get('size',0) for r in self.client.records)
        if self.bytes>self.max_bytes:return SourceAcquisitionResult('BUDGET_EXHAUSTED',reason='BYTE_BUDGET',metadata=dict(calls=self.client.calls))
        code=record.get('http_status');error=record.get('error') or ''
        if record.get('status')=='SUCCESS' and body:
            artifact=self.store.import_body(request,body,dict(record,returned_resource_url=record.get('url')))
            return SourceAcquisitionResult('FOUND',artifact,metadata=dict(calls=self.client.calls))
        if code==304:return SourceAcquisitionResult('CACHE_INVALID',reason='304 without a usable verified cached body')
        if code==200 and not body:return SourceAcquisitionResult('MALFORMED',reason='EMPTY_RESPONSE')
        outcome=('RATE_LIMITED' if code==429 else 'ACCESS_RESTRICTED' if code in {401,403} or any(x in error for x in ['POLICY','DISALLOWED','ACCESS_DENIED','CHALLENGE'])
                 else 'UNAVAILABLE' if code in {404,410} else 'REDIRECT_REQUIRES_REVIEW' if code in {301,302,303,307,308}
                 else 'MALFORMED' if record.get('status')=='VALIDATION_FAILED' else 'TEMPORARILY_FAILED')
        return SourceAcquisitionResult(outcome,reason=error or 'HTTP_'+str(code),metadata=dict(http_status=code,calls=self.client.calls))

    def pages(self, request, page_parameters, max_pages=3):
        from dataclasses import replace
        if len(page_parameters)>max_pages:return [SourceAcquisitionResult('BUDGET_EXHAUSTED',reason='PAGE_BUDGET')]
        return [self.acquire(replace(request,parameters=tuple(params))) for params in page_parameters]

    def observe(self, request, prior=None):
        from .source_parsing import parse
        acquired=self.acquire(request,prior)
        if acquired.outcome!='FOUND':return acquired,None
        return acquired,parse(acquired.artifact,self.store.read(acquired.artifact))


class AssetArchiveReader:
    """Read existing verified tar assets by body hash, without path extraction.

    Network/authentication belongs to the existing authorized asset backend.
    This adapter consumes already-downloaded assets and never chooses sources.
    """
    def __init__(self, assets, wanted_hashes=None, max_member=16000000,
                 max_archive_bytes=512000000,max_cached_bytes=128000000):
        import tarfile
        self.bodies={};self.owners={};self.archive_versions={};cached=0
        for reference,item in assets.items():
            path=Path(item['path']).resolve()
            if sha(path.read_bytes())!=item['sha256']:raise ValueError('ASSET_ARCHIVE_HASH_MISMATCH')
            self.archive_versions[str(reference)]=item['sha256'];total=0
            with tarfile.open(path) as tar:
                for member in tar:
                    if not member.isfile():continue
                    if member.size>max_member:raise ValueError('ASSET_MEMBER_BUDGET')
                    total+=member.size
                    if total>max_archive_bytes:raise ValueError('ASSET_ARCHIVE_BYTE_BUDGET')
                    body=tar.extractfile(member).read();digest=sha(body)
                    if wanted_hashes is not None and digest not in wanted_hashes:continue
                    self.owners.setdefault(digest,[]).append(str(reference))
                    if digest not in self.bodies:
                        cached+=len(body)
                        if cached>max_cached_bytes:raise ValueError('ASSET_CACHE_BYTE_BUDGET')
                        self.bodies[digest]=body

    def find_asset(self, expected_hash):
        return next(iter(self.owners.get(expected_hash,[])),None)

    def __call__(self, reference, expected_hash):
        if str(reference) not in self.archive_versions:raise FileNotFoundError('ASSET_NOT_CONFIGURED')
        if str(reference) not in self.owners.get(expected_hash,[]):raise FileNotFoundError('BODY_NOT_IN_DECLARED_ASSET')
        return self.bodies[expected_hash]
