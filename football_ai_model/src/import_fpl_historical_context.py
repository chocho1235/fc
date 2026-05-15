import argparse
import csv
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from train_1x2_model import CONTEXT_COLUMNS, DEFAULT_CONTEXT, EXTERNAL_DIR, RAW_DIR, parse_date


RAW_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
CONTEXT_PATH = EXTERNAL_DIR / "match_context.csv"
CACHE_DIR = EXTERNAL_DIR / "api_cache" / "fpl_historical"

SEASON_MAP = {
    "1718": "2017-18",
    "1819": "2018-19",
    "1920": "2019-20",
    "2021": "2020-21",
    "2122": "2021-22",
    "2223": "2022-23",
    "2324": "2023-24",
    "2425": "2024-25",
}

TEAM_ALIASES = {
    "Man Utd": "Man United",
    "Manchester United": "Man United",
    "Man City": "Man City",
    "Manchester City": "Man City",
    "Spurs": "Tottenham",
    "Tottenham Hotspur": "Tottenham",
    "Nott'm Forest": "Nott'm Forest",
    "Nottingham Forest": "Nott'm Forest",
    "Wolves": "Wolves",
    "Wolverhampton Wanderers": "Wolves",
    "West Ham United": "West Ham",
    "Newcastle United": "Newcastle",
    "Brighton and Hove Albion": "Brighton",
}


def normalise_team(name):
    return TEAM_ALIASES.get(name, name)


def download(url, path):
    if path.exists():
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(url, timeout=45) as response:
            path.write_bytes(response.read())
        return True
    except (HTTPError, URLError) as exc:
        print(f"Skipping unavailable {url}: {exc}")
        return False


def cached_csv(season, rel_path):
    cache_path = CACHE_DIR / season / rel_path
    url = f"{RAW_BASE}/{season}/{rel_path}"
    if not download(url, cache_path):
        return []
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with cache_path.open(newline="", encoding=enc) as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    return []


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


def append_note(row, note):
    existing = row.get("notes", "").strip()
    if note not in existing:
        row["notes"] = f"{existing} | {note}".strip(" |")


def as_float(value):
    try:
        if value in ("", None):
            return 0.0
        return float(value)
    except ValueError:
        return 0.0


def fpl_team_lookup(teams):
    lookup = {}
    for row in teams:
        name = normalise_team(row.get("name", ""))
        short_name = normalise_team(row.get("short_name", ""))
        team_id = row.get("id")
        if team_id:
            lookup[team_id] = name
            if short_name:
                lookup[short_name] = name
    return lookup


def season_matches(season_code):
    path = RAW_DIR / f"{season_code}_E0.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [
            {
                "date": parse_date(row["Date"]).isoformat(),
                "home_team": row["HomeTeam"],
                "away_team": row["AwayTeam"],
            }
            for row in csv.DictReader(handle)
            if row.get("Date") and row.get("HomeTeam") and row.get("AwayTeam") and row.get("FTR")
        ]


def player_roster_score(row):
    """Score used only for ranking players on a roster — minutes-agnostic so absent players rank correctly."""
    value = as_float(row.get("value")) or as_float(row.get("now_cost"))
    selected = as_float(row.get("selected")) / 1_000_000
    return (min(value / 120, 1) * 0.55) + (min(selected / 3, 1) * 0.45)


def player_importance(row):
    minutes = as_float(row.get("minutes"))
    value = as_float(row.get("value")) or as_float(row.get("now_cost"))
    selected = as_float(row.get("selected")) / 1_000_000
    starts = 0.35 if minutes >= 60 else 0.12 if minutes > 0 else 0.0
    return (min(minutes / 90, 1) * 0.45) + (min(value / 120, 1) * 0.25) + (min(selected / 3, 1) * 0.18) + starts


