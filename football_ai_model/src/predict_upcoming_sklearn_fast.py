import csv
import json

import joblib
import numpy as np

from train_1x2_model import (
    LABELS,
    BUILDER_PROFILE_PATH,
    MODELS_DIR,
    PROCESSED_DIR,
    apply_builder_profile,
    build_dataset,
    fair_odds,
    find_best_bet_with_rule,
    over_25_probability,
    read_matches,
    read_upcoming_fixtures,
    rule_name,
    value_odds,
)


MODEL_PATH = MODELS_DIR / "sklearn_one_x_two_model.joblib"
MODEL_META_PATH = MODELS_DIR / "sklearn_one_x_two_model_meta.json"
OVER_25_MODEL_PATH = MODELS_DIR / "sklearn_over_25_model.joblib"
OVER_25_META_PATH = MODELS_DIR / "sklearn_over_25_model_meta.json"
BETTING_RULES_PATH = MODELS_DIR / "betting_rules.json"
ACTIVE_BETTING_RULES_PATH = MODELS_DIR / "betting_rules_active.json"


def rows_to_matrix(rows, feature_names):
    return np.array([[row[name] for name in feature_names] for row in rows], dtype=float)


def predict_rows(model, rows, feature_names):
    probabilities = model.predict_proba(rows_to_matrix(rows, feature_names))
    classes = list(model.classes_)
    output = []
    for row_probs in probabilities:
        by_label = {label: 0.0 for label in LABELS}
        for label, probability in zip(classes, row_probs):
            by_label[label] = float(probability)
        output.append(by_label)
    return output


def predict_over_25(model, rows, feature_names):
    probabilities = model.predict_proba(rows_to_matrix(rows, feature_names))
    classes = list(model.classes_)
    if 1 not in classes:
        return [0.0 for _ in rows]
    positive_index = classes.index(1)
    return [float(row[positive_index]) for row in probabilities]


def write_upcoming(rows, probabilities, threshold, over_25_probabilities=None, over_25_threshold=None, rules=None):
    over_25_threshold = threshold if over_25_threshold is None else over_25_threshold
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / "upcoming_predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "date", "time", "season", "league_code", "league", "home_team", "away_team", "context_summary", "context_notes",
            "h2h_home_form", "h2h_away_form",
            "home_auto_importance", "away_auto_importance", "home_motivation", "away_motivation",
            "home_lineup_strength", "away_lineup_strength",
            "home_key_absences", "away_key_absences", "home_injury_count", "away_injury_count",
            "home_win_probability", "draw_probability", "away_win_probability",
            "home_bookmaker_odds", "draw_bookmaker_odds", "away_bookmaker_odds",
            "home_fair_odds", "draw_fair_odds", "away_fair_odds",
            "home_value_odds", "draw_value_odds", "away_value_odds",
            "over_25_probability", "over_25_bookmaker_odds", "over_25_fair_odds", "over_25_value_odds",
            "expected_home_goals", "expected_away_goals", "expected_total_goals",
            "btts_probability", "over_15_probability", "over_35_probability",
            "home_expected_sot", "away_expected_sot", "total_expected_sot", "total_expected_corners", "total_expected_cards",
            "builder_leg_keys", "builder_suggestion", "builder_confidence",
            "builder_leg_results", "builder_legs_won", "builder_legs_total", "builder_result",
            "predicted_result", "suggested_bet", "suggested_edge", "bet_rule", "bet_rule_bets", "bet_rule_roi",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, (row, probs) in enumerate(zip(rows, probabilities)):
            best_bet, best_edge, bet_rule = find_best_bet_with_rule(row, probs, threshold, rules)
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
                "home_value_odds": round(value_odds(probs["H"], threshold), 2),
                "draw_value_odds": round(value_odds(probs["D"], threshold), 2),
                "away_value_odds": round(value_odds(probs["A"], threshold), 2),
                "over_25_probability": round(over_probability, 4),
                "over_25_bookmaker_odds": round(row["over_25_odds"], 3) if row["over_25_odds"] else "",
                "over_25_fair_odds": round(fair_odds(over_probability), 2),
                "over_25_value_odds": round(value_odds(over_probability, over_25_threshold), 2),
                "expected_home_goals": round(row["expected_home_goals"], 2),
                "expected_away_goals": round(row["expected_away_goals"], 2),
                "expected_total_goals": round(row["expected_total_goals"], 2),
                "btts_probability": round(row["btts_probability"], 4),
                "over_15_probability": round(row["over_15_probability"], 4),
                "over_35_probability": round(row["over_35_probability"], 4),
                "home_expected_sot": round(row["home_expected_sot"], 2),
                "away_expected_sot": round(row["away_expected_sot"], 2),
                "total_expected_sot": round(row["total_expected_sot"], 2),
                "total_expected_corners": round(row["total_expected_corners"], 2),
                "total_expected_cards": round(row["total_expected_cards"], 2),
                "builder_leg_keys": row.get("builder_leg_keys", ""),
                "builder_suggestion": row["builder_suggestion"],
                "builder_confidence": row["builder_confidence"],
                "builder_leg_results": row.get("builder_leg_results", ""),
                "builder_legs_won": row.get("builder_legs_won", ""),
                "builder_legs_total": row.get("builder_legs_total", 0),
                "builder_result": row.get("builder_result", ""),
                "predicted_result": max(probs, key=probs.get),
                "suggested_bet": best_bet or "",
                "suggested_edge": round(best_edge, 4) if best_bet else "",
                "bet_rule": rule_name(bet_rule),
                "bet_rule_bets": bet_rule.get("bets", "") if bet_rule else "",
                "bet_rule_roi": round(float(bet_rule.get("roi", 0.0)), 4) if bet_rule else "",
            })
    print(f"Wrote {path} with {len(rows)} upcoming fixture(s)")


