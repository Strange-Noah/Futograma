"""Source-evidenced identity verification; similarity never authorizes a merge.
Pure functions are shared by live acquisition and replay. No player-specific rules.
"""
from dataclasses import dataclass,field,asdict
from difflib import SequenceMatcher
from functools import lru_cache
from urllib.parse import urlsplit
import unicodedata,re,hashlib,json
TRANSLITERATION=str.maketrans({'ø':'o','ł':'l','đ':'d','ð':'d','þ':'th','æ':'ae','œ':'oe','ı':'i','ħ':'h','ŋ':'n','ŧ':'t','ə':'e'})

@lru_cache(maxsize=20000)
def variants(name):
    original=str(name or '');unicode=unicodedata.normalize('NFKC',original).casefold()
    folded=''.join(c for c in unicodedata.normalize('NFKD',unicode) if not unicodedata.combining(c))
    forms={}
    for method,value in [('UNICODE_CASEFOLD',unicode),('DIACRITIC_FOLD',folded),('LATIN_TRANSLITERATION',folded.translate(TRANSLITERATION))]:
        tokens=re.findall(r"[^\W_]+",value,flags=re.UNICODE)
        if tokens:
            forms[method]=' '.join(tokens)
            forms[method+'_TOKEN_ORDER']=' '.join(sorted(tokens))
            forms[method+'_JOINED']=''.join(tokens)
    return {'original':original,'forms':forms}

@lru_cache(maxsize=250000)
def similarity(left,right):
    a=variants(left)['forms'];b=variants(right)['forms']
    if not a or not b:return 0.0
    score=max(SequenceMatcher(None,x,y).ratio() for x in set(a.values()) for y in set(b.values()))
    at=set(a.get('LATIN_TRANSLITERATION','').split());bt=set(b.get('LATIN_TRANSLITERATION','').split())
    # Compound/initial variants generate candidates only; the verifier requires independent evidence.
    if min(len(at),len(bt))>=2 and (at<=bt or bt<=at):score=max(score,.9)
    return round(score,4)

@dataclass
class Record:
    source_url:str
    source_sha256:str
    locator:str
    provider:str
    names:list
    provider_ids:dict=field(default_factory=dict)
    birth_date:str|None=None
    official_clubs:list=field(default_factory=list)
    scope:str='IDENTITY_ONLY'
    observed_on:str|None=None
    effective_from:str|None=None
    end_exclusive:str|None=None
    date_precision:str='OBSERVED_SNAPSHOT'
    assertion:str|None=None
    retrieved:bool=True
    authority:str='PUBLIC_PROVIDER'
    source_status:str='SUCCESS'
    raw_fields:dict=field(default_factory=dict)
    def evidence(self):return asdict(self)

@dataclass
class Candidate:
    player_id:int
    names:list
    records:list=field(default_factory=list)
    def ids(self):
        result={}
        for r in self.records:
            if r.raw_fields.get('_candidate_only'):continue
            for provider,value in r.provider_ids.items():result.setdefault(provider,set()).add(str(value))
        return result
    def dobs(self):return {r.birth_date for r in self.records if r.birth_date}


def ranked_candidates(record,candidates,limit=8):
    ranked=[]
    for c in candidates:
        score=max((similarity(a,b) for a in record.names for b in c.names),default=0)
        shared={p:str(v) for p,v in record.provider_ids.items() if str(v) in c.ids().get(p,set())}
        if shared:score=1.0
        if score>=.4:ranked.append({'player_id':str(c.player_id),'score':score,'shared_stable_ids':shared,'variants':[variants(n) for n in c.names]})
    return sorted(ranked,key=lambda r:(-r['score'],r['player_id']))[:limit]

