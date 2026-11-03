"""Routing diagnostics only; retain strict parser and quarantine policy."""
from urllib.parse import urlsplit
from .match_diagnostics import inspect_match

def routing_review(raw,expected,source):
 d=inspect_match(raw,expected,source);p=urlsplit(source.get('url',''))
 reason='EXACT_IDS_REQUIRE_REMAINING_ADAPTER_VALIDATION' if d['classification']=='EXACT_MATCH' else 'FRAGMENT_NOT_SENT_TO_SERVER' if p.fragment==str(expected) and not p.query else 'RESPONSE_IDENTITY_MISMATCH'
 return dict(diagnostic=d,reason=reason,accept=False,retry_authorized=False)

def shared_routes(reviews):
 groups={}
 for r in reviews:
  d=r['diagnostic'];g=groups.setdefault(d['request_url_without_fragment'],dict(expected_ids=set(),actual_ids=set(),hashes=set(),observations=0));g['expected_ids'].add(d['expected_id']);g['actual_ids'].add(str(d['actual_id']));g['hashes'].add(str(d['body_sha256']));g['observations']+=1
 return [dict(wire_url=k,expected_ids=sorted(v['expected_ids']),actual_ids=sorted(v['actual_ids']),body_hashes=sorted(v['hashes']),observations=v['observations'],ambiguous_route=len(v['expected_ids'])>1) for k,v in sorted(groups.items())]
