"""
Pull live bookmaker odds from The Odds API (https://the-odds-api.com).

Requires env var ODDS_API_KEY. Writes to:
  football_ai_model/data/external/live_odds.json

Then import_live_odds_to_upcoming() patches upcoming_predictions.csv with
current bookmaker odds (home/draw/away and over-2.5 lines) so the model uses
real market prices instead of the stale football-data.co.uk snapshot.

Free tier: 500 credits/month.  Each (sport × market) request costs 1 credit.
We query h2h + totals per sport = 2 credits per sport, 10 credits for 5 leagues
per run.  At 6-hourly = 4 runs/day × 10 = 40 credits/day — comfortably within
the free quota of ~16 credits/day.  Keep to h2h only if quota is tight.
"""

import csv
import json
import os
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

MODEL_ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_DIR = MODEL_ROOT / "data" / "external"
UPCOMING_PATH = MODEL_ROOT / "data" / "processed" / "upcoming_predictions.csv"
LIVE_ODDS_PATH = EXTERNAL_DIR / "live_odds.json"
CUP_FIXTURES_PATH = EXTERNAL_DIR / "cup_fixtures.json"

BASE_URL = "https://api.the-odds-api.com/v4"

# Map our internal league codes to The Odds API sport keys
LEAGUE_SPORT_MAP = {
    "E0": "soccer_epl",
    "D1": "soccer_germany_bundesliga",
    "I1": "soccer_italy_serie_a",
    "F1": "soccer_france_ligue_one",
    "SP1": "soccer_spain_la_liga",
}

# Cup competitions to also fetch — mapped to (league_code, display_name)
CUP_SPORT_MAP = {
    "soccer_england_league_cup":     ("CUP", "Carabao Cup"),
    "soccer_england_fa_cup":         ("CUP", "FA Cup"),
    "soccer_germany_cup":            ("CUP", "DFB Pokal"),
    "soccer_spain_copa_del_rey":     ("CUP", "Copa del Rey"),
    "soccer_italy_coppa_italia":     ("CUP", "Coppa Italia"),
    "soccer_france_coupe_de_france": ("CUP", "Coupe de France"),
}

# Map The Odds API team names → football-data.co.uk team names
# Add entries here whenever a cup team name doesn't match automatically
_CUP_TEAM_ALIASES = {
    "manchester city": "Man City",
    "manchester united": "Man United",
    "chelsea fc": "Chelsea",
    "arsenal fc": "Arsenal",
    "liverpool fc": "Liverpool",
    "tottenham hotspur": "Tottenham",
    "newcastle united": "Newcastle",
    "west ham united": "West Ham",
    "aston villa": "Aston Villa",
    "nottingham forest": "Nott'm Forest",
    "wolverhampton wanderers": "Wolves",
    "brighton & hove albion": "Brighton",
    "brighton and hove albion": "Brighton",
    "crystal palace": "Crystal Palace",
    "brentford fc": "Brentford",
    "leicester city": "Leicester",
    "leeds united": "Leeds",
    "everton fc": "Everton",
    "fulham fc": "Fulham",
    "burnley fc": "Burnley",
    "sheffield united": "Sheffield United",
    "luton town": "Luton",
    "bournemouth": "Bournemouth",
    "ipswich town": "Ipswich",
    "sunderland afc": "Sunderland",
    "southampton fc": "Southampton",
    # German
    "bayer leverkusen": "Leverkusen",
    "borussia dortmund": "Dortmund",
    "rb leipzig": "RB Leipzig",
    "eintracht frankfurt": "Ein Frankfurt",
    "vfb stuttgart": "Stuttgart",
    "sc freiburg": "Freiburg",
    "union berlin": "Union Berlin",
    "vfl wolfsburg": "Wolfsburg",
    "fc augsburg": "Augsburg",
    "1. fc heidenheim": "Heidenheim",
    "1. fsv mainz 05": "Mainz",
    "hamburger sv": "Hamburg",
    "fc koln": "FC Koln",
    "1. fc cologne": "FC Koln",
    # Spanish
    "atletico madrid": "Ath Madrid",
    "real madrid": "Real Madrid",
    "fc barcelona": "Barcelona",
    "real sociedad": "Sociedad",
    "athletic bilbao": "Ath Bilbao",
    "real betis": "Betis",
    "villarreal cf": "Villarreal",
    # Italian
    "ac milan": "Milan",
    "inter milan": "Inter",
    "juventus fc": "Juventus",
    "as roma": "Roma",
    "ss lazio": "Lazio",
    "ssc napoli": "Napoli",
    "atalanta bc": "Atalanta",
    "acf fiorentina": "Fiorentina",
    # French
    "paris saint-germain": "Paris SG",
    "olympique de marseille": "Marseille",
    "olympique lyonnais": "Lyon",
    "as monaco": "Monaco",
    "stade rennais fc": "Rennes",
    "lille osc": "Lille",
    "rc lens": "Lens",
    "ogc nice": "Nice",
}

