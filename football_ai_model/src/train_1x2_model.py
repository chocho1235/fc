import csv
import json
import math
import os
import random
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from leagues import DEFAULT_LEAGUE_CODES, league_feature, league_name


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
EXTERNAL_DIR = ROOT / "data" / "external"
PROCESSED_DIR = ROOT / "data" / "processed"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"

LABELS = ["H", "D", "A"]
ODDS_COLUMNS = [("AvgH", "AvgD", "AvgA"), ("B365H", "B365D", "B365A")]
BET_THRESHOLDS = [0.03, 0.05, 0.08, 0.10, 0.12, 0.15]
DEFAULT_BET_THRESHOLD = float(os.getenv("BET_THRESHOLD", "0.05") or "0.05")
TRAINING_WINDOW_SEASONS = int(os.getenv("TRAINING_WINDOW_SEASONS", "5") or "5")
ODDS_BANDS = [
    {"name": "short", "min_odds": 1.01, "max_odds": 1.75},
    {"name": "mid", "min_odds": 1.75, "max_odds": 3.0},
    {"name": "long", "min_odds": 3.0, "max_odds": 6.0},
    {"name": "very_long", "min_odds": 6.0, "max_odds": 100.0},
]
ENABLE_CONTEXT_FEATURES = os.getenv("ENABLE_CONTEXT_FEATURES", "0") == "1"
ENABLE_WEATHER_FEATURES = os.getenv("ENABLE_WEATHER_FEATURES", "0") == "1"
CONTEXT_COLUMNS = [
    "home_injury_count",
    "away_injury_count",
    "home_key_absences",
    "away_key_absences",
    "home_suspensions",
    "away_suspensions",
    "home_lineup_strength",
    "away_lineup_strength",
    "home_rotation_risk",
    "away_rotation_risk",
    "derby_or_rivalry",
    "home_motivation",
    "away_motivation",
    "weather_severity",
    "away_travel_fatigue",
]
DEFAULT_CONTEXT = {
    "home_injury_count": 0.0,
    "away_injury_count": 0.0,
    "home_key_absences": 0.0,
    "away_key_absences": 0.0,
    "home_suspensions": 0.0,
    "away_suspensions": 0.0,
    "home_lineup_strength": 1.0,
    "away_lineup_strength": 1.0,
    "home_rotation_risk": 0.0,
    "away_rotation_risk": 0.0,
    "derby_or_rivalry": 0.0,
    "home_motivation": 0.5,
    "away_motivation": 0.5,
    "weather_severity": 0.0,
    "away_travel_fatigue": 0.0,
}


def parse_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except ValueError:
        return default


def parse_date(value):
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported date format: {value}")


def read_matches():
    matches = []
    for path in sorted(RAW_DIR.glob("*.csv")):
        if path.name == "fixtures.csv" or "_" not in path.stem:
            continue
        season, league_code = path.stem.split("_", 1)
        if league_code not in DEFAULT_LEAGUE_CODES:
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not row.get("Date") or not row.get("HomeTeam") or not row.get("AwayTeam"):
                    continue
                if row.get("FTR") not in LABELS:
                    continue

                row["Season"] = season
                row["LeagueCode"] = league_code
                row["LeagueName"] = league_name(league_code)
                row["ParsedDate"] = parse_date(row["Date"])
                row["FTHG"] = int(parse_float(row.get("FTHG")))
                row["FTAG"] = int(parse_float(row.get("FTAG")))
                matches.append(row)

    return sorted(matches, key=lambda item: (item["ParsedDate"], item["LeagueCode"], item["HomeTeam"], item["AwayTeam"]))


def read_upcoming_fixtures(latest_completed_date=None):
    path = RAW_DIR / "fixtures.csv"
    fixtures = []
    if not path.exists():
        return fixtures

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            league_code = row.get("Div")
            if league_code not in DEFAULT_LEAGUE_CODES:
                continue
            if not row.get("Date") or not row.get("HomeTeam") or not row.get("AwayTeam"):
                continue
            parsed_date = parse_date(row["Date"])
            if latest_completed_date and parsed_date <= latest_completed_date:
                continue
            row["Season"] = "upcoming"
            row["LeagueCode"] = league_code
            row["LeagueName"] = league_name(league_code)
            row["ParsedDate"] = parsed_date
            row["FTR"] = ""
            fixtures.append(row)

    return sorted(fixtures, key=lambda item: (item["ParsedDate"], item["LeagueCode"], item.get("Time", ""), item["HomeTeam"], item["AwayTeam"]))


def read_external_context():
    path = EXTERNAL_DIR / "match_context.csv"
    context = {}
    if not path.exists():
        return context

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row.get("date") or not row.get("home_team") or not row.get("away_team"):
                continue
            key = (row["date"], row["home_team"], row["away_team"])
            values = {name: parse_float(row.get(name), DEFAULT_CONTEXT[name]) for name in CONTEXT_COLUMNS}
            values["notes"] = row.get("notes", "")
            context[key] = values
    return context


def points_for(result, is_home):
    if result == "D":
        return 1
    if result == "H" and is_home:
        return 3
    if result == "A" and not is_home:
        return 3
    return 0


def result_letter(points):
    if points == 3:
        return "W"
    if points == 1:
        return "D"
    return "L"


def h2h_form_for_team(h2h, team):
    if not h2h:
        return "No recent H2H"
    form = []
    for item in h2h[-5:]:
        points = item["home_points"] if item["home_team"] == team else item["away_points"]
        form.append(result_letter(points))
    return " ".join(form)


def result_score(result, is_home):
    if result == "D":
        return 0.5
    if result == "H" and is_home:
        return 1.0
    if result == "A" and not is_home:
        return 1.0
    return 0.0


def avg(items):
    if not items:
        return 0.0
    return sum(items) / len(items)


