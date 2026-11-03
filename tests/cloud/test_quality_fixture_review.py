import copy
import hashlib
import json
import unittest
from squad_pipeline.quality_fixture_review import review_completed


def html(data):
    return '<script id="__NEXT_DATA__">' + json.dumps({'props': {'pageProps': {'data': data}}}) + '</script>'

class IndependentFixtureReviewTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(id=10, home=dict(id=1, name='A', score=2), away=dict(id=2, name='B', score=1), tournament=dict(leagueId=3), status=dict(finished=True, utcTime='2026-10-01T19:00:00Z', scoreStr='2 - 1'))
        self.data = dict(general=dict(matchId=10, homeTeam=dict(id=1), awayTeam=dict(id=2), finished=True, parentLeagueId=3, matchTimeUTCDate='2026-10-01T19:00:00Z'), header=dict(teams=[dict(id=1, score=2), dict(id=2, score=1)], status=copy.deepcopy(self.row['status'])), content=dict(lineup=dict(matchId=10, homeTeam=dict(id=1), awayTeam=dict(id=2))))
        self.export = dict(provider_match_id='10', actual_provider_match_id='10', home=dict(id='canonical:A'), away=dict(id='canonical:B'), competition_key='3', status='FINISHED', kickoff_utc='2026-10-01T19:00:00Z', home_score=2, away_score=1, source_provenance=dict(field_evidence={s:dict(value=dict(id=k)) for s,k in [('home','canonical:A'),('away','canonical:B')]}))
    def run_review(self, **kw):
        raw=html(self.data);source=dict(url='https://example.org/detail',sha256=hashlib.sha256(raw.encode()).hexdigest());source.update(kw.pop('source_changes',{}))
        for side in ('home','away'):
            proof=self.export['source_provenance']['field_evidence'][side]
            proof.setdefault('sha256',source['sha256']);proof.setdefault('state','VERIFIED')
        return review_completed(raw,source,self.row,self.export,'fingerprint',**kw)
    def test_independent_fixture_only_review(self):
        r=self.run_review(expected_home_key='canonical:A',expected_away_key='canonical:B')
        self.assertTrue(r['independent']);self.assertIn('minutes',r['excludes'])
    def test_wrong_body_hash(self):
        with self.assertRaisesRegex(ValueError,'BODY_HASH'):self.run_review(source_changes=dict(sha256='a'*64))
    def test_lineup_id_and_team_orientation(self):
        self.data['content']['lineup']['matchId']=99
        with self.assertRaisesRegex(ValueError,'MATCH_OR_LINEUP'):self.run_review()
    def test_swapped_export_canonical_side(self):
        self.export['home']['id']='canonical:B'
        with self.assertRaisesRegex(ValueError,'CANONICAL_SIDE'):self.run_review()
    def test_missing_score_rejected(self):
        self.export['home_score']=None
        with self.assertRaisesRegex(ValueError,'NUMERIC_SCORE'):self.run_review()
    def test_future_match_rejected(self):
        self.data['general']['matchTimeUTCDate']='2099-10-01T19:00:00Z'
        self.data['header']['status']['utcTime']=self.row['status']['utcTime']=self.export['kickoff_utc']='2099-10-01T19:00:00Z'
        with self.assertRaisesRegex(ValueError,'FUTURE'):self.run_review()
    def test_finished_flags_required(self):
        self.row['status']['finished']=False
        with self.assertRaisesRegex(ValueError,'COMPLETED_ONLY'):self.run_review()
    def test_directory_original_body_and_membership(self):
        raw=html(dict(fixtures=[self.row]));source=dict(url='https://example.org/fixtures',raw=raw,sha256=hashlib.sha256(raw.encode()).hexdigest())
        self.assertTrue(self.run_review(directory_source=source)['directory_body_rehashed'])
        raw=html(dict(fixtures=[]));source.update(raw=raw,sha256=hashlib.sha256(raw.encode()).hexdigest())
        with self.assertRaisesRegex(ValueError,'ROW_NOT_IN'):self.run_review(directory_source=source)
    def test_competition_must_agree(self):
        self.export['competition_key']='another'
        with self.assertRaisesRegex(ValueError,'COMPETITION'):self.run_review()

    def test_unbound_side_provenance_rejected(self):
        self.export['source_provenance']['field_evidence']['home']['sha256']='b'*64
        with self.assertRaisesRegex(ValueError,'PROVENANCE'):self.run_review()
    def test_boolean_score_not_a_numeric_zero(self):
        self.row['home']['score']=False
        with self.assertRaisesRegex(ValueError,'NUMERIC_SCORE'):self.run_review()
