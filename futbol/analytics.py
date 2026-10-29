from __future__ import annotations
from bisect import bisect_left
from collections import defaultdict
from datetime import date,datetime,timedelta
from statistics import mean
from zoneinfo import ZoneInfo
from .common import UTC,instant,iso,day_start,day_end,ROOT,read_csv

TYPES=("Domestic","UCL","International","Other")

def percentile(values,q):
    if not values:return None
    values=sorted(values);x=(len(values)-1)*q
    lo=int(x);hi=min(lo+1,len(values)-1)
    return values[lo]*(hi-x)+values[hi]*(x-lo) if hi!=lo else values[lo]

class LoadIndex:
    def __init__(self,appearances,matches,histories,config):
        self.matches={m["match_id"]:m for m in matches}
        self.histories=histories;self.config=config;self.events=defaultdict(list)
        for app in appearances:
            m=self.matches[app["match_id"]]
            self.events[app["player_id"]].append((instant(m["datetime"]),app,m))
        self.times={};self.prefix={};self.last75={}
        for pid,events in self.events.items():
            events.sort(key=lambda e:(e[0],e[1]["match_id"]))
            self.times[pid]=[x[0] for x in events]
            self.last75[pid]=[x[0] for x in events if x[1]["minutes_played"] is not None and x[1]["minutes_played"]>=config["high_minutes"]]
            cols=["total","club",*TYPES]
            prefix={k:[0] for k in cols};missing={k:[0] for k in cols}
            for t,app,m in events:
                value=app["minutes_played"];unknown=value is None or app.get("minute_conflict",False)
                for key in cols:
                    relevant=key=="total" or (key=="club" and not m["is_national"]) or key==m["competition_type"]
                    prefix[key].append(prefix[key][-1]+(value or 0 if relevant else 0))
                    missing[key].append(missing[key][-1]+int(relevant and unknown))
            self.prefix[pid]=(prefix,missing)
    def window(self,pid,lower,upper):
        times=self.times.get(pid,[])
        left=bisect_left(times,lower);right=bisect_left(times,upper)
        prefixes,missing=self.prefix.get(pid,({k:[0] for k in ("total","club",*TYPES)},{k:[0] for k in ("total","club",*TYPES)}))
        values={k:prefixes[k][right]-prefixes[k][left] for k in prefixes}
        nulls={k:missing[k][right]-missing[k][left] for k in missing}
        hist=self.histories.get(pid,{})
        history_complete=bool(hist.get("source_history_reached_start")) and not hist.get("errors")
        left_complete=bool(hist.get("requested_start")) and instant(hist["requested_start"])<=lower
        source_complete=history_complete and left_complete
        return {"known":values,"values":{k:(v if source_complete and not nulls[k] else None) for k,v in values.items()},
            "complete":source_complete and not nulls["total"],"left_complete":left_complete,
            "source_history_complete":history_complete,"missing_minutes":nulls["total"],"appearances":right-left,
            "left":left,"right":right}
    def recovery(self,pid,t,high=False):
        values=self.last75.get(pid,[]) if high else self.times.get(pid,[])
        j=bisect_left(values,t)-1
        return (t-values[j]).total_seconds()/86400 if j>=0 else None

def load_columns(index,pid,upper,config,prematch=False):
    result={}
    for w in config["windows_days"]:
        lower=upper-timedelta(days=w)
        block=index.window(pid,lower,upper)
        stem=f"pre_match_{w}d" if prematch else f"load_{w}d"
        result[stem]=block["values"]["total"]
        result[("club_pre_match_" if prematch else "club_load_")+str(w)+"d"]=block["values"]["club"]
        for typ in TYPES:
            result[stem+"_"+typ]=block["values"][typ]
        result[f"known_{stem}"]=block["known"]["total"]
        result[f"known_club_{stem}"]=block["known"]["club"]
        result[f"{stem}_complete"]=block["complete"]
        result[f"{stem}_left_complete"]=block["left_complete"]
        result[f"{stem}_missing_minutes"]=block["missing_minutes"]
        total=block["values"]["total"];national=block["values"]["International"]
        result[f"nt_share_{w}d"]=national/total if total is not None and total>0 and national is not None else None
    return result

