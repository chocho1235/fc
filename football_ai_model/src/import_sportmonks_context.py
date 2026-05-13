import argparse
import csv
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import json

from train_1x2_model import CONTEXT_COLUMNS, DEFAULT_CONTEXT, EXTERNAL_DIR


CONTEXT_PATH = EXTERNAL_DIR / "match_context.csv"
BASE_URL = "https://api.sportmonks.com/v3/football"


def read_existing_context():
    if not CONTEXT_PATH.exists():
        return {}
    with CONTEXT_PATH.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return {
            (row["date"], row["home_team"], row["away_team"]): row
            for row in reader
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


def request_json(path, token, params=None):
    query = {"api_token": token}
    if params:
        query.update(params)
    url = f"{BASE_URL}{path}?{urlencode(query)}"
    with urlopen(url, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def context_row(date, home_team, away_team):
    row = {"date": date, "home_team": home_team, "away_team": away_team}
    row.update({name: str(DEFAULT_CONTEXT[name]) for name in CONTEXT_COLUMNS})
    row["notes"] = ""
    return row


def count_sidelined(team_id, sidelined):
    players = [item for item in sidelined if str(item.get("team_id", "")) == str(team_id)]
    injuries = len([item for item in players if "injur" in str(item.get("type", "")).lower()])
    suspensions = len([item for item in players if "suspend" in str(item.get("type", "")).lower()])
    return injuries, suspensions, len(players)


def lineup_strength(lineups, team_id):
    players = [item for item in lineups if str(item.get("team_id", "")) == str(team_id)]
    starters = [item for item in players if item.get("type_id") in (11, "11") or str(item.get("type", "")).lower() == "starting xi"]
    if not players:
        return 1.0, 0.0
    starter_ratio = len(starters) / 11 if starters else min(len(players), 11) / 11
    rotation_risk = max(0.0, 1 - starter_ratio)
    return round(min(1.0, starter_ratio), 3), round(rotation_risk, 3)


def import_date(date, token, existing):
    # Sportmonks exposes lineups and sidelined players as fixture includes.
    data = request_json(f"/fixtures/date/{date}", token, {
        "include": "participants;lineups;sidelined.sideline",
    })

    for fixture in data.get("data", []):
        participants = fixture.get("participants", [])
        if len(participants) < 2:
            continue

        home = next((team for team in participants if str(team.get("meta", {}).get("location", "")).lower() == "home"), participants[0])
        away = next((team for team in participants if str(team.get("meta", {}).get("location", "")).lower() == "away"), participants[1])
        home_name = home.get("name")
        away_name = away.get("name")
        if not home_name or not away_name:
            continue

        key = (date, home_name, away_name)
        row = existing.get(key, context_row(date, home_name, away_name))
        lineups = fixture.get("lineups", [])
        sidelined = fixture.get("sidelined", [])

        home_injuries, home_suspensions, home_absences = count_sidelined(home.get("id"), sidelined)
        away_injuries, away_suspensions, away_absences = count_sidelined(away.get("id"), sidelined)
        home_lineup_strength, home_rotation = lineup_strength(lineups, home.get("id"))
        away_lineup_strength, away_rotation = lineup_strength(lineups, away.get("id"))

        row["home_injury_count"] = str(home_injuries)
        row["away_injury_count"] = str(away_injuries)
        row["home_key_absences"] = str(home_absences)
        row["away_key_absences"] = str(away_absences)
        row["home_suspensions"] = str(home_suspensions)
        row["away_suspensions"] = str(away_suspensions)
        row["home_lineup_strength"] = str(home_lineup_strength)
        row["away_lineup_strength"] = str(away_lineup_strength)
        row["home_rotation_risk"] = str(home_rotation)
        row["away_rotation_risk"] = str(away_rotation)
        note = f"Sportmonks context imported for fixture {fixture.get('id')}"
        row["notes"] = f"{row.get('notes', '').strip()} | {note}".strip(" |")
        existing[key] = row

    return existing


def main():
    parser = argparse.ArgumentParser(description="Import injury, suspension and lineup context from Sportmonks.")
    parser.add_argument("--date", required=True, help="Fixture date, YYYY-MM-DD")
    args = parser.parse_args()

    token = os.getenv("SPORTMONKS_API_TOKEN")
    if not token:
        raise SystemExit("Set SPORTMONKS_API_TOKEN before running this importer.")

    existing = read_existing_context()
    merged = import_date(args.date, token, existing)
    write_context(merged)


if __name__ == "__main__":
    main()

