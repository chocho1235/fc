"""
Build data/raw/fixtures.csv: upcoming matches plus the best odds we can find.

Sources (all merged, none required):
  1. ESPN's public scoreboard API: free, no key. Fixtures for every league and,
     where ESPN shows them, one bookmaker's 1X2 prices.
  2. football-data.co.uk fixtures.csv: free, no key. Only lists a few days at
     a time but has Pinnacle, market-average and best-price odds.
  3. The Odds API: only if the ODDS_API_KEY secret is set. Prices from many
     bookmakers. Called at most every 20 hours to stay inside the free quota.

Team names from every source are mapped to football-data's results spelling
(see team_names.py). For each match the "market" price used by the model is
the sharpest one available (Pinnacle, then market average, then any single
bookmaker) and "Best" is the highest price on offer anywhere.
"""

import csv
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from download_data import decode_csv
from leagues import LEAGUES
from team_names import canonical_team

MODEL_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = MODEL_ROOT / "data" / "raw"
EXTERNAL_DIR = MODEL_ROOT / "data" / "external"
OUTPUT_PATH = RAW_DIR / "fixtures.csv"
ODDS_API_CACHE_PATH = EXTERNAL_DIR / "odds_api_cache.json"
SOURCES_REPORT_PATH = EXTERNAL_DIR / "fixture_sources.json"

FOOTBALL_DATA_URL = "https://www.football-data.co.uk/fixtures.csv"
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={start}-{end}&limit=300"
ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/{sport}/odds?regions=eu&markets=h2h&oddsFormat=decimal&apiKey={key}"
ODDS_API_MAX_AGE_HOURS = 20
DAYS_AHEAD = 10
UK = ZoneInfo("Europe/London")
USER_AGENT = "Mozilla/5.0 (compatible; football-model/1.0)"

OUTPUT_FIELDS = [
    "Div", "Date", "Time", "HomeTeam", "AwayTeam",
    "PSH", "PSD", "PSA", "AvgH", "AvgD", "AvgA", "B365H", "B365D", "B365A",
    "MktH", "MktD", "MktA", "MaxH", "MaxD", "MaxA", "BestH", "BestD", "BestA",
    "Avg>2.5", "Avg<2.5", "B365>2.5", "B365<2.5", "Max>2.5", "Max<2.5",
    "Sources",
]


def fetch(url: str, retries: int = 3, timeout: int = 60) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError:
            raise
        except OSError as exc:
            if attempt == retries:
                raise
            print(f"  {url.split('?')[0]} failed ({exc}), retrying in {attempt * 15}s")
            time.sleep(attempt * 15)
    raise RuntimeError("unreachable")


def to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def american_to_decimal(value) -> float:
    if value in (None, "", "EVEN", "even"):
        return 2.0 if str(value).lower() == "even" else 0.0
    try:
        number = float(str(value).replace("+", ""))
    except ValueError:
        return 0.0
    if number >= 100:
        return round(1 + number / 100, 3)
    if number <= -100:
        return round(1 + 100 / -number, 3)
    return 0.0


class Fixtures:
    """Collects matches from all sources, keyed on (league, date, home, away)."""

    def __init__(self):
        self.matches: dict = {}
        self.unmatched: dict = {}
        self.counts: dict = {}

    def add(self, source: str, league_code: str, kickoff_uk: datetime, home_raw: str, away_raw: str, prices: dict):
        home = canonical_team(home_raw, league_code)
        away = canonical_team(away_raw, league_code)
        if not home or not away:
            for raw, mapped in ((home_raw, home), (away_raw, away)):
                if not mapped:
                    self.unmatched.setdefault(source, set()).add(f"{league_code}: {raw}")
            return
        key = (league_code, kickoff_uk.date(), home, away)
        match = self.matches.setdefault(key, {
            "Div": league_code,
            "Date": kickoff_uk.strftime("%d/%m/%Y"),
            "Time": kickoff_uk.strftime("%H:%M"),
            "HomeTeam": home,
            "AwayTeam": away,
            "Sources": set(),
        })
        match["Sources"].add(source)
        for column, price in prices.items():
            price = to_float(price)
            if price > 1 and not to_float(match.get(column)) > 1:
                match[column] = price
        self.counts[source] = self.counts.get(source, 0) + 1

    def rows(self) -> list[dict]:
        rows = []
        for match in sorted(self.matches.values(), key=lambda m: (datetime.strptime(m["Date"], "%d/%m/%Y"), m["Time"], m["Div"])):
            row = dict(match)
            for side in "HDA":
                prices = [to_float(row.get(prefix + side)) for prefix in ("Max", "PS", "Avg", "B365", "Mkt", "Best")]
                best = max(prices)
                row["Best" + side] = best if best > 1 else ""
            row["Sources"] = "+".join(sorted(row["Sources"]))
            rows.append(row)
        return rows


