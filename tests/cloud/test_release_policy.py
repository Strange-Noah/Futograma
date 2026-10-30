import unittest
from datetime import datetime,timezone,timedelta
from squad_pipeline.minute_policy import select
from squad_pipeline.roster_policy import classify
from squad_pipeline.cloud.incremental import bounds
class ReleasePolicyTests(unittest.TestCase):
 def obs(self,minutes,provider='FotMob',status='STARTER'):return dict(minutes=minutes,provider=provider,status=status)
 def test_five_inclusive(self):self.assertEqual(select([self.obs(85),self.obs(90,'Sky')])['minutes'],85)
 def test_six_is_conflict(self):self.assertFalse(select([self.obs(84),self.obs(90,'Sky')])['accepted'])
 def test_input_order_does_not_select(self):
  a=[self.obs(90,'Sky'),self.obs(87)];self.assertEqual(select(a),select(a))
  self.assertEqual(select(a)['selected_source'],select(a[::-1])['selected_source'])
 def test_no_appearance_status_tolerance(self):self.assertFalse(select([self.obs(1),self.obs(0,'Sky','UNUSED_SUBSTITUTE')])['accepted'])
 def test_played_zero_is_not_unused(self):self.assertEqual(select([self.obs(1,status='SUBSTITUTE_USED'),self.obs(0,'Sky','SUBSTITUTE_USED')])['minutes'],1)
 def test_invalid_bool_is_not_minute(self):self.assertFalse(select([self.obs(True)])['accepted'])
 def test_retain_accepted_before_lower_priority(self):self.assertEqual(select([self.obs(78,'RETAINED_ACCEPTED'),self.obs(80)])['minutes'],78)
 def test_first_team_never_downgraded_by_youth_eligibility(self):self.assertEqual(classify(dict(management=dict(state='INCLUDED',included=True),academy_affiliation_status='VERIFIED_CURRENT_ACADEMY_EVIDENCE'))['cohort'],'FIRST_TEAM')
 def test_youth_needs_no_continuous_management(self):
  r=classify(dict(management=dict(state='UNKNOWN'),academy_affiliation_status='VERIFIED_LEGACY_ACADEMY_ASSOCIATION'));self.assertEqual(r['state'],'YOUTH');self.assertFalse(r['collect_internationals']);self.assertFalse(r['included'])
 def test_age_is_not_youth_evidence(self):self.assertEqual(classify(dict(management=dict(state='UNKNOWN'),age=17))['cohort'],'UNKNOWN')
 def test_departure_wins(self):self.assertEqual(classify(dict(management=dict(state='EXCLUDED'),academy_affiliation_status='VERIFIED_LEGACY_ACADEMY_ASSOCIATION'))['cohort'],'EXCLUDED')
 def test_window_is_runtime_and_catches_gap(self):
  now=datetime(2027,2,2,tzinfo=timezone.utc);last=now-timedelta(days=10);start,end=bounds(last.isoformat(),now);self.assertEqual(start,last);self.assertEqual(end,now)
 def test_normal_window_72_hours(self):
  now=datetime.now(timezone.utc);self.assertEqual(bounds(None,now)[0],now-timedelta(hours=72))

class StatusContradictionTests(unittest.TestCase):
 def test_sky_explicit_unused_cannot_override_entry_even_equal_minutes(self):
  from squad_pipeline.international_sky_fallback import target_minutes
  f=dict(provider_match_id='1',home=dict(id='2'),away=dict(id='3'),kickoff='2026-10-01T12:00:00Z')
  c=dict(discovery_claim=dict(id=1,teamId=2,opponentTeamId=3,isHomeTeam=True,matchDate=dict(utcTime=f['kickoff']),minutesPlayed=0,playedInMatch=False,onBench=True))
  r=target_minutes(dict(role='bench',participation_status='SUBSTITUTE_USED',derived_minutes=0,entry=dict(base=90)),c,f,'home',90)
  self.assertIsNone(r['minutes']);self.assertIn('APPEARANCE_STATUS_CONTRADICTION',r['evidence_scope'])
