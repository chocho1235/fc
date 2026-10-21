"""
Learn how starting line-ups move the price, and test it fairly.

Testing showed that line-ups don't help predict *results* beyond the price
once you're at kick-off: the market prices them in. What they do predict is
where the price is heading. So the layer predicts Pinnacle's closing price
(the sharpest estimate there is) from the pre-line-up price plus line-up
features:

    p_close(outcome) ~ p_pre(outcome) * exp(beta_outcome . x)

p_pre is the no-vig Pinnacle price taken before teams are announced
(football-data's PS columns), x are the line-up features for both teams (see
lineup_features.py), and the draw is the reference outcome. The live watcher
bets when the current price is still better than the predicted closing price,
i.e. when it got there before the market moved.

Walk-forward test: each season is scored by a layer fitted only on earlier
seasons. The layer is only enabled for live bets if it predicts the closing
price better than the pre-line-up price does.

Writes models/lineup_layer.json and reports/lineup_layer_report.txt.
"""

import json
import math
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from lineup_features import FEATURES, feature_vector, match_features
from lineups import read_lineups
from train_1x2_model import parse_float, read_matches

ROOT = Path(__file__).resolve().parents[1]
LAYER_PATH = ROOT / "models" / "lineup_layer.json"
REPORT_PATH = ROOT / "reports" / "lineup_layer_report.txt"
L2 = 1.0
EDGE = 0.03
MIN_TEST_MATCHES = 2000
LABELS = ["H", "D", "A"]


def no_vig(prices):
    inverse = [1 / p for p in prices]
    total = sum(inverse)
    return [x / total for x in inverse]


def prices(match, prefix):
    values = [parse_float(match.get(prefix + side)) for side in "HDA"]
    return values if all(v > 1 for v in values) else None


def build_dataset() -> list:
    results = {(m["ParsedDate"].isoformat(), m["LeagueCode"], m["HomeTeam"], m["AwayTeam"]): m for m in read_matches()}
    data = []
    for info in match_features(read_lineups()).values():
        home_row, away_row = info["row_home"], info["row_away"]
        match = results.get((home_row["date"], home_row["league_code"], home_row["team"], away_row["team"]))
        if not match:
            continue
        pre, close = prices(match, "PS"), prices(match, "PSC")
        best = [max(parse_float(match.get(prefix + side)) for prefix in ("Max", "PS", "Avg", "B365")) for side in "HDA"]
        if not pre or not close or min(best) <= 1:
            continue
        data.append({
            "season": home_row["season"],
            "y": LABELS.index(match["FTR"]),
            "pre": no_vig(pre),
            "close": no_vig(close),
            "best": best,
            "x": feature_vector(info["home"], info["away"]),
        })
    return data


def fit(rows: list) -> np.ndarray:
    """beta as a (2, n_features) array: row 0 = home win, row 1 = away win."""
    x = np.array([r["x"] for r in rows])
    offset = np.log(np.array([r["pre"] for r in rows]))
    target = np.array([r["close"] for r in rows])
    n_features = x.shape[1]

    def loss(flat):
        beta = flat.reshape(2, n_features)
        scores = offset.copy()
        scores[:, 0] += x @ beta[0]
        scores[:, 2] += x @ beta[1]
        scores -= scores.max(axis=1, keepdims=True)
        probs = np.exp(scores)
        probs /= probs.sum(axis=1, keepdims=True)
        value = -np.sum(target * np.log(probs + 1e-12)) + 0.5 * L2 * np.sum(flat ** 2)
        diff = probs - target
        grad = np.vstack([x.T @ diff[:, 0], x.T @ diff[:, 2]]) + L2 * beta
        return value, grad.ravel()

    return minimize(loss, np.zeros(2 * n_features), jac=True, method="L-BFGS-B").x.reshape(2, n_features)


def predict(beta: np.ndarray, pre: list, x: list) -> list:
    scores = [math.log(pre[0]) + float(np.dot(beta[0], x)), math.log(pre[1]), math.log(pre[2]) + float(np.dot(beta[1], x))]
    top = max(scores)
    exps = [math.exp(s - top) for s in scores]
    return [e / sum(exps) for e in exps]


