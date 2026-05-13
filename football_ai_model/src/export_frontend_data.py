import csv
import json
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
SITE_ROOT = MODEL_ROOT.parent
PREDICTIONS_PATH = MODEL_ROOT / "data" / "processed" / "predictions.csv"
UPCOMING_PATH = MODEL_ROOT / "data" / "processed" / "upcoming_predictions.csv"
SUMMARY_PATH = MODEL_ROOT / "reports" / "backtest_summary.txt"
OUTPUT_PATH = SITE_ROOT / "public" / "football-model-data.json"


def parse_summary():
    summary = {}
    if not SUMMARY_PATH.exists():
        return summary

    for line in SUMMARY_PATH.read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        summary[key.strip().lower().replace(" ", "_").replace("-", "_")] = value.strip()
    return summary


def parse_predictions():
    with PREDICTIONS_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_upcoming():
    if not UPCOMING_PATH.exists():
        return []
    with UPCOMING_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def main():
    if not PREDICTIONS_PATH.exists():
        raise SystemExit("No predictions file found. Run train_1x2_model.py first.")

    predictions = parse_predictions()
    upcoming = parse_upcoming()
    suggested = [row for row in predictions if row.get("suggested_bet")]
    latest = predictions[-40:]

    leagues = sorted({
        row.get("league") or row.get("league_code") or "Unknown"
        for row in [*predictions, *upcoming]
    })

    output = {
        "summary": parse_summary(),
        "generated_from": str(PREDICTIONS_PATH.relative_to(SITE_ROOT)),
        "leagues": leagues,
        "matches": len(predictions),
        "upcoming_matches": len(upcoming),
        "value_bets": len(suggested),
        "latest_matches": latest,
        "upcoming": upcoming,
        "suggested_bets": suggested[-20:],
        "probability_average": {
            "home": round(sum(to_float(row["home_win_probability"]) for row in predictions) / len(predictions), 4),
            "draw": round(sum(to_float(row["draw_probability"]) for row in predictions) / len(predictions), 4),
            "away": round(sum(to_float(row["away_win_probability"]) for row in predictions) / len(predictions), 4),
        },
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
