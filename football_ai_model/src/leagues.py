# espn: league slug on ESPN's public scoreboard API (used for fixtures + odds)
# odds_api: sport key on The Odds API (only used if ODDS_API_KEY is set)
LEAGUES = {
    "E0": {"name": "Premier League", "country": "England", "feature": 0.0, "espn": "eng.1", "odds_api": "soccer_epl"},
    "SP1": {"name": "La Liga", "country": "Spain", "feature": 1.0, "espn": "esp.1", "odds_api": "soccer_spain_la_liga"},
    "D1": {"name": "Bundesliga", "country": "Germany", "feature": 2.0, "espn": "ger.1", "odds_api": "soccer_germany_bundesliga"},
    "I1": {"name": "Serie A", "country": "Italy", "feature": 3.0, "espn": "ita.1", "odds_api": "soccer_italy_serie_a"},
    "F1": {"name": "Ligue 1", "country": "France", "feature": 4.0, "espn": "fra.1", "odds_api": "soccer_france_ligue_one"},
    # Second tiers: bookmakers put less work into these, so prices are softer.
    "E1": {"name": "Championship", "country": "England", "feature": 5.0, "espn": "eng.2", "odds_api": "soccer_efl_champ"},
    "E2": {"name": "League One", "country": "England", "feature": 6.0, "espn": "eng.3", "odds_api": "soccer_england_league1"},
    "E3": {"name": "League Two", "country": "England", "feature": 7.0, "espn": "eng.4", "odds_api": "soccer_england_league2"},
    "SC0": {"name": "Scottish Premiership", "country": "Scotland", "feature": 8.0, "espn": "sco.1", "odds_api": "soccer_spl"},
    "SP2": {"name": "La Liga 2", "country": "Spain", "feature": 9.0, "espn": "esp.2", "odds_api": "soccer_spain_segunda_division"},
    "D2": {"name": "2. Bundesliga", "country": "Germany", "feature": 10.0, "espn": "ger.2", "odds_api": "soccer_germany_bundesliga2"},
    "I2": {"name": "Serie B", "country": "Italy", "feature": 11.0, "espn": "ita.2", "odds_api": "soccer_italy_serie_b"},
    "F2": {"name": "Ligue 2", "country": "France", "feature": 12.0, "espn": "fra.2", "odds_api": "soccer_france_ligue_two"},
}

DEFAULT_LEAGUE_CODES = list(LEAGUES)

# Leagues where we place bets; others contribute training data only
BET_LEAGUE_CODES = list(LEAGUES)


def league_name(code):
    return LEAGUES.get(code, {}).get("name", code)


def league_feature(code):
    return LEAGUES.get(code, {}).get("feature", 0.0)
