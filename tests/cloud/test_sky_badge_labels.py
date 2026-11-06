import unittest
from squad_pipeline.live_parse import Tree,certified_badge_sides

class BadgeLabelBindingTests(unittest.TestCase):
 def proof(self,home='Swansea',away='Man City',alts=('Swansea City','Manchester City'),ids=('375','345')):
  raw=''.join('<div class="sdc-site-team-lineup__col"><h3 class="sdc-site-team-lineup__team-name">'+name+'</h3><img class="sdc-site-team-lineup__team-badge-image" alt="'+alt+'" src="https://e0.365dm.com/football/badges/96/'+key+'.png"></div>' for name,alt,key in zip((home,away),alts,ids))
  return Tree(raw).root.all('sdc-site-team-lineup__col'),dict(home_team='swanseacity',away_team='manchestercity'),dict(url='https://www.skysports.com/football/a-vs-b/teams/7',sha256='a'*64)
 def test_both_ordered_captions_certify_shortened_labels(self):
  self.assertTrue(certified_badge_sides(*self.proof()))
 def test_swapped_or_wrong_caption_cannot_bind(self):
  for alts in [('Manchester City','Swansea City'),('Swansea Town','Manchester City')]:self.assertFalse(certified_badge_sides(*self.proof(alts=alts)))
 def test_duplicate_badges_or_unrelated_heading_rejected(self):
  self.assertFalse(certified_badge_sides(*self.proof(ids=('375','375'))))
  self.assertFalse(certified_badge_sides(*self.proof(home='Cardiff')))
 def test_missing_hash_other_origin_or_missing_column_rejected(self):
  cols,f,r=self.proof();r['sha256']='';self.assertFalse(certified_badge_sides(cols,f,r))
  r['sha256']='a'*64;r['url']='https://not-skysports.com/a';self.assertFalse(certified_badge_sides(cols,f,r))
  self.assertFalse(certified_badge_sides(cols[:1],f,r))
 def test_badge_query_or_duplicate_caption_rejected(self):
  cols,f,r=self.proof();badge=cols[0].all('sdc-site-team-lineup__team-badge-image')[0];badge.attrs['src']+='?id=1';self.assertFalse(certified_badge_sides(cols,f,r))
  cols,f,r=self.proof();cols[0].children.append(cols[0].all('sdc-site-team-lineup__team-badge-image')[0]);self.assertFalse(certified_badge_sides(cols,f,r))

 def test_initial_plus_full_word_is_bound_but_wrong_initial_is_not(self):
  cols,f,r=self.proof(home='S City');self.assertTrue(certified_badge_sides(cols,f,r))
  cols,f,r=self.proof(home='C City');self.assertFalse(certified_badge_sides(cols,f,r))