def team_features(history, prefix, match_date):
    last_5 = list(history)[-5:]
    last_10 = list(history)[-10:]
    last_7_days = [item for item in history if (match_date - item["date"]).days <= 7]
    last_14_days = [item for item in history if (match_date - item["date"]).days <= 14]

    return {
        f"{prefix}_points_last_5": avg([item["points"] for item in last_5]),
        f"{prefix}_points_last_10": avg([item["points"] for item in last_10]),
        f"{prefix}_win_rate_last_5": avg([item["win"] for item in last_5]),
        f"{prefix}_draw_rate_last_5": avg([item["draw"] for item in last_5]),
        f"{prefix}_goals_for_last_5": avg([item["goals_for"] for item in last_5]),
        f"{prefix}_goals_against_last_5": avg([item["goals_against"] for item in last_5]),
        f"{prefix}_goal_diff_last_5": avg([item["goals_for"] - item["goals_against"] for item in last_5]),
        f"{prefix}_goals_for_last_10": avg([item["goals_for"] for item in last_10]),
        f"{prefix}_goals_against_last_10": avg([item["goals_against"] for item in last_10]),
        f"{prefix}_goal_diff_last_10": avg([item["goals_for"] - item["goals_against"] for item in last_10]),
        f"{prefix}_shots_for_last_5": avg([item["shots_for"] for item in last_5]),
        f"{prefix}_shots_against_last_5": avg([item["shots_against"] for item in last_5]),
        f"{prefix}_shots_on_target_for_last_5": avg([item["shots_on_target_for"] for item in last_5]),
        f"{prefix}_shots_on_target_against_last_5": avg([item["shots_on_target_against"] for item in last_5]),
        f"{prefix}_corners_for_last_5": avg([item["corners_for"] for item in last_5]),
        f"{prefix}_corners_against_last_5": avg([item["corners_against"] for item in last_5]),
        f"{prefix}_cards_last_5": avg([item["cards"] for item in last_5]),
        f"{prefix}_red_cards_last_10": avg([item["red_cards"] for item in last_10]),
        f"{prefix}_performance_proxy_last_5": avg([item["performance_proxy"] for item in last_5]),
        f"{prefix}_games_last_7_days": min(len(last_7_days), 4) / 4,
        f"{prefix}_games_last_14_days": min(len(last_14_days), 6) / 6,
        f"{prefix}_matches_played": min(len(history), 20) / 20,
    }


def odds_features(match):
    for home_col, draw_col, away_col in ODDS_COLUMNS:
        home_odds = parse_float(match.get(home_col))
        draw_odds = parse_float(match.get(draw_col))
        away_odds = parse_float(match.get(away_col))
        if home_odds > 1 and draw_odds > 1 and away_odds > 1:
            raw = [1 / home_odds, 1 / draw_odds, 1 / away_odds]
            total = sum(raw)
            features = {
                "market_home_prob": raw[0] / total,
                "market_draw_prob": raw[1] / total,
                "market_away_prob": raw[2] / total,
                "home_odds": home_odds,
                "draw_odds": draw_odds,
                "away_odds": away_odds,
            }
            features.update(odds_movement_features(match, home_odds, draw_odds, away_odds))
            return features

    return {
        "market_home_prob": 0.0,
        "market_draw_prob": 0.0,
        "market_away_prob": 0.0,
        "home_odds": 0.0,
        "draw_odds": 0.0,
        "away_odds": 0.0,
        "home_odds_move": 0.0,
        "draw_odds_move": 0.0,
        "away_odds_move": 0.0,
        "home_closing_prob": 0.0,
        "draw_closing_prob": 0.0,
        "away_closing_prob": 0.0,
    }


def over_under_odds_features(match):
    over_odds = first_valid_odds(match, ["Avg>2.5", "B365>2.5", "Max>2.5"])
    under_odds = first_valid_odds(match, ["Avg<2.5", "B365<2.5", "Max<2.5"])
    over_probability = implied_probability(over_odds)
    under_probability = implied_probability(under_odds)
    total = over_probability + under_probability
    return {
        "over_25_odds": over_odds,
        "under_25_odds": under_odds,
        "over_25_market_prob": over_probability / total if total > 0 else 0.0,
        "under_25_market_prob": under_probability / total if total > 0 else 0.0,
    }


def first_valid_odds(match, columns):
    for column in columns:
        odds = parse_float(match.get(column))
        if odds > 1:
            return odds
    return 0.0


def odds_movement_features(match, home_odds, draw_odds, away_odds):
    close_home = parse_float(match.get("AvgCH"))
    close_draw = parse_float(match.get("AvgCD"))
    close_away = parse_float(match.get("AvgCA"))
    if close_home <= 1 or close_draw <= 1 or close_away <= 1:
        close_home = parse_float(match.get("B365CH"))
        close_draw = parse_float(match.get("B365CD"))
        close_away = parse_float(match.get("B365CA"))

    if close_home <= 1 or close_draw <= 1 or close_away <= 1:
        return {
            "home_odds_move": 0.0,
            "draw_odds_move": 0.0,
            "away_odds_move": 0.0,
            "home_closing_prob": 0.0,
            "draw_closing_prob": 0.0,
            "away_closing_prob": 0.0,
        }

    raw = [1 / close_home, 1 / close_draw, 1 / close_away]
    total = sum(raw)
    return {
        "home_odds_move": (home_odds - close_home) / home_odds,
        "draw_odds_move": (draw_odds - close_draw) / draw_odds,
        "away_odds_move": (away_odds - close_away) / away_odds,
        "home_closing_prob": raw[0] / total,
        "draw_closing_prob": raw[1] / total,
        "away_closing_prob": raw[2] / total,
    }


