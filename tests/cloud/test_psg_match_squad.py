import json,unittest
from squad_pipeline.live_parse import psg_match_squad
class PsgMatchSquad(unittest.TestCase):
 url='https://www.psg.fr/en/matches/mens-football/20242025/published-match'
 def data(self):
  ps=[dict(id='p'+str(i),firstName='Observed',lastName=str(i),position='Defender' if i<11 else 'Substitute',stat=[dict(type='gameStarted',value='1')] if i<11 else []) for i in range(15)]
  return dict(matchId='match-id',lineupPlayers=[dict(name='Unrelated current card')],initialMatchData=dict(id='match-id',matchStartDateTime='$D2024-10-01T18:45:00.000Z',lineupStatus=dict(available=True,show=True),teams=dict(home=dict(id='team-id',Name_official='Paris Saint-Germain',Gender='Male',Local_section='Pros',players=ps),away=dict(id='other-id'))))
 def raw(self,d):return '<script>self.__next_f.push('+json.dumps([1,'1:'+json.dumps(d)+'\n'])+')</script>'
 def parse(self,d,mid='match-id',tid='team-id'):return psg_match_squad(self.raw(d),self.url,mid,tid,9847)
 def test_native_players_not_current_cards_and_no_minutes_invented(self):
  r=self.parse(self.data());p=r['sheets'][0]['players'];self.assertEqual(len(p),15);self.assertEqual(sum(x['role']=='starters' for x in p),11);self.assertNotIn('minutes',p[-1]);self.assertEqual(p[-1]['role'],'bench');self.assertEqual(p[0]['provider_id_namespace'],'PSG_STATS_PERFORM')
 def test_unavailable_lineup_cannot_use_related_cards(self):
  d=self.data();d['initialMatchData']['lineupStatus']['available']=False;d['initialMatchData']['id']='';r=self.parse(d);self.assertFalse(r['lineup_available']);self.assertEqual(r['sheets'],[])
 def test_requested_returned_match_and_team_must_agree(self):
  for mid,tid in [('wrong','team-id'),('match-id','wrong'),(None,'team-id')]:
   with self.assertRaises(ValueError):self.parse(self.data(),mid,tid)
 def test_scope_and_duplicate_player_are_held(self):
  for field,value in [('Gender','Female'),('Local_section','Academy'),('Name_official','Other team')]:
   d=self.data();d['initialMatchData']['teams']['home'][field]=value
   with self.assertRaises(ValueError):self.parse(d)
  d=self.data();d['initialMatchData']['teams']['home']['players'][-1]['id']='p0'
  with self.assertRaises(ValueError):self.parse(d)
 def test_role_conflict_or_missing_starter_is_held(self):
  for mode in ['conflict','missing']:
   d=self.data();ps=d['initialMatchData']['teams']['home']['players']
   if mode=='conflict':ps[-1]['stat']=[dict(type='gameStarted',value='1')]
   else:ps[0]['stat']=[]
   with self.assertRaises(ValueError):self.parse(d)
 def test_legal_common_name_bridge_requires_exact_observed_native_id(self):
  d=self.data();d['initialMatchData']['teams']['home']['players'][0].update(firstName='Observed Middle',lastName='Player');d['lineupPlayers']=[dict(mdmPlayerId='p0',link=dict(id='cms-id',contentType='playerPage',name='Observed Player'))]
  p=self.parse(d)['sheets'][0]['players'][0];self.assertEqual(p['documented_alias_bridge']['common_name'],'Observed Player')
  d['lineupPlayers'][0]['mdmPlayerId']='unrelated-current-player';p=self.parse(d)['sheets'][0]['players'][0];self.assertNotIn('documented_alias_bridge',p)
