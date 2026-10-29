"""Atomic SQLite rebuild plus reproducible CSV exports, with relational checks."""
import sqlite3,json
from .common import safe,write_csv,write_json,checksum
KEYS={'players':['player_id'],'teams':['team_id'],'registrations':['registration_id'],'matches':['match_id'],
      'player_appearances':['player_id','match_id','team_id'],'player_availability':['player_id','match_id','team_id'],
      'daily_load':['player_id','date'],'prematch_load':['player_id','match_id','team_id'],'player_summary':['player_id','period']}

def validate(tables,config):
    failures=[];players={p['player_id'] for p in tables['players']};matches={m['match_id'] for m in tables['matches']};teams={t['team_id'] for t in tables['teams']}
    for name,rows in tables.items():
        if name in KEYS:
            ids=[tuple(r[k] for k in KEYS[name]) for r in rows]
            if len(ids)!=len(set(ids)):failures.append(name+': duplicate primary key')
        for r in rows:
            if 'player_id' in r and r['player_id'] not in players:failures.append(name+': missing player')
            if 'match_id' in r and r['match_id'] not in matches:failures.append(name+': missing match')
            for k in ('team_id','home_team_id','away_team_id'):
                if r.get(k) and r[k] not in teams:failures.append(name+': missing team')
    for name,prefix,clubprefix in [('daily_load','load_','club_load_'),('prematch_load','pre_match_','club_pre_match_')]:
        for r in tables[name]:
            for w in config['windows_days']:
                key=f'{prefix}{w}d';total=r[key]
                if total is not None:
                    parts=[r[f'{key}_{k}'] for k in ('Domestic','UCL','International','Other')]
                    if any(v is None for v in parts) or abs(total-sum(parts))>1e-8:failures.append(name+': category reconciliation failed')
                    if abs(total-r[f'{clubprefix}{w}d']-r[f'{key}_International'])>1e-8:failures.append(name+': club/national reconciliation failed')
    expected=(__import__('datetime').date.fromisoformat(config['research']['as_of'])-__import__('datetime').date.fromisoformat(config['research']['start'])).days+1
    if len(tables['daily_load'])!=len(players)*expected:failures.append('daily calendar gaps')
    return {'passed':not failures,'failures':list(dict.fromkeys(failures)),'rows':{n:len(r) for n,r in tables.items()}}

def export(tables,out,database,metadata):
    out=safe(out);out.mkdir(parents=True,exist_ok=True);database=safe(database);database.parent.mkdir(parents=True,exist_ok=True)
    temp=database.with_suffix('.sqlite.tmp')
    if temp.exists():temp.unlink()
    con=sqlite3.connect(temp);con.execute('PRAGMA foreign_keys=ON')
    for name,rows in tables.items():
        fields=list(dict.fromkeys(k for r in rows for k in r))
        write_csv(out/f'{name}.csv',rows,fields or ['no_records'])
        if not fields:continue
        types={k:next((type(r[k]) for r in rows if r.get(k) is not None),str) for k in fields}
        sqltype=lambda k:'INTEGER' if types[k] in (int,bool) else 'REAL' if types[k] is float else 'TEXT'
        defs=[f'"{k}" {sqltype(k)}' for k in fields]
        if name in KEYS:defs.append('PRIMARY KEY ('+','.join(f'"{k}"' for k in KEYS[name])+')')
        for key,ref in [('player_id','players'),('match_id','matches'),('team_id','teams'),('home_team_id','teams'),('away_team_id','teams')]:
            if key in fields and name!=ref:defs.append(f'FOREIGN KEY ("{key}") REFERENCES "{ref}" ("{key if key in ("player_id","match_id") else "team_id"}")')
        con.execute(f'CREATE TABLE "{name}" ({",".join(defs)})')
        def cell(v):return json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v
        con.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" for k in fields)})',[[cell(r.get(k)) for k in fields] for r in rows])
    con.execute('CREATE TABLE build_metadata (json TEXT NOT NULL)');con.execute('INSERT INTO build_metadata VALUES (?)',(json.dumps(metadata,ensure_ascii=False),));con.commit()
    if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('SQLite integrity failed')
    con.close();temp.replace(database)
    metadata['exports']={p.name:checksum(p.read_bytes()) for p in sorted(out.glob('*.csv'))}
    write_json(out/'manifest.json',metadata)