def table_features(table, home, away):
    teams = sorted(table.items(), key=lambda item: (-item[1]["points"], -item[1]["goal_diff"], item[0]))
    ranks = {team: rank + 1 for rank, (team, _stats) in enumerate(teams)}
    team_count = max(len(teams), 1)

    def one_team(team):
        stats = table[team]
        played = stats["played"]
        ppg = stats["points"] / played if played else 0.0
        rank = ranks.get(team, team_count)
        normalized_rank = 1 - ((rank - 1) / max(team_count - 1, 1))
        top_four_pressure = 1.0 if played >= 20 and rank <= 7 else 0.0
        title_pressure = 1.0 if played >= 20 and rank <= 3 else 0.0
        relegation_pressure = 1.0 if played >= 20 and rank >= max(team_count - 5, 1) else 0.0
        late_season_multiplier = min(1.0, max(0.0, (played - 24) / 10))
        return {
            "ppg": ppg,
            "rank": rank,
            "rank_strength": normalized_rank,
            "goal_diff_per_game": stats["goal_diff"] / played if played else 0.0,
            "importance": max(top_four_pressure, title_pressure, relegation_pressure),
            "late_pressure": max(title_pressure, top_four_pressure, relegation_pressure) * late_season_multiplier,
        }

    home_stats = one_team(home)
    away_stats = one_team(away)
    return {
        "home_ppg": home_stats["ppg"],
        "away_ppg": away_stats["ppg"],
        "ppg_difference": home_stats["ppg"] - away_stats["ppg"],
        "home_rank_strength": home_stats["rank_strength"],
        "away_rank_strength": away_stats["rank_strength"],
        "rank_strength_difference": home_stats["rank_strength"] - away_stats["rank_strength"],
        "home_goal_diff_per_game": home_stats["goal_diff_per_game"],
        "away_goal_diff_per_game": away_stats["goal_diff_per_game"],
        "home_auto_importance": home_stats["importance"],
        "away_auto_importance": away_stats["importance"],
        "importance_difference": home_stats["importance"] - away_stats["importance"],
        "home_late_season_pressure": home_stats["late_pressure"],
        "away_late_season_pressure": away_stats["late_pressure"],
        "late_season_pressure_difference": home_stats["late_pressure"] - away_stats["late_pressure"],
    }


def context_features(external_context, date, home, away):
    context = DEFAULT_CONTEXT.copy()
    context.update(external_context.get((date.isoformat(), home, away), {}))
    if os.getenv("DISABLE_WEATHER_CONTEXT") == "1" or not ENABLE_WEATHER_FEATURES:
        context["weather_severity"] = 0.0
    features = {name: context[name] for name in CONTEXT_COLUMNS}
    features.update({
        "injury_count_difference": context["away_injury_count"] - context["home_injury_count"],
        "key_absence_difference": context["away_key_absences"] - context["home_key_absences"],
        "suspension_difference": context["away_suspensions"] - context["home_suspensions"],
        "lineup_strength_difference": context["home_lineup_strength"] - context["away_lineup_strength"],
        "rotation_risk_difference": context["away_rotation_risk"] - context["home_rotation_risk"],
        "motivation_difference": context["home_motivation"] - context["away_motivation"],
    })
    return features, context


def context_summary(context):
    signals = []
    if context["home_key_absences"] or context["away_key_absences"]:
        signals.append(f"Key absences H{context['home_key_absences']:.0f}-A{context['away_key_absences']:.0f}")
    if context["home_injury_count"] or context["away_injury_count"]:
        signals.append(f"Injuries H{context['home_injury_count']:.0f}-A{context['away_injury_count']:.0f}")
    if context["home_suspensions"] or context["away_suspensions"]:
        signals.append(f"Suspensions H{context['home_suspensions']:.0f}-A{context['away_suspensions']:.0f}")
    if context["derby_or_rivalry"]:
        signals.append("Derby/rivalry")
    if context["weather_severity"]:
        signals.append(f"Weather {context['weather_severity']:.1f}")
    if context["away_travel_fatigue"]:
        signals.append(f"Away travel {context['away_travel_fatigue']:.1f}")
    return "; ".join(signals) if signals else "No external context supplied"


def performance_proxy(shots, shots_on_target, corners, goals):
    return (shots * 0.03) + (shots_on_target * 0.12) + (corners * 0.025) + (goals * 0.18)


