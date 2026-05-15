"""
Blend Dixon-Coles 1X2 probabilities with LightGBM predictions.

Reads:
  data/processed/upcoming_predictions.csv (from predict_upcoming_sklearn_fast)
  data/processed/dc_upcoming_predictions.csv (from train_dixon_coles)
  data/processed/sklearn_rolling_predictions.csv (backtest for blend calibration)
  data/processed/dc_rolling_predictions.csv (backtest for blend calibration)

Overwrites home_win_probability / draw_probability / away_win_probability
in upcoming_predictions.csv with the calibrated ensemble blend.

Saves models/ensemble_meta.json with w_dc, log-loss, and match count.
"""

import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_1x2_model import MODELS_DIR, PROCESSED_DIR

LABELS = ["H", "D", "A"]
MIN_CALIBRATION_ROWS = 200
EPS = 1e-9


# ── Blending ─────────────────────────────────────────────────────────────────

def _to_logodds(probs: dict) -> dict:
    ref = probs["D"] + EPS
    return {k: math.log((probs[k] + EPS) / ref) for k in LABELS}


def logodds_blend(p_lgbm: dict, p_dc: dict, w_dc: float) -> dict:
    w_lgbm = 1.0 - w_dc
    lo_lgbm = _to_logodds(p_lgbm)
    lo_dc = _to_logodds(p_dc)
    blended = {k: w_lgbm * lo_lgbm[k] + w_dc * lo_dc[k] for k in LABELS}
    exps = {k: math.exp(blended[k]) for k in LABELS}
    total = sum(exps.values())
    return {k: exps[k] / total for k in LABELS}


def _log_loss_one(probs: dict, result: str) -> float:
    return -math.log(max(probs.get(result, EPS), EPS))


# ── Calibration (isotonic) ────────────────────────────────────────────────────

def _fit_isotonic(probs_list: list, results: list, label: str):
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        return None
    raw_p = [p[label] for p in probs_list]
    y = [1 if r == label else 0 for r in results]
    ir = IsotonicRegression(out_of_bounds="clip")
    ir.fit(raw_p, y)
    return ir


def _apply_isotonic(probs: dict, calibrators: dict) -> dict:
    if not calibrators:
        return probs
    raw = {k: float(calibrators[k].predict([probs[k]])[0]) for k in LABELS}
    total = sum(raw.values())
    if total <= 0:
        return probs
    return {k: raw[k] / total for k in LABELS}


# ── CSV helpers ──────────────────────────────────────────────────────────────

