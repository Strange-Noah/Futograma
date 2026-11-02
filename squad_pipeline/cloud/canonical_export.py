"""One match's metadata decorates accepted appearance rows; issues stay separate."""
import json
from ..canonical_fixture import metadata, score_fields, exists, instant


def enrich_records(db, records, domain):
    cache={}
    for rows in records.values():
        for row in rows:
            if domain=='NATIONAL' and row.get('match_type')!='NATIONAL':continue
            mid=row['match_id']
            if mid not in cache:
                m=metadata(db,mid)
                if m:
                    fields=m['fields'];info={k:(fields.get(k) or {}).get('value') for k in ['home_score','away_score','kickoff','match_status','duration_minutes','extra_time_status']}
                    states=[(fields.get(k) or {}).get('state','UNKNOWN') for k in ['home_score','away_score']]
                    info.update(home_team=m['home_team'],away_team=m['away_team'],competition_name=m['competition'],score_state='SOURCE_CONFLICT' if 'SOURCE_CONFLICT' in states else 'VERIFIED' if states==['VERIFIED','VERIFIED'] else 'UNKNOWN',canonical_metadata=m)
                else:
                    table='international_matches' if domain=='NATIONAL' else 'matches';r=db.execute('SELECT * FROM '+table+' WHERE match_id=?',(mid,)).fetchone();r=dict(r) if r else {};meta=json.loads(r.get('metadata_json') or '{}');score=score_fields(r.get('score') if domain=='NATIONAL' else r.get('result'));info=dict(score,home_team=meta.get('home_team'),away_team=meta.get('away_team'),kickoff=r.get('kickoff_utc') or r.get('kickoff'),match_status=r.get('match_state'),score_state='RETAINED_ACCEPTED' if len(score)==2 else 'UNKNOWN')
                cache[mid]=info
            row.update(cache[mid]);row['record_kind']='APPEARANCE_FACT'
            m=row.get('canonical_metadata')
            if m and row.get('club_id') is not None:
                own='club:'+str(row['club_id'])
                if m['home_team_id']==own and m['away_team']:row['opponent']=m['away_team']
                elif m['away_team_id']==own and m['home_team']:row['opponent']=m['home_team']
            if row.get('kickoff'):row['kickoff_utc']=instant(row['kickoff'])

            if row.get('competition_name'):row['competition']=row['competition_name']
