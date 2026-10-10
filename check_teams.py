#!/usr/bin/env python3
"""Checks which team each tracked goalie has actually been on this season.

Looks at every NHL roster, and every finished game this season in the AHL, ECHL, OHL,
WHL, QMJHL and NCAA (box scores), and finds your goalies by name in any league.
Run from the tracker folder:  python3 check_teams.py
Results are printed and saved to team_check.txt. Takes a few minutes the first time.
"""
import collections, datetime as dt, time
import goalie_tracker as t

t.load_cache()
NAMES = {n: (lg, team, abbr) for lg, pl in t.ROSTER.items() for n, team, abbr in pl}
seen = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))  # name -> where -> [dressed, played]

def same_person(found, tracked):
    f, parts = t.norm(found), tracked.split()
    return t.norm(parts[-1]) in f and f.startswith(t.norm(parts[0])[:3])

def hit(found_name, where, played):
    for n in NAMES:
        if same_person(found_name, n):
            seen[n][where][0] += 1
            if played: seen[n][where][1] += 1

# ---- NHL: every team's current roster ----
try:
    st = t.get(f"{t.NHL}/standings/now").json()["standings"]
    for ab in sorted({x["teamAbbrev"]["default"] for x in st}):
        r = t.get(f"{t.NHL}/roster/{ab}/current").json()
        for g in r.get("goalies", []):
            hit(f'{g["firstName"]["default"]} {g["lastName"]["default"]}', f"NHL {ab} (current roster)", False)
    print("NHL rosters checked")
except Exception as e:
    print("NHL check failed:", e)

# ---- HockeyTech leagues: every finished game this season ----
for lg in t.HT:
    try:
        n = 0
        for g in t.ht_schedule(lg):
            if t.ht_status(g) != "final": continue
            gid = g.get("id") or g.get("game_id")
            home = (g.get("home_team_city","")+" "+g.get("home_team_nickname","")).strip() or g.get("home_team_name","")
            away = (g.get("visiting_team_city","")+" "+g.get("visiting_team_nickname","")).strip() or g.get("visiting_team_name","")
            for x in t.cached_goalies(lg, gid, lambda i: t.ht_goalies(lg, i, {})):
                if "error" in x: continue
                hit(x.get("name",""), f'{lg} {home if x.get("side") == "homeTeam" else away}', x.get("played"))
            n += 1
        print(f"{lg}: {n} finished games checked")
    except Exception as e:
        print(f"{lg} check failed:", e)
    t.save_cache()

# ---- HockeyTech leagues: every team's full roster for the current regular season ----
def walk(x):
    if isinstance(x, dict):
        yield x
        for v in x.values(): yield from walk(v)
    elif isinstance(x, list):
        for v in x: yield from walk(v)

for lg in t.HT:
    try:
        seasons = t.ht_json(lg, feed="modulekit", view="seasons")["SiteKit"]["Seasons"]
        reg = [x for x in seasons if str(x.get("playoff", "0")) == "0" and "regular" in str(x.get("season_name", "")).lower()]
        sid = max(reg, key=lambda x: int(x.get("season_id") or 0))["season_id"] if reg else None
        teams = {}
        for g in t.ht_schedule(lg):
            for side, nick in (("home", "home_team"), ("visiting", "visiting_team")):
                tid = g.get(f"{side}_team") or g.get(f"{side}_team_id")
                if tid: teams[str(tid)] = (g.get(f"{side}_team_city","")+" "+g.get(f"{side}_team_nickname","")).strip() or g.get(f"{side}_team_name","")
        n = 0
        for tid, tname in teams.items():
            try:
                params = dict(feed="modulekit", view="roster", team_id=tid)
                if sid: params["season_id"] = sid
                r = t.ht_json(lg, **params)
                for p in walk(r):
                    nm = p.get("name") or f'{p.get("first_name","")} {p.get("last_name","")}'.strip()
                    if nm and (p.get("first_name") or p.get("last_name") or p.get("position")):
                        hit(nm, f"{lg} {tname} (on roster)", False)
                n += 1
            except Exception as e:
                print(f"{lg} roster for {tname} skipped: {e}")
        print(f"{lg}: {n} team rosters checked")
    except Exception as e:
        print(f"{lg} roster check failed:", e)

# ---- NCAA: every finished game since Oct 1 ----
def ncaa_box_with_team(gid):
    k = f"NCAAteam:{gid}"
    if k in t.CACHE["box"]: return t.CACHE["box"][k]
    b = t.get(f"{t.NCAA_API}/game/{gid}/boxscore").json()
    names = {str(x.get("teamId")): x.get("nameShort", "?") for x in b.get("teams", [])}
    out = []
    for tb in b.get("teamBoxscore", []):
        for p in tb.get("playerStats", []):
            mins = t._num(p.get("goalieMinutes"))
            if str(p.get("position","")).upper() not in ("G","GK") and not mins: continue
            out.append(dict(name=f'{p.get("firstName","")} {p.get("lastName","")}', team=names.get(str(tb.get("teamId")), "?"), played=bool(mins)))
    t.CACHE["box"][k] = out
    return out

try:
    today = dt.datetime.now(t.ET).date(); day = dt.date(today.year, 10, 1); n = 0
    while day < today:
        try:
            d = t.get(f"{t.NCAA_API}/scoreboard/icehockey-men/d1/{day:%Y/%m/%d}/all-conf").json()
            for x in d.get("games", []):
                x = x["game"]
                if (x.get("gameState") or "").lower() != "final": continue
                for p in ncaa_box_with_team(x["gameID"]):
                    hit(p["name"], f'NCAA {p["team"]}', p["played"])
                n += 1
        except Exception as e:
            print(f"NCAA {day} skipped: {e}")
        day += dt.timedelta(days=1); time.sleep(0.25)
    print(f"NCAA: {n} finished games checked")
except Exception as e:
    print("NCAA check failed:", e)
t.save_cache()

# ---- report ----
lines = []
for n, (lg, team, abbr) in NAMES.items():
    where = seen.get(n, {})
    found = "; ".join(f"{w} (dressed {d}, played {p})" if "roster" not in w else w for w, (d, p) in where.items())
    key = lambda x: t.norm(x.replace(" St.", " State"))
    match = any(key(team) in key(w) or (abbr and f" {abbr} " in w) for w in where)
    tag = "OK      " if match else ("CHECK   " if where else "NO DATA ")
    lines.append(f"{tag}{n} | listed: {lg} {team} | found: {found or 'nothing yet this season'}")
order = {"CHECK": 0, "NO": 1, "OK": 2}
lines.sort(key=lambda s: order[s.split()[0]])
report = "\n".join(lines)
print("\n" + report)
open("team_check.txt", "w").write(report + "\n")
print("\nSaved to team_check.txt")
