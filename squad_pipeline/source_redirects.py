"""Follow observed public canonical redirects without relaxing source access policy."""
from urllib.parse import urljoin,urlsplit,urlunsplit,unquote
REDIRECTS={301,302,303,307,308}
def fetch_public_redirects(client,url,purpose,max_hops=3,allowed_hosts=frozenset({'www.skysports.com'})):
 original=url;origin=urlsplit(url);visited=set();chain=[]
 for hop in range(max_hops+1):
  if url in visited:raise ValueError('PUBLIC_REDIRECT_LOOP')
  visited.add(url);record,body=client.fetch(url,purpose,conditional=False)
  if record.get('http_status') not in REDIRECTS:
   return (dict(record,requested_url=original,redirect_chain=chain) if chain else record),body
  if origin.hostname not in allowed_hosts:raise ValueError('PUBLIC_REDIRECT_PROVIDER_NOT_ALLOWED')
  if hop==max_hops:raise ValueError('PUBLIC_REDIRECT_LIMIT')
  headers={k.lower():v for k,v in record.get('headers',{}).items()};location=headers.get('location')
  if not location:raise ValueError('PUBLIC_REDIRECT_WITHOUT_LOCATION')
  target=urljoin(url,location);parsed=urlsplit(target)
  if parsed.scheme!='https' or parsed.hostname!=origin.hostname or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.query or '/api/' in unquote(parsed.path).lower():raise ValueError('PUBLIC_REDIRECT_POLICY_RESTRICTED')
  chain.append(dict(url=url,http_status=record['http_status'],location=location,sha256=record.get('sha256'),path=record.get('path')));url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,parsed.query,''))
 raise AssertionError('UNREACHABLE')
