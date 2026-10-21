"""Small client for ESPN's free, keyless soccer API."""

import json
import time
from datetime import date, datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}"
USER_AGENT = "Mozilla/5.0 (compatible; football-model/1.0)"
UK = ZoneInfo("Europe/London")
SCOREBOARD_CAP = 100  # ESPN returns at most this many events per scoreboard call


def get_json(url: str, retries: int = 3, timeout: int = 30) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            if exc.code < 500 or attempt == retries:
                raise
        except (OSError, ValueError):
            if attempt == retries:
                raise
        time.sleep(attempt * 5)
    raise RuntimeError("unreachable")


def as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def scoreboard(slug: str, dates: str) -> list:
    """Events for a day (YYYYMMDD) or a month (YYYYMM)."""
    return get_json(f"{BASE_URL.format(slug=slug)}/scoreboard?dates={dates}").get("events") or []


def month_events(slug: str, year: int, month: int) -> list:
    """All events in a month, falling back to day by day if the month is capped."""
    events = scoreboard(slug, f"{year}{month:02d}")
    if len(events) < SCOREBOARD_CAP:
        return events
    events = []
    day = date(year, month, 1)
    while day.month == month:
        events.extend(scoreboard(slug, day.strftime("%Y%m%d")))
        day = date.fromordinal(day.toordinal() + 1)
    return events


def summary(slug: str, event_id: str) -> dict:
    return get_json(f"{BASE_URL.format(slug=slug)}/summary?event={event_id}")


def event_state(event: dict) -> str:
    competition = as_dict((event.get("competitions") or [{}])[0])
    status = as_dict(competition.get("status") or event.get("status"))
    return as_dict(status.get("type")).get("state", "")


def kickoff_uk(event: dict) -> datetime:
    return datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(UK)


def american_to_decimal(value) -> float:
    if value is None or value == "":
        return 0.0
    if str(value).strip().lower() == "even":
        return 2.0
    try:
        number = float(str(value).replace("+", ""))
    except ValueError:
        return 0.0
    if number >= 100:
        return round(1 + number / 100, 3)
    if number <= -100:
        return round(1 + 100 / -number, 3)
    return 0.0


def moneyline(summary_or_competition: dict, which: str) -> dict:
    """{'H','D','A'} decimal prices. which: 'open' or 'close' (close = current
    price before kick-off)."""
    for odds in summary_or_competition.get("odds") or summary_or_competition.get("pickcenter") or []:
        line = as_dict(as_dict(odds).get("moneyline"))
        prices = {}
        for side, key in (("H", "home"), ("D", "draw"), ("A", "away")):
            entry = as_dict(line.get(key))
            prices[side] = american_to_decimal(as_dict(entry.get(which)).get("odds"))
        if all(price > 1 for price in prices.values()):
            return prices
        if which == "close":  # older payloads only carry the current moneyLine
            odds = as_dict(odds)
            prices = {
                "H": american_to_decimal(as_dict(odds.get("homeTeamOdds")).get("moneyLine")),
                "D": american_to_decimal(as_dict(odds.get("drawOdds")).get("moneyLine")),
                "A": american_to_decimal(as_dict(odds.get("awayTeamOdds")).get("moneyLine")),
            }
            if all(price > 1 for price in prices.values()):
                return prices
    return {}


def lineups(summary_data: dict) -> dict:
    """{'home': {...}, 'away': {...}} with team name, formation, starters and
    bench as lists of (player_id, name, position). Empty until announced."""
    teams = {}
    for team in summary_data.get("rosters") or []:
        team = as_dict(team)
        side = team.get("homeAway")
        roster = [as_dict(p) for p in team.get("roster") or []]
        if side not in ("home", "away") or not roster:
            continue

        def player(entry):
            athlete = as_dict(entry.get("athlete"))
            return (
                str(athlete.get("id", "")),
                athlete.get("displayName", ""),
                as_dict(entry.get("position")).get("abbreviation", ""),
            )

        starters = [player(p) for p in roster if str(p.get("starter")).lower() == "true"]
        bench = [player(p) for p in roster if str(p.get("starter")).lower() != "true"]
        teams[side] = {
            "team": as_dict(team.get("team")).get("displayName", ""),
            "formation": team.get("formation") or "",
            "starters": starters,
            "bench": bench,
        }
    if len(teams) == 2 and all(len(t["starters"]) >= 11 for t in teams.values()):
        return teams
    return {}
