from collections import defaultdict
from statistics import mean
from datetime import date,timedelta
from .common import instant,day_start,day_end
from .analytics import percentile

def longest_run(sequence,predicate):
    best=current=0
    for r in sequence:
        current=current+1 if predicate(r) else 0
        best=max(best,current)
    return best

def summary_table(data,daily,config):
    mlookup={m["match_id"]:m for m in data["matches"]}
    dgroups=defaultdict(list);agroups=defaultdict(list)
    for r in daily:dgroups[r["player_id"]].append(r)
    for av in data["player_availability"]:
        agroups[av["player_id"]].append(dict(av,_m=mlookup[av["match_id"]]))
    players={r["player_id"]:r for r in data["players"]}
    rows=[]
    for pid,days in dgroups.items():
        periods={"All":days}
        for season in config["seasons"]:
            year=int(season[:4]);start=f"{year}-07-01";end=f"{year+1}-07-01"
            subset=[d for d in days if start<=d["date"]<end]
            if subset:periods[season]=subset
        for label,subset in periods.items():
            start,end=subset[0]["date"],subset[-1]["date"]
            contexts=sorted((a for a in agroups[pid] if start<=a["_m"]["date_madrid"]<=end),key=lambda a:a["_m"]["datetime"])
            row={"player_id":pid,"player_name":players[pid]["player_name"],"period":label,
                "period_start":start,"period_end":end,"calendar_days":len(subset),"usage_team_id":__import__("futbol.common",fromlist=["stable_id"]).stable_id("teams",config["club"]["fotmob_team_id"])}
            for w in config["windows_days"]:
                for prefix in ("load","club_load"):
                    col=f"{prefix}_{w}d";v=[d[col] for d in subset if d[col] is not None]
                    row[f"{col}_valid_days"]=len(v)
                    row[f"{col}_coverage_fraction"]=len(v)/len(subset)
                    row[f"{col}_mean"]=mean(v) if v else None
                    row[f"{col}_p50"]=percentile(v,.5)
                    row[f"{col}_p90"]=percentile(v,.9)
                    row[f"{col}_max"]=max(v) if v else None
                    threshold=config["thresholds_minutes"][str(w)]
                    row[f"{col}_threshold"]=threshold
                    row[f"{col}_fraction_above_threshold"]=sum(x>threshold for x in v)/len(v) if v else None
            for typ,national in [("club",False),("national",True)]:
                groups=defaultdict(list)
                for av in contexts:
                    if av["_m"]["is_national"]==national:groups[av["team_id"]].append(av)
                row[f"longest_observed_{typ}_starts"]=max((longest_run(g,lambda a:a["started"] is True) for g in groups.values()),default=0)
                row[f"longest_observed_{typ}_75min"]=max((longest_run(g,lambda a:a["minutes_played"] is not None and a["minutes_played"]>=config["high_minutes"] and not a.get("minute_conflict",False)) for g in groups.values()),default=0)
                row[f"{typ}_sequence_catalog_complete"]=(all(a["sequence_fixture_verified"] and a["match_detail_available"] and a.get("lineup_available",True) and (a["squad_status"]!="appeared" or (a["started"] is not None and a["minutes_played"] is not None and not a.get("minute_conflict",False))) for g in groups.values() for a in g) if groups else None)
            # Available-for-selection denominator; unknown cases are excluded and counted.
            for comp in ("Domestic","UCL"):
                eligible=[a for a in contexts if a["_m"]["competition_type"]==comp
                    and a["team_id"]==row["usage_team_id"] and not a["_m"]["is_national"] and a["status"]=="available"]
                row[f"{comp}_available_matches"]=len(eligible)
                minutes=[a["minutes_played"] for a in eligible]
                row[f"{comp}_available_usage"]=sum(minutes)/(90*len(eligible)) if eligible and all(x is not None for x in minutes) and not any(a.get("minute_conflict",False) for a in eligible) else None
                row[f"{comp}_available_start_rate"]=sum(a["started"] is True for a in eligible)/len(eligible) if eligible and all(a["started"] is not None for a in eligible) else None
                row[f"{comp}_available_appearance_rate"]=sum(a["squad_status"]=="appeared" for a in eligible)/len(eligible) if eligible else None
            for status in ("injured","suspended","unknown"):
                row[f"club_{status}_matches"]=sum(not a["_m"]["is_national"] and a["status"]==status for a in contexts)
            row["confirmed_healthy_rested_matches"]=sum(a["healthy_rotation_confirmed"] for a in contexts)
            peak=max((d for d in subset if d["load_28d"] is not None),key=lambda d:d["load_28d"],default=None)
            row["peak_28d_date"]=peak["date"] if peak else None
            row["peak_28d_minutes"]=peak["load_28d"] if peak else None
            if peak:
                hi=day_end(peak["date"],config["research"]["timezone"])
                lo=day_start(date.fromisoformat(peak["date"])-timedelta(days=27),config["research"]["timezone"])
                inwindow=[a for a in agroups[pid] if lo<=instant(a["_m"]["datetime"])<hi]
                row["peak_28d_known_team_matches"]=len(inwindow)
                row["peak_28d_starts"]=sum(a["started"] is True for a in inwindow)
                row["peak_28d_75min_matches"]=sum(a["minutes_played"] is not None and a["minutes_played"]>=config["high_minutes"] and not a.get("minute_conflict",False) for a in inwindow)
                row["peak_28d_team_sequence_complete"]=all(a["sequence_fixture_verified"] for a in inwindow)
            rows.append(row)
    return rows

def attendance_table(data,config):
    matches={m["match_id"]:m for m in data["matches"]};groups=defaultdict(list)
    for av in data["player_availability"]:
        m=matches[av["match_id"]]
        if not config["research"]["start"]<=m["date_madrid"]<=config["research"]["as_of"]:continue
        from .common import stable_id
        if not m["is_national"] and av["team_id"]!=stable_id("teams",config["club"]["fotmob_team_id"]):continue
        groups[(av["player_id"],m["competition_type"])].append(av)
    rows=[]
    for (pid,typ),aa in groups.items():
        played=[a for a in aa if a["squad_status"]=="appeared"]
        v=[a["minutes_played"] for a in played if a["minutes_played"] is not None]
        denominator_complete=all(a["sequence_fixture_verified"] and a["match_detail_available"] and a.get("lineup_available",True) for a in aa)
        rows.append({"player_id":pid,"competition_type":typ,"known_team_matches":len(aa),
            "appearances":len(played),"starts":sum(a["started"] is True for a in aa),
            "appearance_rate":len(played)/len(aa) if denominator_complete and aa else None,
            "start_rate":sum(a["started"] is True for a in aa)/len(aa) if denominator_complete and all(a["started"] is not None for a in played) else None,
            "mean_minutes_per_appearance":mean(v) if v and len(v)==len(played) and not any(a.get("minute_conflict",False) for a in played) else None,
            "denominator_complete":denominator_complete,"denominator_definition":"Known affiliated-team fixtures, not national call-up eligibility.",
            "unknown_availability":sum(a["status"]=="unknown" for a in aa)})
    return rows
