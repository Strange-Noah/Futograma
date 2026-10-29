"""Controlled edge cases and checks against acquired evidence; no network or legacy writes."""
import unittest,json
from pathlib import Path
from squad_pipeline.multi_source_dataset import classify,identity,team,wiki_date,wiki_kickoff,wiki_comp,football_boxes,Dataset,resolve_all,OUT,COMP_NAMES
class MultiSourceTests(unittest.TestCase):
 def test_missing_not_zero(self):self.assertEqual(classify({}), 'UNKNOWN')
 def test_bench_alone_unknown(self):self.assertEqual(classify(dict(bench=True)), 'UNKNOWN')
 def test_starter_minutes_optional(self):self.assertEqual(classify(dict(starter=True)), 'STARTER')
 def test_used_sub(self):self.assertEqual(classify(dict(bench=True,substitution_in=True)), 'SUBSTITUTE_USED')
 def test_explicit_unused(self):self.assertEqual(classify(dict(bench=True,unused_explicit=True)), 'UNUSED_SUBSTITUTE')
 def test_missing_from_partial(self):self.assertEqual(classify(dict(in_squad=False)), 'UNKNOWN')
 def test_verified_absent(self):self.assertEqual(classify(dict(in_squad=False,complete_squad_verified=True)), 'NOT_IN_MATCHDAY_SQUAD')
 def test_contradictory(self):self.assertEqual(classify(dict(starter=True,unused_explicit=True)), 'UNKNOWN')
 def test_identity_error(self):self.assertEqual(classify(dict(starter=True,identity_errors=['wrong_player'])), 'UNKNOWN')
 def test_exact_identity(self):
  a=identity(54,'2026-08-28T18:30Z','FC Bayern München','VfB Stuttgart');self.assertEqual(a,identity(54,'2026-08-28T20:30+02:00','Bayern','Stuttgart'))
  self.assertNotEqual(a,identity(54,'2026-08-28T18:31Z','Bayern','Stuttgart'));self.assertNotEqual(a,identity(42,'2026-08-28T18:30Z','Bayern','Stuttgart'));self.assertNotEqual(a,identity(54,'2026-08-28T18:30Z','Stuttgart','Bayern'))
 def test_explicit_aliases(self):self.assertEqual(team('FC Schalke 04'),team('Schalke 04'));self.assertEqual(team('SV 07 Elversberg'),team('Elversberg'))
 def test_fifa_template(self):self.assertEqual(team('{{fb-rt|NOR}}'),'norway');self.assertEqual(team('{{fb|BRA}}'),'brazil')
 def test_yearless_date(self):self.assertIsNone(wiki_date('6 October'));self.assertEqual(wiki_date('6 October',2026),'2026-10-06')
 def test_utz(self):self.assertEqual(wiki_kickoff('2026-10-06','{{UTZ|20:00|9}}'),'2026-10-06T11:00:00+00:00')
 def test_half_hour_timezone(self):self.assertEqual(wiki_kickoff('2026-10-03','{{UTZ|19:30|5.5}}'),'2026-10-03T14:00:00+00:00')
 def test_unknown_timezone(self):self.assertIsNone(wiki_kickoff('2026-10-03','20:00'))
 def test_template_aliases(self):self.assertEqual(len(list(football_boxes('{{footballbox collapsible\n|time={{UTZ|20:00|9}}\n}}'))),1)
 def test_cups_distinct(self):
  self.assertEqual(wiki_comp('===DFB-Pokal===',{},'Bayern season'),209);self.assertEqual(wiki_comp('===DFB-Pokal===\n===Franz Beckenbauer Supercup===',{},'Bayern season'),'DFL_SUPERCUP');self.assertEqual(COMP_NAMES[138],'Copa del Rey')
 def test_club_friendly_excluded(self):self.assertIsNone(Dataset().candidate(737066,489,'2026-08-16T14:00Z','Manchester City','Arsenal','controlled',None,{}))
 def test_reported_and_calculated_separate(self):
  d=Dataset();k=d.candidate(737066,47,'2026-09-05T14:00Z','Manchester City','Coventry','controlled','x',{});d.observe(737066,k,'controlled',{},starter=True,reported_minutes=None,calculated_minutes=80);r,q=resolve_all(d);self.assertIsNone(r[0]['minutes']);self.assertEqual(r[0]['calculated_minutes'],[80]);self.assertEqual(q,[])
 def test_real_zero_requires_crosschecked_rosters(self):
  obs=json.loads((OUT/'source_observations.json').read_text());zeros=[o for o in obs if o['provider']=='ManchesterCity' and o['participation_status'] in ('UNUSED_SUBSTITUTE','NOT_IN_MATCHDAY_SQUAD')];self.assertEqual(len(zeros),2)
  for o in zeros:self.assertTrue(o['team_section']['cross_check'][0]['exact_unique_name_set_agreement'])
 def test_real_unknown_null(self):
  rows=json.loads((OUT/'matches.json').read_text());self.assertEqual(len(rows),49)
  for r in rows:
   self.assertNotEqual(r['competition_id'],489)
   if r['participation_status']=='UNKNOWN':self.assertIsNone(r['minutes'])
if __name__=='__main__':unittest.main()
