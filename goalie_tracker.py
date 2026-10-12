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

# ---------- ROSTER ----------
# Each goalie: (name, team as shown on the site, how to find the team in that league's feed).
# For NHL the third value is the team code; for NCAA it is the school's ncaa.com slug(s), "|"-separated.
ROSTER = {
 "NHL": [("Jakub Dobeš","Montréal Canadiens","MTL"),("Arvid Söderblom","Chicago Blackhawks","CHI")],
 "PWHL":[("Callie Shanahan","New York Sirens","Sirens")],
 "AHL": [("Domenic DiVincentiis","Manitoba Moose","Moose"),("Carter George","Ontario Reign","Reign"),
         ("Mads Søgaard","Syracuse Crunch","Crunch"),("Damian Clara","San Diego Gulls","Gulls")],
 "ECHL":[("Logan Terness","Kansas City Mavericks","Mavericks"),("Owen Flores","Greenville Swamp Rabbits","Swamp Rabbits")],
 "SPHL":[("Alex Aslanidis","Roanoke Rail Yard Dawgs","Rail Yard Dawgs")],
 "Liiga":[("Rastislav Eliáš","HIFK","HIFK")],
 "NCAA D1":[("Michael Polston","Western Michigan","western-mich"),("Cole Sheffield","Minnesota Duluth","minn-duluth"),
            ("Austin Elliott","UMass Lowell","umass-lowell"),("Easton Rye","Bentley","bentley"),
            ("Joshua Ravensbergen","Michigan State","michigan-st"),
            ("Dylan Silverstein","Vermont","vermont"),("Xander Miceli","Wisconsin","wisconsin"),
            ("Luca Di Pasquo","Minnesota","minnesota"),("Christian Kirsch","Quinnipiac","quinnipiac")],
 "NCAA D3":[("Aiden McKenna","SUNY Oswego","oswego-st|suny-oswego")],
 "OHL": [("Cruz Reznik","Oshawa Generals","Generals"),("William Camputaro","Soo Greyhounds","Greyhounds")],
 "QMJHL":[("Finn Moffett","Gatineau Olympiques","Olympiques"),("Seth Ronan","Charlottetown Islanders","Islanders"),
          ("Arseni Radkov","Val-d'Or Foreurs","Foreurs"),("Justin Lee","Chicoutimi Saguenéens","Saguenéens")],
 "WHL": [("Peyton Shore","Prince George Cougars","Cougars"),("Taylor Tabashniuk","Regina Pats","Pats"),
         ("Carter Esler","Spokane Chiefs","Chiefs"),("Matthew Kondro","Red Deer Rebels","Rebels"),
         ("Cash Christie","Medicine Hat Tigers","Tigers"),("Taye Timmerman","Saskatoon Blades","Blades"),
         ("Cruz Chase","Portland Winterhawks","Winterhawks")],
 "USHL":[("Nate Chizik","U.S. National U18 Team (NTDP)","NTDP")],
 "BCHL":[("Dayton Kitchener","Alberni Valley Bulldogs","Alberni Valley Bulldogs"),("Colten Scott","Spruce Grove Saints","Spruce Grove Saints")],
 "AJHL":[("Berney Weston","Fort McMurray Oil Barons","Oil Barons")],
 "SJHL":[("Noah Martin","Flin Flon Bombers","Bombers"),("Brady Low","Yorkton Terriers","Terriers"),
         ("Mattias Radke","Kindersley Klippers","Klippers")],
 "MJHL":[("Stefan Kostin","Selkirk Steelers","Steelers")],
 "OJHL":[("Jonathan Frattaroli","St. Michael's Buzzers","Buzzers"),("Hudson Cicchelli","Stouffville Spirit","Spirit"),
         ("James Meredith","Mississauga Chargers","Chargers")],
 "GOJHL":[("Brogan Colquhoun","St. Marys Lincolns","Lincolns"),("Gio Scally","Strathroy Rockets","Strathroy Rockets")],
}
# Goalies who can move between two teams (e.g. an AHL contract assigned to the ECHL affiliate).
# The tracker watches these extra teams too and shows whichever one he is playing for.
ALSO_TRACK = {
 "Logan Terness": [("AHL", "Coachella Valley Firebirds", "Firebirds")],
}
# HockeyTech public feed keys (the ones each league's own website uses). Where two keys are known,
# the second is tried if the first stops working.
HT = {
 "AHL":  {"client": "ahl",   "keys": ["ccb91f29d6744675"]},
 "ECHL": {"client": "echl",  "keys": ["2c2b89ea7345cae8"]},
 "SPHL": {"client": "sphl",  "keys": ["8fa10d218c49ec96"]},
 "PWHL": {"client": "pwhl",  "keys": ["446521baf8c38984", "694cfeed58c932ee"]},
 "OHL":  {"client": "ohl",   "keys": ["2976319eb44abe94", "f1aa699db3d81487"]},
 "WHL":  {"client": "whl",   "keys": ["41b145a848f4bd67", "f1aa699db3d81487"]},
 "QMJHL":{"client": "lhjmq", "keys": ["f322673b6bcae299", "f1aa699db3d81487"]},
 "USHL": {"client": "ushl",  "keys": ["e828f89b243dc43f"]},
 "BCHL": {"client": "bchl",  "keys": ["f3ed30007ad2124e", "ca4e9e599d4dae55"]},
 "AJHL": {"client": "ajhl",  "keys": ["cbe60a1d91c44ade"]},
 "SJHL": {"client": "sjhl",  "keys": ["2fb5c2e84bf3e4a8"]},
 "MJHL": {"client": "mjhl",  "keys": ["f894c324fe5fd8f0"]},
 "OJHL": {"client": "ojhl",  "keys": ["cce66dd6bebf4790", "77a0bd73d9d363d3"]},
 "GOJHL":{"client": "gojhl", "keys": ["34b10d4d34d7b59a"]},
}
HT_URL = "https://lscluster.hockeytech.com/feed/index.php"
NHL = "https://api-web.nhle.com/v1"
LIIGA = "https://liiga.fi/api/v2"
UPCOMING = 5        # upcoming games shown per goalie
RECENT = 5          # past games played shown per goalie
SCAN = 20           # how many of the team's most recent finished games to check for each goalie
NCAA_LOOKBACK = 35  # days of past NCAA scoreboards to check
ET = ZoneInfo("America/New_York")
NCAA_API = "https://ncaa-api.henrygd.me"
NCAA_DIV = {"NCAA D1": "d1", "NCAA D3": "d3"}

