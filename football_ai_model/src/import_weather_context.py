import argparse
import csv
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from train_1x2_model import CONTEXT_COLUMNS, DEFAULT_CONTEXT, EXTERNAL_DIR, RAW_DIR, parse_date, parse_float


CONTEXT_PATH = EXTERNAL_DIR / "match_context.csv"
STADIUMS_PATH = EXTERNAL_DIR / "stadiums.csv"
ARCHIVE_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"


def read_stadiums():
    with STADIUMS_PATH.open(newline="", encoding="utf-8") as handle:
        return {
            row["team"]: {
                "stadium": row["stadium"],
                "latitude": parse_float(row["latitude"]),
                "longitude": parse_float(row["longitude"]),
            }
            for row in csv.DictReader(handle)
        }


def read_matches(season):
    matches = []
    paths = sorted(RAW_DIR.glob("*_E0.csv"))
    if season:
        paths = [path for path in paths if path.stem.startswith(f"{season}_")]

    for path in paths:
        season_code = path.stem.split("_")[0]
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not row.get("Date") or not row.get("HomeTeam") or not row.get("AwayTeam"):
                    continue
                matches.append({
                    "season": season_code,
                    "date": parse_date(row["Date"]).isoformat(),
                    "home_team": row["HomeTeam"],
                    "away_team": row["AwayTeam"],
                })
    return matches


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


def fetch_weather(latitude, longitude, start_date, end_date):
    query = urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "precipitation_sum,wind_gusts_10m_max,snowfall_sum,temperature_2m_max,temperature_2m_min",
        "timezone": "Europe/London",
    })
    url = f"{ARCHIVE_ENDPOINT}?{query}"
    with urlopen(url, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def severity_score(day):
    precipitation = parse_float(day.get("precipitation_sum"))
    gusts = parse_float(day.get("wind_gusts_10m_max"))
    snowfall = parse_float(day.get("snowfall_sum"))
    temp_max = parse_float(day.get("temperature_2m_max"))
    temp_min = parse_float(day.get("temperature_2m_min"))

    rain_score = min(1.0, precipitation / 18)
    wind_score = min(1.0, max(0.0, gusts - 25) / 45)
    snow_score = min(1.0, snowfall / 5)
    heat_score = min(1.0, max(0.0, temp_max - 28) / 10)
    cold_score = min(1.0, max(0.0, 2 - temp_min) / 8)
    return round(max(rain_score, wind_score, snow_score, heat_score, cold_score), 3)


def daily_rows(weather):
    daily = weather.get("daily", {})
    times = daily.get("time", [])
    rows = {}
    for index, day in enumerate(times):
        values = {key: daily.get(key, [None] * len(times))[index] for key in daily.keys() if key != "time"}
        rows[day] = values
    return rows


def empty_context_row(match):
    row = {
        "date": match["date"],
        "home_team": match["home_team"],
        "away_team": match["away_team"],
    }
    row.update({name: str(DEFAULT_CONTEXT[name]) for name in CONTEXT_COLUMNS})
    row["notes"] = ""
    return row


def merge_weather(matches, existing, stadiums):
    by_home_team = {}
    for match in matches:
        by_home_team.setdefault(match["home_team"], []).append(match)

    missing_stadiums = sorted(team for team in by_home_team if team not in stadiums)
    if missing_stadiums:
        raise SystemExit(f"Missing stadium coordinates for: {', '.join(missing_stadiums)}")

    for team, team_matches in sorted(by_home_team.items()):
        dates = sorted(match["date"] for match in team_matches)
        stadium = stadiums[team]
        print(f"Fetching weather for {team}: {dates[0]} to {dates[-1]}")
        weather = fetch_weather(stadium["latitude"], stadium["longitude"], dates[0], dates[-1])
        weather_by_day = daily_rows(weather)

        for match in team_matches:
            key = (match["date"], match["home_team"], match["away_team"])
            row = existing.get(key, empty_context_row(match))
            day = weather_by_day.get(match["date"], {})
            row["weather_severity"] = str(severity_score(day))
            note = (
                f"Weather: rain {parse_float(day.get('precipitation_sum')):.1f}mm, "
                f"gusts {parse_float(day.get('wind_gusts_10m_max')):.1f}km/h, "
                f"snow {parse_float(day.get('snowfall_sum')):.1f}cm"
            )
            row["notes"] = f"{row.get('notes', '').strip()} | {note}".strip(" |")
            existing[key] = row

    return existing


def write_context(rows):
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["date", "home_team", "away_team", *CONTEXT_COLUMNS, "notes"]
    sorted_rows = sorted(rows.values(), key=lambda row: (row["date"], row["home_team"], row["away_team"]))
    with CONTEXT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(sorted_rows)
    print(f"Wrote {CONTEXT_PATH} with {len(sorted_rows)} rows")


def latest_season():
    seasons = sorted(path.stem.split("_")[0] for path in RAW_DIR.glob("*_E0.csv"))
    return seasons[-1] if seasons else None


def main():
    parser = argparse.ArgumentParser(description="Import Open-Meteo weather into match_context.csv")
    parser.add_argument("--season", default=latest_season(), help="Season code such as 2526. Use 'all' for all raw seasons.")
    args = parser.parse_args()

    season = None if args.season == "all" else args.season
    matches = read_matches(season)
    if not matches:
        raise SystemExit("No matches found. Run download_data.py first.")

    existing = read_existing_context()
    stadiums = read_stadiums()
    merged = merge_weather(matches, existing, stadiums)
    write_context(merged)


if __name__ == "__main__":
    main()
