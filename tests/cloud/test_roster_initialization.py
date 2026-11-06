import unittest
from squad_pipeline.roster_initialization import PeriodUnit,initialize,cross_period_findings
from squad_pipeline.quality_pipeline import participation_contract
class RosterInitializationTests(unittest.TestCase):
 def setUp(self):
  self.unit=PeriodUnit(1,'2024/25','H1','2024-07-01','2025-01-01');self.catalogue=[dict(match_id='m',on='2024-09-01',state='COMPLETED')];self.source=dict(url='https://source.example/m',sha256='a'*64)
  self.sheet=dict(match_id='m',club_id=1,on='2024-09-01',admission='EXISTING_VERIFIED_FIXTURE_AND_SOURCE',complete_squad=True,source=self.source,players=[dict(provider_key='p1',canonical_player_id=7,role='bench',participation_status='BENCH_PARTICIPATION_UNKNOWN',minutes=None)])
 def historical(self,**kw):return initialize('HISTORICAL_SQUAD_UNION',self.unit,catalogue=self.catalogue,squads=[self.sheet],catalogue_complete=True,**kw)
 def test_bench_unknown_included_without_zero_or_interval_invention(self):
  r=self.historical();self.assertEqual(r['player_ids'],[7]);self.assertTrue(r['observed_population_accepted']);self.assertFalse(r['management_intervals_inferred']);self.assertIsNone(self.sheet['players'][0]['minutes'])
 def test_current_roster_cannot_seed_historical_unit(self):
  with self.assertRaisesRegex(ValueError,'CROSS_PERIOD'):self.historical(accepted_current=[dict(player_id=99)])
 def test_incomplete_catalogue_or_missing_squad_never_claims_complete_union(self):
  for catalogue_complete,squads in [(False,[self.sheet]),(True,[])]:self.assertFalse(initialize('HISTORICAL_SQUAD_UNION',self.unit,catalogue=self.catalogue,squads=squads,catalogue_complete=catalogue_complete)['observed_population_accepted'])
 def test_common_output_contract_keeps_shared_participation_adjudication(self):
  current=initialize('ACCEPTED_CURRENT',self.unit,accepted_current=[dict(player_id=7,included=True,classification='FIRST_TEAM')]);historical=self.historical()
  def downstream(population):return [participation_contract(dict(provider_player_id=str(pid),participation_status='STARTER',minutes=90),self.source) for pid in population['player_ids']]
  self.assertEqual(downstream(current),downstream(historical));self.assertEqual(current['player_ids'],historical['player_ids'])
 def test_cross_period_injury_is_valid_explanation_not_population_insertion(self):
  previous=self.historical();unit=PeriodUnit(1,'2024/25','H2','2025-01-01','2025-07-01');sheet=dict(self.sheet,match_id='n',on='2025-02-01',players=[dict(provider_key='p2',canonical_player_id=8,role='starters')]);current=initialize('HISTORICAL_SQUAD_UNION',unit,catalogue=[dict(match_id='n',on='2025-02-01',state='COMPLETED')],squads=[sheet],catalogue_complete=True)
  f=cross_period_findings(previous,current)[0];self.assertEqual(f['change'],'DISAPPEARED');self.assertIn('DOCUMENTED_WHOLE_PERIOD_INJURY_OR_PROLONGED_ABSENCE',f['accepted_explanation_types']);self.assertFalse(f['human_pending']);self.assertEqual(current['player_ids'],[8])

 def test_initialization_does_not_import_supplementary_audits(self):
  import builtins,importlib
  from unittest.mock import patch
  import squad_pipeline.roster_initialization as module
  original=builtins.__import__
  def blocked(name,*args,**kwargs):
   if 'reconstruction' in name:raise ImportError('AUDIT_DISABLED')
   return original(name,*args,**kwargs)
  with patch('builtins.__import__',side_effect=blocked):importlib.reload(module)

 def test_same_canonical_player_twice_in_one_sheet_is_held(self):
  self.sheet['players'].append(dict(provider_key='other-key',canonical_player_id=7,role='starters'))
  r=self.historical();self.assertFalse(r['observed_population_accepted']);self.assertEqual(r['issues'][0]['reason'],'CANONICAL_IDENTITY_COLLISION_OR_TYPE')
