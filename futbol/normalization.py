from __future__ import annotations
from collections import defaultdict
from datetime import datetime,timedelta
from pathlib import Path
from .common import *
from .collection import profile_teams
from .availability import resolve

def player_key(pid):return stable_id("players",int(pid))
def team_key(tid):return stable_id("teams",int(tid))
def match_key(mid):return "fotmob:"+str(int(mid))

def profile_row(profile):
    birth=(profile.get("birthDate") or {}).get("utcTime")
    pos=profile.get("positionDescription") or {}
    return {"player_id":player_key(profile["id"]),"player_name":profile["name"],
        "fotmob_player_id":int(profile["id"]),"date_of_birth":birth[:10] if birth else None,
        "national_team":next((x.get("team") for x in ((profile.get("careerHistory",{}).get("careerItems",{}).get("national team") or {}).get("teamEntries") or []) if x.get("active")),None),
        "national_team_id":next((team_key(x["teamId"]) for x in ((profile.get("careerHistory",{}).get("careerItems",{}).get("national team") or {}).get("teamEntries") or []) if x.get("active") and x.get("teamId") and int(x["teamId"])>0),None),
        "primary_position":pos.get("primaryPosition",{}).get("label"),
        "source":f"https://www.fotmob.com/players/{profile['id']}"}

def statistics(player):
    result={}
    for group in player.get("stats") or []:
        for label,entry in (group.get("stats") or {}).items():
            value=(entry.get("stat") or {}).get("value")
            if value is not None:result[entry.get("key") or label]=value
    return result

def minute_mark(event):
    if event is None:return None
    base=event.get("time")
    extra=event.get("overloadTime")
    if base is None:return None
    return str(base)+(f"+{extra}" if extra else "")

def detail_index(detail):
    c=detail.get("content") or {};lineup=c.get("lineup") or {}
    result={};team_meta={}
    for side in ("homeTeam","awayTeam"):
        team=lineup.get(side) or {}
        if not team.get("id"):continue
        tid=int(team["id"]);team_meta[tid]=team
        for kind in ("starters","subs","unavailable"):
            for p in team.get(kind) or []:
                pid=int(p["id"]);stats=statistics((c.get("playerStats") or {}).get(str(pid),{}))
                result[pid]={"kind":kind,"team_id":tid,"lineup":p,"stats":stats}
    # Main events preserve stoppage-time marks when lineup metadata omits them.
    events=((c.get("matchFacts") or {}).get("events") or {}).get("events") or []
    subs={}
    for e in events:
        if e.get("type")=="Substitution":
            pair=e.get("swap") or []
            if len(pair)==2:
                for p,k in [(pair[0],"sub_on"),(pair[1],"sub_off")]:
                    if p.get("id"):subs.setdefault(int(p["id"]),{})[k]=minute_mark(e)
    for pid,record in result.items():
        marks=subs.get(pid,{})
        for event in record["lineup"].get("performance",{}).get("substitutionEvents") or []:
            key="sub_on" if event.get("type")=="subIn" else "sub_off"
            marks.setdefault(key,minute_mark(event))
        record.update(marks)
    return result,team_meta

def load_detail(mid):
    for p in [ROOT/f"raw/matches/{mid}.json",ROOT/f"match_{mid}.json",ROOT/f"match_{mid}_api.json"]:
        if p.exists():
            d=read_json(p)
            if str(d.get("general",{}).get("matchId"))!=str(mid):raise DataError("Wrong cached match")
            return d
    return None

def category(lid,team_id,team_types,country,config):
    if team_types.get(team_id)=="national":return "International",True
    if lid in config["ucl_competition_ids"]:return "UCL",True
    if country and not str(country).startswith("INT") and country not in ("World","International","EUR","Europe"):
        return "Domestic",True
    return "Other",bool(country)

def research_season(kickoff,tz):
    local=instant(kickoff).astimezone(__import__("zoneinfo").ZoneInfo(tz))
    year=local.year if local.month>=7 else local.year-1
    return f"{year}/{year+1}"

