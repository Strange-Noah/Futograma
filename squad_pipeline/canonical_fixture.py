"""Canonical fixture registry. Provider observations never constitute appearances.

Existing IDs are imported unchanged. Alias assertions require a retained evidence
record, never a similarity threshold. No caller may merge two registered fixtures.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

VERSION = 'CANONICAL_FIXTURES_V1'
DDL = '''
CREATE TABLE IF NOT EXISTS canonical_migrations(version TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS canonical_entities(kind TEXT,entity_id TEXT,name TEXT,PRIMARY KEY(kind,entity_id));
CREATE TABLE IF NOT EXISTS canonical_entity_aliases(kind TEXT,provider TEXT,alias TEXT,entity_id TEXT,evidence_json TEXT NOT NULL,PRIMARY KEY(kind,provider,alias));
CREATE TABLE IF NOT EXISTS canonical_provider_entities(kind TEXT,provider TEXT,provider_id TEXT,entity_id TEXT,evidence_json TEXT NOT NULL,PRIMARY KEY(kind,provider,provider_id));
CREATE TABLE IF NOT EXISTS canonical_fixtures(match_id TEXT PRIMARY KEY,domain TEXT NOT NULL,home_key TEXT NOT NULL,away_key TEXT NOT NULL,competition_key TEXT NOT NULL,edition TEXT,stage TEXT);
CREATE TABLE IF NOT EXISTS canonical_fixture_sources(domain TEXT,provider TEXT,provider_match_id TEXT,match_id TEXT NOT NULL REFERENCES canonical_fixtures(match_id),evidence_json TEXT NOT NULL,PRIMARY KEY(domain,provider,provider_match_id));
CREATE TABLE IF NOT EXISTS canonical_observations(observation_id TEXT PRIMARY KEY,domain TEXT,provider TEXT,provider_match_id TEXT,match_id TEXT,payload_json TEXT,evidence_json TEXT);
CREATE TABLE IF NOT EXISTS canonical_field_claims(match_id TEXT,field TEXT,observation_id TEXT,value_json TEXT,priority INTEGER,PRIMARY KEY(match_id,field,observation_id));
CREATE TABLE IF NOT EXISTS canonical_field_values(match_id TEXT,field TEXT,value_json TEXT,state TEXT,decision_json TEXT,PRIMARY KEY(match_id,field));
CREATE TABLE IF NOT EXISTS canonical_reconciliation_events(event_id TEXT PRIMARY KEY,match_id TEXT,observation_id TEXT,operation TEXT,details_json TEXT);
CREATE TABLE IF NOT EXISTS canonical_source_issues(issue_key TEXT PRIMARY KEY,domain TEXT,match_id TEXT,provider TEXT,reason TEXT,scope TEXT,payload_json TEXT);
CREATE TABLE IF NOT EXISTS canonical_revalidation_events(event_id TEXT PRIMARY KEY,queue TEXT,task_key TEXT,old_class TEXT,new_class TEXT,action TEXT,evidence_json TEXT);
CREATE TABLE IF NOT EXISTS canonical_id_diagnostics(diagnostic_id TEXT PRIMARY KEY,expected_id TEXT,actual_id TEXT,lineup_id TEXT,source_url TEXT,body_sha256 TEXT,details_json TEXT);
'''


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def instant(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('EXPLICIT_KICKOFF_TIMEZONE_REQUIRED')
    return dt.astimezone(timezone.utc).isoformat()


def exists(db, table):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())


def migrate(db):
    # Avoid executescript: it commits an enclosing ingestion transaction.
    for statement in DDL.split(';'):
        if statement.strip():
            db.execute(statement)
    db.execute('INSERT OR IGNORE INTO canonical_migrations VALUES(?)', (VERSION,))


def proof(evidence):
    if not evidence or not (evidence.get('sha256') or evidence.get('snapshot_sha256')):
        raise ValueError('RETAINED_IDENTITY_EVIDENCE_REQUIRED')
    return encoded(evidence)


def entity(db, kind, key, name):
    db.execute('INSERT OR IGNORE INTO canonical_entities VALUES(?,?,?)', (kind, str(key), name))
    return str(key)


def assert_alias(db, kind, provider, alias, entity_id, evidence, provider_id=None):
    evidence_json = proof(evidence)
    if not db.execute('SELECT 1 FROM canonical_entities WHERE kind=? AND entity_id=?', (kind, str(entity_id))).fetchone():
        raise ValueError('CANONICAL_ENTITY_REQUIRED')
    old = db.execute('SELECT entity_id FROM canonical_entity_aliases WHERE kind=? AND provider=? AND alias=?', (kind, provider, alias)).fetchone()
    if old and old[0] != str(entity_id):
        raise ValueError('ALIAS_MAPPING_CONFLICT')
    db.execute('INSERT OR IGNORE INTO canonical_entity_aliases VALUES(?,?,?,?,?)', (kind, provider, alias, str(entity_id), evidence_json))
    if provider_id is not None:
        old = db.execute('SELECT entity_id FROM canonical_provider_entities WHERE kind=? AND provider=? AND provider_id=?', (kind, provider, str(provider_id))).fetchone()
        if old and old[0] != str(entity_id):
            raise ValueError('PROVIDER_ENTITY_MAPPING_CONFLICT')
        db.execute('INSERT OR IGNORE INTO canonical_provider_entities VALUES(?,?,?,?,?)', (kind, provider, str(provider_id), str(entity_id), evidence_json))


def resolve_entity(db, kind, provider, alias=None, provider_id=None):
    if provider_id is not None:
        row = db.execute('SELECT entity_id FROM canonical_provider_entities WHERE kind=? AND provider=? AND provider_id=?', (kind, provider, str(provider_id))).fetchone()
        if row:
            return row[0]
    row = db.execute('SELECT entity_id FROM canonical_entity_aliases WHERE kind=? AND provider=? AND alias=?', (kind, provider, alias)).fetchone()
    return row[0] if row else None


def value(db, mid, field):
    row = db.execute('SELECT value_json,state FROM canonical_field_values WHERE match_id=? AND field=?', (mid, field)).fetchone()
    return (json.loads(row[0]), row[1]) if row else (None, 'UNKNOWN')


def select_fields(db, mid):
    for field, in db.execute('SELECT DISTINCT field FROM canonical_field_claims WHERE match_id=?', (mid,)).fetchall():
        claims = [dict(observation_id=r[0], value=json.loads(r[1]), priority=r[2]) for r in db.execute('SELECT observation_id,value_json,priority FROM canonical_field_claims WHERE match_id=? AND field=? ORDER BY priority DESC,observation_id', (mid, field))]
        distinct = {encoded(r['value']) for r in claims}
        top = claims[0]
        lifecycle={'SCHEDULED':0,'NOT_STARTED':0,'IN_PROGRESS':1,'STARTED':1,'COMPLETED':2,'FINISHED':2}
        monotonic=field=='match_status' and all(r['value'] in lifecycle for r in claims)
        if monotonic:top=sorted(claims,key=lambda r:(-lifecycle[r['value']],-r['priority'],r['observation_id']))[0]
        # A documented same-provider kickoff correction supersedes older values;
        # unrelated conflicting verified values remain visible as conflicts.
        correction=field=='kickoff' and top['priority']>=200 and len({encoded(r['value']) for r in claims if r['priority']==top['priority']})==1
        state = 'VERIFIED' if monotonic or len(distinct) == 1 or correction else 'SOURCE_CONFLICT'
        db.execute('INSERT INTO canonical_field_values VALUES(?,?,?,?,?) ON CONFLICT(match_id,field) DO UPDATE SET value_json=excluded.value_json,state=excluded.state,decision_json=excluded.decision_json', (mid, field, encoded(top['value']), state, encoded(dict(rule=VERSION, selected=top, observations=claims))))


def reconcile(db, observation, create=False, registered_id=None):
    """Shared entry for club, national, initial ingestion and archived replay.

    All ordered team keys and competition keys must already be evidence-resolved.
    No name/date-only or proximity-only matching; an unregistered source can bind
    by exact UTC kickoff plus ordered canonical teams, competition and edition.
    """
    migrate(db)
    o = dict(observation)
    ev = o['evidence']; proof(ev)
    if o.get('actual_provider_match_id') is not None and str(o['actual_provider_match_id']) != str(o['provider_match_id']):
        raise ValueError('RETURNED_MATCH_ID_MISMATCH')
    required = ('domain', 'provider', 'provider_match_id', 'home_key', 'away_key', 'competition_key')
    if any(o.get(k) is None or o.get(k) == '' for k in required):
        raise ValueError('CANONICAL_FIXTURE_IDENTITIES_REQUIRED')
    if o['home_key'] == o['away_key']:
        raise ValueError('DISTINCT_ORDERED_TEAMS_REQUIRED')
    fields = {k: v for k, v in o.get('fields', {}).items() if v is not None}
    if fields.get('kickoff'):
        fields['kickoff'] = instant(fields['kickoff'])
    semantic_evidence={k:ev.get(k) for k in ('sha256','snapshot_sha256','url','correction_basis','correction_revision') if ev.get(k) is not None}
    oid = digest([VERSION,o.get('parser_version'),o.get('retained_accepted',False),o['domain'], o['provider'], str(o['provider_match_id']), o['home_key'], o['away_key'], str(o['competition_key']), o.get('edition'), o.get('stage'), fields, semantic_evidence])
    prior = db.execute('SELECT match_id FROM canonical_fixture_sources WHERE domain=? AND provider=? AND provider_match_id=?', (o['domain'], o['provider'], str(o['provider_match_id']))).fetchone()
    mid = prior[0] if prior else registered_id
    if mid:
        old = db.execute('SELECT domain,home_key,away_key,competition_key,edition,stage FROM canonical_fixtures WHERE match_id=?', (mid,)).fetchone()
        if old:
            if tuple(old[:4]) != (o['domain'], o['home_key'], o['away_key'], str(o['competition_key'])):
                raise ValueError('PHYSICAL_MATCH_IDENTITY_CONFLICT')
            if any(old[i] is not None and o.get(k) is not None and old[i] != o[k] for i, k in [(4, 'edition'), (5, 'stage')]):
                raise ValueError('FIXTURE_EDITION_OR_STAGE_CONFLICT')
    else:
        candidates = []
        for row in db.execute('SELECT match_id,edition,stage FROM canonical_fixtures WHERE domain=? AND home_key=? AND away_key=? AND competition_key=?', (o['domain'], o['home_key'], o['away_key'], str(o['competition_key']))):
            if any(row[i] is not None and o.get(k) is not None and row[i] != o[k] for i, k in [(1, 'edition'), (2, 'stage')]):
                continue
            kick, _ = value(db, row[0], 'kickoff')
            if fields.get('kickoff') and kick == fields['kickoff']:
                candidates.append(row[0])
        if len(candidates) > 1:
            raise ValueError('AMBIGUOUS_CANONICAL_FIXTURE')
        mid = candidates[0] if candidates else None
    is_new = mid is None or not db.execute('SELECT 1 FROM canonical_fixtures WHERE match_id=?', (mid,)).fetchone()
    if is_new:
        if not create and registered_id is None:
            raise ValueError('UNRESOLVED_FIXTURE_OBSERVATION;NO_IMPLICIT_CREATION')
        if not fields.get('kickoff') and registered_id is None:
            raise ValueError('VERIFIED_CREATION_ANCHOR_REQUIRED')
        mid = mid or 'fx_' + uuid.uuid4().hex
        db.execute('INSERT INTO canonical_fixtures VALUES(?,?,?,?,?,?,?)', (mid, o['domain'], o['home_key'], o['away_key'], str(o['competition_key']), o.get('edition'), o.get('stage')))
    if db.execute('SELECT 1 FROM canonical_observations WHERE observation_id=?',(oid,)).fetchone():
        return dict(match_id=mid,observation_id=oid,operation='REDUNDANT',fields={k:value(db,mid,k) for k in fields})
    before = {k: value(db, mid, k) for k in fields}
    db.execute('INSERT OR IGNORE INTO canonical_observations VALUES(?,?,?,?,?,?,?)', (oid, o['domain'], o['provider'], str(o['provider_match_id']), mid, encoded(o), encoded(ev)))
    db.execute('INSERT OR IGNORE INTO canonical_fixture_sources VALUES(?,?,?,?,?)', (o['domain'], o['provider'], str(o['provider_match_id']), mid, encoded(ev)))
    for field, val in fields.items():
        priority = 100 if o.get('retained_accepted') else 50
        if field == 'kickoff' and o.get('verified_kickoff_correction') and prior and ev.get('correction_basis') == 'EXACT_PROVIDER_EVENT_ID_RECONFIRMED':
            revision=ev.get('correction_revision')
            if not isinstance(revision,int) or isinstance(revision,bool) or revision<1:raise ValueError('VERIFIED_PROVIDER_CORRECTION_REVISION_REQUIRED')
            priority = 200+revision
        db.execute('INSERT OR IGNORE INTO canonical_field_claims VALUES(?,?,?,?,?)', (mid, field, oid, encoded(val), priority))
    select_fields(db, mid)
    after = {k: value(db, mid, k) for k in fields}
    operation = 'CREATE_NEW' if is_new else 'SOURCE_CONFLICT' if any(v[1] == 'SOURCE_CONFLICT' for v in after.values()) else 'CORRECT' if any(before[k][0] is not None and before[k][0] != after[k][0] for k in fields) else 'ENRICH' if any(before[k][0] is None and after[k][0] is not None for k in fields) else 'REDUNDANT'
    event_id = digest([oid, operation])
    db.execute('INSERT OR IGNORE INTO canonical_reconciliation_events VALUES(?,?,?,?,?)', (event_id, mid, oid, operation, encoded(dict(before=before, after=after))))
    return dict(match_id=mid, observation_id=oid, operation=operation, fields=after)


def score_fields(score):
    if score is None:
        return {}
    m = re.fullmatch(r'\s*(\d+)\s*[-–:]\s*(\d+)\s*', str(score))
    return dict(home_score=int(m[1]), away_score=int(m[2])) if m else dict(score_text=str(score))


def legacy_team(db, token, known_id, evidence):
    key = 'club:' + str(known_id) if known_id is not None else 'legacy-team:' + digest(token)[:24]
    entity(db, 'TEAM', key, token)
    assert_alias(db, 'TEAM', 'LEGACY_ACCEPTED', token, key, evidence)
    return key


def bootstrap(db, snapshot_sha256='RETAINED_DATABASE'):
    migrate(db)
    ev = dict(snapshot_sha256=snapshot_sha256, basis='RETAINED_ACCEPTED_SNAPSHOT;NO_NEW_IDENTITY_CLAIM')
    if exists(db, 'clubs'):
        for row in db.execute('SELECT club_id,name FROM clubs').fetchall():
            entity(db,'TEAM','club:'+str(row[0]),row[1])
    if exists(db, 'competitions'):
        for cid, name in db.execute('SELECT competition_id,name FROM competitions').fetchall():
            entity(db, 'COMPETITION', str(cid), name)
            assert_alias(db, 'COMPETITION', 'LEGACY_ACCEPTED', name, str(cid), ev)
    if exists(db, 'matches'):
        db.row_factory = __import__('sqlite3').Row
        for r in db.execute('SELECT * FROM matches').fetchall():
            if 'metadata_json' not in r.keys():
                continue
            m = json.loads(r['metadata_json'] or '{}')
            if not m.get('home_team') or not m.get('away_team'):
                continue
            home = legacy_team(db, m['home_team'], r['home_id'], ev)
            away = legacy_team(db, m['away_team'], r['away_id'], ev)
            reconcile(db, dict(domain='CLUB', provider='LEGACY_ACCEPTED', provider_match_id=r['match_id'], home_key=home, away_key=away, competition_key=str(r['competition_id']), edition=club_period(r['local_date'])[1], fields=dict(kickoff=r['kickoff'], match_status=r['match_state'], **score_fields(r['result'])), evidence=ev, retained_accepted=True), registered_id=r['match_id'])
            for source in m.get('source_evidence') or []:
                if not source.get('provider') or not source.get('url'):continue
                pid=source.get('directory_match_id') or source.get('provider_match_id') or source['url']
                db.execute('INSERT OR IGNORE INTO canonical_fixture_sources VALUES(?,?,?,?,?)',('CLUB',source['provider'],str(pid),r['match_id'],encoded(dict(ev,source_evidence=source))))
    if exists(db, 'football_teams'):
        for row in db.execute('SELECT team_key,display_name FROM football_teams').fetchall():
            entity(db,'TEAM',row[0],row[1])
    if exists(db, 'international_matches'):
        for r in db.execute('SELECT * FROM international_matches').fetchall():
            r = dict(r)
            meta=json.loads(r['metadata_json'] or '{}')
            entity(db,'COMPETITION',str(r['competition_key']),meta.get('competition') or str(r['competition_key']))
            reconcile(db, dict(domain='NATIONAL', provider='LEGACY_ACCEPTED', provider_match_id=r['match_id'], home_key=r['home_team_key'], away_key=r['away_team_key'], competition_key=r['competition_key'], fields=dict(kickoff=r['kickoff_utc'], match_status=r['match_state'], duration_minutes=r['duration_minutes'], extra_time_status=r['extra_time_status'], **score_fields(r['score'])), evidence=ev, retained_accepted=True), registered_id=r['match_id'])
        if exists(db, 'international_match_provider_ids'):
            for r in db.execute('SELECT * FROM international_match_provider_ids').fetchall():
                db.execute('INSERT OR IGNORE INTO canonical_fixture_sources VALUES(?,?,?,?,?)', ('NATIONAL', r['provider'], r['provider_match_id'], r['match_id'], encoded(ev)))
    return db.execute('SELECT COUNT(*) FROM canonical_fixtures').fetchone()[0]


def metadata(db, mid):
    if not exists(db, 'canonical_fixtures'):
        return None
    r = db.execute('SELECT * FROM canonical_fixtures WHERE match_id=?', (mid,)).fetchone()
    if not r:
        return None
    r = dict(r);fields = {}
    for field, val, state, decision in db.execute('SELECT field,value_json,state,decision_json FROM canonical_field_values WHERE match_id=?', (mid,)):
        fields[field] = dict(value=json.loads(val), state=state, provenance=json.loads(decision))
    def name(kind, key):
        row = db.execute('SELECT name FROM canonical_entities WHERE kind=? AND entity_id=?', (kind, key)).fetchone()
        return row[0] if row else None
    return dict(match_id=mid, home_team_id=r['home_key'], away_team_id=r['away_key'], home_team=name('TEAM',r['home_key']), away_team=name('TEAM',r['away_key']), competition_id=r['competition_key'], competition=name('COMPETITION',r['competition_key']), fields=fields)


def club_period(day):
    """European club season fallback; explicit provider edition wins in adapters."""
    from datetime import date
    d=date.fromisoformat(day);year=d.year if d.month>=7 else d.year-1
    half='H1' if d.month>=7 else 'H2';season=f'{year}/{str(year+1)[2:]}'
    return (f'{year}-{str(year+1)[2:]}_{half}',season,half,f'{year}-07-01' if half=='H1' else f'{year+1}-01-01',f'{year+1}-01-01' if half=='H1' else f'{year+1}-07-01','Europe/Madrid')