def build_dataset(matches):
    external_context = read_external_context()
    team_history = defaultdict(lambda: deque(maxlen=30))
    h2h_history = defaultdict(lambda: deque(maxlen=10))
    table = defaultdict(lambda: {"played": 0, "points": 0, "goal_diff": 0})
    elo = defaultdict(lambda: 1500.0)
    last_played = {}
    rows = []

    for match in matches:
        home = match["HomeTeam"]
        away = match["AwayTeam"]
        date = match["ParsedDate"]
        pair_key = tuple(sorted([home, away]))

        home_history = team_history[home]
        away_history = team_history[away]
        h2h = list(h2h_history[pair_key])
        home_elo = elo[home]
        away_elo = elo[away]
        home_elo_with_advantage = home_elo + 65
        expected_home = 1 / (1 + 10 ** ((away_elo - home_elo_with_advantage) / 400))
        context_values, full_context = context_features(external_context, date, home, away)

        row = {
            "season": match["Season"],
            "league_code": match.get("LeagueCode", match.get("Div", "")),
            "league": match.get("LeagueName", league_name(match.get("LeagueCode", match.get("Div", "")))),
            "league_feature": league_feature(match.get("LeagueCode", match.get("Div", ""))),
            "date": date.isoformat(),
            "time": match.get("Time", ""),
            "home_team": home,
            "away_team": away,
            "result": match.get("FTR", ""),
            "total_goals": parse_float(match.get("FTHG")) + parse_float(match.get("FTAG")),
            "home_advantage": 1.0,
            "home_rest_days": min((date - last_played[home]).days, 14) / 14 if home in last_played else 1.0,
            "away_rest_days": min((date - last_played[away]).days, 14) / 14 if away in last_played else 1.0,
            "home_elo": home_elo / 2000,
            "away_elo": away_elo / 2000,
            "elo_difference": (home_elo - away_elo) / 400,
            "elo_expected_home": expected_home,
            "h2h_home_points_last_5": avg([
                item["home_points"] if item["home_team"] == home else item["away_points"]
                for item in h2h[-5:]
            ]),
            "h2h_away_points_last_5": avg([
                item["away_points"] if item["away_team"] == away else item["home_points"]
                for item in h2h[-5:]
            ]),
            "h2h_goals_last_5": avg([item["total_goals"] for item in h2h[-5:]]),
            "h2h_home_form": h2h_form_for_team(h2h, home),
            "h2h_away_form": h2h_form_for_team(h2h, away),
            "context_summary": context_summary(full_context),
            "context_notes": full_context.get("notes", ""),
        }

        row.update(team_features(home_history, "home", date))
        row.update(team_features(away_history, "away", date))
        row.update(table_features(table, home, away))
        row.update(context_values)
        row.update(odds_features(match))
        row.update(over_under_odds_features(match))
        rows.append(row)

        if match.get("FTR") not in LABELS:
            continue

        home_goals = match["FTHG"]
        away_goals = match["FTAG"]
        result = match["FTR"]
        home_shots = parse_float(match.get("HS"))
        away_shots = parse_float(match.get("AS"))
        home_sot = parse_float(match.get("HST"))
        away_sot = parse_float(match.get("AST"))
        home_corners = parse_float(match.get("HC"))
        away_corners = parse_float(match.get("AC"))
        home_yellow = parse_float(match.get("HY"))
        away_yellow = parse_float(match.get("AY"))
        home_red = parse_float(match.get("HR"))
        away_red = parse_float(match.get("AR"))

        team_history[home].append({
            "date": date,
            "points": points_for(result, True),
            "win": 1.0 if result == "H" else 0.0,
            "draw": 1.0 if result == "D" else 0.0,
            "goals_for": home_goals,
            "goals_against": away_goals,
            "shots_for": home_shots,
            "shots_against": away_shots,
            "shots_on_target_for": home_sot,
            "shots_on_target_against": away_sot,
            "corners_for": home_corners,
            "corners_against": away_corners,
            "cards": home_yellow + (home_red * 2),
            "red_cards": home_red,
            "performance_proxy": performance_proxy(home_shots, home_sot, home_corners, home_goals),
        })
        team_history[away].append({
            "date": date,
            "points": points_for(result, False),
            "win": 1.0 if result == "A" else 0.0,
            "draw": 1.0 if result == "D" else 0.0,
            "goals_for": away_goals,
            "goals_against": home_goals,
            "shots_for": away_shots,
            "shots_against": home_shots,
            "shots_on_target_for": away_sot,
            "shots_on_target_against": home_sot,
            "corners_for": away_corners,
            "corners_against": home_corners,
            "cards": away_yellow + (away_red * 2),
            "red_cards": away_red,
            "performance_proxy": performance_proxy(away_shots, away_sot, away_corners, away_goals),
        })
        h2h_history[pair_key].append({
            "home_team": home,
            "away_team": away,
            "home_points": points_for(result, True),
            "away_points": points_for(result, False),
            "total_goals": home_goals + away_goals,
        })
        last_played[home] = date
        last_played[away] = date
        table[home]["played"] += 1
        table[home]["points"] += points_for(result, True)
        table[home]["goal_diff"] += home_goals - away_goals
        table[away]["played"] += 1
        table[away]["points"] += points_for(result, False)
        table[away]["goal_diff"] += away_goals - home_goals

        actual_home = result_score(result, True)
        margin = max(abs(home_goals - away_goals), 1)
        k_factor = 22 * math.log(margin + 1)
        elo[home] = home_elo + k_factor * (actual_home - expected_home)
        elo[away] = away_elo + k_factor * ((1 - actual_home) - (1 - expected_home))

    return rows


def is_experimental_performance_feature(name):
    tokens = [
        "shots_",
        "shots_on_target",
        "corners_",
        "cards",
        "performance_proxy",
        "games_last_7_days",
        "games_last_14_days",
        "late_season_pressure",
    ]
    return any(token in name for token in tokens)


def is_context_feature(name):
    tokens = [
        "injury",
        "key_absence",
        "suspension",
        "lineup_strength",
        "rotation_risk",
        "motivation",
        "derby_or_rivalry",
        "away_travel_fatigue",
    ]
    return any(token in name for token in tokens)


def is_weather_feature(name):
    return "weather" in name


def feature_names(rows):
    excluded = {
        "season",
        "league_code",
        "league",
        "date",
        "time",
        "home_team",
        "away_team",
        "result",
        "home_odds",
        "draw_odds",
        "away_odds",
        "home_fair_odds",
        "draw_fair_odds",
        "away_fair_odds",
        "home_value_odds",
        "draw_value_odds",
        "away_value_odds",
        "over_25_odds",
        "under_25_odds",
        "over_25_market_prob",
        "under_25_market_prob",
        "over_25_probability",
        "over_25_fair_odds",
        "over_25_value_odds",
        "total_goals",
        "context_summary",
        "context_notes",
        "h2h_home_form",
        "h2h_away_form",
    }
    names = [key for key in rows[0].keys() if key not in excluded]
    if os.getenv("ENABLE_EXPERIMENTAL_PERFORMANCE_FEATURES") != "1":
        names = [name for name in names if not is_experimental_performance_feature(name)]
    if not ENABLE_CONTEXT_FEATURES:
        names = [name for name in names if not is_context_feature(name)]
    if not ENABLE_WEATHER_FEATURES:
        names = [name for name in names if not is_weather_feature(name)]
    return names


def standardize(train_rows, test_rows, names):
    stats = {}
    for name in names:
        values = [row[name] for row in train_rows]
        mean = avg(values)
        variance = avg([(value - mean) ** 2 for value in values])
        std = math.sqrt(variance) or 1.0
        stats[name] = {"mean": mean, "std": std}

    def transform(row):
        return [(row[name] - stats[name]["mean"]) / stats[name]["std"] for name in names]

    return [transform(row) for row in train_rows], [transform(row) for row in test_rows], stats