# ---------- cache of finished games (saved to cache.json so old box scores are fetched only once) ----------
CACHE_PATH = "cache.json"
CACHE = {"box": {}, "ncaa_days": {}, "logos": {}}

def load_cache():
    global CACHE
    try:
        c = json.load(open(CACHE_PATH))
        box = {k: v for k, v in c.get("box", {}).items() if not k.startswith("NCAA:")}  # old NCAA lookups were misread
        CACHE = {"box": box, "ncaa_days": c.get("ncaa_days", {}), "logos": c.get("logos", {}), "liiga_logos": c.get("liiga_logos", {})}
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
    c = HT[league]; last = None
    for key in c["keys"]:
        try:
            p = dict(key=key, client_code=c["client"], fmt="json", lang="en", site_id=0, **params)
            txt = get(HT_URL, **p).text.strip()
            txt = re.sub(r"^[^{\[]*\(", "", txt).rstrip(");") if not txt.startswith(("{", "[")) else txt  # strip JSONP
            d = json.loads(txt)
            if isinstance(d, dict) and (d.get("error") or (d.get("SiteKit") or {}).get("error")) :
                raise RuntimeError(str(d.get("error") or d["SiteKit"]["error"])[:120])
            if key != c["keys"][0]: c["keys"].remove(key); c["keys"].insert(0, key)  # remember the one that works
            return d
        except Exception as e:
            last = e
    raise last

