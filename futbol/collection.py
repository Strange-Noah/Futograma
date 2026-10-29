from __future__ import annotations
import json,time,urllib.request,urllib.parse,urllib.error,threading,http.client
from datetime import datetime,timedelta
from pathlib import Path
from .common import ROOT, UTC, DataError, safe, read_json, write_json, checksum, instant, iso

class FotMob:
    """Reuse legacy response caches; paginate via website's verified BFF URL."""
    def __init__(self,config,offline=False,refresh=False):
        self.config=config;self.offline=offline;self.refresh=refresh
        cfg=config["collection"]
        self.delay=max(.5,cfg.get("delay_seconds",1))
        self.timeout=cfg.get("timeout_seconds",25);self.retries=cfg.get("retries",3)
        self.last_request=0.;self.sources=[];self.errors=[];self.request_lock=threading.Lock()
    def get(self,endpoint,params,path,fallbacks=(),mutable=False):
        url="https://www.fotmob.com/api/data/"+endpoint+"?"+urllib.parse.urlencode(params)
        p=safe(path)
        for candidate in [p,*fallbacks]:
            candidate=safe(candidate)
            if candidate.exists() and (self.offline or not mutable or (not self.refresh and time.time()-candidate.stat().st_mtime < self.config["update"]["interval_hours"]*3600)):
                raw=candidate.read_bytes()
                source={"url":url,"cache":str(candidate.relative_to(ROOT)),"sha256":checksum(raw),"mode":"cache"}
                meta=candidate.with_suffix(".meta.json")
                if meta.exists():source["fetched_at"]=read_json(meta).get("fetched_at")
                self.sources.append(source);return json.loads(raw)
        if self.offline:raise DataError("Missing offline cache "+str(p.relative_to(ROOT)))
        for attempt in range(self.retries):
            with self.request_lock:
                time.sleep(max(0,self.last_request+self.delay-time.monotonic()));self.last_request=time.monotonic()
            try:
                req=urllib.request.Request(url,headers={"User-Agent":"FutbolResearch/3.0","Accept":"application/json"})
                with urllib.request.urlopen(req,timeout=self.timeout) as f:raw=f.read()
                data=json.loads(raw)
                if p.exists():
                    old=p.read_bytes()
                    if json.loads(old)!=data:
                        version=ROOT/f"raw/versions/{endpoint}/{p.stem}/{checksum(old)}.json"
                        version.parent.mkdir(parents=True,exist_ok=True)
                        if not version.exists():version.write_bytes(old)
                        oldmeta=p.with_suffix('.meta.json')
                        if oldmeta.exists():write_json(version.with_suffix('.meta.json'),read_json(oldmeta))
                write_json(p,data)
                meta={"url":url,"fetched_at":datetime.now(UTC).isoformat(),"sha256":checksum(p.read_bytes()),"mode":"network","cache":str(p.relative_to(ROOT))}
                write_json(p.with_suffix(".meta.json"),meta);self.sources.append(meta);return data
            except urllib.error.HTTPError as exc:
                if exc.code not in (429,500,502,503,504) or attempt+1==self.retries:
                    raise DataError(f"HTTP {exc.code}: {url}") from exc
                time.sleep(min(10,2**(attempt+1)))
            except (urllib.error.URLError,TimeoutError,json.JSONDecodeError,ConnectionError,OSError,http.client.IncompleteRead) as exc:
                if attempt+1==self.retries:raise DataError(f"Request failed: {url}: {exc}") from exc
                time.sleep(2**(attempt+1))
        raise DataError("Request exhausted")
    def profile(self,pid):
        fallback=[]
        d=self.get("playerData",{"id":pid},ROOT/f"raw/player_load/profiles/{pid}.json",fallback,mutable=True)
        if int(d.get("id",-1))!=int(pid):raise DataError("Player ID mismatch")
        return d
    def history(self,pid,profile,lower,upper):
        first=profile.get("initialMatchesPage")
        if first is None:
            result={"fotmob_player_id":pid,"records":[],"pages":0,"source_history_reached_start":False,
                "errors":["FotMob profile has no paginated history; absence is not evidence of zero appearances"],
                "requested_start":iso(lower),"requested_end_exclusive":iso(upper),"source":"FotMob player profile; history unavailable"}
            write_json(ROOT/f"data/collected_histories/{pid}.json",result)
            return result
        records={};page=first;seen=set();pages=0;reached=False;errors=[]
        while True:
            pages+=1
            matches=page.get("matches")
            if not isinstance(matches,list):raise DataError("Player history schema changed")
            for row in matches:
                if not row.get("matchDate",{}).get("utcTime"):continue
                k=int(row["id"]);old=records.get(k)
                if old and old.get("teamId")!=row.get("teamId"):raise DataError("History team conflict")
                records.setdefault(k,row)
            dates=[instant(x["matchDate"]["utcTime"]) for x in matches if x.get("matchDate",{}).get("utcTime")]
            previous=page.get("previous")
            if dates and min(dates)<=lower:reached=True;break
            if not previous:reached=True;break
            parsed=urllib.parse.urlparse(previous)
            if parsed.hostname not in ("pub.fotmob.com","www.fotmob.com") or f"/player/{pid}/matches" not in parsed.path:
                raise DataError("Unexpected pagination URL")
            if previous in seen:raise DataError("Repeated history cursor")
            seen.add(previous)
            digest=checksum(previous.encode())[:20]
            try:
                page=self.get("playerMatches",{"url":previous},ROOT/f"raw/player_load/history/{pid}/{digest}.json")
            except DataError as exc:
                errors.append(str(exc));break
        all_records=sorted(records.values(),key=lambda r:(r["matchDate"]["utcTime"],int(r["id"])))
        selected=[r for r in all_records if lower<=instant(r["matchDate"]["utcTime"])<upper]
        result={"fotmob_player_id":pid,"records":selected,"pages":pages,
            "source_history_reached_start":reached,"errors":errors,
            "requested_start":iso(lower),"requested_end_exclusive":iso(upper),
            "earliest_fetched":min((r["matchDate"]["utcTime"] for r in all_records),default=None),
            "latest_fetched":max((r["matchDate"]["utcTime"] for r in all_records),default=None),
            "source":"FotMob paginated player history",
            "limitation":"Pagination completeness is not independent proof that source contains every official appearance."}
        write_json(ROOT/f"data/collected_histories/{pid}.json",result)
        return result
    def match(self,mid,mutable=False):
        if self.config["update"].get("weekly_historical_audit"):
            candidates=[ROOT/f"raw/matches/{mid}.json",ROOT/f"match_{mid}.json",ROOT/f"match_{mid}_api.json"]
            cached=next((p for p in candidates if p.exists()),None)
            if cached and time.time()-cached.stat().st_mtime>=7*86400:mutable=True
        d=self.get("matchDetails",{"matchId":mid},ROOT/f"raw/matches/{mid}.json",
            [ROOT/f"match_{mid}.json",ROOT/f"match_{mid}_api.json"],mutable=mutable)
        if str(d.get("general",{}).get("matchId"))!=str(mid):raise DataError("Match detail ID mismatch")
        return d
    def league(self,lid,season=None):
        slug=str(season or "latest").replace("/","-")
        d=self.get("leagues",{"id":lid,**({"season":season} if season else {})},
                   ROOT/f"raw/leagues/{lid}_{slug}.json",[ROOT/f"reference/league_{lid}_{slug}.json"],mutable=True)
        det=d.get("details",{})
        if int(det.get("id",-1))!=int(lid):raise DataError("League ID mismatch")
        if season and str(det.get("selectedSeason"))!=str(season):raise DataError("Server substituted another season")
        return d

