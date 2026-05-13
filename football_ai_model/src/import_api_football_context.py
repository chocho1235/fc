import argparse
import csv
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from train_1x2_model import CONTEXT_COLUMNS, DEFAULT_CONTEXT, EXTERNAL_DIR


BASE_URL = "https://v3.football.api-sports.io"
PREMIER_LEAGUE_ID = 39
CONTEXT_PATH = EXTERNAL_DIR / "match_context.csv"
TEAM_MAP_PATH = EXTERNAL_DIR / "api_football_team_map.csv"
CACHE_DIR = EXTERNAL_DIR / "api_cache" / "api_football"


def season_start_year(date_text):
    year = int(date_text[:4])
    month = int(date_text[5:7])
    return year if month >= 7 else year - 1


def read_team_map():
    if not TEAM_MAP_PATH.exists():
        return {}
    with TEAM_MAP_PATH.open(newline="", encoding="utf-8-sig") as handle:
        return {row["api_name"]: row["football_data_name"] for row in csv.DictReader(handle)}


def normalise_team(name, team_map):
    return team_map.get(name, name)


def request_json(endpoint, params, token):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = endpoint.strip("/").replace("/", "_") + "_" + "_".join(f"{k}-{v}" for k, v in sorted(params.items()))
    cache_path = CACHE_DIR / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    url = f"{BASE_URL}{endpoint}?{urlencode(params)}"
    request = Request(url, headers={"x-apisports-key": token})
    with urlopen(request, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8"))

    cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def read_context():
    if not CONTEXT_PATH.exists():
        return {}
    with CONTEXT_PATH.open(newline="", encoding="utf-8-sig") as handle:
        return {
            (row["date"], row["home_team"], row["away_team"]): row
            for row in csv.DictReader(handle)
            if row.get("date") and row.get("home_team") and row.get("away_team")
        }


def blank_context(date, home_team, away_team):
    row = {"date": date, "home_team": home_team, "away_team": away_team}
    row.update({name: str(DEFAULT_CONTEXT[name]) for name in CONTEXT_COLUMNS})
    row["notes"] = ""
    return row


def write_context(rows):
    fieldnames = ["date", "home_team", "away_team", *CONTEXT_COLUMNS, "notes"]
    sorted_rows = sorted(rows.values(), key=lambda row: (row["date"], row["home_team"], row["away_team"]))
    with CONTEXT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted_rows)
    print(f"Wrote {CONTEXT_PATH} with {len(sorted_rows)} rows")


def fixture_rows(date_text, token):
    payload = request_json("/fixtures", {
        "league": PREMIER_LEAGUE_ID,
        "season": season_start_year(date_text),
        "date": date_text,
    }, token)
    return payload.get("response", [])


def injuries_for_fixture(fixture_id, token):
    payload = request_json("/injuries", {"fixture": fixture_id}, token)
    return payload.get("response", [])


def lineups_for_fixture(fixture_id, token):
    payload = request_json("/fixtures/lineups", {"fixture": fixture_id}, token)
    return payload.get("response", [])


def team_injury_counts(injuries, team_id):
    team_rows = [item for item in injuries if item.get("team", {}).get("id") == team_id]
    suspensions = [
        item for item in team_rows
        if "suspend" in str(item.get("player", {}).get("reason", "")).lower()
        or "suspend" in str(item.get("type", "")).lower()
    ]
    return len(team_rows), len(suspensions)


def lineup_strength(lineups, team_name):
    team_lineup = next((item for item in lineups if item.get("team", {}).get("name") == team_name), None)
    if not team_lineup:
        return 1.0, 0.0
    starters = team_lineup.get("startXI", [])
    strength = min(1.0, len(starters) / 11) if starters else 1.0
    return round(strength, 3), round(max(0.0, 1 - strength), 3)


def append_note(row, note):
    existing = row.get("notes", "").strip()
    row["notes"] = f"{existing} | {note}".strip(" |")


def import_date(date_text, token):
    team_map = read_team_map()
    existing = read_context()
    fixtures = fixture_rows(date_text, token)
    imported = 0

    for item in fixtures:
        fixture_id = item.get("fixture", {}).get("id")
        teams = item.get("teams", {})
        home_api = teams.get("home", {})
        away_api = teams.get("away", {})
        home_team = normalise_team(home_api.get("name", ""), team_map)
        away_team = normalise_team(away_api.get("name", ""), team_map)
        if not fixture_id or not home_team or not away_team:
            continue

        key = (date_text, home_team, away_team)
        row = existing.get(key, blank_context(date_text, home_team, away_team))

        injuries = injuries_for_fixture(fixture_id, token)
        lineups = lineups_for_fixture(fixture_id, token)
        home_injuries, home_suspensions = team_injury_counts(injuries, home_api.get("id"))
        away_injuries, away_suspensions = team_injury_counts(injuries, away_api.get("id"))
        home_strength, home_rotation = lineup_strength(lineups, home_api.get("name", ""))
        away_strength, away_rotation = lineup_strength(lineups, away_api.get("name", ""))

        row["home_injury_count"] = str(home_injuries)
        row["away_injury_count"] = str(away_injuries)
        row["home_key_absences"] = str(home_injuries)
        row["away_key_absences"] = str(away_injuries)
        row["home_suspensions"] = str(home_suspensions)
        row["away_suspensions"] = str(away_suspensions)
        row["home_lineup_strength"] = str(home_strength)
        row["away_lineup_strength"] = str(away_strength)
        row["home_rotation_risk"] = str(home_rotation)
        row["away_rotation_risk"] = str(away_rotation)
        append_note(row, f"API-Football fixture {fixture_id}")
        existing[key] = row
        imported += 1

    write_context(existing)
    print(f"Imported API-Football context for {imported} Premier League fixture(s) on {date_text}")


def main():
    parser = argparse.ArgumentParser(description="Import free-plan API-Football injury and lineup context.")
    parser.add_argument("--date", required=True, help="Fixture date as YYYY-MM-DD")
    args = parser.parse_args()

    token = os.getenv("APIFOOTBALL_API_KEY")
    if not token:
        raise SystemExit("Set APIFOOTBALL_API_KEY first. Free plan is available from API-Football/API-Sports.")

    import_date(args.date, token)


if __name__ == "__main__":
    main()

