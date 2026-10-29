import unittest,json,re,copy
from pathlib import Path
from squad_pipeline.core import ROOT,BASE,read
from squad_pipeline.html_rosters import parse_city,parse_inter
from squad_pipeline.workload_audit import madrid_date,prefix_recompute
from squad_pipeline.identity_reverse import edit_distance,age_on,validated_reverse_pool

class OfficialHtmlTests(unittest.TestCase):
    def source(self,club):
        return next((BASE/'raw/official'/club).glob('*/response.html')).read_text()
    def test_city_all_cards_once_and_staff_excluded(self):
        rows,q=parse_city(self.source('manchester_city_club'));self.assertEqual(q['unique_included_player_cards'],len(rows));self.assertEqual(len(rows),26);self.assertTrue(q['excluded_cards']);self.assertTrue(all(r['official_source_player_id'] and r['official_profile_url'] for r in rows));self.assertNotIn('Pep Guardiola',[r['name'] for r in rows])
    def test_inter_rendered_and_data_cards_agree(self):
        rows,q=parse_inter(self.source('inter_club'));self.assertEqual(len(rows),24);self.assertTrue(q['every_unique_card_once']);self.assertTrue(all('/player/' in r['official_profile_url'] for r in rows))
    def test_inter_missing_data_card_fails_render_check(self):
        text=self.source('inter_club');match=re.search(r'(<script[^>]*id="__NEXT_DATA__"[^>]*>)(.*?)(</script>)',text,re.S);data=json.loads(match[2]);data['props']['pageProps']['players'].pop();altered=text[:match.start(2)]+json.dumps(data)+text[match.end(2):]
        with self.assertRaisesRegex(ValueError,'differ'):parse_inter(altered)
    def test_city_wrong_team_fails(self):
        text=self.source('manchester_city_club').replace('"category":"mens"','"category":"womens"')
        with self.assertRaisesRegex(ValueError,'mens'):parse_city(text)

class WindowAuditTests(unittest.TestCase):
    def event(self,kickoff,v=10,category='DomesticLeague'):return dict(kickoff=kickoff,minutes=v,category=category)
    def test_midnight_madrid(self):
        self.assertEqual(madrid_date('2026-10-22T22:30:00Z'),'2026-10-23');self.assertEqual(madrid_date('2026-10-26T23:30:00Z'),'2026-10-27')
    def test_spring_dst_calendar_dates(self):
        self.assertEqual(madrid_date('2026-03-28T23:30:00Z'),'2026-03-29');self.assertEqual(madrid_date('2026-03-29T22:30:00Z'),'2026-03-30')
    def test_fall_dst_repeated_hour_same_date(self):
        self.assertEqual(madrid_date('2026-10-25T00:30:00Z'),madrid_date('2026-10-25T01:30:00Z'))
    def test_strict_lower_inclusive_upper(self):
        rows=[self.event('2026-10-16T10:00:00Z',99),self.event('2026-10-17T10:00:00Z',20),self.event('2026-10-23T21:59:00Z',30),self.event('2026-10-23T22:01:00Z',100)]
        self.assertEqual(prefix_recompute(rows,'2026-10-23',7),50)
    def test_cup_is_not_league_or_ucl(self):
        rows=[self.event('2026-10-23T10:00:00Z',90,'DomesticCup'),self.event('2026-10-23T10:00:00Z',20,'UCL')]
        self.assertEqual(prefix_recompute(rows,'2026-10-23',7,'DomesticLeague'),0);self.assertEqual(prefix_recompute(rows,'2026-10-23',7,'UCL'),20);self.assertEqual(prefix_recompute(rows,'2026-10-23',7),110)
    def test_missing_is_excluded_from_known_sum_not_certified(self):
        rows=[self.event('2026-10-23T10:00:00Z',None)];self.assertEqual(prefix_recompute(rows,'2026-10-23',7),0)

class ReverseEvidenceTests(unittest.TestCase):
    def test_exact_id_lookup_retains_full_david_observation(self):
        pool=validated_reverse_pool();self.assertTrue(any(e['name']=='David Santos Daiber' and e['match_id']==4953803 and e['minutes']==90 for e in pool[1771197]))
    def test_unused_and_played_evidence_distinct(self):
        pool=validated_reverse_pool();self.assertTrue(any(e['minutes'] is None and e['role']=='subs' for e in pool[1798782]));self.assertTrue(any(e['minutes'] and e['minutes']>0 for e in pool[1798782]))
    def test_profile_age_and_variant_validation(self):
        self.assertEqual(age_on('2002-09-01','2026-10-22'),24);self.assertEqual(edit_distance('illia','ilya'),2)
