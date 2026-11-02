"""Structured identity diagnostics; mismatch checks remain mandatory."""
import json
from urllib.parse import urlsplit,parse_qs
from .international_discovery import payload
from .canonical_fixture import digest,encoded,migrate


def inspect_match(raw, expected, source):
    if isinstance(raw, bytes):raw=raw.decode('utf-8','replace')
    parts=urlsplit(source.get('url',''));result=dict(expected_id=str(expected),source_url=source.get('url'),request_url_without_fragment=parts._replace(fragment='').geturl(),fragment_id=parts.fragment,query=parse_qs(parts.query),body_sha256=source.get('sha256'),actual_id=None,lineup_id=None,ssr_path=None)
    try:
        page=payload(raw);result['actual_id']=str((page.get('general') or {}).get('matchId')) if (page.get('general') or {}).get('matchId') is not None else None
        result['lineup_id']=str(((page.get('content') or {}).get('lineup') or {}).get('matchId')) if ((page.get('content') or {}).get('lineup') or {}).get('matchId') is not None else None
        result['ssr_path']='$.props.pageProps.data' if 'general' in page else '$.props.pageProps'
        fallback=page.get('fallback') or {};result['exact_fallback_keys']=[k for k,v in fallback.items() if isinstance(v,dict) and str((v.get('general') or {}).get('matchId'))==str(expected)]
        result['classification']='EXACT_MATCH' if result['actual_id']==str(expected) and result['lineup_id']==str(expected) else 'RETURNED_OTHER_MATCH' if result['actual_id'] else 'SSR_OBJECT_NOT_FOUND'
        result['fragment_only_routing_risk']=bool(parts.fragment and not parts.query)
    except (ValueError,KeyError,TypeError) as exc:result.update(classification='INVALID_SSR',error=type(exc).__name__+':'+str(exc))
    return result


def record(db,diagnostic):
    migrate(db);key=digest(diagnostic)
    db.execute('INSERT OR IGNORE INTO canonical_id_diagnostics VALUES(?,?,?,?,?,?,?)',(key,diagnostic['expected_id'],diagnostic.get('actual_id'),diagnostic.get('lineup_id'),diagnostic.get('source_url'),diagnostic.get('body_sha256'),encoded(diagnostic)))
    return key


class MatchIdentityMismatch(ValueError):
    def __init__(self,code,diagnostic):
        self.diagnostic=diagnostic
        super().__init__(code+';diagnostic='+encoded(diagnostic))
