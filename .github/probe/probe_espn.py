"""Temporary probe: what line-up / injury / odds data does ESPN expose?"""
import json
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}"


def get(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, e.read()[:300].decode("utf-8", "replace")


def shape(obj, depth=0, max_depth=3):
    if depth >= max_depth:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: shape(v, depth + 1, max_depth) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return [shape(obj[0], depth + 1, max_depth), f"len={len(obj)}"] if obj else []
    return repr(obj)[:60]


today = date(2026, 10, 20)
print("=== date formats (eng.3) ===")
for dates in ["20261018", "20261001-20261007", "202610", "20261001-20261031&limit=500", "2026"]:
    status, data = get(f"{BASE.format(slug='eng.3')}/scoreboard?dates={dates}")
    n = len(data.get("events", [])) if isinstance(data, dict) else data
    print(dates, status, n)

for slug in ["eng.1", "eng.3", "ita.2", "esp.1"]:
    status, sb = get(f"{BASE.format(slug=slug)}/scoreboard?dates=20261018")
    events = sb.get("events", []) if isinstance(sb, dict) else []
    print(f"\n=== {slug}: {len(events)} events on 18 Oct ===")
    if not events:
        status, sb = get(f"{BASE.format(slug=slug)}/scoreboard?dates=20261017")
        events = sb.get("events", []) if isinstance(sb, dict) else []
    if not events:
        continue
    ev = events[0]
    print("event:", ev.get("name"), ev.get("date"))
    status, summ = get(f"{BASE.format(slug=slug)}/summary?event={ev['id']}")
    print("summary status", status)
    if not isinstance(summ, dict):
        print(summ)
        continue
    print("summary keys:", list(summ.keys()))
    rosters = summ.get("rosters") or []
    print("rosters:", len(rosters))
    for team in rosters[:2]:
        print(" team:", (team.get("team") or {}).get("displayName"), "keys:", list(team.keys()), "formation:", team.get("formation"))
        roster = team.get("roster") or []
        print("  roster len", len(roster), "starters", sum(1 for p in roster if p.get("starter")))
        if roster:
            print("  player shape:", json.dumps(shape(roster[0], max_depth=3))[:1500])
    for key in ["injuries", "odds", "pickcenter", "gameInfo", "keyEvents", "headToHeadGames", "news"]:
        if key in summ:
            print(f" {key}:", json.dumps(shape(summ[key], max_depth=3))[:800])

print("\n=== upcoming match (before line-ups) ===")
for slug in ["eng.1", "eng.2"]:
    status, sb = get(f"{BASE.format(slug=slug)}/scoreboard?dates=20261024")
    events = sb.get("events", []) if isinstance(sb, dict) else []
    if events:
        status, summ = get(f"{BASE.format(slug=slug)}/summary?event={events[0]['id']}")
        rosters = summ.get("rosters") if isinstance(summ, dict) else None
        print(slug, events[0].get("name"), "rosters:", [(len(t.get("roster") or []), t.get("formation")) for t in rosters or []])
        if isinstance(summ, dict):
            print(" keys:", list(summ.keys()))
            for key in ["injuries", "odds", "pickcenter"]:
                if key in summ:
                    print(f" {key}:", json.dumps(shape(summ[key], max_depth=4))[:1200])

print("\n=== team injuries / roster endpoints ===")
status, sb = get(f"{BASE.format(slug='eng.1')}/teams")
teams = sb["sports"][0]["leagues"][0]["teams"] if isinstance(sb, dict) else []
if teams:
    tid = teams[0]["team"]["id"]
    for path in [f"/teams/{tid}/injuries", f"/teams/{tid}/roster", f"/teams/{tid}"]:
        status, data = get(BASE.format(slug="eng.1") + path)
        print(path, status, json.dumps(shape(data, max_depth=2))[:600] if isinstance(data, dict) else data)
