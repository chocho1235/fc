"""
Map team names from any source (ESPN, The Odds API, football-data fixtures)
onto the spelling football-data.co.uk uses in its results files, which is what
the model's form, Elo and table history are keyed by.
"""

import csv
import difflib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from leagues import LEAGUES

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"

# Words that carry no identity ("FC", "City", ...). Removed before fuzzy matching.
GENERIC_WORDS = {
    "fc", "afc", "cf", "sc", "ac", "as", "us", "ss", "ssc", "sv", "vfb", "vfl", "tsg", "fsv", "bsc",
    "club", "calcio", "cd", "ud", "sd", "rcd", "rc", "ca", "aj", "ogc", "losc", "sco", "ea", "es",
    "town", "city", "united", "utd", "county", "athletic", "rovers", "rvs", "wanderers", "albion",
    "hotspur", "argyle", "alexandra", "stanley", "de", "la", "le", "1", "1846", "05", "07", "96", "98",
    "foot", "football", "stade", "olympique", "real", "the",
}

# Normalised external name -> football-data spellings (first one present wins).
ALIASES = {
    # England
    "manchester united": ["Man United"], "man utd": ["Man United"], "manchester utd": ["Man United"],
    "manchester city": ["Man City"],
    "wolverhampton wanderers": ["Wolves"], "wolverhampton": ["Wolves"],
    "brighton and hove albion": ["Brighton"],
    "nottingham forest": ["Nott'm Forest"], "nottm forest": ["Nott'm Forest"],
    "tottenham hotspur": ["Tottenham"], "spurs": ["Tottenham"],
    "west ham united": ["West Ham"],
    "west bromwich albion": ["West Brom"],
    "queens park rangers": ["QPR"],
    "sheffield united": ["Sheffield United"], "sheffield utd": ["Sheffield United"],
    "sheffield wednesday": ["Sheffield Weds", "Sheffield Wed"],
    "peterborough united": ["Peterboro"], "peterborough": ["Peterboro"],
    "mk dons": ["Milton Keynes Dons"], "milton keynes dons": ["Milton Keynes Dons"],
    "bristol rovers": ["Bristol Rvs"], "bristol city": ["Bristol City"],
    "preston north end": ["Preston"],
    "crawley town": ["Crawley Town"], "fleetwood town": ["Fleetwood Town"], "newport county": ["Newport County"],
    "notts county": ["Notts County"], "leyton orient": ["Leyton Orient"],
    "bradford city": ["Bradford", "Bradford City"],
    "afc wimbledon": ["AFC Wimbledon"],
    # Scotland
    "heart of midlothian": ["Hearts"],
    "dundee united": ["Dundee United", "Dundee Utd"], "dundee utd": ["Dundee United", "Dundee Utd"],
    "dundee": ["Dundee", "Dundee FC"], "dundee fc": ["Dundee", "Dundee FC"],
    "st mirren": ["St Mirren", "St. Mirren"], "st johnstone": ["St Johnstone"],
    "inverness caledonian thistle": ["Inverness C", "Inverness"],
    "queens park": ["Queen's Park", "Queens Park"],
    # Spain
    "athletic club": ["Ath Bilbao"], "athletic bilbao": ["Ath Bilbao"],
    "atletico madrid": ["Ath Madrid"], "atl madrid": ["Ath Madrid"], "club atletico de madrid": ["Ath Madrid"],
    "real madrid": ["Real Madrid"],
    "real betis": ["Betis"], "celta vigo": ["Celta"], "rc celta": ["Celta"],
    "rayo vallecano": ["Vallecano"], "real sociedad": ["Sociedad"], "real sociedad b": ["Sociedad B"],
    "espanyol": ["Espanol"], "rcd espanyol": ["Espanol"],
    "deportivo alaves": ["Alaves"],
    "deportivo la coruna": ["La Coruna"], "deportivo": ["La Coruna"], "dep a coruna": ["La Coruna"], "rc deportivo": ["La Coruna"],
    "racing santander": ["Santander"], "racing de santander": ["Santander"],
    "real oviedo": ["Oviedo"], "real valladolid": ["Valladolid"], "real zaragoza": ["Zaragoza"],
    "sporting gijon": ["Sp Gijon"], "sporting de gijon": ["Sp Gijon"],
    "ud las palmas": ["Las Palmas"], "las palmas": ["Las Palmas"],
    "celta vigo b": ["Celta B"], "celta b": ["Celta B"],
    "cultural leonesa": ["Cultural Leonesa"],
    # Germany
    "borussia dortmund": ["Dortmund"], "bayer leverkusen": ["Leverkusen"],
    "eintracht frankfurt": ["Ein Frankfurt"],
    "borussia monchengladbach": ["M'gladbach"], "monchengladbach": ["M'gladbach"], "gladbach": ["M'gladbach"],
    "1 fc koln": ["FC Koln"], "fc koln": ["FC Koln"], "koln": ["FC Koln"], "fc cologne": ["FC Koln"], "cologne": ["FC Koln"],
    "hamburg sv": ["Hamburg"], "hamburger sv": ["Hamburg"],
    "st pauli": ["St Pauli"], "fc st pauli": ["St Pauli"],
    "hertha berlin": ["Hertha"], "hertha bsc": ["Hertha"],
    "union berlin": ["Union Berlin"], "1 fc union berlin": ["Union Berlin"],
    "eintracht braunschweig": ["Braunschweig"], "arminia bielefeld": ["Bielefeld"],
    "dynamo dresden": ["Dresden"], "energie cottbus": ["Cottbus"], "hannover 96": ["Hannover"],
    "karlsruher sc": ["Karlsruhe"], "greuther furth": ["Greuther Furth"], "spvgg greuther furth": ["Greuther Furth"],
    "preussen munster": ["Preußen Münster"], "sc preussen munster": ["Preußen Münster"],
    "fortuna dusseldorf": ["Fortuna Dusseldorf"], "schalke 04": ["Schalke 04"], "fc schalke 04": ["Schalke 04"],
    "werder bremen": ["Werder Bremen"], "sv werder bremen": ["Werder Bremen"], "rb leipzig": ["RB Leipzig"],
    "bayern munich": ["Bayern Munich"], "fc bayern munchen": ["Bayern Munich"], "bayern munchen": ["Bayern Munich"],
    # Italy
    "internazionale": ["Inter"], "inter milan": ["Inter"], "fc internazionale milano": ["Inter"],
    "ac milan": ["Milan"], "hellas verona": ["Verona"],
    "juve stabia": ["Juve Stabia"], "virtus entella": ["Virtus Entella"],
    # France
    "paris saint germain": ["Paris SG"], "paris sg": ["Paris SG"], "psg": ["Paris SG"],
    "paris fc": ["Paris FC"],
    "olympique lyonnais": ["Lyon"], "olympique de marseille": ["Marseille"],
    "stade rennais": ["Rennes"], "stade brestois": ["Brest"], "stade brestois 29": ["Brest"],
    "stade de reims": ["Reims"], "saint etienne": ["St Etienne"], "as saint etienne": ["St Etienne"],
    "red star": ["Red Star"], "red star fc": ["Red Star"], "pau": ["Pau FC"], "pau fc": ["Pau FC"],
    "stade lavallois": ["Laval"], "le havre ac": ["Le Havre"], "le havre": ["Le Havre"], "le mans": ["Le Mans"],
}


