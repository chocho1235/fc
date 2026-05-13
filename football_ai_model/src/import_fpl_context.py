import argparse
import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

from train_1x2_model import CONTEXT_COLUMNS, DEFAULT_CONTEXT, EXTERNAL_DIR, RAW_DIR, parse_date


BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
CONTEXT_PATH = EXTERNAL_DIR / "match_context.csv"
TEAM_MAP_PATH = EXTERNAL_DIR / "fpl_team_map.csv"
CACHE_DIR = EXTERNAL_DIR / "api_cache" / "fpl"


def fetch_bootstrap(refresh_cache=False):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "bootstrap-static.json"
    if cache_path.exists() and not refresh_cache:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    try:
        with urlopen(BOOTSTRAP_URL, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        if cache_path.exists():
            print("FPL request failed; using cached bootstrap-static.json")
            return json.loads(cache_path.read_text(encoding="utf-8"))
        raise

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload["_snapshot_fetched_at"] = fetched_at
    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    snapshot_path = CACHE_DIR / f"bootstrap-static-{fetched_at.replace(':', '').replace('-', '')}.json"
    snapshot_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def read_team_map():
    if not TEAM_MAP_PATH.exists():
        return {}
    with TEAM_MAP_PATH.open(newline="", encoding="utf-8-sig") as handle:
        return {row["fpl_name"]: row["football_data_name"] for row in csv.DictReader(handle)}


def read_context():
    if not CONTEXT_PATH.exists():
        return {}
    with CONTEXT_PATH.open(newline="", encoding="utf-8-sig") as handle:
        return {
            (row["date"], row["home_team"], row["away_team"]): row
            for row in csv.DictReader(handle)
            if row.get("date") and row.get("home_team") and row.get("away_team")
        }


def write_context(rows):
    fieldnames = ["date", "home_team", "away_team", *CONTEXT_COLUMNS, "notes"]
    sorted_rows = sorted(rows.values(), key=lambda row: (row["date"], row["home_team"], row["away_team"]))
    with CONTEXT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted_rows)
    print(f"Wrote {CONTEXT_PATH} with {len(sorted_rows)} rows")


def blank_context(date, home_team, away_team):
    row = {"date": date, "home_team": home_team, "away_team": away_team}
    row.update({name: str(DEFAULT_CONTEXT[name]) for name in CONTEXT_COLUMNS})
    row["notes"] = ""
    return row


def upcoming_fixtures_for_date(date_text):
    path = RAW_DIR / "fixtures.csv"
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("Div") != "E0":
                continue
            if parse_date(row["Date"]).isoformat() == date_text:
                rows.append({
                    "date": date_text,
                    "home_team": row["HomeTeam"],
                    "away_team": row["AwayTeam"],
                })
    return rows


def upcoming_fixtures_between(start_date, end_date):
    path = RAW_DIR / "fixtures.csv"
    if not path.exists():
        return []
    rows = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("Div") != "E0":
                continue
            fixture_date = parse_date(row["Date"])
            if start_date <= fixture_date <= end_date:
                rows.append({
                    "date": fixture_date.isoformat(),
                    "home_team": row["HomeTeam"],
                    "away_team": row["AwayTeam"],
                })
    return rows


def append_note(row, note):
    existing = row.get("notes", "").strip()
    if note not in existing:
        row["notes"] = f"{existing} | {note}".strip(" |")


def player_weight(player, max_minutes):
    minutes = float(player.get("minutes") or 0)
    cost = float(player.get("now_cost") or 0) / 100
    selected = float(player.get("selected_by_percent") or 0) / 100
    minute_score = minutes / max(max_minutes, 1)
    cost_score = min(cost / 10, 1)
    selected_score = min(selected, 1)
    return round((minute_score * 0.62) + (cost_score * 0.25) + (selected_score * 0.13), 3)