def softmax(scores):
    offset = max(scores)
    exps = [math.exp(score - offset) for score in scores]
    total = sum(exps)
    return [value / total for value in exps]


def train_softmax_classifier(x_train, y_train, epochs=520, learning_rate=0.045, l2=0.001):
    random.seed(42)
    feature_count = len(x_train[0])
    class_count = len(LABELS)
    weights = [[random.uniform(-0.01, 0.01) for _ in range(feature_count)] for _ in range(class_count)]
    biases = [0.0 for _ in range(class_count)]

    for _ in range(epochs):
        grad_w = [[0.0 for _ in range(feature_count)] for _ in range(class_count)]
        grad_b = [0.0 for _ in range(class_count)]

        for features, label in zip(x_train, y_train):
            scores = [
                biases[class_index] + sum(weights[class_index][i] * features[i] for i in range(feature_count))
                for class_index in range(class_count)
            ]
            probabilities = softmax(scores)
            expected_index = LABELS.index(label)

            for class_index in range(class_count):
                error = probabilities[class_index] - (1.0 if class_index == expected_index else 0.0)
                grad_b[class_index] += error
                for i in range(feature_count):
                    grad_w[class_index][i] += error * features[i]

        count = len(x_train)
        for class_index in range(class_count):
            biases[class_index] -= learning_rate * grad_b[class_index] / count
            for i in range(feature_count):
                penalty = l2 * weights[class_index][i]
                weights[class_index][i] -= learning_rate * ((grad_w[class_index][i] / count) + penalty)

    return {"weights": weights, "biases": biases}


def predict_probabilities(model, features):
    scores = [
        model["biases"][class_index] + sum(model["weights"][class_index][i] * features[i] for i in range(len(features)))
        for class_index in range(len(LABELS))
    ]
    return dict(zip(LABELS, softmax(scores)))


def implied_probability(odds):
    return 1 / odds if odds and odds > 1 else 0.0


def fair_odds(probability):
    return 1 / probability if probability > 0 else 0.0


def value_odds(probability, edge_threshold=DEFAULT_BET_THRESHOLD):
    required_probability = probability - edge_threshold
    return 1 / required_probability if required_probability > 0 else 0.0


def poisson_over_25_probability(expected_goals):
    expected_goals = min(max(expected_goals, 0.5), 5.5)
    probability_0 = math.exp(-expected_goals)
    probability_1 = probability_0 * expected_goals
    probability_2 = probability_1 * expected_goals / 2
    return 1 - (probability_0 + probability_1 + probability_2)


def over_25_probability(row):
    expected_home = avg([
        row.get("home_goals_for_last_10", 0),
        row.get("away_goals_against_last_10", 0),
    ])
    expected_away = avg([
        row.get("away_goals_for_last_10", 0),
        row.get("home_goals_against_last_10", 0),
    ])
    expected_goals = expected_home + expected_away
    if expected_goals <= 0:
        expected_goals = row.get("h2h_goals_last_5", 0) or 2.55
    return poisson_over_25_probability(expected_goals)


def no_vig_market_probabilities(row):
    raw = {
        "H": implied_probability(row["home_odds"]),
        "D": implied_probability(row["draw_odds"]),
        "A": implied_probability(row["away_odds"]),
    }
    total = sum(raw.values())
    if total <= 0:
        return {"H": 0.0, "D": 0.0, "A": 0.0}
    return {label: value / total for label, value in raw.items()}


def bet_probability(row, model_probabilities, label):
    market = no_vig_market_probabilities(row)
    if market[label] <= 0:
        return model_probabilities[label]
    return (model_probabilities[label] * 0.72) + (market[label] * 0.28)


def decimal_return(result, selection, odds):
    if result == selection and odds > 1:
        return odds - 1
    return -1


def odds_band(odds):
    for band in ODDS_BANDS:
        if band["min_odds"] <= odds < band["max_odds"]:
            return band["name"]
    return "unknown"


def candidate_bets(row, probs):
    odds_by_label = {"H": row["home_odds"], "D": row["draw_odds"], "A": row["away_odds"]}
    market = no_vig_market_probabilities(row)
    candidates = []

    for label in LABELS:
        adjusted_probability = bet_probability(row, probs, label)
        edge = adjusted_probability - market[label]
        odds = odds_by_label[label]
        if odds > 1 and adjusted_probability >= 0.22:
            candidates.append({
                "selection": label,
                "odds": odds,
                "edge": edge,
                "adjusted_probability": adjusted_probability,
                "market_probability": market[label],
                "odds_band": odds_band(odds),
            })

    return sorted(candidates, key=lambda item: item["edge"], reverse=True)


def matching_betting_rule(row, candidate, rules):
    if not rules:
        return None

    league = row.get("league") or row.get("league_code") or ""
    for rule in rules:
        if rule.get("league") not in ("all", league):
            continue
        if rule.get("selection") not in ("all", candidate["selection"]):
            continue
        if rule.get("odds_band") not in ("all", candidate["odds_band"]):
            continue
        if candidate["odds"] < float(rule.get("min_odds", 1.01)):
            continue
        if candidate["odds"] >= float(rule.get("max_odds", 100.0)):
            continue
        if candidate["edge"] < float(rule.get("min_edge", 0.0)):
            continue
        return rule
    return None


def find_best_bet_with_rule(row, probs, threshold, rules=None):
    best_edge = 0.0
    for candidate in candidate_bets(row, probs):
        best_edge = max(best_edge, candidate["edge"])
        if candidate["edge"] < threshold:
            continue
        rule = matching_betting_rule(row, candidate, rules)
        if rules and not rule:
            continue
        return candidate["selection"], candidate["edge"], rule
    return None, best_edge, None


def find_best_bet(row, probs, threshold, rules=None):
    best_bet, best_edge, _rule = find_best_bet_with_rule(row, probs, threshold, rules)
    return best_bet, best_edge


