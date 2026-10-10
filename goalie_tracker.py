#!/usr/bin/env python3
"""Goalie tracker: fetches schedules + live goalie usage -> data.json, serves dashboard.

Usage:
  python goalie_tracker.py --demo            # write fake data.json to test the dashboard
  python goalie_tracker.py                   # fetch live data once
  python goalie_tracker.py --serve           # fetch every few min + serve http://localhost:8000
Needs: pip install requests
"""
from zoneinfo import ZoneInfo
import argparse, datetime as dt, io, json, os, re, threading, time, http.server, socketserver, unicodedata
try:
    import requests
except ImportError:
    requests = None

# ---------- ROSTER (edit freely; team = nickname as it appears in the league's team name) ----------
ROSTER = {
 "NHL": [("Jakub Dobes","Canadiens","MTL"),("Arvid Soderblom","Blackhawks","CHI")],
 "AHL": [("Carter George","Reign",""),("Mads Sogaard","Crunch",""),("Logan Terness","Firebirds",""),
         ("Damian Clara","Gulls",""),("Owen Flores","Reign",""),("Jiri Patera","Bruins",""),
         ("Jackson Parsons","Senators",""),("Dom Divincentiis","Moose",""),
         ("Carson Bjarnason","Phantoms",""),("Cal Petersen","Bears","")],
 "ECHL":[("Tyriq Outen","Everblades",""),("Alex Aslanidis","Mavericks",""),("Evan Cormier","Pirates","")],
 "OHL": [("Trenten Bennett","Firebirds",""),("Gavin Betts","Frontenacs",""),("Colin Ellsworth","Storm","")],
 "WHL": [("Jayden Kraus","Silvertips",""),("Jake Pilon","Royals",""),("Taylor Tabashuik","Pats",""),
         ("Matthew Kondro","Rebels",""),("Ryley Budd","Wheat Kings",""),("Marek Schlenker","Pats",""),
         ("Nathan Kam","Rockets",""),("Koen Cleaver","Hurricanes",""),("Cash Christie","Tigers",""),
         ("Carter Esler","Chiefs",""),("Grayson Malinoski","Thunderbirds",""),("Burke Hood","Giants","")],
 "QMJHL":[("Charlie Larocque","Olympiques",""),("Finn Moffett","Olympiques",""),
          ("Rafael Courchesne","Sea Dogs",""),("Arseni Radkov","Foreurs",""),("Dayton Kitchener","Oceanic",""),
          ("Seth Ronan","Islanders","")],
 "NCAA":[(n,t,"") for n,t in [("Christian Kirsch","Quinnipiac"),("Joey Costanzo","Michigan Tech"),
         ("Easton Rye","Bentley"),("Josh Ravensbergen","Michigan State"),("Austin Elliott","UMass Lowell"),
         ("Josh Fleming","Penn State"),("Michael Polston","Western Michigan"),
         ("Cole Sheffield","Minnesota Duluth"),("Kristoffer Eberly","Ohio State"),("Kolby Hay","Canisius")]],
}
# HockeyTech public feed keys. UNVERIFIED: confirm in your browser's network tab on each league's
# stats site (look for "lscluster.hockeytech.com/feed" requests) and fix if a league returns errors.
HT = {
 "AHL":  {"client": "ahl",   "key": "ccb91f29d6744675"},
 "ECHL": {"client": "echl",  "key": "2c2b89ea7345cae8"},
 "OHL":  {"client": "ohl",   "key": "2976319eb44abe94"},
 "WHL":  {"client": "whl",   "key": "41b145a848f4bd67"},
 "QMJHL":{"client": "lhjmq", "key": "f322673b6bcae299"},
}
HT_URL = "https://lscluster.hockeytech.com/feed/index.php"
NHL = "https://api-web.nhle.com/v1"
UPCOMING = 5        # upcoming games shown per goalie
RECENT = 5          # past games played shown per goalie
SCAN = 20           # how many of the team's most recent finished games to check for each goalie
NCAA_LOOKBACK = 35  # days of past NCAA scoreboards to check
ET = ZoneInfo("America/New_York")
NCAA_API = "https://ncaa-api.henrygd.me"
NCAA_ALIASES = {"Quinnipiac":["quinnipiac"],"Michigan Tech":["michigantech"],"Bentley":["bentley"],
 "Michigan State":["michiganst"],"UMass Lowell":["umasslowell","massachusettslowell"],"Penn State":["pennst"],
 "Western Michigan":["westernmich"],"Minnesota Duluth":["minnesotaduluth","minnduluth","duluth"],"Ohio State":["ohiost"],"Canisius":["canisius"]}

