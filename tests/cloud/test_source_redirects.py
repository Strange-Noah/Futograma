import unittest
from squad_pipeline.source_redirects import fetch_public_redirects
class Client:
 def __init__(self,responses):self.responses=responses;self.calls=[]
 def fetch(self,url,purpose,conditional=False):self.calls.append(url);return self.responses[url]
class PublicRedirectTests(unittest.TestCase):
 def response(self,status,location=None):return dict(url='source',http_status=status,status='SUCCESS' if status==200 else 'SOURCE_UNAVAILABLE',headers={'Location':location} if location else {},sha256='raw-hash',path='raw.response'),b'raw'
 def test_observed_same_host_canonical_redirect(self):
  a='https://www.skysports.com/france-results';b='https://www.skysports.com/france-scores-fixtures';c=Client({a:self.response(301,'/france-scores-fixtures'),b:self.response(200)});r,body=fetch_public_redirects(c,a,'directory');self.assertEqual(c.calls,[a,b]);self.assertEqual(r['requested_url'],a);self.assertEqual(len(r['redirect_chain']),1);self.assertEqual(body,b'raw')
 def test_cross_host_and_insecure_and_api_redirects_rejected(self):
  a='https://www.skysports.com/france-results'
  for target in ('https://evil.example/x','http://www.skysports.com/x','https://user:pass@www.skysports.com/x','https://www.skysports.com/api/data','https://www.skysports.com/%61pi/data','https://www.skysports.com/x?token=x'):
   with self.subTest(target=target):
    c=Client({a:self.response(301,target)})
    with self.assertRaisesRegex(ValueError,'POLICY_RESTRICTED'):fetch_public_redirects(c,a,'directory')
    self.assertEqual(c.calls,[a])
 def test_access_denial_never_bypassed(self):
  a='https://www.skysports.com/france-results';c=Client({a:self.response(403)});r,_=fetch_public_redirects(c,a,'directory');self.assertEqual(r['http_status'],403);self.assertEqual(c.calls,[a])
 def test_loop_and_limit_are_bounded(self):
  a='https://www.skysports.com/a';b='https://www.skysports.com/b';c=Client({a:self.response(301,b),b:self.response(301,a)})
  with self.assertRaisesRegex(ValueError,'LOOP'):fetch_public_redirects(c,a,'directory')
  c=Client({a:self.response(301,b),b:self.response(301,'/c')})
  with self.assertRaisesRegex(ValueError,'LIMIT'):fetch_public_redirects(c,a,'directory',max_hops=1)
 def test_other_provider_redirect_not_implicitly_allowed(self):
  a='https://www.fotmob.com/a';c=Client({a:self.response(301,'/b')})
  with self.assertRaisesRegex(ValueError,'PROVIDER_NOT_ALLOWED'):fetch_public_redirects(c,a,'profile')