def evaluate(rows, probabilities, threshold=0.08, rules=None):
    correct = 0
    bets = []
    losses = []

    for row, probs in zip(rows, probabilities):
        prediction = max(probs, key=probs.get)
        if prediction == row["result"]:
            correct += 1

        odds_by_label = {"H": row["home_odds"], "D": row["draw_odds"], "A": row["away_odds"]}
        best_bet, _best_edge = find_best_bet(row, probs, threshold, rules)
        if best_bet:
            profit = decimal_return(row["result"], best_bet, odds_by_label[best_bet])
            bets.append(profit)

        actual_prob = max(probs[row["result"]], 0.000001)
        losses.append(-math.log(actual_prob))

    profit = sum(bets)
    return {
        "matches": len(rows),
        "accuracy": correct / len(rows),
        "log_loss": avg(losses),
        "bets": len(bets),
        "profit_units": profit,
        "roi": profit / len(bets) if bets else 0.0,
    }


def rule_name(rule):
    if not rule:
        return ""
    league = rule.get("league", "all")
    selection = rule.get("selection", "all")
    odds = rule.get("odds_band", "all")
    edge = float(rule.get("min_edge", 0.0))
    return f"{league} {selection} {odds} {edge:.0%}+"


def write_predictions(rows, probabilities, over_25_probabilities=None, bet_threshold=DEFAULT_BET_THRESHOLD, over_25_bet_threshold=None, rules=None):
    over_25_bet_threshold = bet_threshold if over_25_bet_threshold is None else over_25_bet_threshold
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / "predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "date",
            "time",
            "season",
            "league_code",
            "league",
            "home_team",
            "away_team",
            "result",
            "context_summary",
            "context_notes",
            "h2h_home_form",
            "h2h_away_form",
            "home_auto_importance",
            "away_auto_importance",
            "home_motivation",
            "away_motivation",
            "home_lineup_strength",
            "away_lineup_strength",
            "home_key_absences",
            "away_key_absences",
            "home_injury_count",
            "away_injury_count",
            "home_win_probability",
            "draw_probability",
            "away_win_probability",
            "home_bookmaker_odds",
            "draw_bookmaker_odds",
            "away_bookmaker_odds",
            "home_fair_odds",
            "draw_fair_odds",
            "away_fair_odds",
            "home_value_odds",
            "draw_value_odds",
            "away_value_odds",
            "over_25_probability",
            "over_25_bookmaker_odds",
            "over_25_fair_odds",
            "over_25_value_odds",
            "predicted_result",
            "suggested_bet",
            "suggested_edge",
            "bet_rule",
            "bet_rule_bets",
            "bet_rule_roi",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, (row, probs) in enumerate(zip(rows, probabilities)):
            best_bet, best_edge, bet_rule = find_best_bet_with_rule(row, probs, bet_threshold, rules)
            over_probability = (
                over_25_probabilities[index]
                if over_25_probabilities is not None
                else over_25_probability(row)
            )

            writer.writerow({
                "date": row["date"],
                "time": row.get("time", ""),
                "season": row["season"],
                "league_code": row.get("league_code", ""),
                "league": row.get("league", ""),
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "result": row["result"],
                "context_summary": row["context_summary"],
                "context_notes": row.get("context_notes", ""),
                "h2h_home_form": row.get("h2h_home_form", "No recent H2H"),
                "h2h_away_form": row.get("h2h_away_form", "No recent H2H"),
                "home_auto_importance": round(row["home_auto_importance"], 3),
                "away_auto_importance": round(row["away_auto_importance"], 3),
                "home_motivation": round(row["home_motivation"], 3),
                "away_motivation": round(row["away_motivation"], 3),
                "home_lineup_strength": round(row["home_lineup_strength"], 3),
                "away_lineup_strength": round(row["away_lineup_strength"], 3),
                "home_key_absences": round(row["home_key_absences"], 3),
                "away_key_absences": round(row["away_key_absences"], 3),
                "home_injury_count": round(row["home_injury_count"], 3),
                "away_injury_count": round(row["away_injury_count"], 3),
                "home_win_probability": round(probs["H"], 4),
                "draw_probability": round(probs["D"], 4),
                "away_win_probability": round(probs["A"], 4),
                "home_bookmaker_odds": round(row["home_odds"], 3) if row["home_odds"] else "",
                "draw_bookmaker_odds": round(row["draw_odds"], 3) if row["draw_odds"] else "",
                "away_bookmaker_odds": round(row["away_odds"], 3) if row["away_odds"] else "",
                "home_fair_odds": round(fair_odds(probs["H"]), 2),
                "draw_fair_odds": round(fair_odds(probs["D"]), 2),
                "away_fair_odds": round(fair_odds(probs["A"]), 2),
                "home_value_odds": round(value_odds(probs["H"], bet_threshold), 2),
                "draw_value_odds": round(value_odds(probs["D"], bet_threshold), 2),
                "away_value_odds": round(value_odds(probs["A"], bet_threshold), 2),
                "over_25_probability": round(over_probability, 4),
                "over_25_bookmaker_odds": round(row["over_25_odds"], 3) if row["over_25_odds"] else "",
                "over_25_fair_odds": round(fair_odds(over_probability), 2),
                "over_25_value_odds": round(value_odds(over_probability, over_25_bet_threshold), 2),
                "predicted_result": max(probs, key=probs.get),
                "suggested_bet": best_bet or "",
                "suggested_edge": round(best_edge, 4) if best_bet else "",
                "bet_rule": rule_name(bet_rule),
                "bet_rule_bets": bet_rule.get("bets", "") if bet_rule else "",
                "bet_rule_roi": round(float(bet_rule.get("roi", 0.0)), 4) if bet_rule else "",
            })


def train_and_predict(train_rows, test_rows, names):
    x_train, x_test, stats = standardize(train_rows, test_rows, names)
    y_train = [row["result"] for row in train_rows]
    model = train_softmax_classifier(x_train, y_train)
    probabilities = [predict_probabilities(model, features) for features in x_test]
    return model, probabilities, stats


