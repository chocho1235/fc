import csv
import json

import joblib
import numpy as np

from train_1x2_model import (
    LABELS,
    MODELS_DIR,
    PROCESSED_DIR,
    build_dataset,
    fair_odds,
    find_best_bet,
    over_25_probability,
    read_matches,
    read_upcoming_fixtures,
    value_odds,
)


MODEL_PATH = MODELS_DIR / "sklearn_one_x_two_model.joblib"
MODEL_META_PATH = MODELS_DIR / "sklearn_one_x_two_model_meta.json"
OVER_25_MODEL_PATH = MODELS_DIR / "sklearn_over_25_model.joblib"
OVER_25_META_PATH = MODELS_DIR / "sklearn_over_25_model_meta.json"


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


def write_upcoming(rows, probabilities, threshold, over_25_probabilities=None):
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / "upcoming_predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "date", "time", "season", "home_team", "away_team", "context_summary", "context_notes",
            "h2h_home_form", "h2h_away_form",
            "home_auto_importance", "away_auto_importance", "home_motivation", "away_motivation",
            "home_lineup_strength", "away_lineup_strength",
            "home_key_absences", "away_key_absences", "home_injury_count", "away_injury_count",
            "home_win_probability", "draw_probability", "away_win_probability",
            "home_bookmaker_odds", "draw_bookmaker_odds", "away_bookmaker_odds",
            "home_fair_odds", "draw_fair_odds", "away_fair_odds",
            "home_value_odds", "draw_value_odds", "away_value_odds",
            "over_25_probability", "over_25_bookmaker_odds", "over_25_fair_odds", "over_25_value_odds",
            "predicted_result", "suggested_bet", "suggested_edge",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for index, (row, probs) in enumerate(zip(rows, probabilities)):
            best_bet, best_edge = find_best_bet(row, probs, threshold)
            over_probability = (
                over_25_probabilities[index]
                if over_25_probabilities is not None
                else over_25_probability(row)
            )
            writer.writerow({
                "date": row["date"],
                "time": row.get("time", ""),
                "season": row["season"],
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
                "over_25_value_odds": round(value_odds(over_probability, threshold), 2),
                "predicted_result": max(probs, key=probs.get),
                "suggested_bet": best_bet or "",
                "suggested_edge": round(best_edge, 4) if best_bet else "",
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

    matches = read_matches()
    latest_completed_date = max(match["ParsedDate"] for match in matches)
    fixtures = read_upcoming_fixtures(latest_completed_date)
    if not fixtures:
        write_upcoming([], [], threshold)
        return

    combined_rows = build_dataset(matches + fixtures)
    upcoming_rows = [row for row in combined_rows if row["season"] == "upcoming"]
    probabilities = predict_rows(model, upcoming_rows, features)
    over_probabilities = predict_over_25(over_model, upcoming_rows, over_features) if over_model else None
    write_upcoming(upcoming_rows, probabilities, threshold, over_probabilities)


if __name__ == "__main__":
    main()
