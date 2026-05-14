import csv
import json
from datetime import datetime, timezone
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
SITE_ROOT = MODEL_ROOT.parent
PREDICTIONS_PATH = MODEL_ROOT / "data" / "processed" / "predictions.csv"
UPCOMING_PATH = MODEL_ROOT / "data" / "processed" / "upcoming_predictions.csv"
SUMMARY_PATH = MODEL_ROOT / "reports" / "backtest_summary.txt"
BETTING_RULES_PATH = MODEL_ROOT / "models" / "betting_rules.json"
ACTIVE_BETTING_RULES_PATH = MODEL_ROOT / "models" / "betting_rules_active.json"
RULE_HEALTH_PATH = MODEL_ROOT / "reports" / "betting_rule_health.csv"
CLOSING_LINE_PATH = MODEL_ROOT / "data" / "processed" / "closing_line_report.csv"
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


def parse_betting_rules():
    if not BETTING_RULES_PATH.exists():
        return []
    return json.loads(BETTING_RULES_PATH.read_text(encoding="utf-8"))


def parse_active_betting_rules():
    if not ACTIVE_BETTING_RULES_PATH.exists():
        return parse_betting_rules()
    return json.loads(ACTIVE_BETTING_RULES_PATH.read_text(encoding="utf-8"))


def parse_rule_health():
    if not RULE_HEALTH_PATH.exists():
        return []
    with RULE_HEALTH_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def parse_closing_line_report():
    if not CLOSING_LINE_PATH.exists():
        return []
    with CLOSING_LINE_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def closing_line_summary(rows):
    if not rows:
        return {
            "snapshots_scored": 0,
            "bets_scored": 0,
            "clv_hit_rate": "--",
            "average_clv": "--",
            "flat_stake_profit": "--",
            "roi": "--",
        }

    scored = [row for row in rows if to_float(row.get("closing_selection_odds")) > 1]
    bets = [row for row in scored if row.get("suggested_bet")]
    clv_values = [to_float(row.get("closing_line_value")) for row in scored if row.get("closing_line_value") not in ("", None)]
    bet_profit = sum(to_float(row.get("profit_units")) for row in bets)
    positive_clv = [value for value in clv_values if value > 0]

    return {
        "snapshots_scored": len(scored),
        "bets_scored": len(bets),
        "clv_hit_rate": f"{(len(positive_clv) / len(clv_values)):.2%}" if clv_values else "--",
        "average_clv": f"{(sum(clv_values) / len(clv_values)):.2%}" if clv_values else "--",
        "flat_stake_profit": f"{bet_profit:.2f} units" if bets else "--",
        "roi": f"{(bet_profit / len(bets)):.2%}" if bets else "--",
    }


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
    closing_lines = parse_closing_line_report()
    suggested = [row for row in predictions if row.get("suggested_bet")]
    latest = predictions[-40:]

    leagues = sorted({
        row.get("league") or row.get("league_code") or "Unknown"
        for row in [*predictions, *upcoming]
    })

    output = {
        "summary": parse_summary(),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "generated_from": str(PREDICTIONS_PATH.relative_to(SITE_ROOT)),
        "leagues": leagues,
        "matches": len(predictions),
        "upcoming_matches": len(upcoming),
        "value_bets": len(suggested),
        "latest_matches": latest,
        "upcoming": upcoming,
        "suggested_bets": suggested[-20:],
        "betting_rules": parse_active_betting_rules(),
        "betting_rule_health": parse_rule_health(),
        "closing_line_summary": closing_line_summary(closing_lines),
        "closing_line_recent": closing_lines[-12:],
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
