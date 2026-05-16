"""
Dixon-Coles bivariate Poisson model for football goal prediction.

Fits attack/defence/home-advantage/rho parameters per league using
time-decay weighted maximum likelihood. Produces 1X2 probabilities
by summing over the score matrix.

Outputs:
  models/dixon_coles_{league_code}.json  — fitted parameters
  data/processed/dc_rolling_predictions.csv  — backtest probabilities
  data/processed/dc_upcoming_predictions.csv — upcoming fixture probs
"""

import csv
import json
import math
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

sys.path.insert(0, str(Path(__file__).resolve().parent))
from leagues import DEFAULT_LEAGUE_CODES, league_name
from train_1x2_model import (
    MODELS_DIR,
    PROCESSED_DIR,
    RAW_DIR,
    parse_date,
    parse_float,
    read_matches,
    read_upcoming_fixtures,
)

XI = float(os.getenv("DC_XI", "0.0018"))   # time-decay; half-life ≈ 385 days
MAX_GOALS = 10
LABELS = ["H", "D", "A"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def decay_weight(match_date: date, ref_date: date, xi: float = XI) -> float:
    delta = (ref_date - match_date).days
    return math.exp(-xi * max(delta, 0))


def tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1.0 - rho * lam * mu
    if x == 0 and y == 1:
        return 1.0 + rho * lam
    if x == 1 and y == 0:
        return 1.0 + rho * mu
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def dc_probabilities(lam: float, mu: float, rho: float, max_goals: int = MAX_GOALS) -> dict:
    home_win = draw = away_win = 0.0
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = tau(x, y, lam, mu, rho) * poisson.pmf(x, lam) * poisson.pmf(y, mu)
            if x > y:
                home_win += p
            elif x == y:
                draw += p
            else:
                away_win += p
    total = home_win + draw + away_win
    if total <= 0:
        return {"H": 1 / 3, "D": 1 / 3, "A": 1 / 3}
    return {"H": home_win / total, "D": draw / total, "A": away_win / total}


# ── Data extraction ──────────────────────────────────────────────────────────

def to_dc_format(matches: list, league_code: str) -> list:
    rows = []
    for m in matches:
        if m["LeagueCode"] != league_code:
            continue
        try:
            home_goals = int(m["FTHG"])
            away_goals = int(m["FTAG"])
        except (ValueError, TypeError):
            continue
        rows.append({
            "season": m["Season"],
            "date": m["ParsedDate"],
            "home": m["HomeTeam"],
            "away": m["AwayTeam"],
            "home_goals": home_goals,
            "away_goals": away_goals,
            "result": m["FTR"],
        })
    return rows


# ── Fitting ──────────────────────────────────────────────────────────────────

def _neg_log_likelihood(params, matches, teams, ref_date, xi):
    n = len(teams)
    attack = np.concatenate([[0.0], params[: n - 1]])
    defence = params[n - 1 : 2 * n - 1]
    home_adv = params[2 * n - 1]
    rho = params[2 * n]

    idx = {t: i for i, t in enumerate(teams)}
    total = 0.0

    for m in matches:
        i = idx[m["home"]]
        j = idx[m["away"]]
        x = m["home_goals"]
        y = m["away_goals"]
        w = decay_weight(m["date"], ref_date, xi)

        lam = math.exp(attack[i] + defence[j] + home_adv)
        mu = math.exp(attack[j] + defence[i])

        t = tau(x, y, lam, mu, rho)
        if t <= 0:
            return 1e12

        ll = w * (math.log(t) + poisson.logpmf(x, lam) + poisson.logpmf(y, mu))
        total += ll

    return -total


def fit_league(matches: list, xi: float = XI, quick: bool = False) -> dict:
    teams = sorted({m["home"] for m in matches} | {m["away"] for m in matches})
    n = len(teams)
    ref_date = max(m["date"] for m in matches)

    x0 = np.concatenate([
        np.zeros(n - 1),
        np.zeros(n),
        [0.25],
        [0.0],
    ])
    bounds = (
        [(None, None)] * (n - 1)
        + [(None, None)] * n
        + [(None, None)]
        + [(-0.99, 0.99)]
    )

    # Quick mode: looser tolerances for the 6-hourly refresh (much faster,
    # negligible accuracy loss for short-horizon predictions).
    # Full mode: tight tolerances for the weekly retrain.
    opts = {"maxiter": 400, "ftol": 1e-7, "gtol": 1e-5} if quick else {"maxiter": 2000, "ftol": 1e-9, "gtol": 1e-6}

    result = minimize(
        _neg_log_likelihood,
        x0,
        args=(matches, teams, ref_date, xi),
        method="L-BFGS-B",
        bounds=bounds,
        options=opts,
    )

    attack_raw = np.concatenate([[0.0], result.x[: n - 1]])
    defence_raw = result.x[n - 1 : 2 * n - 1]
    home_adv = float(result.x[2 * n - 1])
    rho = float(result.x[2 * n])

    return {
        "teams": teams,
        "attack": dict(zip(teams, attack_raw.tolist())),
        "defence": dict(zip(teams, defence_raw.tolist())),
        "home_advantage": home_adv,
        "rho": rho,
        "ref_date": ref_date.isoformat(),
        "n_matches": len(matches),
        "converged": bool(result.success),
    }


# ── Prediction ───────────────────────────────────────────────────────────────

def _league_avg(params: dict) -> tuple:
    avg_atk = float(np.mean(list(params["attack"].values())))
    avg_def = float(np.mean(list(params["defence"].values())))
    return avg_atk, avg_def


def predict_match(params: dict, home_team: str, away_team: str) -> tuple:
    avg_atk, avg_def = _league_avg(params)
    ha = params["attack"].get(home_team, avg_atk)
    hd = params["defence"].get(home_team, avg_def)
    aa = params["attack"].get(away_team, avg_atk)
    ad = params["defence"].get(away_team, avg_def)
    lam = math.exp(ha + ad + params["home_advantage"])
    mu = math.exp(aa + hd)
    probs = dc_probabilities(lam, mu, params["rho"])
    return probs, lam, mu


# ── Rolling backtest ─────────────────────────────────────────────────────────

def rolling_dc_backtest(league_code: str, league_matches: list) -> list:
    seasons = sorted({m["season"] for m in league_matches})
    rows = []
    for i, season in enumerate(seasons):
        if i < 3:
            continue
        prior = seasons[max(0, i - 5) : i]
        train = [m for m in league_matches if m["season"] in prior]
        test = [m for m in league_matches if m["season"] == season]
        if len(train) < 50 or not test:
            continue
        params = fit_league(train)
        for m in test:
            probs, lam, mu = predict_match(params, m["home"], m["away"])
            rows.append({
                "season": season,
                "date": m["date"].isoformat(),
                "league_code": league_code,
                "home_team": m["home"],
                "away_team": m["away"],
                "result": m["result"],
                "dc_home_prob": round(probs["H"], 6),
                "dc_draw_prob": round(probs["D"], 6),
                "dc_away_prob": round(probs["A"], 6),
                "dc_lambda": round(lam, 4),
                "dc_mu": round(mu, 4),
            })
    return rows


def _write_csv(path: Path, rows: list, fieldnames: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ── Main ─────────────────────────────────────────────────────────────────────

QUICK = os.getenv("DC_QUICK", "0") == "1"

DC_ROLLING_PATH = PROCESSED_DIR / "dc_rolling_predictions.csv"
DC_UPCOMING_PATH = PROCESSED_DIR / "dc_upcoming_predictions.csv"


def main(quick: bool = QUICK) -> None:
    matches = read_matches()
    all_rolling: list = []

    for league_code in DEFAULT_LEAGUE_CODES:
        league_matches = to_dc_format(matches, league_code)
        if len(league_matches) < 50:
            print(f"  {league_code}: too few matches ({len(league_matches)}), skipping")
            continue

        seasons = sorted({m["season"] for m in league_matches})

        if not quick:
            print(f"  {league_code}: rolling backtest ({len(seasons)} seasons) ...", flush=True)
            rows = rolling_dc_backtest(league_code, league_matches)
            all_rolling.extend(rows)
            print(f"    {len(rows)} backtest predictions")

        # Final fit on last 5 seasons of completed matches
        train_seasons = seasons[-5:]
        train = [m for m in league_matches if m["season"] in train_seasons]
        params = fit_league(train, quick=quick)
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = MODELS_DIR / f"dixon_coles_{league_code}.json"
        out_path.write_text(json.dumps(params, indent=2), encoding="utf-8")
        print(
            f"  {league_code}: {len(params['teams'])} teams, "
            f"rho={params['rho']:.4f}, home_adv={params['home_advantage']:.4f}, "
            f"converged={params['converged']}"
        )

    if not quick and all_rolling:
        _write_csv(
            DC_ROLLING_PATH,
            all_rolling,
            ["season", "date", "league_code", "home_team", "away_team",
             "result", "dc_home_prob", "dc_draw_prob", "dc_away_prob",
             "dc_lambda", "dc_mu"],
        )
        print(f"Wrote {len(all_rolling)} rows → {DC_ROLLING_PATH}")

    # Predict upcoming fixtures
    latest_date = max(m["ParsedDate"] for m in matches)
    fixtures = read_upcoming_fixtures(latest_date)
    upcoming_rows = _predict_upcoming(fixtures, matches)
    _write_csv(
        DC_UPCOMING_PATH,
        upcoming_rows,
        ["date", "league_code", "home_team", "away_team",
         "dc_home_prob", "dc_draw_prob", "dc_away_prob",
         "dc_lambda", "dc_mu"],
    )
    print(f"Wrote {len(upcoming_rows)} upcoming DC predictions → {DC_UPCOMING_PATH}")


def _predict_upcoming(fixtures: list, matches: list) -> list:
    rows = []
    for league_code in DEFAULT_LEAGUE_CODES:
        model_path = MODELS_DIR / f"dixon_coles_{league_code}.json"
        if not model_path.exists():
            continue
        params = json.loads(model_path.read_text(encoding="utf-8"))
        # Convert date strings back to date objects for params (not needed here)
        for fix in fixtures:
            if fix.get("Div") != league_code:
                continue
            home = fix.get("HomeTeam", "")
            away = fix.get("AwayTeam", "")
            if not home or not away:
                continue
            try:
                d = parse_date(fix["Date"])
                probs, lam, mu = predict_match(params, home, away)
                rows.append({
                    "date": d.isoformat(),
                    "league_code": league_code,
                    "home_team": home,
                    "away_team": away,
                    "dc_home_prob": round(probs["H"], 6),
                    "dc_draw_prob": round(probs["D"], 6),
                    "dc_away_prob": round(probs["A"], 6),
                    "dc_lambda": round(lam, 4),
                    "dc_mu": round(mu, 4),
                })
            except Exception:
                continue
    return rows


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Skip rolling backtest")
    args = parser.parse_args()
    main(quick=args.quick)
