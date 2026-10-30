import unittest,json,sqlite3
from squad_pipeline.identity_pipeline import DDL,Record,Candidate
from squad_pipeline.provider_identity_discovery import verify_profile_aliases
from squad_pipeline.identity_registry import lookup
class VerifiedProfileAliasTests(unittest.TestCase):
 def setUp(self):self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row;self.db.executescript(DDL)
 def tearDown(self):self.db.close()
 def inputs(self):
  anchor=Record('registry://retained','x','retained','FotMob',['Example Player'],provider_ids={'FotMob':'11'},birth_date='2000-01-01',retrieved=False)
  candidate=Candidate(-7,['Example Player'],[anchor]);data=dict(id=11,name='Example Player',gender='male',isCoach=False,primaryTeam=dict(teamId=1),birthDate=dict(utcTime='2000-01-01T00:00:00Z'),meta=dict(personJSONLD=dict(name='Example Player',alternateName='Example Middle Player')))
  raw='<script id="__NEXT_DATA__" type="application/json">'+json.dumps(dict(props=dict(pageProps=dict(data=data))))+'</script>';return raw,dict(url='https://www.fotmob.com/players/11',sha256='a'*64,status='SUCCESS'),[candidate]
 def test_actual_profile_names_become_reusable_with_same_stable_id(self):
  raw,source,candidates=self.inputs();verify_profile_aliases(self.db,raw,source,-7,candidates,'2026-10-31');self.assertEqual(lookup(self.db,1,dict(name='Example Middle Player',provider_key='sky-name:1:examplemiddleplayer')),-7)
 def test_profile_cannot_reassign_canonical_identity(self):
  raw,source,candidates=self.inputs()
  with self.assertRaisesRegex(ValueError,'CANONICAL_IDENTITY_CONFLICT'):verify_profile_aliases(self.db,raw,source,-8,candidates,'2026-10-31')
  self.assertEqual(self.db.execute('SELECT COUNT(*) FROM verified_name_aliases').fetchone()[0],0)