def write_upcoming_predictions(rows, probabilities, over_25_probabilities=None, bet_threshold=DEFAULT_BET_THRESHOLD, over_25_bet_threshold=None, rules=None):
    over_25_bet_threshold = bet_threshold if over_25_bet_threshold is None else over_25_bet_threshold
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / "upcoming_predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "date",
            "time",
            "season",
            "league_code",
            "league",
            "home_team",
            "away_team",
            "context_summary",
            "context_notes",
            "h2h_home_form",
            "h2h_away_form",
            "home_auto_importance",
            "away_auto_importance",
            "home_motivation",
            "away_motivation",
            "home_lineup_strength",
            "away_lineup_strength",
            "home_key_absences",
            "away_key_absences",
            "home_injury_count",
            "away_injury_count",
            "home_win_probability",
            "draw_probability",
            "away_win_probability",
            "home_bookmaker_odds",
            "draw_bookmaker_odds",
            "away_bookmaker_odds",
            "home_fair_odds",
            "draw_fair_odds",
            "away_fair_odds",
            "home_value_odds",
            "draw_value_odds",
            "away_value_odds",
            "over_25_probability",
            "over_25_bookmaker_odds",
            "over_25_fair_odds",
            "over_25_value_odds",
            "predicted_result",
            "suggested_bet",
            "suggested_edge",
            "bet_rule",
            "bet_rule_bets",
            "bet_rule_roi",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, (row, probs) in enumerate(zip(rows, probabilities)):
            best_bet, best_edge, bet_rule = find_best_bet_with_rule(row, probs, bet_threshold, rules)
            over_probability = (
                over_25_probabilities[index]
                if over_25_probabilities is not None
                else over_25_probability(row)
            )
            writer.writerow({
                "date": row["date"],
                "time": row.get("time", ""),
                "season": row["season"],
                "league_code": row.get("league_code", ""),
                "league": row.get("league", ""),
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "context_summary": row["context_summary"],
                "context_notes": row.get("context_notes", ""),
                "h2h_home_form": row.get("h2h_home_form", "No recent H2H"),
                "h2h_away_form": row.get("h2h_away_form", "No recent H2H"),
                "home_auto_importance": round(row["home_auto_importance"], 3),
                "away_auto_importance": round(row["away_auto_importance"], 3),
                "home_motivation": round(row["home_motivation"], 3),
                "away_motivation": round(row["away_motivation"], 3),
                "home_lineup_strength": round(row["home_lineup_strength"], 3),
                "away_lineup_strength": round(row["away_lineup_strength"], 3),
                "home_key_absences": round(row["home_key_absences"], 3),
                "away_key_absences": round(row["away_key_absences"], 3),
                "home_injury_count": round(row["home_injury_count"], 3),
                "away_injury_count": round(row["away_injury_count"], 3),
                "home_win_probability": round(probs["H"], 4),
                "draw_probability": round(probs["D"], 4),
                "away_win_probability": round(probs["A"], 4),
                "home_bookmaker_odds": round(row["home_odds"], 3) if row["home_odds"] else "",
                "draw_bookmaker_odds": round(row["draw_odds"], 3) if row["draw_odds"] else "",
                "away_bookmaker_odds": round(row["away_odds"], 3) if row["away_odds"] else "",
                "home_fair_odds": round(fair_odds(probs["H"]), 2),
                "draw_fair_odds": round(fair_odds(probs["D"]), 2),
                "away_fair_odds": round(fair_odds(probs["A"]), 2),
                "home_value_odds": round(value_odds(probs["H"], bet_threshold), 2),
                "draw_value_odds": round(value_odds(probs["D"], bet_threshold), 2),
                "away_value_odds": round(value_odds(probs["A"], bet_threshold), 2),
                "over_25_probability": round(over_probability, 4),
                "over_25_bookmaker_odds": round(row["over_25_odds"], 3) if row["over_25_odds"] else "",
                "over_25_fair_odds": round(fair_odds(over_probability), 2),
                "over_25_value_odds": round(value_odds(over_probability, over_25_bet_threshold), 2),
                "predicted_result": max(probs, key=probs.get),
                "suggested_bet": best_bet or "",
                "suggested_edge": round(best_edge, 4) if best_bet else "",
                "bet_rule": rule_name(bet_rule),
                "bet_rule_bets": bet_rule.get("bets", "") if bet_rule else "",
                "bet_rule_roi": round(float(bet_rule.get("roi", 0.0)), 4) if bet_rule else "",
            })