def prematch_table(data,index,config):
    lookup={m["match_id"]:m for m in data["matches"]}
    lower=day_start(config["research"]["start"],config["research"]["timezone"])
    upper=day_end(config["research"]["as_of"],config["research"]["timezone"])
    out=[]
    for av in data["player_availability"]:
        m=lookup[av["match_id"]];t=instant(m["datetime"])
        if not lower<=t<upper:continue
        row={k:av.get(k) for k in ("player_id","match_id","team_id","minutes_played","started","status","squad_status","registration_confirmed")}
        row.update(datetime=m["datetime"],competition=m["competition"],
            competition_type=m["competition_type"],is_club=not m["is_national"],ucl_stage=m["ucl_stage"],
            days_since_last_appearance=index.recovery(av["player_id"],t),
            days_since_last_75min=index.recovery(av["player_id"],t,True))
        row["last_appearance_left_censored"]=row["days_since_last_appearance"] is None
        row["last_75min_left_censored"]=row["days_since_last_75min"] is None
        row.update(load_columns(index,av["player_id"],t,config,True));out.append(row)
    return sorted(out,key=lambda r:(r["player_id"],r["datetime"],r["match_id"]))

def daily_table(data,index,config):
    tz=config["research"]["timezone"]
    start=date.fromisoformat(config["research"]["start"]);last=date.fromisoformat(config["research"]["as_of"])
    rows=[]
    for player in data["players"]:
        pid=player["player_id"];d=start
        while d<=last:
            upper=day_end(d,tz)
            row={"player_id":pid,"date":d.isoformat(),"day_end_exclusive":iso(upper),"timezone":tz}
            # Calendar-day windows preserve Madrid midnight boundaries over DST.
            for w in config["windows_days"]:
                lower=day_start(d-timedelta(days=w-1),tz)
                block=index.window(pid,lower,upper)
                stem=f"load_{w}d"
                row[stem]=block["values"]["total"];row[f"club_load_{w}d"]=block["values"]["club"]
                for typ in TYPES:row[stem+"_"+typ]=block["values"][typ]
                row[f"known_{stem}"]=block["known"]["total"]
                row[f"known_club_{stem}"]=block["known"]["club"]
                row[f"{stem}_complete"]=block["complete"]
                row[f"{stem}_left_complete"]=block["left_complete"]
                row[f"{stem}_missing_minutes"]=block["missing_minutes"]
                total=block["values"]["total"];national=block["values"]["International"]
                row[f"nt_share_{w}d"]=national/total if total is not None and total>0 and national is not None else None
            rows.append(row);d+=timedelta(days=1)
    return rows

def ucl_rotation(data,prematch,config):
    matches={m["match_id"]:m for m in data["matches"]}
    pmap={(r["player_id"],r["match_id"],r["team_id"]):r for r in prematch}
    avmap={(r["player_id"],r["match_id"],r["team_id"]):r for r in data["player_availability"]}
    club_id=str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL,f"https://www.fotmob.com/teams/{config['club']['fotmob_team_id']}"))
    domestic=sorted([m for m in data["matches"] if m["competition_type"]=="Domestic"
        and club_id in (m["home_team_id"],m["away_team_id"])],key=lambda m:m["datetime"])
    out=[]
    for av in data["player_availability"]:
        m=matches[av["match_id"]]
        if av["team_id"]!=club_id or m["competition_type"]!="UCL":continue
        if (av["player_id"],av["match_id"],club_id) not in pmap:continue
        prior=next((d for d in reversed(domestic) if instant(d["datetime"])<instant(m["datetime"])),None)
        old=avmap.get((av["player_id"],prior["match_id"],club_id)) if prior else None
        minutes=old.get("minutes_played") if old and not old.get("minute_conflict",False) else None
        row={"player_id":av["player_id"],"ucl_match_id":m["match_id"],"ucl_datetime":m["datetime"],
            "ucl_stage":m["ucl_stage"],"ucl_minutes":av["minutes_played"],"ucl_started":av["started"],
            "ucl_status":av["status"],"ucl_squad_status":av["squad_status"],"ucl_registration_confirmed":av["registration_confirmed"],"membership_basis":av["membership_basis"],
            "previous_domestic_match_id":prior["match_id"] if prior else None,
            "previous_domestic_minutes":minutes,"B_90_minus_previous_minutes":90-minutes if minutes is not None else None,
            "previous_domestic_available":old["status"] if old else "unknown",
            "previous_domestic_started":old["started"] if old else None,
            "previous_domestic_squad_status":old["squad_status"] if old else "unknown",
            "gap_days":(instant(m["datetime"])-instant(prior["datetime"])).total_seconds()/86400 if prior else None,
            "healthy_rest_confirmed":bool(old and old["healthy_rotation_confirmed"]),
            "interpretation":"Descriptive minutes difference; not proof of deliberate rotation."}
        pm=pmap[(av["player_id"],av["match_id"],club_id)]
        row.update({k:v for k,v in pm.items() if k.startswith(("pre_match_","club_pre_match_","nt_share_"))})
        out.append(row)
    return out

