"""Pipeline A query. It never discovers matches or opens the workload database."""
import sqlite3
from .core import BASE,within
from .roster_import import season_key

def get_players(club,season,half,path=None,affiliation_path=None,affiliation_as_of=None):
    if half not in ('H1','H2'):raise ValueError('Unknown half-season')
    season=season_key(season);period=season.replace('/','-')+'_'+half;path=within(path or BASE/'state/pipeline.sqlite');db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    c=db.execute('SELECT * FROM clubs WHERE club_key=?',(club,)).fetchone()
    if not c:raise ValueError('Unknown configured club')
    ids=sorted({r[0] for r in db.execute('SELECT player_id FROM combined_membership WHERE club_id=? AND period_id=?',(c['club_id'],period))});unresolved=[dict(r) for r in db.execute('SELECT snapshot_id,official_member_id,player_name_official,identity_status FROM standardized_official_roster WHERE club_id=? AND season=? AND half_season=? AND fotmob_player_id IS NULL',(c['club_id'],season,half))];db.close()
    from .dynamic_acquisition import get_affiliations
    affiliations=get_affiliations(ids,**({'path':affiliation_path} if affiliation_path else {}),as_of=affiliation_as_of)
    return dict(national_team_affiliations=affiliations,club=club,season=season,half=half,player_ids=ids,unresolved_official_entries=unresolved,scope='Accepted half-season population; exact effective dates may be unknown')

def roster_workloads(club,season,half,as_of,windows=(7,14,28),roster_path=None,workload_path=None):
    from .personal_workload import get_workloads,DB
    selected=get_players(club,season,half,roster_path)
    return dict(roster=selected,workloads=get_workloads(selected['player_ids'],as_of,windows,workload_path or DB))