def write_rolling_backtest(rows, names):
    seasons = sorted({row["season"] for row in rows})
    season_summaries = []
    all_rows = []

    for season in seasons[3:]:
        previous_seasons = [item for item in seasons if item < season]
        if TRAINING_WINDOW_SEASONS > 0:
            previous_seasons = previous_seasons[-TRAINING_WINDOW_SEASONS:]
        train_rows = [row for row in rows if row["season"] in previous_seasons]
        test_rows = [row for row in rows if row["season"] == season]
        if not train_rows or not test_rows:
            continue

        _model, probabilities, _stats = train_and_predict(train_rows, test_rows, names)
        base_summary = evaluate(test_rows, probabilities, threshold=DEFAULT_BET_THRESHOLD)
        threshold_summaries = {
            f"{threshold:.2f}": evaluate(test_rows, probabilities, threshold=threshold)
            for threshold in BET_THRESHOLDS
        }

        season_summaries.append({
            "season": season,
            "training_matches": len(train_rows),
            **base_summary,
            "thresholds": threshold_summaries,
        })

        for row, probs in zip(test_rows, probabilities):
            best_bet, best_edge = find_best_bet(row, probs, DEFAULT_BET_THRESHOLD)
            all_rows.append({
                "season": season,
                "date": row["date"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "result": row["result"],
                "context_summary": row["context_summary"],
                "home_auto_importance": round(row["home_auto_importance"], 3),
                "away_auto_importance": round(row["away_auto_importance"], 3),
                "home_motivation": round(row["home_motivation"], 3),
                "away_motivation": round(row["away_motivation"], 3),
                "home_win_probability": round(probs["H"], 4),
                "draw_probability": round(probs["D"], 4),
                "away_win_probability": round(probs["A"], 4),
                "predicted_result": max(probs, key=probs.get),
                "suggested_bet": best_bet or "",
                "suggested_edge": round(best_edge, 4) if best_bet else "",
            })

    path = REPORTS_DIR / "rolling_backtest.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "season",
            "training_matches",
            "matches",
            "accuracy",
            "log_loss",
            "bets",
            "profit_units",
            "roi",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for summary in season_summaries:
            writer.writerow({
                "season": summary["season"],
                "training_matches": summary["training_matches"],
                "matches": summary["matches"],
                "accuracy": round(summary["accuracy"], 4),
                "log_loss": round(summary["log_loss"], 4),
                "bets": summary["bets"],
                "profit_units": round(summary["profit_units"], 2),
                "roi": round(summary["roi"], 4),
            })

    if all_rows:
        path = PROCESSED_DIR / "rolling_predictions.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(all_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_rows)

    return season_summaries


def average_summary(season_summaries):
    if not season_summaries:
        return {}
    total_matches = sum(item["matches"] for item in season_summaries)
    total_bets = sum(item["bets"] for item in season_summaries)
    total_profit = sum(item["profit_units"] for item in season_summaries)
    weighted_accuracy = sum(item["accuracy"] * item["matches"] for item in season_summaries) / total_matches
    weighted_log_loss = sum(item["log_loss"] * item["matches"] for item in season_summaries) / total_matches

    return {
        "seasons": len(season_summaries),
        "matches": total_matches,
        "accuracy": weighted_accuracy,
        "log_loss": weighted_log_loss,
        "bets": total_bets,
        "profit_units": total_profit,
        "roi": total_profit / total_bets if total_bets else 0.0,
    }


def main():
    matches = read_matches()
    if not matches:
        raise SystemExit("No raw CSV files found. Run: python3 src/download_data.py")

    rows = build_dataset(matches)
    completed_rows = [row for row in rows if row["result"] in LABELS]
    test_season = sorted({row["season"] for row in rows})[-1]
    train_rows = [row for row in rows if row["season"] != test_season]
    if TRAINING_WINDOW_SEASONS > 0:
        train_seasons = sorted({row["season"] for row in rows if row["season"] < test_season})[-TRAINING_WINDOW_SEASONS:]
        train_rows = [row for row in rows if row["season"] in train_seasons]
    test_rows = [row for row in rows if row["season"] == test_season]

    names = feature_names(rows)
    model, probabilities, stats = train_and_predict(train_rows, test_rows, names)
    summary = evaluate(test_rows, probabilities, threshold=DEFAULT_BET_THRESHOLD)

    write_predictions(test_rows, probabilities)

    latest_completed_date = max(match["ParsedDate"] for match in matches)
    upcoming_fixtures = read_upcoming_fixtures(latest_completed_date)
    upcoming_count = 0
    if upcoming_fixtures:
        combined_rows = build_dataset(matches + upcoming_fixtures)
        upcoming_rows = [row for row in combined_rows if row["season"] == "upcoming"]
        all_seasons = sorted({row["season"] for row in completed_rows})
        if TRAINING_WINDOW_SEASONS > 0:
            live_train_seasons = all_seasons[-TRAINING_WINDOW_SEASONS:]
            live_train_rows = [row for row in completed_rows if row["season"] in live_train_seasons]
        else:
            live_train_rows = completed_rows
        live_model, upcoming_probabilities, _live_stats = train_and_predict(live_train_rows, upcoming_rows, names)
        write_upcoming_predictions(upcoming_rows, upcoming_probabilities)
        upcoming_count = len(upcoming_rows)

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    rolling_summaries = write_rolling_backtest(rows, names)
    rolling_average = average_summary(rolling_summaries)

    (MODELS_DIR / "one_x_two_model.json").write_text(json.dumps({
        "labels": LABELS,
        "features": names,
        "stats": stats,
        "model": model,
        "live_model": live_model if upcoming_fixtures else None,
        "live_stats": _live_stats if upcoming_fixtures else None,
        "test_season": test_season,
        "bet_threshold": DEFAULT_BET_THRESHOLD,
        "training_window_seasons": TRAINING_WINDOW_SEASONS or "all",
        "context_features_enabled": ENABLE_CONTEXT_FEATURES,
        "weather_features_enabled": ENABLE_WEATHER_FEATURES,
        "external_context_file": str((EXTERNAL_DIR / "match_context.csv").relative_to(ROOT)),
    }, indent=2), encoding="utf-8")

    report = [
        "Multi-league 1X2 Backtest",
        f"Training matches: {len(train_rows)}",
        f"Test season: {test_season}",
        f"Test matches: {summary['matches']}",
        f"Accuracy: {summary['accuracy']:.2%}",
        f"Log loss: {summary['log_loss']:.4f}",
        f"Value bets at {DEFAULT_BET_THRESHOLD:.0%}+ edge: {summary['bets']}",
        f"Flat-stake profit: {summary['profit_units']:.2f} units",
        f"ROI: {summary['roi']:.2%}",
        "",
        "Rolling season backtest",
        f"Seasons tested: {rolling_average['seasons']}",
        f"Rolling matches: {rolling_average['matches']}",
        f"Rolling accuracy: {rolling_average['accuracy']:.2%}",
        f"Rolling log loss: {rolling_average['log_loss']:.4f}",
        f"Rolling value bets at {DEFAULT_BET_THRESHOLD:.0%}+ edge: {rolling_average['bets']}",
        f"Rolling flat-stake profit: {rolling_average['profit_units']:.2f} units",
        f"Rolling ROI: {rolling_average['roi']:.2%}",
        f"Upcoming fixtures predicted: {upcoming_count}",
    ]
    (REPORTS_DIR / "backtest_summary.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
