import unittest
from squad_pipeline.international_parse import minutes_from_events
from squad_pipeline.live_parse import ParseError
class InternationalMinuteTests(unittest.TestCase):
    def players(self):return [dict(provider_key=str(i),role='starters' if i<=11 else 'bench',name='Person '+str(i)) for i in range(1,14)]
    def event(self,typ,pid,base,added=0):return dict(type=typ,provider_key=str(pid),clock=dict(base=base,added=added,raw=str(base)))
    def test_extra_time_player_interval(self):
        rows=minutes_from_events(self.players(),[self.event('sub-on',12,100),self.event('sub-off',1,100)],120);self.assertEqual(rows[0]['derived_minutes'],100);self.assertEqual(rows[11]['derived_minutes'],20);self.assertEqual(rows[1]['derived_minutes'],120)
    def test_shootout_not_extra_minutes(self):self.assertEqual(minutes_from_events(self.players(),[],90)[0]['derived_minutes'],90)
    def test_ambiguous_late_clock_keeps_null(self):
        rows=minutes_from_events(self.players(),[self.event('sub-on',12,93),self.event('sub-off',1,93)],90);self.assertIsNone(rows[0]['derived_minutes']);self.assertIsNone(rows[11]['derived_minutes']);self.assertEqual(rows[12]['derived_minutes'],0)
    def test_explicit_added_clock_uses_nominal_base(self):
        rows=minutes_from_events(self.players(),[self.event('sub-on',12,90,3),self.event('sub-off',1,90,3)],90);self.assertEqual(rows[0]['derived_minutes'],90);self.assertEqual(rows[11]['derived_minutes'],0)
    def test_unbalanced_substitution_rejected(self):
        with self.assertRaises(ParseError):minutes_from_events(self.players(),[self.event('sub-on',12,60)],90)

class SkyDurationTemplateTests(unittest.TestCase):
 def test_hidden_aet_template_never_implies_extra_time(self):
  from squad_pipeline.international_parse import Tree,sky_duration
  h=Tree('<div class="sdc-site-match-header" data-status="4"><span>FT Full Time</span><span>AET After Extra Time</span></div>').root.one('sdc-site-match-header');self.assertEqual(sky_duration(h,{}),90)
 def test_explicit_extra_time_requires_same_match_evidence(self):
  from squad_pipeline.international_parse import Tree,sky_duration
  h=Tree('<div class="sdc-site-match-header" data-status="4"></div>').root.one('sdc-site-match-header')
  with self.assertRaisesRegex(ValueError,'DURATION_EVIDENCE'):sky_duration(h,dict(verified_duration_minutes=120))
  self.assertEqual(sky_duration(h,dict(verified_duration_minutes=120,duration_evidence=dict(scope='EXACT_MATCH_DURATION',sha256='verified-source'))),120)
