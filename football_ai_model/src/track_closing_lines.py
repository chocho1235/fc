import csv
from datetime import datetime, timezone
from pathlib import Path

from train_1x2_model import LABELS, read_matches


ROOT = Path(__file__).resolve().parents[1]
EXTERNAL_DIR = ROOT / "data" / "external"
PROCESSED_DIR = ROOT / "data" / "processed"
SNAPSHOT_PATH = EXTERNAL_DIR / "odds_snapshots.csv"
REPORT_PATH = PROCESSED_DIR / "closing_line_report.csv"
UPCOMING_PATH = PROCESSED_DIR / "upcoming_predictions.csv"

SNAPSHOT_FIELDS = [
    "snapshot_at",
    "date",
    "time",
    "league_code",
    "league",
    "home_team",
    "away_team",
    "predicted_result",
    "suggested_bet",
    "suggested_edge",
    "home_bookmaker_odds",
    "draw_bookmaker_odds",
    "away_bookmaker_odds",
    "over_25_bookmaker_odds",
    "bet_rule",
]

REPORT_FIELDS = [
    *SNAPSHOT_FIELDS,
    "result",
    "selection_odds",
    "closing_selection_odds",
    "closing_line_value",
    "profit_units",
]


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def row_key(row):
    return (
        row.get("date", ""),
        row.get("league_code", ""),
        row.get("home_team", ""),
        row.get("away_team", ""),
    )


def snapshot_identity(row):
    return (
        row.get("date", ""),
        row.get("league_code", ""),
        row.get("home_team", ""),
        row.get("away_team", ""),
        row.get("home_bookmaker_odds", ""),
        row.get("draw_bookmaker_odds", ""),
        row.get("away_bookmaker_odds", ""),
        row.get("suggested_bet", ""),
    )


def append_prediction_snapshot():
    upcoming = read_csv(UPCOMING_PATH)
    if not upcoming:
        return

    snapshots = read_csv(SNAPSHOT_PATH)
    existing = {snapshot_identity(row) for row in snapshots}
    snapshot_at = now_utc()
    additions = []
    for row in upcoming:
        snapshot = {field: row.get(field, "") for field in SNAPSHOT_FIELDS}
        snapshot["snapshot_at"] = snapshot_at
        if snapshot_identity(snapshot) in existing:
            continue
        additions.append(snapshot)
        existing.add(snapshot_identity(snapshot))

    if additions:
        write_csv(SNAPSHOT_PATH, [*snapshots, *additions], SNAPSHOT_FIELDS)
        print(f"Saved {len(additions)} odds snapshot(s)")
    else:
        print("No new odds snapshots to save")


def completed_match_index():
    completed = {}
    for row in read_matches():
        completed[(row["Date"], row["LeagueCode"], row["HomeTeam"], row["AwayTeam"])] = row
    return completed


def decimal(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def selection_odds(row, prefix):
    selection = row.get("suggested_bet") or row.get("predicted_result")
    if selection == "H":
        return decimal(row.get(f"{prefix}H"))
    if selection == "D":
        return decimal(row.get(f"{prefix}D"))
    if selection == "A":
        return decimal(row.get(f"{prefix}A"))
    return 0.0


def snapshot_selection_odds(row):
    selection = row.get("suggested_bet") or row.get("predicted_result")
    if selection == "H":
        return decimal(row.get("home_bookmaker_odds"))
    if selection == "D":
        return decimal(row.get("draw_bookmaker_odds"))
    if selection == "A":
        return decimal(row.get("away_bookmaker_odds"))
    return 0.0


def profit(result, selection, odds):
    if selection not in LABELS or odds <= 1:
        return 0.0
    return odds - 1 if result == selection else -1.0


def score_snapshots():
    snapshots = read_csv(SNAPSHOT_PATH)
    completed = completed_match_index()
    scored = []
    seen = set()

    for snapshot in snapshots:
        match = completed.get(row_key(snapshot))
        if not match:
            continue
        selection = snapshot.get("suggested_bet") or snapshot.get("predicted_result")
        taken_odds = snapshot_selection_odds(snapshot)
        closing_odds = selection_odds(match, "AvgC")
        if closing_odds <= 1:
            closing_odds = selection_odds(match, "Avg")
        report_row = {field: snapshot.get(field, "") for field in SNAPSHOT_FIELDS}
        report_row.update({
            "result": match.get("FTR", ""),
            "selection_odds": round(taken_odds, 3) if taken_odds else "",
            "closing_selection_odds": round(closing_odds, 3) if closing_odds else "",
            "closing_line_value": round((taken_odds / closing_odds) - 1, 4) if taken_odds and closing_odds else "",
            "profit_units": round(profit(match.get("FTR", ""), selection, taken_odds), 3),
        })
        identity = (snapshot.get("snapshot_at"), *row_key(snapshot), selection)
        if identity in seen:
            continue
        scored.append(report_row)
        seen.add(identity)

    write_csv(REPORT_PATH, scored, REPORT_FIELDS)
    print(f"Scored {len(scored)} completed odds snapshot(s)")


def main():
    append_prediction_snapshot()
    score_snapshots()


if __name__ == "__main__":
    main()
