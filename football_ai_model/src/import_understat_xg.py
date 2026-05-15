"""
Scrape match-level xG data from understat.com and save to
  football_ai_model/data/external/understat_xg.csv

Columns: season, date, league_code, home_team, away_team,
         home_goals, away_goals, home_xg, away_xg

Covers seasons 2014/15 (understat's first) to current.
Rate-limited to avoid hammering the server.
"""

import csv
import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

EXTERNAL_DIR = Path(__file__).resolve().parents[1] / "data" / "external"
OUTPUT_PATH = EXTERNAL_DIR / "understat_xg.csv"

# understat league name → our internal league code
UNDERSTAT_LEAGUES = {
    "EPL":        "E0",
    "Bundesliga": "D1",
    "Serie_A":    "I1",
    "La_liga":    "SP1",
    "Ligue_1":    "F1",
}

# understat uses start year: 2014 → 2014/15 season
START_YEAR = 2014
END_YEAR   = 2025  # inclusive

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; football-model-bot/1.0)"}
DELAY = 1.5   # seconds between requests


# ── Team name normalisation ──────────────────────────────────────────────────

def _norm(name: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_ = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_.lower())

# Aliases: understat canonical name → possible football-data names (all normalised)
_ALIASES: dict[str, list[str]] = {
    "manchesterunited":     ["manunited", "manutd", "manchesterutd"],
    "manchestercity":       ["mancity"],
    "wolverhamptonwanderers": ["wolves", "wolverhampton"],
    "nottinghamforest":     ["nottmforest"],
    "tottenhamhotspur":     ["tottenham", "spurs"],
    "brightonandhovealbion": ["brighton"],
    "westbromwichalbion":   ["westbrom"],
    "queensparkrangers":    ["qpr"],
    "sheffieldunited":      ["sheffieldutd"],
    "newcastleunited":      ["newcastle"],
    "leicestercity":        ["leicester"],
    "norwichcity":          ["norwich"],
    "cardiffcity":          ["cardiff"],
    "hullcity":             ["hull"],
    "stortonfc":            ["stoke"],
    "stokecity":            ["stoke"],
    "swanseacity":          ["swansea"],
    "middlesbrough":        ["middlesbrough"],
    "atleticomadrid":       ["atlmadrid", "atletico"],
    "realmadriddcf":        ["realmadrid", "real"],
    "realsociedad":         ["sociedad"],
    "realbetis":            ["betis"],
    "athleticbilbao":       ["athbilbao", "athleticbilbao"],
    "rcdespanyol":          ["espanyol"],
    "rcdmallorca":          ["mallorca"],
    "deportivoalaves":      ["alaves"],
    "ucsampdoria":          ["sampdoria"],
    "hellasveronafc":       ["verona"],
    "spalmpalermo":         ["palermo"],
    "uscitta palermo":      ["palermo"],
    "internazioanle":       ["inter", "intermilan"],
    "internazionale":       ["inter", "intermilan"],
    "acmilan":              ["milan"],
    "parissaintgermain":    ["parisg", "psg"],
    "borussiadortmund":     ["dortmund"],
    "borussiamonchengladbach": ["mgladbach", "gladbach"],
    "rbleipzig":            ["leipzig"],
    "fccologne":            ["koln", "cologne"],
    "bayerbayer04leverkusen": ["leverkusen"],
    "bayer04leverkusen":    ["leverkusen"],
    "eintrachfrankfurt":    ["eintracht", "frankfurt"],
    "eintrachtfrankfurt":   ["eintracht", "frankfurt"],
    "vfbstuttgart":         ["stuttgart"],
    "vflwolfsburg":         ["wolfsburg"],
    "scfreiburg":           ["freiburg"],
    "unionberlin":          ["unionberlin"],
    "fcaugsburg":           ["augsburg"],
    "tsghofffenheim":       ["hoffenheim"],
    "tsg1899hoffenheim":    ["hoffenheim"],
    "svwerderbremen":       ["werder", "werderbremen"],
    "fcschalke04":          ["schalke"],
    "hamburgsv":            ["hamburg"],
}