def fixtures(data,conflicts=None):
    result={};invalid=set()
    def walk(node):
        if isinstance(node,dict):
            mid=node.get("matchId",node.get("id"))
            if mid and all(k in node for k in ("home","away","status")):
                if int(mid) in invalid:return
                old=result.get(int(mid))
                if old and any(str(old[s]["id"])!=str(node[s]["id"]) for s in ("home","away")):
                    if conflicts is None:raise DataError("Conflicting fixture identities")
                    conflicts.append({"match_id":int(mid),"reason":"Conflicting fixture/bracket team IDs; quarantined", "fixture_team_ids":[old[s]["id"] for s in ("home","away")],"bracket_team_ids":[node[s]["id"] for s in ("home","away")]})
                    invalid.add(int(mid));result.pop(int(mid),None);return
                result.setdefault(int(mid),dict(node,id=int(mid)))
            for v in node.values():walk(v)
        elif isinstance(node,list):
            for v in node:walk(v)
    walk(data.get("fixtures",{}));walk(data.get("playoff",{}))
    return list(result.values())

def profile_teams(profile):
    national={};clubs={}
    for key,group in profile.get("careerHistory",{}).get("careerItems",{}).items():
        for r in group.get("teamEntries") or []:
            if r.get("teamId") is None or int(r["teamId"])<=0:continue
            target=national if key=="national team" else clubs
            target[int(r["teamId"])]=r
    return clubs,national

def fetch_players(client,pids,lower,upper):
    results={}
    for pid in sorted(set(map(int,pids))):
        try:
            profile=client.profile(pid);hist=client.history(pid,profile,lower,upper)
            results[pid]=(profile,hist)
            print(f"PLAYER {pid} {profile['name']}: {len(hist['records'])} records, {hist['pages']} pages, complete_to_start={hist['source_history_reached_start']}",flush=True)
        except DataError as exc:
            client.errors.append({"kind":"player","id":pid,"error":str(exc)});print(f"PLAYER ERROR {pid}: {exc}",flush=True)
    return results
