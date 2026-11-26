"""Follow observed public canonical redirects without relaxing source access policy."""
import re
from urllib.parse import urljoin,urlsplit,urlunsplit,unquote
REDIRECTS={301,302,303,307,308}
def fetch_public_redirects(client,url,purpose,max_hops=3,allowed_hosts=frozenset({'www.skysports.com'}), path_allowed=None):
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
  if path_allowed is not None and not path_allowed(unquote(parsed.path)):
   raise ValueError('PUBLIC_REDIRECT_ENTITY_MISMATCH')
  chain.append(dict(url=url,http_status=record['http_status'],location=location,sha256=record.get('sha256'),path=record.get('path')));url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,parsed.query,''))
 raise AssertionError('UNREACHABLE')

def fetch_fixture_discovery(client, url, purpose):
    """Follow an observed public redirect only for the same entity and page type."""
    origin = urlsplit(url)
    profile = re.fullmatch(r'/players/([0-9]+)/?', origin.path)
    if origin.scheme == 'https' and origin.netloc == 'www.fotmob.com' and profile and not origin.query:
        player = profile[1]
        return fetch_public_redirects(
            client, url, purpose, allowed_hosts=frozenset({'www.fotmob.com'}),
            path_allowed=lambda path: bool(re.fullmatch(
                r'/players/' + player + r'(?:/[a-zA-Z0-9-]+)?/?', path)))
    team = re.fullmatch(r'/teams/([0-9]+)/(fixtures|squad)/?', origin.path)
    if origin.scheme == 'https' and origin.netloc == 'www.fotmob.com' and team and not origin.query:
        native, section = team.groups()
        return fetch_public_redirects(
            client, url, purpose, allowed_hosts=frozenset({'www.fotmob.com'}),
            path_allowed=lambda path: bool(re.fullmatch(
                r'/teams/' + native + '/' + section + r'(?:/[a-zA-Z0-9-]+)?/?', path)))
    return fetch_public_redirects(client, url, purpose)