PREFERRED_BOOKMAKERS = ["bet365", "betfair", "williamhill", "paddypower", "unibet"]


def _get(path: str, params: dict) -> dict | list:
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        raise EnvironmentError("ODDS_API_KEY environment variable not set")
    qs = "&".join(f"{k}={v}" for k, v in {**params, "apiKey": api_key}.items())
    url = f"{BASE_URL}{path}?{qs}"
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=20) as resp:
            remaining = resp.headers.get("x-requests-remaining", "?")
            print(f"  Odds API credits remaining: {remaining}")
            return json.loads(resp.read())
    except HTTPError as exc:
        print(f"  Odds API HTTP {exc.code}: {exc.reason}")
        raise
    except URLError as exc:
        print(f"  Odds API connection error: {exc.reason}")
        raise


def _normalise(name: str) -> str:
    """Lower-case, strip accents and punctuation for fuzzy team name matching."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_ = nfkd.encode("ascii", "ignore").decode()
    return "".join(c for c in ascii_.lower() if c.isalnum() or c == " ").strip()


def _best_bookmaker_odds(bookmakers: list, market_key: str) -> dict:
    """Pick the best available odds across preferred bookmakers for a market."""
    best: dict[str, float] = {}
    # Try preferred bookmakers first, then fall back to any available
    ordered = sorted(bookmakers, key=lambda b: (
        PREFERRED_BOOKMAKERS.index(b["key"]) if b["key"] in PREFERRED_BOOKMAKERS else 99
    ))
    for bm in ordered:
        for mkt in bm.get("markets", []):
            if mkt["key"] != market_key:
                continue
            for outcome in mkt["outcomes"]:
                name = outcome["name"]
                price = float(outcome["price"])
                if name not in best or price > best[name]:
                    best[name] = price
        if best:
            break  # use the first (preferred) bookmaker that has the market
    return best


def fetch_odds_for_sport(sport_key: str) -> list[dict]:
    """Fetch H2H + totals odds for all upcoming fixtures in a sport."""
    now = datetime.now(timezone.utc)
    commence_to = (now + timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"  Fetching {sport_key} ...")
    events = _get(f"/sports/{sport_key}/odds", {
        "regions": "uk",
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
        "commenceTimeTo": commence_to,
    })
    results = []
    for event in events:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        commence = event.get("commence_time", "")
        h2h = _best_bookmaker_odds(event.get("bookmakers", []), "h2h")
        totals = _best_bookmaker_odds(event.get("bookmakers", []), "totals")
        if not h2h:
            continue
        home_odds = h2h.get(home, 0.0)
        away_odds = h2h.get(away, 0.0)
        draw_odds = h2h.get("Draw", 0.0)
        over_25 = next((o["price"] for o in _raw_totals(event, 2.5, "Over")), 0.0)
        results.append({
            "sport_key": sport_key,
            "home_team": home,
            "away_team": away,
            "home_norm": _normalise(home),
            "away_norm": _normalise(away),
            "commence_time": commence,
            "home_odds": home_odds,
            "draw_odds": draw_odds,
            "away_odds": away_odds,
            "over_25_odds": over_25,
        })
    return results


def _raw_totals(event: dict, point: float, name: str) -> list:
    for bm in event.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt["key"] != "totals":
                continue
            return [o for o in mkt["outcomes"]
                    if o.get("name") == name and abs(float(o.get("point", 0)) - point) < 0.1]
    return []


def fetch_all_odds() -> list[dict]:
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("No ODDS_API_KEY set — skipping live odds import")
        return []
    all_odds = []
    for league_code, sport_key in LEAGUE_SPORT_MAP.items():
        try:
            odds = fetch_odds_for_sport(sport_key)
            for o in odds:
                o["league_code"] = league_code
            all_odds.extend(odds)
            print(f"  {league_code}: {len(odds)} fixtures with odds")
        except Exception as exc:
            print(f"  {league_code}: skipped ({exc})")
    return all_odds


def save_live_odds(odds: list[dict]) -> None:
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(odds),
        "odds": odds,
    }
    LIVE_ODDS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved {len(odds)} odds records → {LIVE_ODDS_PATH}")


def patch_upcoming_with_live_odds(odds: list[dict]) -> int:
    """Update upcoming_predictions.csv with current bookmaker odds."""
    if not UPCOMING_PATH.exists() or not odds:
        return 0

    # Build lookup: (home_norm, away_norm) → odds row
    lookup: dict[tuple, dict] = {}
    for o in odds:
        lookup[(o["home_norm"], o["away_norm"])] = o

    with UPCOMING_PATH.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    # Ensure required columns exist
    for col in ("home_bookmaker_odds", "draw_bookmaker_odds", "away_bookmaker_odds", "over_25_bookmaker_odds"):
        if col not in fieldnames:
            fieldnames.append(col)

    patched = 0
    for row in rows:
        home_norm = _normalise(row.get("home_team", ""))
        away_norm = _normalise(row.get("away_team", ""))
        match = lookup.get((home_norm, away_norm))
        if not match:
            continue
        row["home_bookmaker_odds"] = match["home_odds"] or row.get("home_bookmaker_odds", "")
        row["draw_bookmaker_odds"] = match["draw_odds"] or row.get("draw_bookmaker_odds", "")
        row["away_bookmaker_odds"] = match["away_odds"] or row.get("away_bookmaker_odds", "")
        if match.get("over_25_odds"):
            row["over_25_bookmaker_odds"] = match["over_25_odds"]
        patched += 1

    with UPCOMING_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Patched {patched} upcoming fixtures with live odds")
    return patched


def _resolve_cup_team(api_name: str) -> str:
    """Map an Odds API team name to our football-data.co.uk canonical name."""
    lower = api_name.lower().strip()
    if lower in _CUP_TEAM_ALIASES:
        return _CUP_TEAM_ALIASES[lower]
    # Strip common suffixes and try again
    for suffix in (" fc", " afc", " cf", " bc", " sc"):
        if lower.endswith(suffix):
            stripped = lower[: -len(suffix)].strip()
            if stripped in _CUP_TEAM_ALIASES:
                return _CUP_TEAM_ALIASES[stripped]
    # Fall back: title-case the API name as-is
    return api_name.title()


def fetch_cup_fixtures() -> list[dict]:
    """Fetch upcoming cup fixtures from The Odds API and save to cup_fixtures.json."""
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        return []
    cup_fixtures = []
    for sport_key, (league_code, cup_name) in CUP_SPORT_MAP.items():
        try:
            odds = fetch_odds_for_sport(sport_key)
            for o in odds:
                home = _resolve_cup_team(o["home_team"])
                away = _resolve_cup_team(o["away_team"])
                cup_fixtures.append({
                    "sport_key": sport_key,
                    "league_code": league_code,
                    "cup_name": cup_name,
                    "home_team": home,
                    "away_team": away,
                    "home_team_raw": o["home_team"],
                    "away_team_raw": o["away_team"],
                    "commence_time": o["commence_time"],
                    "home_odds": o["home_odds"],
                    "draw_odds": o["draw_odds"],
                    "away_odds": o["away_odds"],
                    "over_25_odds": o.get("over_25_odds", 0.0),
                })
            if odds:
                print(f"  {cup_name}: {len(odds)} fixture(s)")
        except Exception as exc:
            print(f"  {cup_name}: skipped ({exc})")

    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    CUP_FIXTURES_PATH.write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "fixtures": cup_fixtures,
    }, indent=2), encoding="utf-8")
    print(f"Saved {len(cup_fixtures)} cup fixture(s) → {CUP_FIXTURES_PATH}")
    return cup_fixtures


def main():
    print("Importing live bookmaker odds from The Odds API ...")
    odds = fetch_all_odds()
    if not odds:
        print("No odds fetched — nothing to patch")
        return
    save_live_odds(odds)
    patch_upcoming_with_live_odds(odds)
    print("Fetching cup fixtures ...")
    fetch_cup_fixtures()


if __name__ == "__main__":
    main()