# ---------- cache of finished games (saved to cache.json so old box scores are fetched only once) ----------
CACHE_PATH = "cache.json"
CACHE = {"box": {}, "ncaa_days": {}, "logos": {}}

def load_cache():
    global CACHE
    try:
        c = json.load(open(CACHE_PATH))
        box = {k: v for k, v in c.get("box", {}).items() if not k.startswith("NCAA:")}  # old NCAA lookups were misread
        CACHE = {"box": box, "ncaa_days": c.get("ncaa_days", {}), "logos": c.get("logos", {})}
    except Exception:
        CACHE = {"box": {}, "ncaa_days": {}, "logos": {}}

def save_cache():
    try: json.dump(CACHE, open(CACHE_PATH, "w"))
    except Exception as e: print("cache save failed:", e)

def cached_goalies(league, gid, fn):
    """Goalie lines for a FINISHED game; remembered so each game is looked up once."""
    k = f"{'NCAAv2' if league == 'NCAA' else league}:{gid}"
    if k in CACHE["box"]: return CACHE["box"][k]
    res = fn(gid)
    if res and not any("error" in x for x in res):
        CACHE["box"][k] = [{kk: v for kk, v in x.items() if kk != "raw"} for x in res]
    return res

# ---------- team logos: saved locally once, with the white background around the edges removed ----------
LOGO_DIR = "logos"

def clear_white_edges(im, cutoff=225):
    """Make the white area touching the image border transparent (white inside the logo stays)."""
    w, h = im.size; px = im.load(); stack = []; seen = bytearray(w * h)
    near_white = lambda p: p[3] < 20 or (p[0] >= cutoff and p[1] >= cutoff and p[2] >= cutoff)
    for x in range(w): stack += [(x, 0), (x, h - 1)]
    for y in range(h): stack += [(0, y), (w - 1, y)]
    while stack:
        x, y = stack.pop()
        if x < 0 or y < 0 or x >= w or y >= h or seen[y * w + x]: continue
        seen[y * w + x] = 1
        p = px[x, y]
        if not near_white(p): continue
        px[x, y] = (p[0], p[1], p[2], 0)
        stack += [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]
    return im

def logo_candidates(url):
    m = re.match(r"^(.*/logos/)(\d+)\.png$", url or "")
    return [url] + ([f"{m[1]}50x50/{m[2]}.png", f"{m[1]}{m[2]}.jpg", f"{m[1]}{m[2]}.gif"] if m else [])

def local_logo(url):
    """Return a local cleaned-up copy of a PNG/JPG/GIF logo (SVGs are already transparent)."""
    if not url or url.lower().endswith(".svg"): return url
    if url in CACHE["logos"] and (CACHE["logos"][url] == "" or os.path.exists(CACHE["logos"][url])):
        return CACHE["logos"][url] or url
    try:
        from PIL import Image
    except ImportError:
        return url  # run: pip3 install pillow
    for cand in logo_candidates(url):
        try:
            im = Image.open(io.BytesIO(get(cand).content)).convert("RGBA")
            os.makedirs(LOGO_DIR, exist_ok=True)
            fn = f"{LOGO_DIR}/" + re.sub(r"[^a-z0-9]+", "_", url.lower().split("//", 1)[-1]).strip("_")[-90:] + ".png"
            clear_white_edges(im).save(fn)
            CACHE["logos"][url] = fn
            return fn
        except Exception:
            continue
    CACHE["logos"][url] = ""  # none of the addresses worked; don't keep retrying
    return url

def get(url, **params):
    r = requests.get(url, params=params, timeout=20, headers={"User-Agent": "goalie-tracker/0.1"})
    r.raise_for_status()
    return r

def ht_json(league, **params):
    c = HT[league]
    p = dict(key=c["key"], client_code=c["client"], fmt="json", lang="en", site_id=0, **params)
    txt = get(HT_URL, **p).text.strip()
    txt = re.sub(r"^[^{\[]*\(", "", txt).rstrip(");") if not txt.startswith(("{", "[")) else txt  # strip JSONP
    return json.loads(txt)