def normalise(name: str) -> str:
    name = unicodedata.normalize("NFKD", name.replace("ß", "ss")).encode("ascii", "ignore").decode()
    name = name.lower().replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", name).strip()


def core(name: str) -> str:
    return " ".join(word for word in normalise(name).split() if word not in GENERIC_WORDS)


@lru_cache(maxsize=1)
def known_teams() -> dict:
    """{league_code: set(names)} from the results files of the last three seasons."""
    seasons = sorted({path.stem.split("_", 1)[0] for path in RAW_DIR.glob("*_*.csv")})[-3:]
    teams: dict = {}
    for path in RAW_DIR.glob("*_*.csv"):
        season, league_code = path.stem.split("_", 1)
        if season not in seasons or league_code not in LEAGUES:
            continue
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
            for row in csv.DictReader(handle):
                for column in ("HomeTeam", "AwayTeam"):
                    if row.get(column):
                        teams.setdefault(league_code, set()).add(row[column].strip())
    return teams


def _candidates(league_code: str) -> list[list[str]]:
    """Same league first, then the rest of that country, then everywhere."""
    teams = known_teams()
    country = LEAGUES.get(league_code, {}).get("country")
    same_league = sorted(teams.get(league_code, set()))
    same_country = sorted({t for code, names in teams.items() if LEAGUES[code]["country"] == country for t in names})
    everywhere = sorted({t for names in teams.values() for t in names})
    return [same_league, same_country, everywhere]


def _unique_match(name: str, pool: list[str]) -> str | None:
    norm, name_core = normalise(name), core(name)
    for test in (
        lambda t: normalise(t) == norm,
        lambda t: name_core and core(t) == name_core,
    ):
        hits = [t for t in pool if test(t)]
        if len(hits) == 1:
            return hits[0]
    # football-data abbreviates ("Peterboro", "Nott'm Forest"): allow prefixes
    if len(name_core) >= 4:
        hits = [t for t in pool if core(t) and len(core(t)) >= 4
                and (name_core.startswith(core(t)) or core(t).startswith(name_core))]
        if len(hits) == 1:
            return hits[0]
    scored = sorted(((difflib.SequenceMatcher(None, name_core, core(t)).ratio(), t) for t in pool if core(t)), reverse=True)
    if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1):
        return scored[0][1]
    return None


@lru_cache(maxsize=None)
def canonical_team(name: str, league_code: str) -> str | None:
    """football-data spelling for `name`, or None if we can't be sure."""
    if not name:
        return None
    pools = _candidates(league_code)
    everyone = set(pools[-1])
    if name in everyone:
        return name
    for alias in ALIASES.get(normalise(name), []):
        if alias in everyone:
            return alias
    if ALIASES.get(normalise(name)):
        # Known club with no history yet (e.g. newly added league): trust the alias.
        return ALIASES[normalise(name)][0]
    for pool in pools:
        match = _unique_match(name, pool)
        if match:
            return match
    return None