def _read_csv(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _key(row: dict, home_col="home_team", away_col="away_team") -> tuple:
    return (row.get("date", ""), row.get(home_col, ""), row.get(away_col, ""))


# ── Grid-search for best blend weight ────────────────────────────────────────

def find_best_blend(lgbm_rows: list, dc_rows: list) -> tuple:
    dc_lookup = {_key(r): r for r in dc_rows}

    matched = []
    for r in lgbm_rows:
        k = _key(r)
        if k in dc_lookup:
            matched.append((r, dc_lookup[k]))

    if len(matched) < MIN_CALIBRATION_ROWS:
        return 0.0, None, matched

    best_w, best_ll = 0.0, float("inf")
    for tenths in range(0, 11):
        w_dc = tenths / 10.0
        ll = 0.0
        for lgbm_r, dc_r in matched:
            p_lgbm = {
                "H": float(lgbm_r["home_win_probability"]),
                "D": float(lgbm_r["draw_probability"]),
                "A": float(lgbm_r["away_win_probability"]),
            }
            p_dc = {
                "H": float(dc_r["dc_home_prob"]),
                "D": float(dc_r["dc_draw_prob"]),
                "A": float(dc_r["dc_away_prob"]),
            }
            result = lgbm_r["result"]
            if result not in LABELS:
                continue
            blended = logodds_blend(p_lgbm, p_dc, w_dc)
            ll += _log_loss_one(blended, result)
        avg = ll / len(matched)
        if avg < best_ll:
            best_ll = avg
            best_w = w_dc

    return best_w, best_ll, matched


# ── Main ─────────────────────────────────────────────────────────────────────

UPCOMING_PATH = PROCESSED_DIR / "upcoming_predictions.csv"
DC_UPCOMING_PATH = PROCESSED_DIR / "dc_upcoming_predictions.csv"
LGBM_ROLLING_PATH = PROCESSED_DIR / "sklearn_rolling_predictions.csv"
DC_ROLLING_PATH = PROCESSED_DIR / "dc_rolling_predictions.csv"
META_PATH = MODELS_DIR / "ensemble_meta.json"


def main() -> None:
    lgbm_rolling = _read_csv(LGBM_ROLLING_PATH)
    dc_rolling = _read_csv(DC_ROLLING_PATH)

    if not lgbm_rolling or not dc_rolling:
        print("ensemble: missing rolling predictions, skipping blend")
        return

    w_dc, best_ll, matched = find_best_blend(lgbm_rolling, dc_rolling)
    n_matched = len(matched)

    print(f"ensemble: w_dc={w_dc:.1f}, log_loss={best_ll:.4f if best_ll else 'N/A'}, n_matched={n_matched}")

    calibrators: dict = {}
    if matched and len(matched) >= MIN_CALIBRATION_ROWS:
        blended_probs = []
        results = []
        for lgbm_r, dc_r in matched:
            p_lgbm = {
                "H": float(lgbm_r["home_win_probability"]),
                "D": float(lgbm_r["draw_probability"]),
                "A": float(lgbm_r["away_win_probability"]),
            }
            p_dc = {
                "H": float(dc_r["dc_home_prob"]),
                "D": float(dc_r["dc_draw_prob"]),
                "A": float(dc_r["dc_away_prob"]),
            }
            result = lgbm_r["result"]
            if result not in LABELS:
                continue
            blended_probs.append(logodds_blend(p_lgbm, p_dc, w_dc))
            results.append(result)

        if len(blended_probs) >= MIN_CALIBRATION_ROWS:
            for label in LABELS:
                ir = _fit_isotonic(blended_probs, results, label)
                if ir is not None:
                    calibrators[label] = ir

    # Load upcoming data
    upcoming = _read_csv(UPCOMING_PATH)
    dc_upcoming = _read_csv(DC_UPCOMING_PATH)

    if not upcoming or not dc_upcoming:
        print("ensemble: missing upcoming predictions, skipping")
        return

    dc_lookup = {}
    for r in dc_upcoming:
        k = _key(r)
        dc_lookup[k] = r

    updated = 0
    for row in upcoming:
        k = _key(row)
        dc_r = dc_lookup.get(k)
        if dc_r is None:
            continue
        try:
            p_lgbm = {
                "H": float(row["home_win_probability"]),
                "D": float(row["draw_probability"]),
                "A": float(row["away_win_probability"]),
            }
            p_dc = {
                "H": float(dc_r["dc_home_prob"]),
                "D": float(dc_r["dc_draw_prob"]),
                "A": float(dc_r["dc_away_prob"]),
            }
        except (ValueError, KeyError):
            continue

        blended = logodds_blend(p_lgbm, p_dc, w_dc)
        if calibrators:
            blended = _apply_isotonic(blended, calibrators)

        row["home_win_probability"] = round(blended["H"], 6)
        row["draw_probability"] = round(blended["D"], 6)
        row["away_win_probability"] = round(blended["A"], 6)
        row["dc_lambda"] = dc_r.get("dc_lambda", "")
        row["dc_mu"] = dc_r.get("dc_mu", "")
        updated += 1

    if updated == 0:
        print("ensemble: no upcoming rows matched DC predictions")
        return

    # Add dc columns to fieldnames if needed
    fieldnames = list(upcoming[0].keys())
    for col in ["dc_lambda", "dc_mu"]:
        if col not in fieldnames:
            fieldnames.append(col)

    with UPCOMING_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(upcoming)

    print(f"ensemble: updated {updated} upcoming predictions → {UPCOMING_PATH}")

    meta = {
        "w_dc": w_dc,
        "best_log_loss": round(best_ll, 6) if best_ll is not None else None,
        "n_matched_backtest": n_matched,
        "upcoming_updated": updated,
        "calibrated": bool(calibrators),
    }
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"ensemble: meta → {META_PATH}")


if __name__ == "__main__":
    main()
