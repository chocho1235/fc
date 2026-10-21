"""
Watch for starting line-ups and re-price games the moment they appear.

Run every few minutes. For each game kicking off in the next ~100 minutes it
asks ESPN for the line-ups. When they are out, it scores both XIs against the
teams' usual line-ups, adjusts the pre-line-up probabilities with the line-up
layer, and compares with the current price. If the price still looks wrong by
5%+ it logs a bet and, if the NTFY_TOPIC secret is set, sends a phone alert
via ntfy.sh.

Uses only the standard library so it starts in seconds.
"""

import csv
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import espn
from leagues import LEAGUES, league_name
from lineup_features import FEATURES, NEUTRAL, build_histories
from lineups import read_lineups
from team_names import canonical_team
from track_closing_lines import LINEUP_SNAPSHOT_PATH, SNAPSHOT_FIELDS

ROOT = Path(__file__).resolve().parents[1]
LAYER_PATH = ROOT / "models" / "lineup_layer.json"
UPCOMING_PATH = ROOT / "data" / "processed" / "upcoming_predictions.csv"
FIXTURES_PATH = ROOT / "data" / "raw" / "fixtures.csv"
STATE_PATH = ROOT / "data" / "external" / "lineup_watch_state.json"
EVENTS_PATH = ROOT / "data" / "external" / "lineup_events.csv"
SITE_PATH = ROOT.parent / "public" / "lineup-watch.json"

WINDOW_MINUTES = 100
EDGE = float(os.getenv("LINEUP_BET_EDGE", "0.05"))
MIN_PROBABILITY, MAX_PROBABILITY = 0.22, 0.65

EVENT_FIELDS = [
    "detected_at", "minutes_before_kickoff", "league_code", "date", "time", "home_team", "away_team",
    *[f"home_{f}" for f in FEATURES], *[f"away_{f}" for f in FEATURES],
    "base_H", "base_D", "base_A", "base_source", "new_H", "new_D", "new_A",
    "price_H", "price_D", "price_A", "price_source", "bet", "bet_odds", "edge", "alerted",
]


def read_csv(path: Path) -> list:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def append_csv(path: Path, row: dict, fields: list) -> None:
    exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def no_vig(prices: list) -> list:
    inverse = [1 / p for p in prices]
    return [x / sum(inverse) for x in inverse]


def apply_layer(layer: dict, base: list, home: dict, away: dict) -> list:
    x = [home[f] for f in FEATURES] + [away[f] for f in FEATURES]
    scores = [
        math.log(base[0]) + sum(b * v for b, v in zip(layer["beta_home_win"], x)),
        math.log(base[1]),
        math.log(base[2]) + sum(b * v for b, v in zip(layer["beta_away_win"], x)),
    ]
    top = max(scores)
    exps = [math.exp(s - top) for s in scores]
    return [e / sum(exps) for e in exps]


def notify(title: str, message: str) -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    request = Request(f"https://ntfy.sh/{topic}", data=message.encode("utf-8"),
                      headers={"Title": title.encode("ascii", "ignore").decode(), "Priority": "high", "Tags": "soccer"})
    try:
        with urlopen(request, timeout=15):
            return True
    except OSError as exc:
        print(f"  ntfy failed: {exc}")
        return False


def base_probabilities(league_code, day, home, away, data, upcoming, fixtures):
    """Pre-line-up probabilities: our model's if we have them, else the
    no-vig opening price from ESPN."""
    for row in upcoming:
        if (row.get("league_code"), row.get("date"), row.get("home_team"), row.get("away_team")) == (league_code, day, home, away):
            probs = [to_float(row.get(k)) for k in ("home_win_probability", "draw_probability", "away_win_probability")]
            if all(p > 0 for p in probs):
                return probs, "model"
    opening = espn.moneyline(data, "open")
    if opening:
        return no_vig([opening["H"], opening["D"], opening["A"]]), "espn-open"
    return None, ""


def current_prices(league_code, kickoff, home, away, data, fixtures):
    """Current price from ESPN, raised to the best price seen at the last
    refresh if that is higher (it may have moved since, so it's a guide)."""
    prices = espn.moneyline(data, "close")
    if not prices:
        return None, ""
    best = dict(prices)
    source = "espn"
    for row in fixtures:
        if (row.get("Div"), row.get("HomeTeam"), row.get("AwayTeam")) == (league_code, home, away) \
                and row.get("Date") == kickoff.strftime("%d/%m/%Y"):
            for side in "HDA":
                if to_float(row.get("Best" + side)) > best[side]:
                    best[side] = to_float(row.get("Best" + side))
                    source = "espn+refresh-best"
    return [prices["H"], prices["D"], prices["A"]], [best["H"], best["D"], best["A"]], source


