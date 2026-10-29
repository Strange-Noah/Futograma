from __future__ import annotations
from datetime import datetime,timedelta
from .common import ROOT,UTC,DataError,read_json,write_json,instant,iso
from .collection import fixtures,profile_teams

def overlaps_season(token,lower,upper):
    import re
    ys=[int(x) for x in re.findall(r"(?:19|20)\d{2}",str(token))]
    if not ys:return False
    return datetime(min(ys),1,1,tzinfo=UTC)<upper and datetime(max(ys)+1,1,1,tzinfo=UTC)>lower

def collect_catalogs(client,profiles,lower,upper,club_id):
    """Source tournament-season metadata drives catalogs, never guessed season labels."""
    specs={}
    national_ids=set()
    for profile in profiles.values():
        _,nts=profile_teams(profile);national_ids.update(nts)
        groups=profile.get("careerHistory",{}).get("careerItems",{})
        for kind,group in groups.items():
            for entry in (group.get("seasonEntries") or []):
                tid=int(entry.get("teamId",-1))
                # Target club needs a full fixture sequence. Other clubs come
                # from player history; national catalogs support NT absences.
                if kind not in ("senior","national team"):continue
                for stat in (entry.get("tournamentStats") or []):
                    season=stat.get("seasonName",entry.get("seasonName"))
                    lid=stat.get("leagueId")
                    if not lid or not overlaps_season(season,lower,upper):continue
                    if stat.get("isFriendly") and (kind!="national team" or not client.config["include_national_friendlies"]):continue
                    key=(int(lid),str(season))
                    specs.setdefault(key,{"league_id":int(lid),"season":str(season),"target_team_ids":set(),"is_national":kind=="national team"})
                    specs[key]["target_team_ids"].add(tid)
    schedules={};catalogs=[];errors=[];labels=[];fixture_conflicts=[]
    pending=list(sorted(specs.items()));visited=set()
    while pending:
        (lid,season),spec=pending.pop(0)
        if (lid,season) in visited:continue
        visited.add((lid,season))
        try:
            try:
                data=client.league(lid,season)
            except DataError as exc:
                cache=ROOT/f"raw/leagues/{lid}_{season.replace('/','-')}.json"
                if "substituted" not in str(exc) or not cache.exists():raise
                data=read_json(cache)
                actual=data.get("details",{}).get("selectedSeason")
                labels.append({"competition_id":lid,"profile_season":season,"catalog_season":actual,
                    "resolution":"Keep actual catalog label and verify match IDs/dates; enumerate other available overlapping labels."})
                for alt in data.get("allAvailableSeasons") or []:
                    if overlaps_season(alt,lower,upper) and (lid,str(alt)) not in visited:
                        alt_spec=dict(spec,season=str(alt))
                        pending.append(((lid,str(alt)),alt_spec))
            count=0
            for f in fixtures(data,fixture_conflicts):
                t=f.get("status",{}).get("utcTime")
                if not t or not lower<=instant(t)<upper or f.get("status",{}).get("cancelled"):continue
                if not any(int(f[s]["id"]) in spec["target_team_ids"] for s in ("home","away")):continue
                if not f["status"].get("finished"):continue
                if f["status"].get("awarded"):continue
                schedules.setdefault(int(f["id"]),dict(f,competition_id=lid,
                    competition=data["details"]["name"],source_season=data["details"].get("selectedSeason"),
                    competition_country=data["details"].get("country"),is_national=spec["is_national"]))
                count+=1
            catalogs.append({"competition_id":lid,"season":season,"finished_selected_matches":count,
                "target_team_ids":sorted(spec["target_team_ids"]),"source_season":data["details"].get("selectedSeason"),
                "source":"FotMob tournament-season catalog"})
            print(f"CATALOG {lid} {season}: {count}",flush=True)
        except (DataError,KeyError,ValueError) as exc:
            errors.append({"competition_id":lid,"season":season,"error":str(exc)})
            print(f"CATALOG ERROR {lid} {season}: {exc}",flush=True)
    result={"matches":list(schedules.values()),"catalogs":catalogs,"errors":errors,"season_label_differences":labels,"fixture_identity_conflicts":fixture_conflicts}
    write_json(ROOT/"data/club_national_schedule.json",result)
    return result

def collect_club_details(client,schedule,club_id,upper):
    details={};candidates={}
    for f in sorted(schedule["matches"],key=lambda x:x["status"]["utcTime"]):
        if club_id not in [int(f[s]["id"]) for s in ("home","away")]:continue
        try:
            d=client.match(f["id"])
            if not d.get("general",{}).get("finished"):raise DataError("Fixture detail unfinished")
            details[int(f["id"])]=d
            lineup=d.get("content",{}).get("lineup") or {}
            for side in ("homeTeam","awayTeam"):
                team=lineup.get(side) or {}
                if int(team.get("id",-1))!=club_id:continue
                for role in ("starters","subs","unavailable"):
                    for player in team.get(role,[]):
                        pid=int(player["id"])
                        candidates.setdefault(pid,{"fotmob_player_id":pid,"name":player["name"],
                            "first_observed":f["status"]["utcTime"],"last_observed":f["status"]["utcTime"],
                            "source_match_ids":[]})
                        candidates[pid]["last_observed"]=f["status"]["utcTime"]
                        candidates[pid]["source_match_ids"].append(int(f["id"]))
            print(f"CLUB DETAIL {f['id']} candidates={len(candidates)}",flush=True)
        except (DataError,KeyError,ValueError) as exc:
            client.errors.append({"kind":"match","id":f["id"],"error":str(exc)})
            print(f"CLUB DETAIL ERROR {f['id']}: {exc}",flush=True)
    write_json(ROOT/"data/observed_club_candidates.json",list(candidates.values()))
    return details,candidates