def verify_identity(record,candidate):
    reasons=[];proof=[]
    if not record.retrieved or record.source_status!='SUCCESS' or not record.source_sha256:return {'verified':False,'reasons':['RAW_SOURCE_UNAVAILABLE_OR_UNHASHED'],'proof':[]}
    ids=candidate.ids();dobs=candidate.dobs()
    # A stable-ID collision must not be papered over by name or birthday similarity.
    for provider,value in record.provider_ids.items():
        if provider in ids and str(value) not in ids[provider]:reasons.append('PROVIDER_ID_CONFLICT:'+provider)
    if record.birth_date and dobs and record.birth_date not in dobs:reasons.append('DATE_OF_BIRTH_CONFLICT')
    if reasons:return {'verified':False,'reasons':reasons,'proof':[]}
    shared={p:str(v) for p,v in record.provider_ids.items() if str(v) in ids.get(p,set())}
    if shared:
        score=max((similarity(a,b) for a in record.names for b in candidate.names),default=0)
        if score<.6:return {'verified':False,'reasons':['STABLE_ID_PERSON_NAME_CONFLICT'],'proof':[]}
        proof.append({'method':'SHARED_STABLE_PROVIDER_ID','ids':shared})
    # Both endpoints must be actually acquired. A seed/display name is never cross-source evidence.
    if record.birth_date and record.birth_date in dobs:
        for other in candidate.records:
            if not other.retrieved or not other.source_sha256 or other.source_status!='SUCCESS':continue
            if urlsplit(other.source_url).netloc==urlsplit(record.source_url).netloc:continue
            if other.birth_date!=record.birth_date:continue
            shared_clubs=set(map(str,record.official_clubs))&set(map(str,other.official_clubs))
            name_score=max((similarity(a,b) for a in record.names for b in other.names),default=0)
            if shared_clubs and name_score>=.82 and ('OFFICIAL' in [record.authority,other.authority]):proof.append({'method':'INDEPENDENT_DOB_NAME_OFFICIAL_AFFILIATION','other_url':other.source_url,'other_sha256':other.source_sha256,'date_of_birth':record.birth_date,'clubs':sorted(shared_clubs),'name_score':name_score})
    bridge=record.raw_fields.get('documented_alias_bridge')
    if bridge and record.authority=='OFFICIAL' and len(bridge.get('common_name','').split())>=2:
        for other in candidate.records:
            if not other.raw_fields.get('_verified_identity'):continue
            if bridge['common_name'] not in other.names:continue
            career_club=record.raw_fields.get('career_club')
            if career_club and career_club not in other.raw_fields.get('career_clubs',[]):continue
            proof.append({'method':'DOCUMENTED_OFFICIAL_SAME_PERSON_NAME_BRIDGE','bridge':bridge,'other_url':other.source_url,'other_sha256':other.source_sha256})
    career=record.raw_fields.get('career_bridge')
    if career and record.authority=='OFFICIAL':
        anchors=[r for r in candidate.records if r.raw_fields.get('_verified_identity') and r.source_url==career.get('profile_url') and r.source_sha256==career.get('profile_sha256') and r.provider_ids.get('FotMob')==career.get('fotmob_id')]
        if anchors and any(n in anchors[0].names and len(n.split())>=2 for n in record.names):proof.append({'method':'OFFICIAL_COMPLETED_ANNOUNCEMENT_AND_STABLE_CAREER_IDENTITY','career_bridge':career,'announcement':record.raw_fields.get('announcement')})
    if record.authority=='OFFICIAL' and record.scope in ('FIRST_TEAM_CARD','SENIOR_REGISTRATION','U21_REGISTRATION','ACADEMY_AFFILIATION'):
        for other in candidate.records:
            if not other.raw_fields.get('_verified_identity'):continue
            if not set(record.official_clubs)&set(other.official_clubs):continue
            exact=any(len(a.split())>=2 and similarity(a,b)==1 for a in record.names for b in other.names)
            if exact:proof.append({'method':'EXACT_PREVIOUSLY_VERIFIED_FULL_NAME_AND_OFFICIAL_CLUB_SCOPE','other_url':other.source_url,'other_sha256':other.source_sha256})
    return {'verified':bool(proof),'reasons':[] if proof else ['SIMILARITY_IS_NOT_IDENTITY_PROOF','NEED_STABLE_ID_OR_INDEPENDENT_DOB_AND_OFFICIAL_AFFILIATION'],'proof':proof}


def resolve(record,candidates):
    ranked=ranked_candidates(record,candidates);byid={str(c.player_id):c for c in candidates};checked=[]
    for rank in ranked:
        checked.append(dict(rank,verification=verify_identity(record,byid[rank['player_id']])))
    accepted=[c for c in checked if c['verification']['verified']]
    if len(accepted)==1:
        hit=accepted[0];return {'identity_status':'VERIFIED','player_id':hit['player_id'],'candidate_similarity':hit['score'],'identity_proof':hit['verification']['proof'],'source_record':record.evidence(),'candidates':checked,'remaining_requirements':[]}
    return {'identity_status':'CONFLICT' if len(accepted)>1 else 'UNRESOLVED','player_id':None,'source_record':record.evidence(),'candidates':checked,'rejection_reasons':['MULTIPLE_VERIFIED_IDENTITIES'] if len(accepted)>1 else ['NO_INDEPENDENTLY_VERIFIED_CANDIDATE'],'remaining_requirements':['Acquire stable provider linkage or official biographical bridge; do not merge on fuzzy name/jersey'],'roster_impact':'No new management membership authorized; retain prior verified intervals'}