def from_football_data(fixtures: Fixtures):
    print(f"football-data: downloading {FOOTBALL_DATA_URL}")
    content = decode_csv(fetch(FOOTBALL_DATA_URL))
    reader = csv.DictReader(content.splitlines())
    if not reader.fieldnames or "Div" not in reader.fieldnames:
        raise ValueError(f"unexpected fixtures CSV header: {content[:120]!r}")
    for row in reader:
        league_code = row.get("Div")
        if league_code not in LEAGUES or not row.get("Date"):
            continue
        day = datetime.strptime(row["Date"], "%d/%m/%Y")
        hour, minute = (row.get("Time") or "23:59").split(":")
        kickoff = day.replace(hour=int(hour), minute=int(minute), tzinfo=UK)
        prices = {column: row.get(column) for column in OUTPUT_FIELDS if column in row and column not in ("Div", "Date", "Time", "HomeTeam", "AwayTeam")}
        fixtures.add("football-data", league_code, kickoff, row.get("HomeTeam", ""), row.get("AwayTeam", ""), prices)


def espn_prices(competition: dict) -> dict:
    """1X2 decimal prices from an ESPN competition, if it carries any odds."""
    for odds in competition.get("odds") or []:
        home = american_to_decimal((odds.get("homeTeamOdds") or {}).get("moneyLine"))
        away = american_to_decimal((odds.get("awayTeamOdds") or {}).get("moneyLine"))
        draw = american_to_decimal((odds.get("drawOdds") or {}).get("moneyLine"))
        moneyline = odds.get("moneyline") or {}
        if not (home and away and draw) and moneyline:
            def line(side):
                entry = moneyline.get(side) or {}
                current = entry.get("close") or entry.get("current") or entry.get("open") or {}
                return american_to_decimal(current.get("odds"))
            home, away, draw = line("home"), line("away"), line("draw")
        if home > 1 and away > 1 and draw > 1:
            return {"MktH": home, "MktD": draw, "MktA": away}
    return {}


def from_espn(fixtures: Fixtures):
    today = datetime.now(UK).date()
    start, end = today.strftime("%Y%m%d"), (today + timedelta(days=DAYS_AHEAD)).strftime("%Y%m%d")
    with_odds = 0
    for league_code, league in LEAGUES.items():
        url = ESPN_URL.format(slug=league["espn"], start=start, end=end)
        try:
            payload = json.loads(fetch(url, retries=2, timeout=30))
        except (OSError, ValueError) as exc:
            print(f"  ESPN {league_code}: skipped ({exc})")
            continue
        count = 0
        for event in payload.get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            state = ((competition.get("status") or event.get("status") or {}).get("type") or {}).get("state")
            if state and state != "pre":
                continue
            teams = {c.get("homeAway"): (c.get("team") or {}) for c in competition.get("competitors", [])}
            if "home" not in teams or "away" not in teams:
                continue
            kickoff = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(UK)
            prices = espn_prices(competition)
            with_odds += bool(prices)
            home = teams["home"].get("displayName") or teams["home"].get("name", "")
            away = teams["away"].get("displayName") or teams["away"].get("name", "")
            fixtures.add("espn", league_code, kickoff, home, away, prices)
            count += 1
        print(f"  ESPN {league_code}: {count} upcoming")
    print(f"ESPN: {with_odds} match(es) came with odds")