def build_gameweek_team_rows(season):
    teams = cached_csv(season, "teams.csv")
    team_lookup = fpl_team_lookup(teams)
    merged = cached_csv(season, "gws/merged_gw.csv")
    if not merged:
        return {}

    by_team_gw = defaultdict(list)
    for row in merged:
        gw = row.get("GW") or row.get("round")
        team = normalise_team(row.get("team") or team_lookup.get(row.get("team"), ""))
        if not gw or not team:
            continue
        by_team_gw[(team, int(float(gw)))].append(row)

    rows = {}
    for key, players in by_team_gw.items():
        # Rank by roster score (value + ownership) so absent stars still rank at top
        expected_core = sorted(players, key=player_roster_score, reverse=True)[:11]
        important_missing = [
            player for player in expected_core
            if as_float(player.get("minutes")) == 0
        ]
        partial_core = [
            player for player in expected_core
            if 0 < as_float(player.get("minutes")) < 45
        ]
        # Weight missing players by their roster score (0-1 scale)
        missing_score = round(
            sum(player_roster_score(player) for player in important_missing)
            + (0.45 * sum(player_roster_score(player) for player in partial_core)),
            3,
        )
        lineup_strength = round(max(0.72, 1 - min(0.28, missing_score / 5)), 3)
        rotation_risk = round(min(1.0, (len(important_missing) + (0.5 * len(partial_core))) / 6), 3)
        rows[key] = {
            "injury_count": len(important_missing),
            "key_absences": missing_score,
            "lineup_strength": lineup_strength,
            "rotation_risk": rotation_risk,
        }
    return rows


def infer_gameweek_index(matches):
    by_team_seen = defaultdict(int)
    indexed = []
    for match in sorted(matches, key=lambda item: (item["date"], item["home_team"], item["away_team"])):
        home = match["home_team"]
        away = match["away_team"]
        by_team_seen[home] += 1
        by_team_seen[away] += 1
        gw = max(by_team_seen[home], by_team_seen[away])
        indexed.append({**match, "gw": gw})
    return indexed


def import_season(season_code, context):
    fpl_season = SEASON_MAP.get(season_code)
    if not fpl_season:
        print(f"No FPL season mapping for {season_code}")
        return 0

    team_gw_rows = build_gameweek_team_rows(fpl_season)
    if not team_gw_rows:
        print(f"No FPL gameweek data for {fpl_season}")
        return 0

    imported = 0
    for match in infer_gameweek_index(season_matches(season_code)):
        home = match["home_team"]
        away = match["away_team"]
        gw = match["gw"]
        home_context = team_gw_rows.get((home, gw))
        away_context = team_gw_rows.get((away, gw))
        if not home_context or not away_context:
            continue

        key = (match["date"], home, away)
        row = context.get(key, blank_context(match["date"], home, away))
        # Historical FPL gameweek data is a lineup/rotation proxy, not a medical injury feed.
        row["home_injury_count"] = "0"
        row["away_injury_count"] = "0"
        row["home_suspensions"] = "0"
        row["away_suspensions"] = "0"
        row["home_key_absences"] = str(home_context["key_absences"])
        row["away_key_absences"] = str(away_context["key_absences"])
        row["home_lineup_strength"] = str(home_context["lineup_strength"])
        row["away_lineup_strength"] = str(away_context["lineup_strength"])
        row["home_rotation_risk"] = str(home_context["rotation_risk"])
        row["away_rotation_risk"] = str(away_context["rotation_risk"])
        append_note(row, f"Historical FPL proxy {fpl_season} GW{gw}")
        context[key] = row
        imported += 1

    print(f"{fpl_season}: imported {imported} fixture proxy rows")
    return imported


def main():
    parser = argparse.ArgumentParser(description="Import historical FPL player availability proxies into match_context.csv.")
    parser.add_argument("--seasons", nargs="+", default=list(SEASON_MAP.keys()), help="football-data season codes, e.g. 2223 2324 2425")
    args = parser.parse_args()

    context = read_context()
    total = 0
    for season_code in args.seasons:
        total += import_season(season_code, context)
    write_context(context)
    print(f"Imported {total} historical FPL proxy rows")


if __name__ == "__main__":
    main()
