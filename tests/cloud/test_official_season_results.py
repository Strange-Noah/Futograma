import unittest,json
from squad_pipeline.live_parse import mancity_catalogue,ParseError,Pending
class OfficialSeasonResultsTests(unittest.TestCase):
 url='https://www.mancity.com/results/mens/season-2024-25'
 def body(self,year='2024',selected=None,score='1-0',visible='1',label='Manchester City versus Example',shown='2024-09-01T17:00:00+00:00'):
  action={'@type':'WinAction','object':{'@type':'SportsEvent','name':'Manchester City vs Example','startDate':'2024-09-01T15:00:00&#x2B;00:00'},'result':score}
  return '<div data-season-id="'+year+'"></div><select><option selected value="'+(selected or self.url.split('.com')[1])+'">2024-25</option></select><script type="application/ld+json">'+json.dumps(action)+'</script><div class="fixtures-list-item" aria-label="'+label+'"><time datetime="'+shown+'"></time><span class="fixture-body__score-goals">'+visible+'</span><span class="fixture-body__score-goals">0</span><a href="/news/mens/example-report">Report</a></div>'
 def test_kickoff_conflict_and_unknown_competition_are_preserved(self):
  row=mancity_catalogue(self.body(),self.url,8456)[0];self.assertTrue(row['datetime_conflict']);self.assertIsNone(row['competition_label']);self.assertFalse(row['canonical_adjudication']);self.assertEqual(row['source_season'],'2024/25')
 def test_visible_date_conflict_is_retained_not_silently_corrected(self):
  row=mancity_catalogue(self.body(shown='2024-09-02T01:00:00+00:00'),self.url,8456)[0];self.assertTrue(row['date_conflict'])
 def test_wrong_season_selection_or_club_rejected(self):
  for raw,cid in [(self.body(year='2025'),8456),(self.body(selected='/results/mens'),8456),(self.body(),1)]:
   with self.assertRaises(ParseError):mancity_catalogue(raw,self.url,cid)
 def test_ordered_team_and_score_disagreement_rejected(self):
  for raw in [self.body(label='Example versus Manchester City'),self.body(visible='2')]:
   with self.assertRaises(ParseError):mancity_catalogue(raw,self.url,8456)
 def test_missing_visible_fixture_cannot_certify_directory(self):
  with self.assertRaises(Pending):mancity_catalogue(self.body().replace('fixtures-list-item','other'),self.url,8456)
 def test_source_service_dispatch_is_native_not_canonical(self):
  from squad_pipeline.source_interface import SourceRequest,RawSourceArtifact,sha
  from squad_pipeline.source_parsing import parse
  body=self.body().encode();req=SourceRequest('Mancity','fixture_catalogue',self.url,'season',context={'club_id':8456});a=RawSourceArtifact(req,sha(body),'relative',{'http_status':200});obs=parse(a,body)
  self.assertEqual(obs.outcome,'PARSED');self.assertFalse(obs.completeness['season_complete']);self.assertTrue(obs.native['fixtures'][0]['datetime_conflict'])

 def test_html_encoded_json_team_name_matches_decoded_visible_caption(self):
  body=self.body(label='Manchester City versus Bodø / Glimt').replace('Manchester City vs Example','Manchester City vs Bod&#xF8; / Glimt')
  row=mancity_catalogue(body,self.url,8456)[0];self.assertEqual(row['away_name'],'Bodø / Glimt');self.assertIn('&#xF8;',row['source_event_name'])
