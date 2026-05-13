import csv
import json
import os
from pathlib import Path

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from train_1x2_model import (
    BET_THRESHOLDS,
    DEFAULT_BET_THRESHOLD,
    LABELS,
    MODELS_DIR,
    ODDS_BANDS,
    PROCESSED_DIR,
    REPORTS_DIR,
    TRAINING_WINDOW_SEASONS,
    average_summary,
    build_dataset,
    candidate_bets,
    decimal_return,
    evaluate,
    fair_odds,
    feature_names,
    find_best_bet,
    read_matches,
    value_odds,
    write_predictions,
)


MODEL_PATH = MODELS_DIR / "sklearn_one_x_two_model.joblib"
MODEL_META_PATH = MODELS_DIR / "sklearn_one_x_two_model_meta.json"
OVER_25_MODEL_PATH = MODELS_DIR / "sklearn_over_25_model.joblib"
OVER_25_META_PATH = MODELS_DIR / "sklearn_over_25_model_meta.json"
BETTING_RULES_PATH = MODELS_DIR / "betting_rules.json"
DEFAULT_OVER_25_BET_THRESHOLD = float(os.getenv("OVER_25_BET_THRESHOLD", "0.10") or "0.10")
MIN_RULE_BETS = int(os.getenv("MIN_RULE_BETS", "20") or "20")
MIN_RULE_ROI = float(os.getenv("MIN_RULE_ROI", "0.03") or "0.03")


def rows_to_matrix(rows, names):
    return np.array([[row[name] for name in names] for row in rows], dtype=float)


def rows_to_labels(rows):
    return np.array([row["result"] for row in rows])


def train_classifier(train_rows, names, labels=None):
    x_train = rows_to_matrix(train_rows, names)
    y_train = labels if labels is not None else rows_to_labels(train_rows)
    class_weight = os.getenv("SKLEARN_CLASS_WEIGHT", "none")
    if class_weight.lower() in {"none", "null", "0"}:
        class_weight = None
    base = Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(
            C=float(os.getenv("SKLEARN_LOGISTIC_C", "0.10")),
            class_weight=class_weight,
            max_iter=3000,
            solver="lbfgs",
        )),
    ])
    calibrated = CalibratedClassifierCV(
        estimator=base,
        method=os.getenv("SKLEARN_CALIBRATION_METHOD", "sigmoid"),
        cv=int(os.getenv("SKLEARN_CALIBRATION_CV", "3")),
    )
    calibrated.fit(x_train, y_train)
    return calibrated


def predict_rows(model, rows, names):
    probabilities = model.predict_proba(rows_to_matrix(rows, names))
    classes = list(model.classes_)
    output = []
    for row_probs in probabilities:
        by_label = {label: 0.0 for label in LABELS}
        for label, probability in zip(classes, row_probs):
            by_label[label] = float(probability)
        output.append(by_label)
    return output


def over_25_feature_names(rows):
    names = feature_names(rows)
    extra_names = ["over_25_market_prob", "under_25_market_prob", "over_25_odds", "under_25_odds"]
    return names + [name for name in extra_names if name in rows[0] and name not in names]


def over_25_labels(rows):
    return np.array([1 if row["total_goals"] > 2.5 else 0 for row in rows])


def predict_over_25(model, rows, names):
    probabilities = model.predict_proba(rows_to_matrix(rows, names))
    classes = list(model.classes_)
    if 1 not in classes:
        return [0.0 for _ in rows]
    positive_index = classes.index(1)
    return [float(row[positive_index]) for row in probabilities]


def binary_log_loss(rows, probabilities):
    losses = []
    for row, probability in zip(rows, probabilities):
        actual = 1 if row["total_goals"] > 2.5 else 0
        probability = min(max(probability, 0.000001), 0.999999)
        if actual:
            losses.append(-np.log(probability))
        else:
            losses.append(-np.log(1 - probability))
    return float(sum(losses) / len(losses)) if losses else 0.0