def norm(s):  # "Océanic" -> "oceanic": drop accents first, then everything but letters
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())

# ---------------- HockeyTech leagues ----------------
def ht_status(g):
    if str(g.get("final")) == "1": return "final"
    if str(g.get("started")) == "1": return "live"
    t, n = str(g.get("game_status") or "").lower(), str(g.get("status") or "")
    if "final" in t or n in ("3", "4"): return "final"
    if n == "2" or any(k in t for k in ("period", "intermission", "progress", "1st", "2nd", "3rd")): return "live"
    return "scheduled"

def ht_schedule(league):
    """Default schedule (can be preseason only) plus the newest regular season, merged."""
    gid = lambda g: str(g.get("id") or g.get("game_id"))
    sched = ht_json(league, feed="modulekit", view="schedule")["SiteKit"]["Schedule"]
    try:
        seasons = ht_json(league, feed="modulekit", view="seasons")["SiteKit"]["Seasons"]
        reg = [x for x in seasons if str(x.get("playoff", "0")) == "0" and "regular" in str(x.get("season_name", "")).lower()]
        if reg:
            sid = max(reg, key=lambda x: int(x.get("season_id") or 0))["season_id"]
            have = {gid(g) for g in sched}
            more = ht_json(league, feed="modulekit", view="schedule", season_id=sid)["SiteKit"]["Schedule"]
            sched = sched + [g for g in more if gid(g) not in have]
    except Exception as e:
        print(f"[{league}] regular-season schedule lookup failed: {e}")
    return sched

def ht_league(league, players):
    out = []
    sched = ht_schedule(league)
    games = []
    for g in sched:
        games.append(dict(
            id=g.get("id") or g.get("game_id"),
            date=g.get("date_played") or (g.get("GameDateISO8601") or "")[:10],
            iso=g.get("GameDateISO8601") or g.get("date_played"),
            status=ht_status(g),
            home=(g.get("home_team_city","")+" "+g.get("home_team_nickname","")).strip() or g.get("home_team_name",""),
            away=(g.get("visiting_team_city","")+" "+g.get("visiting_team_nickname","")).strip() or g.get("visiting_team_name",""),
            hs=g.get("home_goal_count"), as_=g.get("visiting_goal_count"),
            hl=ht_logo(league, g, "home"), al=ht_logo(league, g, "visiting")))
    cache = {}
    for name, team, _ in players:
        mine = [g for g in games if norm(team) in norm(g["home"]) or norm(team) in norm(g["away"])]
        out.append(build_player(league, name, team, mine, lambda gid: ht_goalies(league, gid, cache)))
    return out

def ht_logo(league, g, side):
    u = g.get(f"{side}_team_logo") or g.get(f"{side}_logo")
    tid = g.get(f"{side}_team") or g.get(f"{side}_team_id")
    return u or (f"https://assets.leaguestat.com/{HT[league]['client']}/logos/{tid}.png" if tid else "")

def ht_goalies(league, gid, cache):
    """Return list of dicts {name, played(bool), started(bool)} for a game (best effort)."""
    if gid in cache: return cache[gid]
    res = []
    try:
        d = ht_json(league, feed="statviewfeed", view="gameSummary", game_id=gid)
        for side in ("homeTeam", "visitingTeam"):
            for gl in (d.get(side, {}) or {}).get("goalies", []) or []:
                info = gl.get("info", {}); st = gl.get("stats", {}) or {}
                nm = f'{info.get("firstName","")} {info.get("lastName","")}'.strip()
                toi = str(st.get("timeOnIce") or st.get("toi") or "0")
                res.append(dict(name=nm, played=bool(toi.strip("0:") ) or str(st.get("shotsAgainst","0")) not in ("0","", "None"),
                                started=bool(gl.get("starting") or info.get("starting")),
                                stats=mk_stats(first(st, ("saves",)), first(st, ("shotsAgainst",)), first(st, ("goalsAgainst",))), raw=gl, side=side))
    except Exception as e:
        res = [dict(error=str(e))]
    for sd in ("homeTeam", "visitingTeam"):  # a team with just one listed goalie: he is the one in net
        mine = [x for x in res if x.get("side") == sd]
        if len(mine) == 1: mine[0]["started"] = True
    cache[gid] = res
    return res