def bets(rows, probabilities):
    """Bets at the best pre-line-up price where probability x price beats 1 + EDGE.
    Returns (profits, value against Pinnacle's closing price)."""
    profits, values = [], []
    for r, p in zip(rows, probabilities):
        edges = [p[k] * r["best"][k] - 1 for k in range(3)]
        k = max(range(3), key=lambda i: edges[i])
        if edges[k] >= EDGE and 0.15 <= p[k] <= 0.7:
            profits.append(r["best"][k] - 1 if k == r["y"] else -1.0)
            values.append(r["close"][k] * r["best"][k] - 1)
    return profits, values


def cross_entropy(rows, probabilities):
    return -sum(sum(c * math.log(q) for c, q in zip(r["close"], p)) for r, p in zip(rows, probabilities))


def main():
    rows = build_dataset()
    seasons = sorted({r["season"] for r in rows})
    lines = [
        "Line-up layer: walk-forward test",
        f"Matches with line-ups and Pinnacle prices before line-ups and at close: {len(rows)}",
        "",
        "How well each predicts Pinnacle's closing price (lower is better), and bets at the",
        f"best pre-line-up price with {EDGE:.0%}+ value. 'Value vs close' is the average",
        "edge against Pinnacle's closing price: the best long-run guide to profit.",
        "",
        "season  matches  pre-line-up  with line-ups  bets  value vs close  ROI   | no line-up info: bets  value vs close  ROI",
    ]
    totals = {"n": 0, "pre": 0.0, "layer": 0.0, "with": ([], []), "without": ([], [])}
    for season in seasons:
        train = [r for r in rows if r["season"] < season]
        test = [r for r in rows if r["season"] == season]
        if len(train) < 1000 or not test:
            continue
        beta = fit(train)
        layer_probs = [predict(beta, r["pre"], r["x"]) for r in test]
        pre_probs = [r["pre"] for r in test]
        with_lineups = bets(test, layer_probs)
        without = bets(test, pre_probs)
        ce_pre, ce_layer = cross_entropy(test, pre_probs), cross_entropy(test, layer_probs)
        totals["n"] += len(test)
        totals["pre"] += ce_pre
        totals["layer"] += ce_layer
        for key, result in (("with", with_lineups), ("without", without)):
            totals[key][0].extend(result[0])
            totals[key][1].extend(result[1])

        def describe(result):
            profits, values = result
            if not profits:
                return f"{0:4}  {'':>14}  {'':>5}"
            return f"{len(profits):4}  {np.mean(values):+14.1%}  {np.mean(profits):+5.1%}"

        lines.append(f"{season:6}  {len(test):7}  {ce_pre / len(test):11.5f}  {ce_layer / len(test):13.5f}  "
                     f"{describe(with_lineups)} | {describe(without)}")

    validated = bool(totals["n"] >= MIN_TEST_MATCHES and totals["layer"] < totals["pre"])
    if totals["n"]:
        lines += ["", f"All test seasons: {totals['n']} matches",
                  f"  closing-price score: pre-line-up {totals['pre'] / totals['n']:.5f}, with line-ups {totals['layer'] / totals['n']:.5f}"]
        for key, label in (("with", "with line-ups"), ("without", "price shopping only")):
            profits, values = totals[key]
            if profits:
                lines.append(f"  {label:20}: {len(profits)} bets, value vs close {np.mean(values):+.1%}, "
                             f"beat the close {np.mean(np.array(values) > 0):.0%}, ROI {np.mean(profits):+.1%}")

    beta = fit(rows) if rows else np.zeros((2, 2 * len(FEATURES)))
    names = [f"home_{f}" for f in FEATURES] + [f"away_{f}" for f in FEATURES]
    lines += ["", "Coefficients (positive = closing price moves towards it; draw is the reference):"]
    for name, home_coef, away_coef in zip(names, beta[0], beta[1]):
        lines.append(f"  {name:24} home win {home_coef:+.3f}   away win {away_coef:+.3f}")
    lines.append(f"\nValidated for live betting: {'yes' if validated else 'no (logging only)'}")

    LAYER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LAYER_PATH.write_text(json.dumps({
        "validated": validated,
        "target": "pinnacle_closing_price",
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
