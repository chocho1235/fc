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
}

DEFAULT_LEAGUE_CODES = ["E0", "SP1"]


def league_name(code):
    return LEAGUES.get(code, {}).get("name", code)


def league_feature(code):
    return LEAGUES.get(code, {}).get("feature", 0.0)