def team_availability(elements, team_id):
    players = [player for player in elements if player.get("team") == team_id]
    max_minutes = max([float(player.get("minutes") or 0) for player in players] or [1])
    flagged = []
    unavailable = []

    for player in players:
        status = player.get("status", "a")
        chance = player.get("chance_of_playing_next_round")
        if status != "a" or chance is not None:
            weighted = player_weight(player, max_minutes)
            entry = {
                "name": player.get("web_name") or player.get("second_name") or "Unknown",
                "status": status,
                "chance": chance,
                "weight": weighted,
                "news": player.get("news", ""),
            }
            flagged.append(entry)
            if status in {"i", "s", "u"} or chance == 0:
                unavailable.append(entry)

    impact = round(sum(item["weight"] for item in unavailable), 3)
    rotation_risk = round(min(1.0, sum(item["weight"] for item in flagged) / 4), 3)
    lineup_strength = round(max(0.55, 1 - min(0.45, impact / 5)), 3)
    return {
        "injury_count": len(unavailable),
        "key_absences": round(impact, 3),
        "suspensions": len([item for item in unavailable if item["status"] == "s"]),
        "lineup_strength": lineup_strength,
        "rotation_risk": rotation_risk,
        "summary": "; ".join(
            f"{item['name']} {item['status']} {item['chance'] if item['chance'] is not None else ''}".strip()
            for item in unavailable[:5]
        ),
    }


def import_fixtures(fixtures, refresh_cache=False):
    payload = fetch_bootstrap(refresh_cache=refresh_cache)
    team_map = read_team_map()
    teams_by_name = {
        team_map.get(team["name"], team["name"]): team["id"]
        for team in payload.get("teams", [])
    }
    elements = payload.get("elements", [])
    existing = read_context()
    fetched_at = payload.get("_snapshot_fetched_at", "cached")

    imported = 0
    for fixture in fixtures:
        date_text = fixture["date"]
        home = fixture["home_team"]
        away = fixture["away_team"]
        home_id = teams_by_name.get(home)
        away_id = teams_by_name.get(away)
        if not home_id or not away_id:
            print(f"Skipping {home} v {away}: no FPL team mapping")
            continue

        key = (date_text, home, away)
        row = existing.get(key, blank_context(date_text, home, away))
        home_avail = team_availability(elements, home_id)
        away_avail = team_availability(elements, away_id)

        row["home_injury_count"] = str(home_avail["injury_count"])
        row["away_injury_count"] = str(away_avail["injury_count"])
        row["home_key_absences"] = str(home_avail["key_absences"])
        row["away_key_absences"] = str(away_avail["key_absences"])
        row["home_suspensions"] = str(home_avail["suspensions"])
        row["away_suspensions"] = str(away_avail["suspensions"])
        row["home_lineup_strength"] = str(home_avail["lineup_strength"])
        row["away_lineup_strength"] = str(away_avail["lineup_strength"])
        row["home_rotation_risk"] = str(home_avail["rotation_risk"])
        row["away_rotation_risk"] = str(away_avail["rotation_risk"])
        append_note(row, f"FPL live snapshot {fetched_at} H[{home_avail['summary'] or 'none'}] A[{away_avail['summary'] or 'none'}]")
        existing[key] = row
        imported += 1

    write_context(existing)
    print(f"Imported FPL availability for {imported} upcoming fixture(s)")


def import_date(date_text, refresh_cache=False):
    import_fixtures(upcoming_fixtures_for_date(date_text), refresh_cache=refresh_cache)


def import_upcoming_window(days=14, refresh_cache=False, start_date=None):
    start = start_date or date.today()
    end = start + timedelta(days=days)
    fixtures = upcoming_fixtures_between(start, end)
    import_fixtures(fixtures, refresh_cache=refresh_cache)


def main():
    parser = argparse.ArgumentParser(description="Import free FPL injury/availability context for upcoming fixtures.")
    parser.add_argument("--date", help="Fixture date as YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=14, help="Upcoming window to import when --date is omitted.")
    parser.add_argument("--refresh-cache", action="store_true", help="Fetch a fresh FPL snapshot instead of reusing cache.")
    args = parser.parse_args()
    if args.date:
        import_date(args.date, refresh_cache=args.refresh_cache)
    else:
        import_upcoming_window(days=args.days, refresh_cache=args.refresh_cache)


if __name__ == "__main__":
    main()