def national_returns(data,prematch,config):
    """Official window input preferred; source-fixture clusters explicitly proxies."""
    matches={m["match_id"]:m for m in data["matches"]}
    pmap={(r["player_id"],r["match_id"],r["team_id"]):r for r in prematch}
    groups=defaultdict(list)
    for av in data["player_availability"]:groups[av["player_id"]].append(av)
    official=read_csv(ROOT/"config/international_windows.csv")
    out=[]
    for pid,contexts in groups.items():
        national=sorted([a for a in contexts if matches[a["match_id"]]["is_national"]],key=lambda a:matches[a["match_id"]]["datetime"])
        blocks=[]
        if official:
            for win in official:
                lo=instant(win["valid_from"]);hi=instant(win["valid_to"])
                chunk=[a for a in national if lo<=instant(matches[a["match_id"]]["datetime"])<hi and not __import__("re").search(r"U\d{2}",matches[a["match_id"]]["home_team_name"] or "") and not __import__("re").search(r"U\d{2}",matches[a["match_id"]]["away_team_name"] or "")]
                if chunk:blocks.append((chunk,hi,"official_calendar",win["source"]))
        covered={a["match_id"] for chunk,_,_,_ in blocks for a in chunk}
        # Tournaments outside regular windows remain explicit observed proxies.
        uncovered=[a for a in national if a["match_id"] not in covered]
        cluster=[]
        for av in uncovered:
            t=instant(matches[av["match_id"]]["datetime"])
            if cluster and (t-instant(matches[cluster[-1]["match_id"]]["datetime"])).total_seconds()>10*86400:
                blocks.append((cluster,instant(matches[cluster[-1]["match_id"]]["datetime"]),"observed_nt_fixture_cluster","FotMob national fixtures; not confirmed FIFA calendar or travel return"))
                cluster=[]
            cluster.append(av)
        if cluster:blocks.append((cluster,instant(matches[cluster[-1]["match_id"]]["datetime"]),"observed_nt_fixture_cluster","FotMob national fixtures; not confirmed FIFA calendar or travel return"))
        blocks.sort(key=lambda b:b[1])
        club=sorted([a for a in contexts if not matches[a["match_id"]]["is_national"]],key=lambda a:matches[a["match_id"]]["datetime"])
        for number,(chunk,end,basis,source) in enumerate(blocks,1):
            following=[a for a in club if instant(matches[a["match_id"]]["datetime"])>end][:2]
            for ordinal,av in enumerate(following,1):
                pm=pmap.get((pid,av["match_id"],av["team_id"]))
                if not pm:continue
                row={"player_id":pid,"nt_block":number,"window_end":iso(end),"window_basis":basis,"source":source,
                    "club_return_match_number":ordinal,"match_id":av["match_id"],"datetime":matches[av["match_id"]]["datetime"],
                    "minutes_played":av["minutes_played"],"started":av["started"],"status":av["status"],
                    "actual_return_to_club_date_confirmed":False}
                for w in config["windows_days"]:row[f"pre_match_{w}d_International"]=pm[f"pre_match_{w}d_International"]
                out.append(row)
    return out

