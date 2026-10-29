import unittest
from squad_pipeline.identity_pipeline import Record,Candidate,variants,similarity,resolve,management_state

def raw(name,provider='uefa',pid='u1',dob='2001-01-28',scope='IDENTITY_ONLY',**kw):
    return Record('https://'+provider+'.example/player/'+pid,'sha-'+provider,'RAW_JSON_PATH',provider,[name],{provider:pid},dob,['club'],scope,observed_on='2026-10-29',authority='OFFICIAL' if provider=='uefa' else 'PUBLIC_PROVIDER',**kw)
class IdentityPipelineTests(unittest.TestCase):
    def test_extended_latin_not_destructively_dropped(self):
        for a,b in [('Ødegaard','Odegaard'),('Pašalić','Pasalic'),('Kolašinac','Kolasinac'),('Łukasz','Lukasz'),('Ægir','Aegir'),('Œzil','Oezil'),('Groß','Gross'),('O’Neill','O Neill')]:self.assertEqual(similarity(a,b),1.0)
        self.assertEqual(variants('Ødegaard')['original'],'Ødegaard')
    def test_similarity_alone_never_authorizes_merge(self):
        for n in ['Gabriel','Benjamin White','Noni Madueke','Ismael Saibari Ben El Basra']:
            r=raw(n,dob=None);c=Candidate(1,[n]);self.assertEqual(resolve(r,[c])['identity_status'],'UNRESOLVED')
    def test_independent_dob_official_affiliation_verifies_without_shared_ids(self):
        c=Candidate(1180288,['Ismael Saibari'],[raw('Ismael Saibari','fotmob','1180288')]);r=raw('Ismael Saibari Ben El Basra');self.assertEqual(resolve(r,[c])['player_id'],'1180288')
    def test_no_global_nickname_expansion(self):
        self.assertLess(similarity('Noni Madueke','Chukwunonso Azuka Tristan Madueke'),.82)
        r=raw('Gabriel',dob=None);self.assertEqual(resolve(r,[Candidate(1,['Gabriel Jesus']),Candidate(2,['Gabriel'])])['identity_status'],'UNRESOLVED')
    def test_documented_common_and_legal_names_share_raw_identity(self):
        c=Candidate(1,['Noni Madueke'],[raw('Noni Madueke','fotmob','1')]);r=raw('Noni Madueke');r.names.append('Chukwunonso Azuka Tristan Madueke');self.assertEqual(resolve(r,[c])['identity_status'],'VERIFIED')
    def test_birthday_conflict_blocks_stable_id(self):
        c=Candidate(1,['Person'],[raw('Person')]);self.assertEqual(resolve(raw('Person',dob='2002-01-01'),[c])['identity_status'],'UNRESOLVED')
    def test_same_domain_not_independent(self):
        c=Candidate(1,['Person'],[raw('Person')]);r=raw('Person',pid='different');self.assertEqual(resolve(r,[c])['identity_status'],'UNRESOLVED')
    def test_retrieval_failure_retains_verified_membership(self):
        prior=dict(state='INCLUDED',included=True,status='FIRST_TEAM');r=raw('Person');r.retrieved=False;r.source_status='SOURCE_POLICY_RESTRICTED';self.assertTrue(management_state([r],'2026-10-29',prior)['included'])
    def test_u21_and_list_b_not_first_team_management(self):
        for scope in ['U21_REGISTRATION','UEFA_LIST_B','ACADEMY_AFFILIATION']:
            r=raw('Youth',scope=scope);d=management_state([r],'2026-10-29');self.assertFalse(d['included']);self.assertTrue(d['academy_affiliation'])
    def test_u21_first_team_card_is_managed(self):
        d=management_state([raw('Young',scope='U21_REGISTRATION'),raw('Young',scope='FIRST_TEAM_CARD')],'2026-10-29');self.assertTrue(d['included'])
    def test_departure_overrides_stale_card_for_any_player(self):
        for status in ['LOAN_OUT','TRANSFERRED_OUT']:
            out=raw('Any Person',assertion=status,effective_from='2026-08-27');card=raw('Any Person',scope='FIRST_TEAM_CARD');self.assertEqual(management_state([out,card],'2026-10-29')['state'],'EXCLUDED')
    def test_loan_expiry_requires_actual_return(self):
        out=raw('Any',assertion='LOAN_OUT',effective_from='2026-08-01',end_exclusive='2026-09-01');self.assertEqual(management_state([out],'2026-10-29')['status'],'RETURN_UNVERIFIED')
    def test_structured_escalation_keeps_candidates_and_requirements(self):
        result=resolve(raw('Candidate',dob=None),[Candidate(1,['Candidate'])]);self.assertTrue(result['candidates']);self.assertTrue(result['remaining_requirements']);self.assertIn('source_record',result)
