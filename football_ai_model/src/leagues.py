LEAGUES = {
    "E0": {
        "name": "Premier League",
        "country": "England",
        "feature": 0.0,
    },
    "SP1": {
        "name": "La Liga",
        "country": "Spain",
        "feature": 1.0,
    },
    "D1": {
        "name": "Bundesliga",
        "country": "Germany",
        "feature": 2.0,
    },
    "I1": {
        "name": "Serie A",
        "country": "Italy",
        "feature": 3.0,
    },
    "F1": {
        "name": "Ligue 1",
        "country": "France",
        "feature": 4.0,
    },
}

DEFAULT_LEAGUE_CODES = ["E0", "SP1", "D1", "I1", "F1"]

# Leagues where we place bets; others contribute training data only
BET_LEAGUE_CODES = ["E0", "SP1", "D1", "I1", "F1"]


def league_name(code):
    return LEAGUES.get(code, {}).get("name", code)


def league_feature(code):
    return LEAGUES.get(code, {}).get("feature", 0.0)
