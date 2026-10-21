"""
Learn how much starting line-ups should move the pre-line-up price, and test
it fairly.

p(outcome) is proportional to p_pre(outcome) * exp(beta_outcome . x), where
p_pre is the no-vig pre-line-up price (football-data's Pinnacle odds, taken
before teams are announced) and x are the line-up features for both teams
(see lineup_features.py). The draw is the reference outcome, so only home and
away get coefficients.

Walk-forward test: each season is scored by a layer fitted only on earlier
seasons. Bets are checked against two prices:
  * best price before line-ups (what you could have had if you were early)
  * ESPN/DraftKings closing price (after line-ups: what's left if you're late)

Writes models/lineup_layer.json and reports/lineup_layer_report.txt.
"""

import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from lineup_features import FEATURES, feature_vector, match_features
from lineups import read_lineups
from train_1x2_model import read_matches, parse_float

ROOT = Path(__file__).resolve().parents[1]
LAYER_PATH = ROOT / "models" / "lineup_layer.json"
REPORT_PATH = ROOT / "reports" / "lineup_layer_report.txt"
L2 = 1.0
EDGE = 0.05
LABELS = ["H", "D", "A"]


def no_vig(prices):
    inverse = [1 / p for p in prices]
    total = sum(inverse)
    return [x / total for x in inverse]


def first_prices(match, prefixes):
    for prefix in prefixes:
        prices = [parse_float(match.get(prefix + side)) for side in "HDA"]
        if all(p > 1 for p in prices):
            return prices
    return None


def build_dataset() -> list:
    results = {(m["ParsedDate"].isoformat(), m["LeagueCode"], m["HomeTeam"], m["AwayTeam"]): m for m in read_matches()}
    data = []
    for event_id, info in match_features(read_lineups()).items():
        home_row, away_row = info["row_home"], info["row_away"]
        match = results.get((home_row["date"], home_row["league_code"], home_row["team"], away_row["team"]))
        if not match:
            continue
        pre = first_prices(match, ["PS", "Avg", "B365"])
        if not pre:
            continue
        best = [max(parse_float(match.get(prefix + side)) for prefix in ("Max", "PS", "Avg", "B365")) for side in "HDA"]
        close = [parse_float(home_row.get(f"close_{side}")) for side in "HDA"]
        data.append({
            "event_id": event_id,
            "season": home_row["season"],
            "league": home_row["league_code"],
            "y": LABELS.index(match["FTR"]),
            "p_pre": no_vig(pre),
            "x": feature_vector(info["home"], info["away"]),
            "best": best,
            "close": close if all(c > 1 for c in close) else None,
        })
    return data


def fit(rows: list) -> np.ndarray:
    """beta as a (2, n_features) array: row 0 = home win, row 1 = away win."""
    x = np.array([r["x"] for r in rows])
    y = np.array([r["y"] for r in rows])
    log_pre = np.log(np.array([r["p_pre"] for r in rows]))
    onehot = np.eye(3)[y]
    n_features = x.shape[1]

    def loss(flat):
        beta = flat.reshape(2, n_features)
        scores = log_pre.copy()
        scores[:, 0] += x @ beta[0]
        scores[:, 2] += x @ beta[1]
        scores -= scores.max(axis=1, keepdims=True)
        probs = np.exp(scores)
        probs /= probs.sum(axis=1, keepdims=True)
        nll = -np.sum(np.log(probs[np.arange(len(y)), y] + 1e-12)) + 0.5 * L2 * np.sum(flat ** 2)
        diff = probs - onehot
        grad = np.vstack([x.T @ diff[:, 0], x.T @ diff[:, 2]]) + L2 * beta
        return nll, grad.ravel()

    result = minimize(loss, np.zeros(2 * n_features), jac=True, method="L-BFGS-B")
    return result.x.reshape(2, n_features)


def predict(beta: np.ndarray, p_pre: list, x: list) -> list:
    scores = [math.log(p_pre[0]) + float(np.dot(beta[0], x)), math.log(p_pre[1]), math.log(p_pre[2]) + float(np.dot(beta[1], x))]
    top = max(scores)
    exps = [math.exp(s - top) for s in scores]
    total = sum(exps)
    return [e / total for e in exps]