def main():
    if not MODEL_PATH.exists() or not MODEL_META_PATH.exists():
        raise SystemExit("No saved sklearn model found. Run train_sklearn_1x2_model.py once first.")

    model = joblib.load(MODEL_PATH)
    meta = json.loads(MODEL_META_PATH.read_text(encoding="utf-8"))
    features = meta["features"]
    threshold = float(meta.get("bet_threshold", 0.08))
    over_model = None
    over_features = []
    if OVER_25_MODEL_PATH.exists() and OVER_25_META_PATH.exists():
        over_model = joblib.load(OVER_25_MODEL_PATH)
        over_meta = json.loads(OVER_25_META_PATH.read_text(encoding="utf-8"))
        over_features = over_meta["features"]
        over_25_threshold = float(over_meta.get("over_25_bet_threshold", over_meta.get("bet_threshold", threshold)))
    else:
        over_25_threshold = threshold
    rules = []
    rules_path = ACTIVE_BETTING_RULES_PATH if ACTIVE_BETTING_RULES_PATH.exists() else BETTING_RULES_PATH
    if rules_path.exists():
        rules = json.loads(rules_path.read_text(encoding="utf-8"))

    matches = read_matches()
    latest_completed_date = max(match["ParsedDate"] for match in matches)
    fixtures = read_upcoming_fixtures(latest_completed_date)
    if not fixtures:
        write_upcoming([], [], threshold, over_25_threshold=over_25_threshold, rules=rules)
        return

    combined_rows = build_dataset(matches + fixtures)
    upcoming_rows = [row for row in combined_rows if row["season"] == "upcoming"]
    if BUILDER_PROFILE_PATH.exists():
        apply_builder_profile(upcoming_rows, json.loads(BUILDER_PROFILE_PATH.read_text(encoding="utf-8")))
    probabilities = predict_rows(model, upcoming_rows, features)
    over_probabilities = predict_over_25(over_model, upcoming_rows, over_features) if over_model else None
    write_upcoming(upcoming_rows, probabilities, threshold, over_probabilities, over_25_threshold, rules)


if __name__ == "__main__":
    main()