def roster_registrations(profiles,config):
    """Affiliation and formal registration are separate evidence classes."""
    rows=[];team_names={};team_types={}
    lower=day_start(config["research"]["start"],config["research"]["timezone"])-timedelta(days=config["research"]["lookback_days"])
    upper=day_end(config["research"]["as_of"],config["research"]["timezone"])
    for pid,profile in profiles.items():
        for kind,group in profile.get("careerHistory",{}).get("careerItems",{}).items():
            national=kind=="national team"
            if kind not in ("senior","national team","youth"):continue
            for item in group.get("teamEntries") or []:
                if not item.get("teamId") or int(item["teamId"])<=0:continue
                tid=int(item["teamId"]);team_names[tid]=item["team"]
                typ="national" if national else "club"
                if tid in team_types and team_types[tid]!=typ:raise DataError("Team identity has incompatible types")
                team_types[tid]=typ
                start=item.get("startDate");end=item.get("endDate")
                if not start:continue
                start=iso(start)
                end=iso(end) if end else None
                if instant(start)>=upper or (end and instant(end)<=lower):continue
                transfer=(item.get("transferType") or {}).get("localizationKey") if isinstance(item.get("transferType"),dict) else item.get("transferType")
                key=stable_id("registration",f"{pid}:{tid}:{kind}:{start}:{end}")
                rows.append({"registration_id":key,"player_id":player_key(pid),"team_id":team_key(tid),
                    "fotmob_player_id":pid,"fotmob_team_id":tid,
                    "competition_scope":"NationalAffiliation" if national else "ClubAffiliation",
                    "valid_from":start,"valid_to":end,"registration_status":"affiliation_only_registration_unknown",
                    "evidence_type":"career_affiliation","registration_confirmed":False,"phase":None,
                    "transfer_type":transfer,"source":f"https://www.fotmob.com/players/{pid}",
                    "note":"Club contract/career dates do not prove competition-specific registration."})
    # Manually verified/imported official snapshots; unknown dates stay unknown.
    for evidence in read_csv(ROOT/"config/registration_evidence.csv"):
        pid=as_number(evidence.get("fotmob_player_id"))
        if pid is None or int(pid) not in profiles:continue
        tid=int(evidence["fotmob_team_id"])
        status=evidence["registration_status"]
        confirmed=status in ("registered_list_a_snapshot","registered_list_b_snapshot","official_registered")
        rows.append({"registration_id":stable_id("registration",json.dumps(evidence,sort_keys=True)),
            "player_id":player_key(pid),"team_id":team_key(tid),"fotmob_player_id":int(pid),"fotmob_team_id":tid,
            "competition_scope":evidence["competition_scope"],"valid_from":evidence.get("valid_from") or None,
            "valid_to":evidence.get("valid_to") or None,"registration_status":status,
            "evidence_type":evidence.get("evidence_type"),"registration_confirmed":confirmed,
            "phase":evidence.get("phase"),"transfer_type":None,"source":evidence["source"],
            "note":evidence.get("note")})
    return rows,team_names,team_types

def active_relation(registrations,pid,tid,t):
    return next((r for r in registrations if r["player_id"]==pid and r["team_id"]==tid
        and r["competition_scope"] in ("ClubAffiliation","NationalAffiliation")
        and r["valid_from"] and instant(r["valid_from"])<=t
        and (not r["valid_to"] or t<instant(r["valid_to"]))),None)