def evaluate_over_25(rows, probabilities, threshold=DEFAULT_BET_THRESHOLD):
    correct = 0
    bets = []
    for row, probability in zip(rows, probabilities):
        actual = row["total_goals"] > 2.5
        if (probability >= 0.5) == actual:
            correct += 1
        required_odds = value_odds(probability, threshold)
        book_odds = row.get("over_25_odds", 0)
        if probability >= 0.45 and required_odds > 0 and book_odds >= required_odds:
            bets.append(book_odds - 1 if actual else -1)

    profit = sum(bets)
    return {
        "matches": len(rows),
        "accuracy": correct / len(rows) if rows else 0.0,
        "log_loss": binary_log_loss(rows, probabilities),
        "bets": len(bets),
        "profit_units": profit,
        "roi": profit / len(bets) if bets else 0.0,
    }


def rolling_candidate_rows(test_rows, probabilities):
    rows = []
    for row, probs in zip(test_rows, probabilities):
        for candidate in candidate_bets(row, probs):
            rows.append({
                "season": row["season"],
                "league": row.get("league") or row.get("league_code") or "",
                "selection": candidate["selection"],
                "odds_band": candidate["odds_band"],
                "odds": candidate["odds"],
                "edge": candidate["edge"],
                "profit": decimal_return(row["result"], candidate["selection"], candidate["odds"]),
            })
    return rows


def aggregate_rule(candidates, league, selection, odds_band, min_edge):
    band = next((item for item in ODDS_BANDS if item["name"] == odds_band), None)
    matches = []
    for item in candidates:
        if league != "all" and item["league"] != league:
            continue
        if selection != "all" and item["selection"] != selection:
            continue
        if odds_band != "all" and item["odds_band"] != odds_band:
            continue
        if item["edge"] < min_edge:
            continue
        matches.append(item)

    if not matches:
        return None

    profit = sum(item["profit"] for item in matches)
    bets = len(matches)
    return {
        "league": league,
        "selection": selection,
        "odds_band": odds_band,
        "min_odds": band["min_odds"] if band else 1.01,
        "max_odds": band["max_odds"] if band else 100.0,
        "min_edge": min_edge,
        "bets": bets,
        "profit_units": round(profit, 2),
        "roi": profit / bets if bets else 0.0,
    }


def optimize_betting_rules(candidates):
    if not candidates:
        return []

    leagues = sorted({item["league"] for item in candidates if item["league"]})
    selections = LABELS
    odds_bands = [item["name"] for item in ODDS_BANDS]
    rules = []

    for league in [*leagues, "all"]:
        for selection in selections:
            for odds_band in [*odds_bands, "all"]:
                for threshold in BET_THRESHOLDS:
                    rule = aggregate_rule(candidates, league, selection, odds_band, threshold)
                    if not rule:
                        continue
                    if rule["bets"] >= MIN_RULE_BETS and rule["roi"] >= MIN_RULE_ROI and rule["profit_units"] > 0:
                        rules.append(rule)

    rules.sort(key=lambda item: (item["roi"], item["profit_units"], item["bets"]), reverse=True)
    selected = []
    covered = set()
    for rule in rules:
        key = (rule["league"], rule["selection"], rule["odds_band"])
        if key in covered:
            continue
        selected.append(rule)
        covered.add(key)
        if len(selected) >= 16:
            break
    return selected


def season_train_rows(rows, season, seasons):
    previous_seasons = [item for item in seasons if item < season]
    if TRAINING_WINDOW_SEASONS > 0:
        previous_seasons = previous_seasons[-TRAINING_WINDOW_SEASONS:]
    return [row for row in rows if row["season"] in previous_seasons]