# ---------------- NHL ----------------
def nhl_league(players):
    out = []
    for name, team, abbr in players:
        try:
            sch = get(f"{NHL}/club-schedule-season/{abbr}/now").json()["games"]
        except Exception as e:
            out.append(dict(name=name, league="NHL", team=team, status="error", error=str(e), upcoming=[])); continue
        games = [dict(id=g["id"], date=g["gameDate"], iso=g.get("startTimeUTC"),
                      status={"FUT":"scheduled","PRE":"scheduled","LIVE":"live","CRIT":"live"}.get(g["gameState"],"final"),
                      home=g["homeTeam"].get("placeName",{}).get("default","")+" "+g["homeTeam"].get("commonName",{}).get("default",""),
                      away=g["awayTeam"].get("placeName",{}).get("default","")+" "+g["awayTeam"].get("commonName",{}).get("default",""),
                      hs=g["homeTeam"].get("score"), as_=g["awayTeam"].get("score"),
                      hl=nhl_logo(g["homeTeam"]), al=nhl_logo(g["awayTeam"]))
                 for g in sch if g.get("gameType", 2) != 1]  # skip preseason
        out.append(build_player("NHL", name, team, games, lambda gid: nhl_goalies(gid)))
    return out

def nhl_logo(t):
    return t.get("darkLogo") or (f"https://assets.nhle.com/logos/nhl/svg/{t['abbrev']}_dark.svg" if t.get("abbrev") else "") or t.get("logo") or ""

def nhl_goalies(gid):
    try:
        b = get(f"{NHL}/gamecenter/{gid}/boxscore").json()["playerByGameStats"]
        return [dict(name=g["name"]["default"], played=g.get("toi","00:00") != "00:00", started=bool(g.get("starter")),
                     decision=g.get("decision"), raw=g, stats=mk_stats(g.get("saves"), g.get("shotsAgainst"), g.get("goalsAgainst")))
                for side in ("homeTeam","awayTeam") for g in b[side].get("goalies",[])]
    except Exception as e:
        return [dict(error=str(e))]

# ---------------- helpers for stats ----------------
def first(p, keys):
    for k in keys:
        if k in p and p[k] not in (None, ""): return p[k]

def _num(x):
    try:
        x = str(x).replace(",", "")
        if ":" in x:
            m, sec = x.split(":")[:2]; return int(m) + int(sec) / 60
        return float(x)
    except Exception: return None

def mk_stats(saves, sa, ga):
    saves, sa, ga = _num(saves), _num(sa), _num(ga)
    if sa is None and saves is not None and ga is not None: sa = saves + ga
    if saves is None and sa is not None and ga is not None: saves = sa - ga
    if saves is None and sa is None and ga is None: return None
    pct = round(saves / sa, 3) if sa and saves is not None else None
    f = lambda v: None if v is None else int(v)
    return dict(saves=f(saves), sa=f(sa), ga=f(ga), svpct=(f"{pct:.3f}".lstrip("0") if pct is not None else None),
                shutout=bool(ga == 0 and sa))

# ---------------- NCAA (NCAA.com data via ncaa-api.henrygd.me) ----------------
def ncaa_league(players):
    a = dt.datetime.now(ET).date(); games = {}; ok = 0; last_err = None
    for i in range(-NCAA_LOOKBACK, 21):  # past weeks (for results) + next 3 weeks (for schedule)
        day = a + dt.timedelta(days=i); key = day.isoformat()
        if key in CACHE["ncaa_days"]:   # old days never change, so they come from cache.json
            for g in CACHE["ncaa_days"][key]: games[g["id"]] = g
            ok += 1; continue
        try:
            d = get(f"{NCAA_API}/scoreboard/icehockey-men/d1/{day:%Y/%m/%d}/all-conf").json()
            day_games = []
            for x in d.get("games", []):
                x = x["game"]; nm = lambda t: "".join(str(v) for v in x[t]["names"].values())
                st = (x.get("gameState") or "").lower()
                day_games.append(dict(id=x["gameID"], date=key, iso=key,
                    status={"live":"live","final":"final"}.get(st, "scheduled"),
                    home=x["home"]["names"].get("short",""), away=x["away"]["names"].get("short",""),
                    hs=x["home"].get("score"), as_=x["away"].get("score"), hk=norm(nm("home")), ak=norm(nm("away")),
                    hl=ncaa_logo(x["home"]), al=ncaa_logo(x["away"])))
            for g in day_games: games[g["id"]] = g
            if i <= -2: CACHE["ncaa_days"][key] = day_games
            ok += 1
        except Exception as e:
            last_err = e
        time.sleep(0.25)
    if not ok: raise RuntimeError(f"NCAA scoreboard unreachable: {last_err}")
    out = []
    for name, school, _ in players:
        al = NCAA_ALIASES.get(school, [norm(school)]); mine = []
        for g in games.values():
            side = "home" if any(k in g["hk"] for k in al) else "away" if any(k in g["ak"] for k in al) else None
            if side: mine.append({**g, "mine": side})
        out.append(build_player("NCAA", name, school, mine, ncaa_goalies))
    return out