# Build reverse lookup: normalised alias → canonical normalised name
_ALIAS_LOOKUP: dict[str, str] = {}
for canon, aliases in _ALIASES.items():
    _ALIAS_LOOKUP[canon] = canon
    for a in aliases:
        _ALIAS_LOOKUP[_norm(a)] = canon


def normalise_team(name: str) -> str:
    """Return a stable normalised key for a team name."""
    n = _norm(name)
    return _ALIAS_LOOKUP.get(n, n)


# ── Understat fetch ──────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str:
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def _parse_dates_data(html: str) -> list[dict]:
    """Extract the datesData JSON from understat page HTML."""
    match = re.search(
        r"var\s+datesData\s*=\s*JSON\.parse\(decodeURIComponent\('([^']+)'\)\)",
        html,
    )
    if not match:
        return []
    return json.loads(unquote(match.group(1)))


def fetch_league_season(league_name: str, year: int) -> list[dict]:
    url = f"https://understat.com/league/{league_name}/{year}"
    try:
        html = _fetch_html(url)
    except (HTTPError, URLError) as exc:
        print(f"    ✗ {url}: {exc}")
        return []
    rows = _parse_dates_data(html)
    return [r for r in rows if r.get("isResult")]


def season_label(year: int) -> str:
    """2014 → '1415', 2024 → '2425'"""
    return f"{str(year)[2:]}{str(year + 1)[2:]}"


# ── Main ─────────────────────────────────────────────────────────────────────

def main(force_refresh: bool = False) -> None:
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)

    existing: set[tuple] = set()
    if OUTPUT_PATH.exists() and not force_refresh:
        with OUTPUT_PATH.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                existing.add((row["season"], row["league_code"],
                               row["home_team"], row["away_team"]))
        print(f"Loaded {len(existing)} existing records — will only fetch new seasons")

    FIELDNAMES = ["season", "date", "league_code",
                  "home_team", "away_team",
                  "home_goals", "away_goals", "home_xg", "away_xg",
                  "home_team_norm", "away_team_norm"]

    new_rows: list[dict] = []

    for league_name, league_code in UNDERSTAT_LEAGUES.items():
        for year in range(START_YEAR, END_YEAR + 1):
            season = season_label(year)
            print(f"  {league_code} {season} ...", end=" ", flush=True)

            first_key = (season, league_code)
            if not force_refresh and any(e[0] == season and e[1] == league_code for e in existing):
                print("cached")
                continue

            rows = fetch_league_season(league_name, year)
            count = 0
            for r in rows:
                try:
                    home_team  = r["h"]["title"]
                    away_team  = r["a"]["title"]
                    home_goals = int(r["goals"]["h"])
                    away_goals = int(r["goals"]["a"])
                    home_xg    = float(r["xG"]["h"])
                    away_xg    = float(r["xG"]["a"])
                    date_str   = r["datetime"][:10]
                except (KeyError, ValueError, TypeError):
                    continue

                new_rows.append({
                    "season":        season,
                    "date":          date_str,
                    "league_code":   league_code,
                    "home_team":     home_team,
                    "away_team":     away_team,
                    "home_goals":    home_goals,
                    "away_goals":    away_goals,
                    "home_xg":       round(home_xg, 4),
                    "away_xg":       round(away_xg, 4),
                    "home_team_norm": normalise_team(home_team),
                    "away_team_norm": normalise_team(away_team),
                })
                count += 1

            print(f"{count} matches")
            time.sleep(DELAY)

    if not new_rows:
        print("Nothing new to write.")
        return

    mode = "a" if OUTPUT_PATH.exists() and not force_refresh else "w"
    with OUTPUT_PATH.open(mode, newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if mode == "w":
            writer.writeheader()
        writer.writerows(new_rows)

    print(f"\nWrote {len(new_rows)} rows → {OUTPUT_PATH}")


if __name__ == "__main__":
    import sys
    force = "--refresh" in sys.argv
    main(force_refresh=force)