def main():
    now = datetime.now(timezone.utc)
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    layer = json.loads(LAYER_PATH.read_text()) if LAYER_PATH.exists() else None
    if not layer:
        print("No line-up layer trained yet: logging line-ups only, no bets")
    elif not layer.get("validated"):
        print("Line-up layer did not beat the pre-line-up price in testing: logging only, no bets")
    upcoming, fixtures = read_csv(UPCOMING_PATH), read_csv(FIXTURES_PATH)
    histories = None
    checked = found = bets = 0

    for league_code, league in LEAGUES.items():
        days = {now.astimezone(espn.UK).date(), (now + timedelta(minutes=WINDOW_MINUTES)).astimezone(espn.UK).date()}
        events = []
        for day in days:
            try:
                events.extend(espn.scoreboard(league["espn"], day.strftime("%Y%m%d")))
            except Exception as exc:
                print(f"  {league_code}: scoreboard failed ({exc})")
        for event in events:
            event_id = event.get("id")
            if espn.event_state(event) != "pre" or event_id in state:
                continue
            kickoff = espn.kickoff_uk(event)
            minutes = (kickoff - now).total_seconds() / 60
            if not -5 <= minutes <= WINDOW_MINUTES:
                continue
            checked += 1
            try:
                data = espn.summary(league["espn"], event_id)
            except Exception as exc:
                print(f"  {event.get('name')}: summary failed ({exc})")
                continue
            teams = espn.lineups(data)
            if not teams:
                continue
            found += 1
            if histories is None:
                histories = build_histories(read_lineups())
            home = canonical_team(teams["home"]["team"], league_code) or teams["home"]["team"]
            away = canonical_team(teams["away"]["team"], league_code) or teams["away"]["team"]
            features = {}
            for side in ("home", "away"):
                starters = [(pid, pos) for pid, _name, pos in teams[side]["starters"]]
                history = histories.get(teams[side]["team"])
                features[side] = history.score(starters) if history else dict(NEUTRAL)
            day = kickoff.date().isoformat()
            base, base_source = base_probabilities(league_code, day, home, away, data, upcoming, fixtures)
            row = {
                "detected_at": now.isoformat(timespec="minutes"),
                "minutes_before_kickoff": round(minutes),
                "league_code": league_code, "date": day, "time": kickoff.strftime("%H:%M"),
                "home_team": home, "away_team": away,
                **{f"home_{k}": v for k, v in features["home"].items()},
                **{f"away_{k}": v for k, v in features["away"].items()},
            }
            state[event_id] = {"detected_at": row["detected_at"], "match": f"{home} v {away}"}
            if not base:
                append_csv(EVENTS_PATH, row, EVENT_FIELDS)
                continue
            new = apply_layer(layer, base, features["home"], features["away"]) if layer else base
            row.update({"base_source": base_source, **{f"base_{s}": round(p, 4) for s, p in zip("HDA", base)},
                        **{f"new_{s}": round(p, 4) for s, p in zip("HDA", new)}})
            quoted = current_prices(league_code, kickoff, home, away, data, fixtures)
            if quoted[0]:
                espn_prices, best_prices, price_source = quoted
                row.update({**{f"price_{s}": p for s, p in zip("HDA", best_prices)}, "price_source": price_source})
                edges = [new[k] * espn_prices[k] - 1 for k in range(3)]
                k = max(range(3), key=lambda i: edges[i])
                if layer and layer.get("validated") and edges[k] >= EDGE and MIN_PROBABILITY <= new[k] <= MAX_PROBABILITY:
                    selection = "HDA"[k]
                    row.update({"bet": selection, "bet_odds": espn_prices[k], "edge": round(edges[k], 4)})
                    bets += 1
                    append_csv(LINEUP_SNAPSHOT_PATH, {
                        "snapshot_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                        "date": day, "time": row["time"], "league_code": league_code, "league": league_name(league_code),
                        "home_team": home, "away_team": away, "predicted_result": "HDA"[max(range(3), key=lambda i: new[i])],
                        "suggested_bet": selection, "suggested_edge": round(edges[k], 4),
                        "home_bookmaker_odds": espn_prices[0], "draw_bookmaker_odds": espn_prices[1], "away_bookmaker_odds": espn_prices[2],
                        "bet_odds": espn_prices[k], "bet_rule": "line-up",
                    }, SNAPSHOT_FIELDS)
                    label = {"H": home, "D": "Draw", "A": away}[selection]
                    missing = f"{home}: {features['home']['missing']:.0f} regulars out, {away}: {features['away']['missing']:.0f} out"
                    row["alerted"] = notify(
                        f"Line-up bet: {label} @ {espn_prices[k]:.2f}",
                        f"{home} v {away} ({league_name(league_code)}, {row['time']})\n"
                        f"{label} at {espn_prices[k]:.2f} or better, edge +{edges[k]:.0%}\n{missing}",
                    )
            append_csv(EVENTS_PATH, row, EVENT_FIELDS)
            print(f"  {home} v {away}: line-ups out {round(minutes)} min before kick-off, bet {row.get('bet') or '-'}")

    # forget games that kicked off more than two days ago
    cutoff = (now - timedelta(days=2)).isoformat()
    state = {k: v for k, v in state.items() if v.get("detected_at", "") >= cutoff}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")
    if found or not SITE_PATH.exists():
        recent = read_csv(EVENTS_PATH)[-8:]
        SITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SITE_PATH.write_text(json.dumps({"updated_at": now.isoformat(timespec="minutes"), "events": list(reversed(recent))}, indent=1), encoding="utf-8")
    print(f"line-up watch: {checked} game(s) in window, {found} with line-ups, {bets} bet(s)")


if __name__ == "__main__":
    main()
