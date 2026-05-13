import argparse
import csv
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from train_1x2_model import CONTEXT_COLUMNS, DEFAULT_CONTEXT, EXTERNAL_DIR
from import_api_football_context import (
    BASE_URL,
    PREMIER_LEAGUE_ID,
    TEAM_MAP_PATH,
    append_note,
    blank_context,
    lineup_strength,
    normalise_team,
    team_injury_counts,
)


CONTEXT_PATH = EXTERNAL_DIR / "match_context.csv"
CACHE_DIR = EXTERNAL_DIR / "api_cache" / "api_football"


class RequestBudget:
    def __init__(self, max_network_requests, sleep_seconds):
        self.max_network_requests = max_network_requests
        self.sleep_seconds = sleep_seconds
        self.used = 0

    def spend(self):
        if self.used >= self.max_network_requests:
            raise RuntimeError("Daily request budget reached")
        self.used += 1
        if self.used > 1 and self.sleep_seconds > 0:
            time.sleep(self.sleep_seconds)


def cache_path(endpoint, params):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = endpoint.strip("/").replace("/", "_") + "_" + "_".join(f"{k}-{v}" for k, v in sorted(params.items()))
    return CACHE_DIR / f"{cache_key}.json"


def request_json(endpoint, params, token, budget):
    path = cache_path(endpoint, params)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")), False

    budget.spend()
    url = f"{BASE_URL}{endpoint}?{urlencode(params)}"
    request = Request(url, headers={"x-apisports-key": token})
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError("API-Football rate limit reached") from exc
        raise
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, True


def read_team_map():
    if not TEAM_MAP_PATH.exists():
        return {}
    with TEAM_MAP_PATH.open(newline="", encoding="utf-8-sig") as handle:
        return {row["api_name"]: row["football_data_name"] for row in csv.DictReader(handle)}


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


def fixtures_for_season(season, token, budget):
    payload, _network = request_json("/fixtures", {
        "league": PREMIER_LEAGUE_ID,
        "season": season,
    }, token, budget)
    errors = payload.get("errors")
    if errors:
        print(f"Season {season} API errors: {errors}")
    return payload.get("response", [])


def fixture_context(fixture_id, token, budget):
    injuries_payload, _injury_network = request_json("/injuries", {"fixture": fixture_id}, token, budget)
    lineups_payload, _lineup_network = request_json("/fixtures/lineups", {"fixture": fixture_id}, token, budget)
    return injuries_payload.get("response", []), lineups_payload.get("response", [])


def already_imported(row, fixture_id):
    return f"API-Football fixture {fixture_id}" in row.get("notes", "")


def import_seasons(seasons, token, max_network_requests, sleep_seconds):
    budget = RequestBudget(max_network_requests, sleep_seconds)
    team_map = read_team_map()
    existing = read_context()
    imported = 0
    skipped = 0

    try:
        for season in seasons:
            fixtures = fixtures_for_season(season, token, budget)
            print(f"Season {season}: {len(fixtures)} fixtures from API-Football")
            for item in fixtures:
                fixture = item.get("fixture", {})
                fixture_id = fixture.get("id")
                date_text = fixture.get("date", "")[:10]
                status = fixture.get("status", {}).get("short", "")
                if not fixture_id or status not in {"FT", "AET", "PEN"}:
                    skipped += 1
                    continue

                teams = item.get("teams", {})
                home_api = teams.get("home", {})
                away_api = teams.get("away", {})
                home_team = normalise_team(home_api.get("name", ""), team_map)
                away_team = normalise_team(away_api.get("name", ""), team_map)
                if not home_team or not away_team:
                    skipped += 1
                    continue

                key = (date_text, home_team, away_team)
                row = existing.get(key, blank_context(date_text, home_team, away_team))
                if already_imported(row, fixture_id):
                    skipped += 1
                    continue

                injuries, lineups = fixture_context(fixture_id, token, budget)
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
    except RuntimeError as exc:
        print(str(exc))

    write_context(existing)
    print(f"Imported {imported} fixture(s), skipped {skipped}, network requests used {budget.used}/{budget.max_network_requests}")


def main():
    parser = argparse.ArgumentParser(description="Backfill historical API-Football injury and lineup context in cached batches.")
    parser.add_argument("--seasons", nargs="+", default=["2022", "2023", "2024"], help="API-Football season start years.")
    parser.add_argument("--max-network-requests", type=int, default=90, help="Daily cap to protect the free 100 request quota.")
    parser.add_argument("--sleep-seconds", type=float, default=6.5, help="Delay between network calls to respect free-plan rate limits.")
    args = parser.parse_args()

    token = os.getenv("APIFOOTBALL_API_KEY")
    if not token:
        raise SystemExit("Set APIFOOTBALL_API_KEY first.")

    import_seasons(args.seasons, token, args.max_network_requests, args.sleep_seconds)


if __name__ == "__main__":
    main()