def evaluate(beta, rows):
    out = {"n": len(rows), "ll_pre": 0.0, "ll_layer": 0.0, "best": [0, 0.0], "close": [0, 0.0], "close_rows": 0}
    for r in rows:
        p = predict(beta, r["p_pre"], r["x"])
        out["ll_pre"] -= math.log(r["p_pre"][r["y"]])
        out["ll_layer"] -= math.log(p[r["y"]])
        for key, prices in (("best", r["best"]), ("close", r["close"])):
            if not prices:
                continue
            if key == "close":
                out["close_rows"] += 1
            edges = [p[k] * prices[k] - 1 for k in range(3)]
            k = max(range(3), key=lambda i: edges[i])
            if edges[k] >= EDGE and 0.22 <= p[k] <= 0.65:
                out[key][0] += 1
                out[key][1] += prices[k] - 1 if k == r["y"] else -1.0
    return out


def main():
    rows = build_dataset()
    seasons = sorted({r["season"] for r in rows})
    lines = ["Line-up layer: walk-forward test", f"Matches with line-ups and pre-line-up odds: {len(rows)}", ""]
    lines.append("season  matches  pre-lineup logloss  with line-ups  bets@best  ROI@best  bets@close  ROI@close")
    totals = {"n": 0, "ll_pre": 0.0, "ll_layer": 0.0, "best": [0, 0.0], "close": [0, 0.0]}
    for i, season in enumerate(seasons):
        train = [r for r in rows if r["season"] < season]
        test = [r for r in rows if r["season"] == season]
        if len(train) < 1000 or not test:
            continue
        result = evaluate(fit(train), test)
        for key in ("n", "ll_pre", "ll_layer"):
            totals[key] += result[key]
        for key in ("best", "close"):
            totals[key][0] += result[key][0]
            totals[key][1] += result[key][1]
        lines.append(
            f"{season:6}  {result['n']:7}  {result['ll_pre'] / result['n']:18.4f}  {result['ll_layer'] / result['n']:13.4f}"
            f"  {result['best'][0]:9}  {result['best'][1] / max(result['best'][0], 1):+8.1%}"
            f"  {result['close'][0]:10}  {result['close'][1] / max(result['close'][0], 1):+9.1%}"
        )
    if totals["n"]:
        lines += [
            "",
            f"All test seasons: {totals['n']} matches",
            f"  log loss before line-ups {totals['ll_pre'] / totals['n']:.4f}, with line-up layer {totals['ll_layer'] / totals['n']:.4f} (lower is better)",
            f"  bets at best pre-line-up price: {totals['best'][0]}, ROI {totals['best'][1] / max(totals['best'][0], 1):+.1%}",
            f"  bets at closing price (after line-ups): {totals['close'][0]}, ROI {totals['close'][1] / max(totals['close'][0], 1):+.1%}",
        ]

    beta = fit(rows) if rows else np.zeros((2, 2 * len(FEATURES)))
    names = [f"home_{f}" for f in FEATURES] + [f"away_{f}" for f in FEATURES]
    lines += ["", "Coefficients (positive = more likely; draw is the reference):"]
    for name, home_coef, away_coef in zip(names, beta[0], beta[1]):
        lines.append(f"  {name:24} home win {home_coef:+.3f}   away win {away_coef:+.3f}")

    LAYER_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Only let the watcher suggest bets if the layer beat the pre-line-up price
    # on seasons it wasn't trained on.
    validated = bool(totals["n"] >= 1000 and totals["ll_layer"] < totals["ll_pre"])
    lines.append(f"\nValidated for live betting: {'yes' if validated else 'no (logging only)'}")
    LAYER_PATH.write_text(json.dumps({
        "validated": validated,
        "features": names,
        "beta_home_win": [round(float(v), 6) for v in beta[0]],
        "beta_away_win": [round(float(v), 6) for v in beta[1]],
        "trained_matches": len(rows),
    }, indent=2), encoding="utf-8")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
