"""Read-only audit calculations; does not replace or mutate the production workload pipeline."""
from datetime import date,timedelta,datetime
from zoneinfo import ZoneInfo
from bisect import bisect_right
from .core import config

def madrid_date(kickoff):return datetime.fromisoformat(kickoff.replace('Z','+00:00')).astimezone(ZoneInfo('Europe/Madrid')).date().isoformat()

def window_rows(db,pid,reference,days):
    lower=(date.fromisoformat(reference)-timedelta(days=days)).isoformat()
    return [dict(r) for r in db.execute('''SELECT v.*,m.local_date,m.kickoff,m.competition_id,c.category FROM player_match_minutes v JOIN matches m USING(match_id) JOIN competitions c USING(competition_id) WHERE v.player_id=? AND m.local_date>? AND m.local_date<=? ORDER BY m.kickoff,m.match_id''',(pid,lower,reference))]

def known_window(db,pid,reference,days):
    """t-w < Madrid kickoff date <= t; never label partial known sums as complete load."""
    rows=window_rows(db,pid,reference,days);league=sum(r['minutes'] for r in rows if r['category']=='DomesticLeague' and r['minutes'] is not None);ucl=sum(r['minutes'] for r in rows if r['category']=='UCL' and r['minutes'] is not None)
    clubs={r['club_id'] for r in rows}|{r[0] for r in db.execute('SELECT DISTINCT club_id FROM standardized_official_roster WHERE fotmob_player_id=?',(pid,))};lower=(date.fromisoformat(reference)-timedelta(days=days)).isoformat()
    gaps=[]
    import csv
    from .core import BASE
    with (BASE/'reports/match_gaps.csv').open(encoding='utf-8-sig') as f:
        gaps=[r for r in csv.DictReader(f) if int(r['club_id']) in clubs and lower<r['date']<=reference]
    return dict(player_id=pid,reference_date=reference,days=days,lower_exclusive=lower,upper_inclusive=reference,timezone='Europe/Madrid',known_league_minutes=league,known_ucl_minutes=ucl,known_league_plus_ucl_minutes=league+ucl,known_all_competition_minutes=sum(r['minutes'] for r in rows if r['minutes'] is not None),observed_missing_minute_values=sum(r['minutes'] is None for r in rows),known_missing_match_ids=sorted({int(g['match_id']) for g in gaps}),schedule_universe_verified=False,exact_membership_interval_known=False,window_complete=False,league_minutes=None,ucl_minutes=None,league_plus_ucl_minutes=None,personal_other_club_and_national_coverage_verified=False,observed_player_match_rows=len(rows))

def prefix_recompute(rows,reference,days,category=None):
    """Independent implementation using kickoff timezone conversion, dates and prefix sums."""
    events=sorted((madrid_date(r['kickoff']),r['minutes']) for r in rows if (category is None or r['category']==category) and r['minutes'] is not None)
    dates=[x[0] for x in events];prefix=[0]
    for d,v in events:prefix.append(prefix[-1]+v)
    lower=(date.fromisoformat(reference)-timedelta(days=days)).isoformat()
    return prefix[bisect_right(dates,reference)]-prefix[bisect_right(dates,lower)]
