import csv
import json
from pathlib import Path

from train_1x2_model import decimal_return, rule_name


ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
PROCESSED_DIR = ROOT / "data" / "processed"
REPORTS_DIR = ROOT / "reports"

RULES_PATH = MODELS_DIR / "betting_rules.json"
ACTIVE_RULES_PATH = MODELS_DIR / "betting_rules_active.json"
PREDICTIONS_PATH = PROCESSED_DIR / "predictions.csv"
HEALTH_PATH = REPORTS_DIR / "betting_rule_health.csv"

RECENT_RULE_BETS = 5
MIN_RECENT_BETS_TO_PAUSE = 1
PAUSE_ROI = -0.20
PAUSE_LOSS_STREAK = 3


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_rules():
    if not RULES_PATH.exists():
        return []
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def bet_profit(row):
    odds_by_label = {
        "H": row.get("home_bookmaker_odds"),
        "D": row.get("draw_bookmaker_odds"),
        "A": row.get("away_bookmaker_odds"),
    }
    selection = row.get("suggested_bet", "")
    try:
        odds = float(odds_by_label.get(selection) or 0)
    except ValueError:
        odds = 0.0
    return decimal_return(row.get("result", ""), selection, odds)


def loss_streak(profits):
    streak = 0
    for profit in reversed(profits):
        if profit < 0:
            streak += 1
        else:
            break
    return streak


def enrich_rule(rule, rows):
    name = rule_name(rule)
    rule_rows = [row for row in rows if row.get("bet_rule") == name and row.get("suggested_bet")]
    recent = rule_rows[-RECENT_RULE_BETS:]
    profits = [bet_profit(row) for row in recent]
    recent_profit = sum(profits)
    recent_bets = len(profits)
    recent_roi = recent_profit / recent_bets if recent_bets else 0.0
    streak = loss_streak(profits)
    paused = recent_bets >= MIN_RECENT_BETS_TO_PAUSE and (
        recent_roi <= PAUSE_ROI or streak >= PAUSE_LOSS_STREAK
    )

    enriched = {
        **rule,
        "name": name,
        "recent_bets": recent_bets,
        "recent_profit_units": round(recent_profit, 2),
        "recent_roi": recent_roi,
        "recent_loss_streak": streak,
        "status": "paused" if paused else "active",
        "pause_reason": (
            f"Recent ROI {recent_roi:.2%}, loss streak {streak}"
            if paused else ""
        ),
    }
    return enriched


def write_health(rules):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "name",
        "league",
        "selection",
        "odds_band",
        "bets",
        "roi",
        "recent_bets",
        "recent_profit_units",
        "recent_roi",
        "recent_loss_streak",
        "status",
        "pause_reason",
    ]
    with HEALTH_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for rule in rules:
            writer.writerow({field: rule.get(field, "") for field in fieldnames})


def main():
    predictions = read_csv(PREDICTIONS_PATH)
    rules = [enrich_rule(rule, predictions) for rule in read_rules()]
    active_rules = [
        {key: value for key, value in rule.items() if key not in {"status", "pause_reason"}}
        for rule in rules
        if rule["status"] == "active"
    ]

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_RULES_PATH.write_text(json.dumps(active_rules, indent=2), encoding="utf-8")
    write_health(rules)
    paused = len(rules) - len(active_rules)
    print(f"Rule health updated: {len(active_rules)} active, {paused} paused")


if __name__ == "__main__":
    main()