def ncaa_logo(t):
    seo = (t.get("names") or {}).get("seo")
    return f"https://www.ncaa.com/sites/default/files/images/logos/schools/bgd/{seo}.svg" if seo else ""

def ncaa_goalies(gid):
    try:
        b = get(f"{NCAA_API}/game/{gid}/boxscore").json()
        res = []
        for tb in b.get("teamBoxscore", []):
            for p in tb.get("playerStats", []):
                mins = _num(p.get("goalieMinutes"))
                if str(p.get("position", "")).upper() not in ("G", "GK") and not mins: continue  # skaters
                stats = mk_stats(p.get("saves"), None, p.get("goalsAllowed"))
                played = (mins > 0) if mins is not None else bool(stats and (stats["saves"] or stats["ga"]))
                res.append(dict(name=f'{p.get("firstName","")} {p.get("lastName","")}'.strip(), played=played,
                                started=p.get("starter") is True, stats=stats if played else None, raw=p))
        return res
    except Exception as e:
        return [dict(error=str(e))]

# ---------------- shared logic ----------------
def is_live(s): return s.lower() in ("2","3","live","in progress","1st period","2nd period","3rd period","ot","so")
def is_final(s): return s.lower().startswith(("4","final")) or s.lower() in ("final","final ot","final so")
def is_sched(s): return s in ("1","") or s.lower() in ("scheduled","not started","pre-game")

def team_side(g, team):
    return g.get("mine") or ("home" if norm(team) in norm(g["home"]) else "away")

def find_me(gs, name):
    last_name = norm(name.split()[-1])
    return next((x for x in gs if "error" not in x and norm(x.get("name")).endswith(last_name)), None)

def game_result(g, side, me):
    try:
        h, a = int(g["hs"]), int(g["as_"]); m, o = (h, a) if side == "home" else (a, h)
        res = "W" if m > o else "L" if m < o else "T"; score = f"{m}-{o}"
    except Exception: res, score = None, None
    dec = {"W":"W","L":"L","O":"L","OTL":"L"}.get(str((me or {}).get("decision") or "").upper())
    return dec or res, score

def past_games(league, name, team, games, goalies_fn, today):
    """The goalie's most recent finished games where he actually played, newest first."""
    finished = [g for g in games if g["date"] <= today and is_final(g["status"])][-SCAN:]
    out = []
    for g in reversed(finished):
        if len(out) >= RECENT: break
        me = find_me(cached_goalies(league, g["id"], goalies_fn), name)
        if not (me and me.get("played")): continue
        side = team_side(g, team)
        res, score = game_result(g, side, me)
        out.append(dict(date=g["date"], opp=g["away"] if side == "home" else g["home"],
                        ha="vs" if side == "home" else "@", result=res, score=score, stats=me.get("stats")))
    return out