def normalize(profiles,histories,schedule,config):
    players=[profile_row(p) for p in profiles.values()]
    regs,team_names,team_types=roster_registrations(profiles,config)
    catalog={int(f["id"]):f for f in schedule.get("matches",[])}
    records=defaultdict(dict);excluded=[];conflicts=[];errors=[]
    for pid,hist in histories.items():
        for r in hist["records"]:
            mid=int(r["id"]);records[mid][pid]=r
            team_names[int(r["teamId"])]=r["teamName"]
            team_names[int(r["opponentTeamId"])]=r["opponentTeamName"]
    mids=set(records)|set(catalog)
    match_rows={};appearances=[];availability=[]
    pid_internal={pid:player_key(pid) for pid in profiles}
    all_reg_by_player=defaultdict(list)
    for r in regs:all_reg_by_player[r["player_id"]].append(r)
    claims=read_csv(ROOT/"config/availability_evidence.csv")
    for mid in sorted(mids):
        observed=records.get(mid,{})
        exemplar=next(iter(observed.values()),None);fixture=catalog.get(mid)
        detail=load_detail(mid)
        g=(detail or {}).get("general") or {}
        if detail and not g.get("finished"):continue
        if fixture:
            kickoff=fixture["status"]["utcTime"];lid=int(fixture["competition_id"]);league=fixture["competition"]
            home=int(fixture["home"]["id"]);away=int(fixture["away"]["id"])
            country=fixture.get("competition_country");season=fixture.get("source_season")
            team_names.setdefault(home,fixture["home"]["name"]);team_names.setdefault(away,fixture["away"]["name"])
        elif exemplar:
            kickoff=exemplar["matchDate"]["utcTime"];lid=int(exemplar["leagueId"]);league=exemplar["leagueName"]
            own=int(exemplar["teamId"]);other=int(exemplar["opponentTeamId"])
            home,away=(own,other) if exemplar["isHomeTeam"] else (other,own)
            country=g.get("countryCode");season=None
        else:continue
        if detail:
            if int(g["homeTeam"]["id"])!=home or int(g["awayTeam"]["id"])!=away:
                raise DataError(f"Team identity mismatch in match {mid}")
            if instant(g["matchTimeUTCDate"])!=instant(kickoff):
                conflicts.append({"match_id":mid,"kind":"kickoff_disagreement","history_or_catalog":kickoff,"detail":g["matchTimeUTCDate"]})
                kickoff=g["matchTimeUTCDate"]
            country=g.get("countryCode") or country
        tids=[home,away];known_national=any(team_types.get(t)=="national" for t in tids)
        if known_national:
            for tid in tids:team_types[tid]="national"
        for tid in tids:team_types.setdefault(tid,"club")
        if detail:
            for side,tid in [("home",home),("away",away)]:team_names.setdefault(tid,g[side+"Team"]["name"])
        if lid in config["exclude_club_friendly_ids"] or (
            not known_national and ("friendly" in league.lower() or "friendlies" in league.lower())):
            excluded.append({"match_id":mid,"reason":"club_friendly","competition":league});continue
        if known_national and lid in config["national_friendly_competition_ids"] and not config["include_national_friendlies"]:
            excluded.append({"match_id":mid,"reason":"national_friendly_policy","competition":league});continue
        comp_type,classification_complete=category(lid,home,team_types,country,config)
        if known_national:comp_type="International"
        stage_raw=g.get("matchRound") or (exemplar or {}).get("stage") or (fixture or {}).get("round")
        if comp_type=="UCL":
            stage="league_phase" if str(stage_raw).isdigit() and 1<=int(stage_raw)<=8 else ("knockout" if stage_raw else "unknown")
        else:stage=None
        m={"match_id":match_key(mid),"fotmob_match_id":mid,"datetime":iso(kickoff),
            "date_madrid":instant(kickoff).astimezone(__import__("zoneinfo").ZoneInfo(config["research"]["timezone"])).date().isoformat(),
            "competition":league,"competition_id":lid,"competition_type":comp_type,
            "is_national":known_national,"is_official":True,"classification_complete":classification_complete,
            "home_team_id":team_key(home),"away_team_id":team_key(away),
            "home_team_name":team_names.get(home),"away_team_name":team_names.get(away),
            "season":season or research_season(kickoff,config["research"]["timezone"]),
            "research_season":research_season(kickoff,config["research"]["timezone"]),
            "home_score":next((x.get("score") for x in (detail or {}).get("header",{}).get("teams",[]) if int(x["id"])==home), (exemplar or {}).get("homeScore")),
            "away_score":next((x.get("score") for x in (detail or {}).get("header",{}).get("teams",[]) if int(x["id"])==away), (exemplar or {}).get("awayScore")),
            "ucl_stage":stage,"stage_source":stage_raw,
            "detail_available":bool(detail),"fixture_catalog_verified":mid in catalog,
            "source":"https://www.fotmob.com"+((fixture or {}).get("pageUrl") or (exemplar or {}).get("matchPageUrl")) if ((fixture or {}).get("pageUrl") or (exemplar or {}).get("matchPageUrl")) else f"https://www.fotmob.com/api/data/matchDetails?matchId={mid}"}
        match_rows[mid]=m
        idx,team_meta=detail_index(detail) if detail else ({},{})
        considered=set(observed)
        t=instant(kickoff)
        for pid,key in pid_internal.items():
            if any(active_relation(all_reg_by_player[key],key,team_key(tid),t) for tid in tids):
                considered.add(pid)
            if pid in idx:considered.add(pid)
        for pid in considered:
            if pid not in profiles:continue
            key=pid_internal[pid];hr=observed.get(pid);entry=idx.get(pid)
            tid=int(hr["teamId"]) if hr else (entry["team_id"] if entry else None)
            if tid is None:
                possible=[x for x in tids if active_relation(all_reg_by_player[key],key,team_key(x),t)]
                if len(possible)!=1:continue
                tid=possible[0]
            if tid not in tids:raise DataError("Player team not part of match")
            rel=active_relation(all_reg_by_player[key],key,team_key(tid),t)
            role=entry["kind"] if entry else None
            hist_played=hr.get("playedInMatch") if hr else None
            hist_minutes=as_number(hr.get("minutesPlayed")) if hr else None
            stats=entry["stats"] if entry else {}
            detail_minutes=as_number(stats.get("minutes_played"))
            minutes=detail_minutes if detail_minutes is not None else hist_minutes
            minute_conflict=hist_minutes is not None and detail_minutes is not None and hist_minutes!=detail_minutes
            if minute_conflict:conflicts.append({"match_id":mid,"player_id":pid,"kind":"minutes_disagreement","history":hist_minutes,"detail":detail_minutes})
            sub_on=entry.get("sub_on") if entry else None;sub_off=entry.get("sub_off") if entry else None
            played=role=="starters" or sub_on is not None or hist_played is True or (minutes is not None and minutes>0)
            started=True if role=="starters" else (False if role=="subs" else None)
            source=m["source"] if detail else f"https://www.fotmob.com/players/{pid}"
            if played:
                if minutes is not None and not 0<=minutes<=150:raise DataError("Invalid appearance minutes")
                appearances.append({"player_id":key,"match_id":m["match_id"],"team_id":team_key(tid),
                    "fotmob_player_id":pid,"minutes_played":minutes,"started":started,
                    "sub_on":sub_on,"sub_off":sub_off,"shirt_number":(entry["lineup"].get("shirtNumber") if entry else None),
                    "source":source,"minutes_source":"match_detail" if detail_minutes is not None else "player_history",
                    "minute_conflict":minute_conflict,"is_club":not known_national,"competition_type":comp_type})
            lineup_available=len((team_meta.get(tid) or {}).get("starters") or [])>=11
            state,squad,reason,evidence=resolve(played,role,hr,entry,lineup_available)
            if squad=="unused_substitute":minutes=0
            evidence_raw_path=None
            # Evidence applies to a concrete match, not to an inferred interval.
            for claim in claims:
                if int(claim["fotmob_player_id"])==pid and (
                    claim.get("fotmob_match_id")==str(mid) or (
                    claim.get("match_date_madrid")==m["date_madrid"] and claim.get("fotmob_team_id")==str(tid))):
                    claimed=claim["status"]
                    if played and claimed in ("injured","suspended"):
                        conflicts.append({"match_id":mid,"player_id":pid,"kind":"availability_conflict","claim":claim})
                    else:
                        state=claimed;reason=claim.get("reason") or reason;evidence=claim.get("evidence_type") or "reviewed_match_specific_evidence"
                        if reason=="rested" and state!="available":raise DataError("Rested requires confirmed available state")
                        source=claim["source"];evidence_raw_path=claim.get("raw_evidence")
            confirmed=any(r["registration_confirmed"] and r["competition_scope"] in
                (comp_type,"Club","All","UCL" if comp_type=="UCL" else "Domestic")
                and r["valid_from"] and instant(r["valid_from"])<=t
                and (not r["valid_to"] or t<instant(r["valid_to"]))
                and r["team_id"]==team_key(tid) for r in all_reg_by_player[key])
            availability.append({"player_id":key,"match_id":m["match_id"],"team_id":team_key(tid),
                "fotmob_player_id":pid,"status":state,"squad_status":squad,"absence_reason":reason,
                "minutes_played":minutes if played or squad=="unused_substitute" else (0 if squad=="not_in_squad" else None),
                "started":started if played else (False if squad!="unknown" else None),"evidence_type":evidence,"minute_conflict":minute_conflict,
                "source":source,"evidence_raw_path":evidence_raw_path,"registration_confirmed":confirmed,
                "membership_basis":"career_affiliation" if rel else "observed_match_squad",
                "sequence_fixture_verified":mid in catalog,"match_detail_available":bool(detail),"lineup_available":lineup_available,
                "healthy_rotation_confirmed":state=="available" and reason=="rested",
                "availability_meaning":"available_for_this_match; not a claim of perfect medical health"})
    teams=[{"team_id":team_key(tid),"fotmob_team_id":tid,"team_name":name,"team_type":team_types.get(tid,"unknown")}
           for tid,name in sorted(team_names.items()) if any(tid in
           [int((r.get("fotmob_team_id") or -1))] for r in regs) or any(team_key(tid) in (m["home_team_id"],m["away_team_id"]) for m in match_rows.values())]
    # Exclude opponents' incidental history outside our player cohort.
    return {"players":players,"teams":teams,"registrations":regs,"matches":list(match_rows.values()),
        "player_appearances":appearances,"player_availability":availability,
        "audit":{"excluded_matches":excluded,"conflicts":conflicts,"errors":errors,
            "registration_limitation":"Career affiliation is never labelled formal registration.",
            "availability_limitation":"Absent/zero-minute players are not inferred injured or rested."}}
