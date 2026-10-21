"""
Collect starting line-ups (and ESPN's opening/closing odds) for finished
matches, so we know each team's usual XI and can tell when regulars are missing.

Stored in data/external/lineups/<season>.csv, one row per team per match.
Starters are saved as "player_id:position" joined by "|". Run with --backfill to fetch past
seasons; without it only the last two months are checked (used by the
6-hourly refresh).
"""

import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import espn
from leagues import LEAGUES
from team_names import canonical_team

EXTERNAL_DIR = Path(__file__).resolve().parents[1] / "data" / "external"
LINEUPS_DIR = EXTERNAL_DIR / "lineups"
PLAYERS_PATH = LINEUPS_DIR / "players.csv"
NO_LINEUP_PATH = LINEUPS_DIR / "no_lineup_events.txt"
BACKFILL_SEASONS = 3          # past seasons to fetch on --backfill, plus the current one
MAX_SUMMARIES_PER_RUN = 20000  # a full 4-season backfill is ~18k matches
WORKERS = 8

FIELDS = [
    "event_id", "league_code", "season", "date", "kickoff", "side", "team", "espn_team", "opponent",
    "formation", "starters", "bench",
    "open_H", "open_D", "open_A", "close_H", "close_D", "close_A",
]


def season_of(day: date) -> str:
    start = day.year if day.month >= 8 else day.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def read_rows(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list, fields: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_lineups() -> list:
    rows = []
    for path in sorted(LINEUPS_DIR.glob("[0-9]*.csv")):
        rows.extend(read_rows(path))
    return sorted(rows, key=lambda row: (row["kickoff"], row["event_id"], row["side"]))


def write_lineups(rows: list) -> None:
    by_season: dict = {}
    for row in rows:
        by_season.setdefault(row["season"], []).append(row)
    for season, season_rows in by_season.items():
        season_rows.sort(key=lambda row: (row["kickoff"], row["event_id"], row["side"]))
        write_rows(LINEUPS_DIR / f"{season}.csv", season_rows, FIELDS)


def months_to_check(backfill: bool, today: date) -> list:
    if not backfill:
        previous = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
        return [(previous.year, previous.month), (today.year, today.month)]
    first_season_start = (today.year if today.month >= 8 else today.year - 1) - BACKFILL_SEASONS
    months = []
    year, month = first_season_start, 7
    while (year, month) <= (today.year, today.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def encode(players) -> str:
    return "|".join(f"{pid}:{pos}" for pid, _name, pos in players if pid)


def rows_for_event(league_code: str, event: dict, data: dict) -> list:
    teams = espn.lineups(data)
    if not teams:
        return []
    kickoff = espn.kickoff_uk(event)
    opening = espn.moneyline(data, "open")
    closing = espn.moneyline(data, "close")
    rows = []
    for side, other in (("home", "away"), ("away", "home")):
        team = teams[side]
        rows.append({
            "event_id": event["id"],
            "league_code": league_code,
            "season": season_of(kickoff.date()),
            "date": kickoff.date().isoformat(),
            "kickoff": kickoff.isoformat(timespec="minutes"),
            "side": side,
            "team": canonical_team(team["team"], league_code) or "",
            "espn_team": team["team"],
            "opponent": canonical_team(teams[other]["team"], league_code) or teams[other]["team"],
            "formation": team["formation"],
            "starters": encode(team["starters"]),
            "bench": encode(team["bench"]),
            **{f"open_{k}": v for k, v in opening.items()},
            **{f"close_{k}": v for k, v in closing.items()},
        })
    return rows


def collect(backfill: bool = False) -> None:
    today = date.today()
    rows = read_lineups()
    no_lineup = set(NO_LINEUP_PATH.read_text().split()) if NO_LINEUP_PATH.exists() else set()
    done = {row["event_id"] for row in rows} | no_lineup
    players = {row["player_id"]: row["name"] for row in read_rows(PLAYERS_PATH)}

    todo = []
    for league_code, league in LEAGUES.items():
        for year, month in months_to_check(backfill, today):
            try:
                events = espn.month_events(league["espn"], year, month)
            except Exception as exc:
                print(f"  {league_code} {year}-{month:02d}: skipped ({exc})")
                continue
            todo.extend((league_code, event) for event in events
                        if espn.event_state(event) == "post" and event.get("id") not in done)
    print(f"line-ups: {len(todo)} finished match(es) to fetch")
    todo = todo[:MAX_SUMMARIES_PER_RUN]

    def fetch(item):
        league_code, event = item
        try:
            return league_code, event, espn.summary(LEAGUES[league_code]["espn"], event["id"])
        except Exception:
            return league_code, event, None

    added = missing = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for league_code, event, data in pool.map(fetch, todo):
            new_rows = rows_for_event(league_code, event, data) if data else []
            if not new_rows:
                missing += 1
                if data:  # fetched fine but ESPN has no line-up: don't ask again
                    no_lineup.add(event["id"])
                continue
            rows.extend(new_rows)
            added += 1
            for team in espn.lineups(data).values():
                for pid, name, _pos in team["starters"] + team["bench"]:
                    if pid and name:
                        players[pid] = name

    write_lineups(rows)
    NO_LINEUP_PATH.write_text("\n".join(sorted(no_lineup)) + "\n")
    write_rows(PLAYERS_PATH, [{"player_id": k, "name": v} for k, v in sorted(players.items())], ["player_id", "name"])
    print(f"line-ups: added {added} match(es), {missing} without line-ups, {len(rows) // 2} stored")


if __name__ == "__main__":
    collect(backfill="--backfill" in sys.argv)
