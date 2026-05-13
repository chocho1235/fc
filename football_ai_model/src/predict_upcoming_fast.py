import csv
import json
from pathlib import Path

from train_1x2_model import (
    LABELS,
    MODELS_DIR,
    PROCESSED_DIR,
    build_dataset,
    find_best_bet,
    predict_probabilities,
    read_matches,
    read_upcoming_fixtures,
)


MODEL_PATH = MODELS_DIR / "one_x_two_model.json"


def transform(row, feature_names, stats):
    return [(row[name] - stats[name]["mean"]) / stats[name]["std"] for name in feature_names]


def write_upcoming(rows, probabilities, threshold):
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / "upcoming_predictions.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "date", "time", "season", "home_team", "away_team", "context_summary",
            "home_auto_importance", "away_auto_importance", "home_motivation", "away_motivation",
            "home_win_probability", "draw_probability", "away_win_probability",
            "predicted_result", "suggested_bet", "suggested_edge",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row, probs in zip(rows, probabilities):
            best_bet, best_edge = find_best_bet(row, probs, threshold)
            writer.writerow({
                "date": row["date"],
                "time": row.get("time", ""),
                "season": row["season"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
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
    print(f"Wrote {path} with {len(rows)} upcoming fixture(s)")


def main():
    if not MODEL_PATH.exists():
        raise SystemExit("No saved model found. Run train_1x2_model.py once first.")

    bundle = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    model = bundle.get("live_model") or bundle["model"]
    stats = bundle.get("live_stats") or bundle["stats"]
    features = bundle["features"]
    threshold = float(bundle.get("bet_threshold", 0.08))

    matches = read_matches()
    latest_completed_date = max(match["ParsedDate"] for match in matches)
    fixtures = read_upcoming_fixtures(latest_completed_date)
    if not fixtures:
        write_upcoming([], [], threshold)
        return

    combined_rows = build_dataset(matches + fixtures)
    upcoming_rows = [row for row in combined_rows if row["season"] == "upcoming"]
    probabilities = [
        predict_probabilities(model, transform(row, features, stats))
        for row in upcoming_rows
    ]
    write_upcoming(upcoming_rows, probabilities, threshold)


if __name__ == "__main__":
    main()