def management_state(records,on,prior=None):
    good=[r for r in records if r.retrieved and r.source_status=='SUCCESS' and r.authority=='OFFICIAL' and (r.effective_from or r.observed_on or '9999')<=on]
    affiliation=[r.evidence() for r in good if r.scope in ('ACADEMY_AFFILIATION','U21_REGISTRATION','UEFA_LIST_B')]
    registration=[r.evidence() for r in good if r.scope in ('SENIOR_REGISTRATION','U21_REGISTRATION','UEFA_LIST_B','UEFA_SENIOR')]
    positive=[r for r in good if r.scope in ('FIRST_TEAM_CARD','SENIOR_REGISTRATION','UEFA_SENIOR','EXPLICIT_MANAGED_DEVELOPMENT','LOAN_IN','EXPLICIT_RETURN','EXPLICIT_SIGNING') and (not r.end_exclusive or on<r.end_exclusive)]
    negative=[r for r in good if r.assertion in ('LOAN_OUT','TRANSFERRED_OUT','MANAGEMENT_ENDED')]
    last=max(negative,key=lambda r:(r.observed_on or '',r.effective_from or ''),default=None)
    if last:
        returned=[r for r in positive if r.scope in ('EXPLICIT_RETURN','EXPLICIT_SIGNING') and r.effective_from and r.effective_from>(last.effective_from or last.observed_on)]
        if not returned:return {'state':'UNKNOWN' if last.end_exclusive and on>=last.end_exclusive else 'EXCLUDED','included':False,'status':'RETURN_UNVERIFIED' if last.end_exclusive and on>=last.end_exclusive else last.assertion,'evidence':[last.evidence()],'academy_affiliation':affiliation,'registration':registration,'date_precision':last.date_precision,'effective_from':last.effective_from}
        positive=returned
    if positive:
        current=max(positive,key=lambda r:(r.observed_on or '',r.effective_from or ''))
        return {'state':'INCLUDED','included':True,'status':'LOAN_IN' if current.scope=='LOAN_IN' else 'CLUB_MANAGED_DEVELOPMENT' if current.scope=='EXPLICIT_MANAGED_DEVELOPMENT' else 'FIRST_TEAM','evidence':[r.evidence() for r in positive],'academy_affiliation':affiliation,'registration':registration,'effective_from':current.effective_from,'date_precision':current.date_precision}
    if prior and prior.get('state')=='INCLUDED' and (not prior.get('end_exclusive') or on<prior['end_exclusive']):
        return dict(prior,source_health='DEGRADED',retention_reason='Source failure or absence is not departure evidence',academy_affiliation=affiliation,registration=registration)
    return {'state':'UNKNOWN','included':False,'status':'MANAGEMENT_SCOPE_UNVERIFIED','evidence':[],'academy_affiliation':affiliation,'registration':registration,'remaining_requirements':['Current first-team or explicit development management proof; academy/U21/List B alone is insufficient']}

DDL="""CREATE TABLE IF NOT EXISTS verified_identity_links(provider TEXT,provider_player_id TEXT,player_id INTEGER,verified_at TEXT,evidence_json TEXT,PRIMARY KEY(provider,provider_player_id));
CREATE TABLE IF NOT EXISTS verified_name_aliases(alias_hash TEXT PRIMARY KEY,player_id INTEGER,source_name TEXT,variants_json TEXT,evidence_json TEXT);
CREATE TABLE IF NOT EXISTS roster_verification_dossiers(dossier_id TEXT PRIMARY KEY,club_id INTEGER,player_id INTEGER,observed_at TEXT,identity_status TEXT,affiliation_status TEXT,registration_status TEXT,management_status TEXT,payload_json TEXT);"""

def persist_verified_alias(db,record,result,on):
    if result['identity_status']!='VERIFIED' or not result.get('identity_proof'):raise ValueError('UNVERIFIED_ALIAS_REJECTED')
    pid=int(result['player_id']);compact=dict(result)
    # Runtime facts/history are not duplicated in every permanent alias certificate.
    source=dict(compact.get('source_record',{}));raw=source.pop('raw_fields',{});source['documented_alias_bridge']=raw.get('documented_alias_bridge');compact['source_record']=source;compact.pop('candidates',None)
    evidence=json.dumps(compact,ensure_ascii=False,sort_keys=True)
    for provider,value in record.provider_ids.items():
        old=db.execute('SELECT player_id FROM verified_identity_links WHERE provider=? AND provider_player_id=?',(provider,str(value))).fetchone()
        if old and old[0]!=pid:raise ValueError('STABLE_PROVIDER_ID_COLLISION')
        db.execute('INSERT OR IGNORE INTO verified_identity_links VALUES(?,?,?,?,?)',(provider,str(value),pid,on,evidence))
    for name in record.names:
        key=hashlib.sha256(json.dumps([pid,name,record.source_sha256],ensure_ascii=False).encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO verified_name_aliases VALUES(?,?,?,?,?)',(key,pid,name,json.dumps(variants(name),ensure_ascii=False),evidence))