def build_player(league, name, team, games, goalies_fn):
    today = dt.datetime.now(ET).date().isoformat()
    games = sorted([g for g in games if g["date"]], key=lambda g: g["iso"] or g["date"])
    upcoming = [g for g in games if g["date"] >= today and not is_final(g["status"])][:UPCOMING]
    status, detail, extra = "no_game", "No game today", {}
    for g in [g for g in games if g["date"] == today]:
        side = team_side(g, team)
        opp = f'{g["away"]} @ {g["home"]}'
        if is_live(g["status"]) or is_final(g["status"]):
            gs = [x for x in goalies_fn(g["id"]) if "error" not in x]
            me = find_me(gs, name)
            if me and (me["started"] or me["played"]):
                if is_final(g["status"]):
                    status = "played"
                    res, score = game_result(g, side, me)
                    extra = dict(result=res, score=score, stats=me.get("stats"))
                    detail = f'{ {"W":"Win","L":"Loss","T":"Tie"}.get(res, "Played") } {score or ""}: {opp}'.replace("  ", " ")
                else:
                    status, detail = "in_net", f'In net now: {g["away"]} {g["as_"]}, {g["home"]} {g["hs"]}'
                    extra = dict(stats=me.get("stats"))
                extra["raw"] = me.get("raw")
            elif gs:
                status, detail = "not_playing", f"Other goalie in net: {opp}"
            else:
                status, detail = "live_unknown", f"Game on, goalie not confirmed: {opp}"
        else:
            status, detail = "scheduled", f"Game today: {opp}"
        break
    logo = next((lg for lg in ((g.get("hl") if team_side(g, team) == "home" else g.get("al")) for g in reversed(games)) if lg), "")
    try:
        recent = past_games(league, name, team, games, goalies_fn, today)
    except Exception as e:
        print(f"[{league}] past games for {name} failed: {e}"); recent = []
    return dict(name=name, league=league, team=team, logo=logo, status=status, detail=detail,
                upcoming=upcoming, recent=recent, **extra)

def run_once(path="data.json"):
    if requests is None: raise SystemExit("pip install requests")
    load_cache()
    players = []
    for lg, plist in ROSTER.items():
        try:
            if lg == "NHL": players += nhl_league(plist)
            elif lg == "NCAA": players += ncaa_league(plist)
            else: players += ht_league(lg, plist)
        except Exception as e:
            print(f"[{lg}] failed: {e}")
            players += [dict(name=n, league=lg, team=t, status="error", detail=f"{lg} feed error: {e}"[:140], upcoming=[]) for n,t,_ in plist]
        save_cache()
    for p in players: p["logo"] = local_logo(p.get("logo"))
    save_cache()
    write(players, path)
    return players

def demo(path="data.json"):
    import random; random.seed(3)
    today = dt.date.today()
    ps = []
    for lg, plist in ROSTER.items():
        for n, t, _ in plist:
            s = random.choice(["in_net","scheduled","no_game","no_game","not_playing","played"])
            up = [dict(date=(today+dt.timedelta(days=random.randint(0,9)+i*2)).isoformat(), home=f"{t} (home)", away=f"Opponent {i}", status="scheduled") for i in range(3)]
            rec = []
            for i in range(random.randint(0, 5)):
                sa = random.randint(18, 40); ga = random.randint(0, 5)
                rec.append(dict(date=(today-dt.timedelta(days=2+i*3)).isoformat(), opp=f"Opponent {i}", ha=random.choice(["vs","@"]),
                                result=random.choice("WWL"), score=f"{random.randint(1,5)}-{ga}", stats=mk_stats(sa-ga, sa, ga)))
            ps.append(dict(name=n, league=lg, team=t, status=s, detail=f"DEMO DATA: {s}", upcoming=up, recent=rec))
    write(ps, path)

def write(players, path):
    json.dump(dict(updated=dt.datetime.now().isoformat(timespec="seconds"), players=players), open(path, "w"), indent=1)
    print("wrote", path, len(players), "goalies")

def serve(port=8000, every=300):
    def loop():
        while True:
            wait = 600
            try:
                st = {p["status"] for p in run_once()}
                wait = 60 if st & {"in_net", "live_unknown"} else 180 if "scheduled" in st else 600  # faster while games are on
            except Exception as e: print("refresh failed:", e)
            time.sleep(wait)
    threading.Thread(target=loop, daemon=True).start()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), http.server.SimpleHTTPRequestHandler) as s:
        print(f"Dashboard: http://localhost:{port}"); s.serve_forever()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--demo", action="store_true"); ap.add_argument("--serve", action="store_true")
    a = ap.parse_args()
    demo() if a.demo else serve() if a.serve else run_once()