def rolling_backtest(rows, names):
    seasons = sorted({row["season"] for row in rows})
    season_summaries = []
    all_rows = []
    all_candidates = []
    latest_rules = []
    for season in seasons[3:]:
        train_rows = season_train_rows(rows, season, seasons)
        test_rows = [row for row in rows if row["season"] == season]
        if not train_rows or not test_rows:
            continue
        model = train_classifier(train_rows, names)
        probabilities = predict_rows(model, test_rows, names)
        rules = optimize_betting_rules(all_candidates)
        latest_rules = rules
        summary = evaluate(test_rows, probabilities, threshold=DEFAULT_BET_THRESHOLD, rules=rules)
        threshold_summaries = {
            f"{threshold:.2f}": evaluate(test_rows, probabilities, threshold=threshold)
            for threshold in BET_THRESHOLDS
        }
        season_summaries.append({
            "season": season,
            "training_matches": len(train_rows),
            **summary,
            "thresholds": threshold_summaries,
        })
        for row, probs in zip(test_rows, probabilities):
            best_bet, best_edge = find_best_bet(row, probs, DEFAULT_BET_THRESHOLD, rules)
            all_rows.append({
                "season": season,
                "date": row["date"],
                "league": row.get("league", ""),
                "home_team": row["home_team"],
                "away_team": row["away_team"],
                "result": row["result"],
                "home_bookmaker_odds": row["home_odds"],
                "draw_bookmaker_odds": row["draw_odds"],
                "away_bookmaker_odds": row["away_odds"],
                "home_win_probability": round(probs["H"], 4),
                "draw_probability": round(probs["D"], 4),
                "away_win_probability": round(probs["A"], 4),
                "predicted_result": max(probs, key=probs.get),
                "suggested_bet": best_bet or "",
                "suggested_edge": round(best_edge, 4) if best_bet else "",
            })
        all_candidates.extend(rolling_candidate_rows(test_rows, probabilities))
    write_rolling_outputs(season_summaries, all_rows)
    final_rules = optimize_betting_rules(all_candidates)
    BETTING_RULES_PATH.write_text(json.dumps(final_rules, indent=2), encoding="utf-8")
    return season_summaries, latest_rules, final_rules


def rolling_over_25_backtest(rows, names):
    seasons = sorted({row["season"] for row in rows})
    season_summaries = []
    for season in seasons[3:]:
        train_rows = season_train_rows(rows, season, seasons)
        test_rows = [row for row in rows if row["season"] == season]
        if not train_rows or not test_rows:
            continue
        model = train_classifier(train_rows, names, labels=over_25_labels(train_rows))
        probabilities = predict_over_25(model, test_rows, names)
        summary = evaluate_over_25(test_rows, probabilities, threshold=DEFAULT_OVER_25_BET_THRESHOLD)
        season_summaries.append({
            "season": season,
            "training_matches": len(train_rows),
            **summary,
        })
    write_over_25_rolling_outputs(season_summaries)
    return season_summaries