_TRANSLIT = str.maketrans({"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "œ": "oe"})

def norm(s):  # "Océanic" -> "oceanic", "Søgaard" -> "sogaard": drop accents, then everything but letters
    s = unicodedata.normalize("NFKD", str(s or "").translate(_TRANSLIT)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())

# ---------------- HockeyTech leagues ----------------
def ht_status(g):
    if str(g.get("final")) == "1": return "final"
    if str(g.get("started")) == "1": return "live"
    t, n = str(g.get("game_status") or "").lower(), str(g.get("status") or "")
    if "final" in t or n in ("3", "4"): return "final"
    if n == "2" or any(k in t for k in ("period", "intermission", "progress", "1st", "2nd", "3rd")): return "live"
    return "scheduled"

SKIP_SEASONS = ("all-star", "all star", "prospect", "combine", "special event", "showcase", "skills")

def ht_schedule(league):
    """Default schedule plus every season running around now (preseason, regular season, playoffs)."""
    gid = lambda g: str(g.get("id") or g.get("game_id"))
    sched = ht_json(league, feed="modulekit", view="schedule")["SiteKit"]["Schedule"]
    have = {gid(g) for g in sched}
    try:
        today = dt.datetime.now(ET).date()
        lo, hi = (today - dt.timedelta(days=75)).isoformat(), (today + dt.timedelta(days=45)).isoformat()
        for x in ht_json(league, feed="modulekit", view="seasons")["SiteKit"]["Seasons"]:
            name = str(x.get("season_name", "")).lower()
            if any(w in name for w in SKIP_SEASONS): continue
            start, end = str(x.get("start_date") or "")[:10], str(x.get("end_date") or "")[:10]
            if not start or start > hi or (end and end < lo): continue
            more = ht_json(league, feed="modulekit", view="schedule", season_id=x["season_id"])["SiteKit"]["Schedule"]
            sched += [g for g in more if gid(g) not in have]; have |= {gid(g) for g in more}
    except Exception as e:
        print(f"[{league}] season schedule lookup failed: {e}")
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
    for name, team, match in players:
        mine = [g for g in games if norm(match) in norm(g["home"]) or norm(match) in norm(g["away"])]
        out.append(build_player(league, name, team, mine, lambda gid: ht_goalies(league, gid, cache), match))
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
                                started=truthy(gl.get("starting")) or truthy(info.get("starting")),
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
        out.append(build_player("NHL", name, team, games, lambda gid: nhl_goalies(gid), team))
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
def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y")

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

# ---------------- NCAA D1 / D3 (NCAA.com data via ncaa-api.henrygd.me) ----------------
def ncaa_league(league, players):
    div = NCAA_DIV[league]
    a = dt.datetime.now(ET).date(); games = {}; ok = 0; last_err = None
    for i in range(-NCAA_LOOKBACK, 21):  # past weeks (for results) + next 3 weeks (for schedule)
        day = a + dt.timedelta(days=i); key = day.isoformat(); ckey = f"{div}v2:{key}"
        if ckey in CACHE["ncaa_days"]:   # old days never change, so they come from cache.json
            for g in CACHE["ncaa_days"][ckey]: games[g["id"]] = g
            ok += 1; continue
        try:
            d = get(f"{NCAA_API}/scoreboard/icehockey-men/{div}/{day:%Y/%m/%d}/all-conf").json()
            day_games = []
            for x in d.get("games", []):
                x = x["game"]
                st = (x.get("gameState") or "").lower()
                day_games.append(dict(id=x["gameID"], date=key, iso=key,
                    status={"live":"live","final":"final"}.get(st, "scheduled"),
                    home=x["home"]["names"].get("short",""), away=x["away"]["names"].get("short",""),
                    hs=x["home"].get("score"), as_=x["away"].get("score"),
                    hseo=x["home"]["names"].get("seo",""), aseo=x["away"]["names"].get("seo",""),
                    hl=ncaa_logo(x["home"]), al=ncaa_logo(x["away"])))
            for g in day_games: games[g["id"]] = g
            if i <= -2: CACHE["ncaa_days"][ckey] = day_games
            ok += 1
        except Exception as e:
            last_err = e
        time.sleep(0.25)
    if not ok: raise RuntimeError(f"NCAA scoreboard unreachable: {last_err}")
    out = []
    for name, school, slugs in players:
        slugs = slugs.split("|"); mine = []
        for g in games.values():
            side = "home" if g["hseo"] in slugs else "away" if g["aseo"] in slugs else None
            if side: mine.append({**g, "mine": side})
        out.append(build_player(league, name, school, mine, ncaa_goalies, school))
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

# ---------------- Liiga (Finland, liiga.fi) ----------------
def liiga_season():
    t = dt.datetime.now(ET).date()
    return t.year + 1 if t.month >= 7 else t.year   # 2026-27 season is "2027"

def liiga_league(players):
    season = liiga_season()
    sched = get(f"{LIIGA}/schedule", season=season, tournament="runkosarja").json()
    games = []
    for g in sched:
        start = g.get("start") or ""
        try: local = dt.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ET)
        except Exception: local = None
        games.append(dict(id=g["id"], date=local.date().isoformat() if local else start[:10], iso=start,
                          status="final" if g.get("ended") else "live" if g.get("started") else "scheduled",
                          home=g.get("homeTeamName", ""), away=g.get("awayTeamName", ""),
                          hs=g.get("homeTeamGoals"), as_=g.get("awayTeamGoals"), hl="", al=""))
    out = []
    for name, team, match in players:
        mine = [g for g in games if norm(match) in (norm(g["home"]), norm(g["away"]))]
        p = build_player("Liiga", name, team, mine, lambda gid: liiga_goalies(season, gid), match)
        if not p.get("logo"): p["logo"] = CACHE.setdefault("liiga_logos", {}).get(norm(match), "")
        out.append(p)
    return out

def liiga_goalies(season, gid):
    try:
        d = get(f"{LIIGA}/games/{season}/{gid}").json()
        game = d.get("game", {})
        for side in ("homeTeam", "awayTeam"):
            t = game.get(side, {}) or {}
            logo = (t.get("logos") or {}).get("darkBg") or (t.get("logos") or {}).get("lightBg")
            if logo: CACHE.setdefault("liiga_logos", {})[norm(t.get("teamName"))] = logo
        stats = {}
        try:
            st = get(f"{LIIGA}/games/stats/{season}/{gid}").json()
            for side in ("homeTeam", "awayTeam"):
                for per in st.get(side, []) or []:
                    for gp in per.get("goaliePeriodStats", []) or []:
                        s = stats.setdefault(gp.get("playerId"), dict(toi=0, saves=0, ga=0))
                        pr = gp.get("period") or {}
                        s["toi"] += pr.get("timeofice") or 0; s["saves"] += pr.get("saves") or 0; s["ga"] += pr.get("goalsAllowed") or 0
        except Exception:
            pass
        res = []
        for key in ("homeTeamPlayers", "awayTeamPlayers"):
            for p in d.get(key, []) or []:
                if p.get("role") != "GOALIE" and p.get("roleCode") != "MV": continue
                if p.get("line") is None: continue       # not dressed
                s = stats.get(p.get("id"))
                starter = p.get("line") == 1
                played = (s["toi"] > 0 or s["saves"] > 0 or s["ga"] > 0) if s else starter
                res.append(dict(name=f'{p.get("firstName","")} {p.get("lastName","")}'.strip(), played=played, started=starter,
                                stats=mk_stats(s["saves"], None, s["ga"]) if (s and played) else None))
        return res
    except Exception as e:
        return [dict(error=str(e))]

# ---------------- shared logic ----------------
def is_live(s): return s.lower() in ("2","3","live","in progress","1st period","2nd period","3rd period","ot","so")
def is_final(s): return s.lower().startswith(("4","final")) or s.lower() in ("final","final ot","final so")
def is_sched(s): return s in ("1","") or s.lower() in ("scheduled","not started","pre-game")

def team_side(g, match):
    return g.get("mine") or ("home" if norm(match) in norm(g["home"]) else "away")

def find_me(gs, name):
    """Find a goalie by last name; if several share it, use the first name to pick the right one."""
    parts = name.split(); last_name = norm(parts[-1]); first_name = norm(parts[0])
    cands = [x for x in gs if "error" not in x and norm(x.get("name")).endswith(last_name)]
    if len(cands) > 1:
        cands = [x for x in cands if norm(x.get("name")).startswith(first_name[:3])] or cands
    return cands[0] if cands else None

def game_result(g, side, me):
    try:
        h, a = int(g["hs"]), int(g["as_"]); m, o = (h, a) if side == "home" else (a, h)
        res = "W" if m > o else "L" if m < o else "T"; score = f"{m}-{o}"
    except Exception: res, score = None, None
    dec = {"W":"W","L":"L","O":"L","OTL":"L"}.get(str((me or {}).get("decision") or "").upper())
    return dec or res, score

def past_games(league, name, match, games, goalies_fn, today):
    """The goalie's most recent finished games where he actually played, newest first."""
    finished = [g for g in games if g["date"] <= today and is_final(g["status"])][-SCAN:]
    out = []
    for g in reversed(finished):
        if len(out) >= RECENT: break
        me = find_me(cached_goalies(league, g["id"], goalies_fn), name)
        if not (me and me.get("played")): continue
        side = team_side(g, match)
        res, score = game_result(g, side, me)
        out.append(dict(date=g["date"], opp=g["away"] if side == "home" else g["home"],
                        ha="vs" if side == "home" else "@", result=res, score=score, stats=me.get("stats")))
    return out

def build_player(league, name, team, games, goalies_fn, match=None):
    match = match or team
    today = dt.datetime.now(ET).date().isoformat()
    games = sorted([g for g in games if g["date"]], key=lambda g: g["iso"] or g["date"])
    upcoming = [g for g in games if g["date"] >= today and not is_final(g["status"])][:UPCOMING]
    status, detail, extra = "no_game", "No game today", {}
    for g in [g for g in games if g["date"] == today]:
        side = team_side(g, match)
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
            elif is_final(g["status"]):
                res, score = game_result(g, side, None)
                status = "result_only"
                detail = f'Final {score or ""}: {opp} (league does not publish who played in net)'.replace("  ", " ")
            else:
                status, detail = "live_unknown", f"Game on, goalie not confirmed: {opp}"
        else:
            status, detail = "scheduled", f"Game today: {opp}"
        break
    logo = next((lg for lg in ((g.get("hl") if team_side(g, match) == "home" else g.get("al")) for g in reversed(games)) if lg), "")
    try:
        recent = past_games(league, name, match, games, goalies_fn, today)
    except Exception as e:
        print(f"[{league}] past games for {name} failed: {e}"); recent = []
    return dict(name=name, league=league, team=team, logo=logo, status=status, detail=detail,
                upcoming=upcoming, recent=recent, **extra)

ST_RANK = {"in_net": 0, "live_unknown": 1, "played": 2, "result_only": 3, "scheduled": 4, "not_playing": 5, "no_game": 6, "error": 7}

def merge_alternates(players, alt_keys):
    """One card per goalie: combine his main team with any ALSO_TRACK teams."""
    by_name, order = {}, []
    for p in players:
        if p["name"] not in by_name: by_name[p["name"]] = []; order.append(p["name"])
        by_name[p["name"]].append(p)
    out = []
    for name in order:
        group = by_name[name]
        main = next((p for p in group if (p["league"], p["name"]) not in alt_keys), group[0])
        alts = [p for p in group if p is not main]
        if not alts: out.append(main); continue
        card = dict(main)
        for a in alts:
            # he played (or is playing) for the other team today -> show that game
            if a["status"] in ("in_net", "played") and main["status"] not in ("in_net", "played"):
                for k in ("status", "detail", "stats", "result", "score"):
                    if k in a: card[k] = a[k]
                card["detail"] = f'{a["team"]} ({a["league"]}): {a["detail"]}'
        games = {(r["date"], r.get("opp")): r for p in group for r in p.get("recent", [])}
        card["recent"] = sorted(games.values(), key=lambda r: r["date"], reverse=True)[:RECENT]
        card["also"] = ", ".join(f'{a["team"]} ({a["league"]})' for a in alts)
        out.append(card)
    return out

def run_once(path="data.json"):
    if requests is None: raise SystemExit("pip install requests")
    load_cache()
    players = []
    roster = {lg: list(pl) for lg, pl in ROSTER.items()}
    alt_keys = set()
    for name, extra in ALSO_TRACK.items():
        for lg, team, match in extra:
            roster.setdefault(lg, []).append((name, team, match)); alt_keys.add((lg, name))
    for lg, plist in roster.items():
        try:
            if lg == "NHL": players += nhl_league(plist)
            elif lg in NCAA_DIV: players += ncaa_league(lg, plist)
            elif lg == "Liiga": players += liiga_league(plist)
            else: players += ht_league(lg, plist)
        except Exception as e:
            print(f"[{lg}] failed: {e}")
            players += [dict(name=n, league=lg, team=t, status="error", detail=f"{lg} feed error: {e}"[:140], upcoming=[]) for n,t,_ in plist]
        save_cache()
    players = merge_alternates(players, alt_keys)
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
    json.dump(dict(updated=dt.datetime.now(ET).isoformat(timespec="seconds"), players=players), open(path, "w"), indent=1)
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
