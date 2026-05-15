import csv
import json
from datetime import datetime, timezone
from pathlib import Path


MODEL_ROOT = Path(__file__).resolve().parents[1]
SITE_ROOT = MODEL_ROOT.parent
PREDICTIONS_PATH = MODEL_ROOT / "data" / "processed" / "predictions.csv"
ROLLING_PREDICTIONS_PATH = MODEL_ROOT / "data" / "processed" / "sklearn_rolling_predictions.csv"
UPCOMING_PATH = MODEL_ROOT / "data" / "processed" / "upcoming_predictions.csv"
SUMMARY_PATH = MODEL_ROOT / "reports" / "backtest_summary.txt"
BETTING_RULES_PATH = MODEL_ROOT / "models" / "betting_rules.json"
ACTIVE_BETTING_RULES_PATH = MODEL_ROOT / "models" / "betting_rules_active.json"
BUILDER_PROFILE_PATH = MODEL_ROOT / "models" / "builder_profile.json"
RULE_HEALTH_PATH = MODEL_ROOT / "reports" / "betting_rule_health.csv"
CLOSING_LINE_PATH = MODEL_ROOT / "data" / "processed" / "closing_line_report.csv"
ENSEMBLE_META_PATH = MODEL_ROOT / "models" / "ensemble_meta.json"
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


def parse_builder_profile():
    if not BUILDER_PROFILE_PATH.exists():
        return {}
    return json.loads(BUILDER_PROFILE_PATH.read_text(encoding="utf-8"))


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


def _pnl_series_point(row, balance, won):
    """Compact series point: [balance, date_str, match_str, won_bool]."""
    return [round(balance, 2), row.get("date", ""), f"{row.get('home_team','')} v {row.get('away_team','')}", won]


def passive_pnl():
    """Simulate passive P&L: bet the highest-prob 1X2 outcome whenever model is >= 65% confident."""
    if not ROLLING_PREDICTIONS_PATH.exists():
        return {"starting_bank": 1000, "current_bank": 1000, "bets": 0, "wins": 0, "win_rate": 0.0, "roi": 0.0, "series": []}
    with ROLLING_PREDICTIONS_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    STAKE = 10.0
    STARTING_BANK = 1000.0
    THRESHOLD = 0.65
    balance = STARTING_BANK
    bets = 0
    wins = 0
    series = []
    settled = [r for r in rows if r.get("result")]
    for row in settled:
        probs = {
            "H": to_float(row.get("home_win_probability", 0)),
            "D": to_float(row.get("draw_probability", 0)),
            "A": to_float(row.get("away_win_probability", 0)),
        }
        best_label = max(probs, key=probs.get)
        best_prob = probs[best_label]
        if best_prob < THRESHOLD:
            continue
        fair_odds = 1 / best_prob
        bets += 1
        won = row.get("result") == best_label
        if won:
            balance += STAKE * (fair_odds - 1)
            wins += 1
        else:
            balance -= STAKE
        series.append(_pnl_series_point(row, balance, won))
    total_staked = bets * STAKE
    roi = (balance - STARTING_BANK) / total_staked if total_staked > 0 else 0.0
    return {
        "starting_bank": STARTING_BANK,
        "current_bank": round(balance, 2),
        "bets": bets,
        "wins": wins,
        "win_rate": round(wins / bets, 4) if bets else 0.0,
        "roi": round(roi, 4),
        "series": series,
    }


def value_bet_pnl():
    if not ROLLING_PREDICTIONS_PATH.exists():
        return {"starting_bank": 1000, "current_bank": 1000, "bets": 0, "wins": 0, "win_rate": 0.0, "roi": 0.0, "series": []}
    with ROLLING_PREDICTIONS_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    STAKE = 10.0
    STARTING_BANK = 1000.0
    balance = STARTING_BANK
    bets = 0
    wins = 0
    series = []
    settled = [r for r in rows if r.get("suggested_bet") and r.get("result")]
    for row in settled:
        suggested = row.get("suggested_bet", "")
        result = row.get("result", "")
        odds_key = {"H": "home_bookmaker_odds", "D": "draw_bookmaker_odds", "A": "away_bookmaker_odds"}.get(suggested, "")
        odds = to_float(row.get(odds_key, 0))
        if odds <= 1:
            continue
        bets += 1
        won = result == suggested
        if won:
            balance += STAKE * (odds - 1)
            wins += 1
        else:
            balance -= STAKE
        series.append(_pnl_series_point(row, balance, won))
    total_staked = bets * STAKE
    roi = (balance - STARTING_BANK) / total_staked if total_staked > 0 else 0.0
    return {
        "starting_bank": STARTING_BANK,
        "current_bank": round(balance, 2),
        "bets": bets,
        "wins": wins,
        "win_rate": round(wins / bets, 4) if bets else 0.0,
        "roi": round(roi, 4),
        "series": series,
    }


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


def builder_summary(rows):
    settled = [row for row in rows if row.get("builder_result") in {"won", "partial", "lost"}]
    won = [row for row in settled if row.get("builder_result") == "won"]
    partial = [row for row in settled if row.get("builder_result") == "partial"]
    lost = [row for row in settled if row.get("builder_result") == "lost"]
    leg_total = sum(int(to_float(row.get("builder_legs_total"))) for row in settled)
    leg_wins = sum(int(to_float(row.get("builder_legs_won"))) for row in settled)
    return {
        "settled": len(settled),
        "won": len(won),
        "partial": len(partial),
        "lost": len(lost),
        "win_rate": round(len(won) / len(settled), 4) if settled else 0.0,
        "avoid_loss_rate": round((len(won) + len(partial)) / len(settled), 4) if settled else 0.0,
        "leg_hit_rate": round(leg_wins / leg_total, 4) if leg_total else 0.0,
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
    builder_profile = parse_builder_profile()
    suggested = [row for row in predictions if row.get("suggested_bet")]
    settled_builders = [row for row in predictions if row.get("builder_result") in {"won", "partial", "lost"}]
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
        "builder_summary": builder_summary(predictions),
        "builder_profile": builder_profile,
        "builder_history": settled_builders[-20:],
        "betting_rules": parse_active_betting_rules(),
        "betting_rule_health": parse_rule_health(),
        "closing_line_summary": closing_line_summary(closing_lines),
        "closing_line_recent": closing_lines[-12:],
        "value_bet_pnl": value_bet_pnl(),
        "passive_pnl": passive_pnl(),
        "ensemble_meta": json.loads(ENSEMBLE_META_PATH.read_text()) if ENSEMBLE_META_PATH.exists() else None,
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