def write_rolling_outputs(season_summaries, all_rows):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORTS_DIR / "sklearn_rolling_backtest.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["season", "training_matches", "matches", "accuracy", "log_loss", "bets", "profit_units", "roi"]
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
        with (PROCESSED_DIR / "sklearn_rolling_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(all_rows[0].keys()))
            writer.writeheader()
            writer.writerows(all_rows)


def write_over_25_rolling_outputs(season_summaries):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with (REPORTS_DIR / "sklearn_over_25_rolling_backtest.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["season", "training_matches", "matches", "accuracy", "log_loss", "bets", "profit_units", "roi"]
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


def main():
    matches = read_matches()
    if not matches:
        raise SystemExit("No raw CSV files found. Run download_data.py first.")

    rows = build_dataset(matches)
    names = feature_names(rows)
    over_names = over_25_feature_names(rows)
    seasons = sorted({row["season"] for row in rows})
    test_season = seasons[-1]
    train_rows = season_train_rows(rows, test_season, seasons)
    test_rows = [row for row in rows if row["season"] == test_season]

    model = train_classifier(train_rows, names)
    probabilities = predict_rows(model, test_rows, names)
    rolling_summaries, latest_rules, final_rules = rolling_backtest(rows, names)
    summary = evaluate(test_rows, probabilities, threshold=DEFAULT_BET_THRESHOLD, rules=latest_rules)
    rolling_average = average_summary(rolling_summaries)

    over_model = train_classifier(train_rows, over_names, labels=over_25_labels(train_rows))
    over_probabilities = predict_over_25(over_model, test_rows, over_names)
    over_summary = evaluate_over_25(test_rows, over_probabilities, threshold=DEFAULT_OVER_25_BET_THRESHOLD)
    over_rolling_summaries = rolling_over_25_backtest(rows, over_names)
    over_rolling_average = average_summary(over_rolling_summaries)

    write_predictions(
        test_rows,
        probabilities,
        over_probabilities,
        bet_threshold=DEFAULT_BET_THRESHOLD,
        over_25_bet_threshold=DEFAULT_OVER_25_BET_THRESHOLD,
        rules=latest_rules,
    )
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    joblib.dump(over_model, OVER_25_MODEL_PATH)
    MODEL_META_PATH.write_text(json.dumps({
        "labels": LABELS,
        "features": names,
        "test_season": test_season,
        "bet_threshold": DEFAULT_BET_THRESHOLD,
        "training_window_seasons": TRAINING_WINDOW_SEASONS or "all",
        "model_type": "sklearn_calibrated_logistic_regression",
        "rules_path": str(BETTING_RULES_PATH.relative_to(MODELS_DIR)),
        "active_rules": len(final_rules),
    }, indent=2), encoding="utf-8")
    OVER_25_META_PATH.write_text(json.dumps({
        "labels": ["Under 2.5", "Over 2.5"],
        "features": over_names,
        "test_season": test_season,
        "bet_threshold": DEFAULT_BET_THRESHOLD,
        "over_25_bet_threshold": DEFAULT_OVER_25_BET_THRESHOLD,
        "training_window_seasons": TRAINING_WINDOW_SEASONS or "all",
        "model_type": "sklearn_calibrated_logistic_regression_binary_over_25",
    }, indent=2), encoding="utf-8")

    report = [
        "Multi-league 1X2 Backtest - scikit-learn calibrated logistic regression",
        f"Training matches: {len(train_rows)}",
        f"Test season: {test_season}",
        f"Test matches: {summary['matches']}",
        f"Accuracy: {summary['accuracy']:.2%}",
        f"Log loss: {summary['log_loss']:.4f}",
        f"Value bets at {DEFAULT_BET_THRESHOLD:.0%}+ edge: {summary['bets']}",
        f"Flat-stake profit: {summary['profit_units']:.2f} units",
        f"ROI: {summary['roi']:.2%}",
        f"Active betting rules: {len(final_rules)}",
        "",
        "Rolling season backtest",
        f"Seasons tested: {rolling_average['seasons']}",
        f"Rolling matches: {rolling_average['matches']}",
        f"Rolling accuracy: {rolling_average['accuracy']:.2%}",
        f"Rolling log loss: {rolling_average['log_loss']:.4f}",
        f"Rolling value bets at {DEFAULT_BET_THRESHOLD:.0%}+ edge: {rolling_average['bets']}",
        f"Rolling flat-stake profit: {rolling_average['profit_units']:.2f} units",
        f"Rolling ROI: {rolling_average['roi']:.2%}",
        "",
        "Over 2.5 Backtest - scikit-learn calibrated logistic regression",
        f"Over 2.5 test matches: {over_summary['matches']}",
        f"Over 2.5 accuracy: {over_summary['accuracy']:.2%}",
        f"Over 2.5 log loss: {over_summary['log_loss']:.4f}",
        f"Over 2.5 value bets at {DEFAULT_OVER_25_BET_THRESHOLD:.0%}+ edge: {over_summary['bets']}",
        f"Over 2.5 flat-stake profit: {over_summary['profit_units']:.2f} units",
        f"Over 2.5 ROI: {over_summary['roi']:.2%}",
        "",
        "Over 2.5 rolling season backtest",
        f"Over 2.5 rolling seasons tested: {over_rolling_average['seasons']}",
        f"Over 2.5 rolling matches: {over_rolling_average['matches']}",
        f"Over 2.5 rolling accuracy: {over_rolling_average['accuracy']:.2%}",
        f"Over 2.5 rolling log loss: {over_rolling_average['log_loss']:.4f}",
        f"Over 2.5 rolling value bets at {DEFAULT_OVER_25_BET_THRESHOLD:.0%}+ edge: {over_rolling_average['bets']}",
        f"Over 2.5 rolling flat-stake profit: {over_rolling_average['profit_units']:.2f} units",
        f"Over 2.5 rolling ROI: {over_rolling_average['roi']:.2%}",
    ]
    report_text = "\n".join(report) + "\n"
    (REPORTS_DIR / "sklearn_backtest_summary.txt").write_text(report_text, encoding="utf-8")
    (REPORTS_DIR / "backtest_summary.txt").write_text(report_text, encoding="utf-8")
    print("\n".join(report))


if __name__ == "__main__":
    main()