def odds_api_prices(league_code: str, events: list) -> list:
    """Pinnacle, average and best 1X2 prices for each Odds API event."""
    records = []
    for event in events:
        home_raw, away_raw = event.get("home_team", ""), event.get("away_team", "")
        by_side: dict = {"H": [], "D": [], "A": []}
        pinnacle = {}
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    side = "H" if outcome["name"] == home_raw else "A" if outcome["name"] == away_raw else "D"
                    by_side[side].append(float(outcome["price"]))
                    if bookmaker.get("key") == "pinnacle":
                        pinnacle[side] = float(outcome["price"])
        if not all(by_side.values()):
            continue
        prices = {f"Avg{side}": round(sum(p) / len(p), 3) for side, p in by_side.items()}
        prices.update({f"Max{side}": max(p) for side, p in by_side.items()})
        if len(pinnacle) == 3:
            prices.update({f"PS{side}": price for side, price in pinnacle.items()})
        records.append({"league_code": league_code, "commence_time": event["commence_time"],
                        "home_team": home_raw, "away_team": away_raw, "prices": prices})
    return records


def odds_api_records() -> list:
    """Odds API prices, cached so the free quota (500 requests a month) is enough."""
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key:
        print("Odds API: no ODDS_API_KEY secret, skipping")
        return []
    if ODDS_API_CACHE_PATH.exists():
        cache = json.loads(ODDS_API_CACHE_PATH.read_text(encoding="utf-8"))
        age = datetime.now(timezone.utc) - datetime.fromisoformat(cache["fetched_at"])
        if age < timedelta(hours=ODDS_API_MAX_AGE_HOURS):
            print(f"Odds API: using prices fetched {age.total_seconds() / 3600:.1f}h ago")
            return cache["records"]
    records = []
    for league_code, league in LEAGUES.items():
        try:
            events = json.loads(fetch(ODDS_API_URL.format(sport=league["odds_api"], key=key), retries=2, timeout=30))
        except (OSError, ValueError) as exc:
            print(f"  Odds API {league_code}: skipped ({exc})")
            continue
        records.extend(odds_api_prices(league_code, events))
        print(f"  Odds API {league_code}: {len(events)} event(s)")
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    ODDS_API_CACHE_PATH.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }, indent=1), encoding="utf-8")
    return records


def from_odds_api(fixtures: Fixtures):
    for record in odds_api_records():
        kickoff = datetime.fromisoformat(record["commence_time"].replace("Z", "+00:00")).astimezone(UK)
        fixtures.add("odds-api", record["league_code"], kickoff, record["home_team"], record["away_team"], record["prices"])


def main():
    fixtures = Fixtures()
    errors = []
    for name, source in (("football-data", from_football_data), ("espn", from_espn), ("odds-api", from_odds_api)):
        try:
            source(fixtures)
        except Exception as exc:  # one source failing must not stop the others
            print(f"{name}: failed ({exc})")
            errors.append(f"{name}: {exc}")

    rows = fixtures.rows()
    today = date.today()
    upcoming = [r for r in rows if datetime.strptime(r["Date"], "%d/%m/%Y").date() >= today]
    if not upcoming:
        raise SystemExit("No upcoming fixtures from any source: " + "; ".join(errors))

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(upcoming)

    with_market = sum(1 for r in upcoming if any(to_float(r.get(c + "H")) > 1 for c in ("PS", "Avg", "B365", "Mkt")))
    by_league: dict = {}
    for r in upcoming:
        by_league[r["Div"]] = by_league.get(r["Div"], 0) + 1
    report = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fixtures": len(upcoming),
        "fixtures_with_odds": with_market,
        "by_league": by_league,
        "rows_per_source": fixtures.counts,
        "errors": errors,
        "unmatched_team_names": {source: sorted(names) for source, names in fixtures.unmatched.items()},
    }
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    SOURCES_REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved {OUTPUT_PATH} with {len(upcoming)} fixture(s), {with_market} with odds")
    print(json.dumps({k: v for k, v in report.items() if k != "fetched_at"}, indent=2))


if __name__ == "__main__":
    main()
