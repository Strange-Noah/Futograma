import json,unittest
from squad_pipeline.live_parse import ParseError,psg_match_squad
class PsgMatchSquad(unittest.TestCase):
 url='https://www.psg.fr/en/matches/mens-football/20242025/published-match'
 def data(self):
  ps=[dict(id='p'+str(i),firstName='Observed',lastName=str(i),position='Defender' if i<11 else 'Substitute',stat=[dict(type='gameStarted',value='1')] if i<11 else []) for i in range(15)]
  return dict(matchId='match-id',lineupPlayers=[dict(name='Unrelated current card')],initialMatchData=dict(id='match-id',matchStartDateTime='$D2024-10-01T18:45:00.000Z',lineupStatus=dict(available=True,show=True),teams=dict(home=dict(id='team-id',Name_official='Paris Saint-Germain',Gender='Male',Local_section='Pros',players=ps),away=dict(id='other-id'))))
 def raw(self,d):return '<script>self.__next_f.push('+json.dumps([1,'1:'+json.dumps(d)+'\n'])+')</script>'
 def parse(self,d,mid='match-id',tid='team-id'):return psg_match_squad(self.raw(d),self.url,mid,tid,9847)
 def test_native_players_not_current_cards_and_no_minutes_invented(self):
  r=self.parse(self.data());p=r['sheets'][0]['players'];self.assertEqual(len(p),15);self.assertEqual(sum(x['role']=='starters' for x in p),11);self.assertIsNone(p[-1]['minutes']);self.assertEqual(p[-1]['role'],'bench');self.assertEqual(p[0]['provider_id_namespace'],'PSG_STATS_PERFORM')
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
  d['lineupPlayers']=['$unresolved-reference',dict(mdmPlayerId='p0',link='$unresolved-reference')];r=self.parse(d);self.assertTrue(r['lineup_available']);self.assertNotIn('documented_alias_bridge',r['sheets'][0]['players'][0])

 def test_direct_minutes_use_shared_policy_for_starter_and_used_bench(self):
  d=self.data();ps=d['initialMatchData']['teams']['home']['players'];ps[0]['stat'].append(dict(type='minsPlayed',value='90'));ps[11]['stat'].append(dict(type='minsPlayed',value='23'))
  players=self.parse(d)['sheets'][0]['players'];self.assertEqual((players[0]['participation_status'],players[0]['minutes']),('STARTER',90));self.assertEqual((players[11]['participation_status'],players[11]['minutes']),('SUBSTITUTE_USED',23));self.assertTrue(players[11]['minute_policy']['accepted']);self.assertIn('.stat[',players[11]['reported_minutes_source_path'])
 def test_missing_or_zero_bench_stat_never_proves_unused(self):
  for value in [None,'0']:
   d=self.data();p=d['initialMatchData']['teams']['home']['players'][11]
   if value is not None:p['stat'].append(dict(type='minsPlayed',value=value))
   out=self.parse(d)['sheets'][0]['players'][11];self.assertEqual(out['participation_status'],'BENCH_PARTICIPATION_UNKNOWN');self.assertIsNone(out['minutes'])
 def test_explicit_substitution_with_missing_minutes_keeps_used_status_unknown_minutes(self):
  d=self.data();d['initialMatchData']['teams']['home']['players'][11]['substitution']=dict(type='in',timeMin=70)
  p=self.parse(d)['sheets'][0]['players'][11];self.assertEqual(p['participation_status'],'SUBSTITUTE_USED');self.assertIsNone(p['minutes'])
 def test_invalid_or_duplicate_minutes_reject_packet(self):
  for stats in [[dict(type='minsPlayed',value='151')],[dict(type='minsPlayed',value=True)],[dict(type='minsPlayed',value='12.5')],[dict(type='minsPlayed',value='90'),dict(type='minsPlayed',value='89')]]:
   d=self.data();d['initialMatchData']['teams']['home']['players'][0]['stat']+=stats
   with self.assertRaises(ParseError):self.parse(d)
 def test_extra_time_reported_minutes_are_not_clamped_to_ninety(self):
  d=self.data();d['initialMatchData']['teams']['home']['players'][0]['stat'].append(dict(type='minsPlayed',value='120'));self.assertEqual(self.parse(d)['sheets'][0]['players'][0]['minutes'],120)
