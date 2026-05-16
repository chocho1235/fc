"""
generate_news.py
----------------
Reads predictions.csv and generates news articles automatically.
No external AI API — uses smart templates with match-specific data.

Usage:
    python generate_news.py                  # writes news_feed.json
    from generate_news import generate_news_feed  # programmatic use
"""

import csv
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

MODEL_ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS_PATH = MODEL_ROOT / "data" / "processed" / "predictions.csv"
NEWS_FEED_PATH = MODEL_ROOT / "data" / "processed" / "news_feed.json"


# ─── Template banks ───────────────────────────────────────────────────────────

UPSET_HEADLINES = [
    "Shock result: {underdog} stun {favourite} despite {pct}% odds",
    "{underdog} produce one of the week's biggest upsets against {favourite}",
    "Against all odds: {underdog} topple {favourite} at {venue}",
    "Model left stunned as {underdog} beat {favourite} ({pct}% chance given)",
]

UPSET_BODIES = [
    "Our model gave {underdog} just a {pct}% chance at {venue}. {favourite} were priced at {odds}, but {underdog} produced one of the weekend's biggest upsets. The model's expected goals had {favourite} projected at {xg_h} goals — reality told a very different story.",
    "A {pct}% probability — that's all the model assigned {underdog} heading into this one at {venue}. {favourite} at {odds} looked the banker pick. Instead, {underdog} rewrote the script.",
    "Sometimes football just doesn't follow the numbers. {underdog} came in at {pct}% and turned over {favourite} ({odds}) in what ranks as a genuine upset. Expected goals had pointed firmly toward {favourite}.",
]

VALUE_WIN_HEADLINES = [
    "Value bet lands: {team} {result_label} at {odds}",
    "Model nails it: {team} delivers at {odds} with {edge}% edge",
    "{team} covers the value angle — {rule_name} rule fires again",
    "Edge found, edge collected: {team} at {odds}",
]

VALUE_WIN_BODIES = [
    "Our model identified {edge}% edge on {team} at {odds}. The {rule_name} rule (historical ROI: {roi}%) fired — and it delivered. Running P&L: +{units} units on this rule.",
    "This was textbook value identification. {team} at {odds} carried a {edge}% model edge. The {rule_name} qualification added confidence, and the market eventually agreed.",
    "The {rule_name} signal pointed to {team} at {odds} with {edge}% edge over Pinnacle's closing line. Result: exactly what the model ordered.",
]

VALUE_LOSS_HEADLINES = [
    "Value bet fell short: {team} at {odds}",
    "Model edge not enough this time: {team} {odds} doesn't land",
    "{team} at {odds} — right process, wrong outcome",
    "Variance bites: {rule_name} rule suffers setback with {team}",
]

VALUE_LOSS_BODIES = [
    "The {rule_name} signal pointed to {team} at {odds} — but the result went the other way. The model had {pct}% on this, and the market was at {mkt_pct}%. Part of the long-term process.",
    "Not every edge converts. {team} at {odds} looked value at {pct}% model probability — the result disagreed. These losing bets are expected and accounted for in the long-run ROI.",
    "The {rule_name} rule backed {team} here with {pct}% confidence at {odds}. This one didn't land, but the historical ROI remains positive across the sample.",
]

NAILED_IT_HEADLINES = [
    "{winner} dominant: model called it at {pct}%",
    "Model conviction rewarded: {winner} wins as predicted ({pct}%)",
    "{winner} deliver exactly what a {pct}% prediction promised",
    "High confidence, high result: {winner} at {pct}% comes through",
]

NAILED_IT_BODIES = [
    "The model was confident here, giving {winner} a {pct}% chance. They delivered, reinforcing the signal in {league}.",
    "At {pct}%, this was about as sure as the model gets. {winner} justified that confidence with a comfortable result in {league}.",
    "{winner} at {pct}% was the model's strongest call of the week. Results like this are why the {league} signal continues to look sharp.",
]

GOALFEST_HEADLINES = [
    "Goals galore: {home} {hg}-{ag} {away}",
    "Over market explodes: {home} vs {away} delivers {total} goals",
    "{total} goals and the model saw it coming in {league}",
    "High-scoring {league} clash: {home} {hg}-{ag} {away}",
]

GOALFEST_BODIES = [
    "The model projected {xg_h} xG for {home} and {xg_a} xG for {away} — and the match delivered {total} goals. Over 2.5 probability was {o25_pct}%.",
    "With {xg_h} expected goals for {home} and {xg_a} for {away}, the model was screaming goals. The {hg}-{ag} scoreline proved it right. O2.5 model probability: {o25_pct}%.",
    "{home} vs {away} in {league} lived up to the model's xG projection of {xg_h}+{xg_a}. The {total}-goal thriller validated the Over 2.5 call at {o25_pct}%.",
]

LEAGUE_FORM_HEADLINES = [
    "Model form check: {league} leading accuracy this week",
    "{league} best-performing league for model calls lately",
    "Form table: {league} tops model accuracy at {acc}%",
    "Model sharpness by league: {league} comes out on top",
]

LEAGUE_FORM_BODIES = [
    "Across the past {n} {league} matches, the model correctly called {correct} results ({acc}% accuracy). {comparison}",
    "The {league} data continues to yield the cleanest signals. {correct} from {n} recent matches ({acc}%) suggests the model is well-calibrated here. {comparison}",
    "{league} accuracy stands at {acc}% from {n} recent predictions. {comparison} The model's European data set remains its strongest suit.",
]

COMPARISON_COMMENTS = [
    "Significantly above the cross-league average.",
    "Outperforming all other leagues in the current window.",
    "One of the model's strongest-performing regions.",
    "Consistency here has been a theme over multiple seasons.",
    "Ahead of the pack in the recent 30-match window.",
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _pct(value):
    """Format a 0–1 float as an integer string like '67' (templates add the % sign)."""
    try:
        return f"{round(float(value) * 100)}"
    except (TypeError, ValueError):
        return "--"


def _f(value, decimals=2):
    """Format a float with given decimal places or return '--'."""
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "--"


def _fl(value):
    """Safe float conversion."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pick(templates):
    return random.choice(templates)


def _prob_for(row, label):
    if label == "H":
        return _fl(row.get("home_win_probability", 0))
    if label == "D":
        return _fl(row.get("draw_probability", 0))
    return _fl(row.get("away_win_probability", 0))


def _odds_for(row, label):
    if label == "H":
        return _fl(row.get("home_bookmaker_odds", 0))
    if label == "D":
        return _fl(row.get("draw_bookmaker_odds", 0))
    return _fl(row.get("away_bookmaker_odds", 0))


def _mkt_pct(odds):
    """Convert decimal odds to implied probability (templates add the % sign)."""
    if odds <= 1:
        return "--"
    return f"{round(100 / odds)}"


def _result_label(code):
    return {"H": "wins", "D": "draws", "A": "wins"}.get(code, "covers")


def _article(story_type, headline, body, league, date, teams, badge):
    return {
        "id": f"{story_type}_{date}_{teams[0][:4]}_{teams[1][:4]}".replace(" ", "_").lower(),
        "type": story_type,
        "headline": headline,
        "body": body,
        "league": league,
        "date": date,
        "teams": teams,
        "badge": badge,
    }


# ─── Story generators ─────────────────────────────────────────────────────────

def _make_upset(row):
    result = row.get("result")
    predicted = row.get("predicted_result")
    if not result or not predicted or result == predicted:
        return None

    # Find the winner and their probability
    if result == "H":
        underdog = row.get("home_team", "Home side")
        favourite = row.get("away_team", "Away side")
        win_prob = _fl(row.get("home_win_probability", 0))
        xg_h = _f(row.get("expected_home_goals", 0))
        fav_odds = _f(_odds_for(row, "A"))
    elif result == "A":
        underdog = row.get("away_team", "Away side")
        favourite = row.get("home_team", "Home side")
        win_prob = _fl(row.get("away_win_probability", 0))
        xg_h = _f(row.get("expected_away_goals", 0))
        fav_odds = _f(_odds_for(row, "H"))
    else:
        return None  # draw upsets less interesting

    if win_prob >= 0.30:
        return None  # not a genuine upset

    pct = f"{round(win_prob * 100)}"
    venue = f"{row.get('home_team', 'home')}'s ground"
    headline = _pick(UPSET_HEADLINES).format(
        underdog=underdog, favourite=favourite, pct=pct, venue=venue
    )
    body = _pick(UPSET_BODIES).format(
        underdog=underdog, favourite=favourite, pct=pct, venue=venue,
        odds=fav_odds, xg_h=xg_h
    )
    return _article(
        "upset", headline, body,
        row.get("league", ""), row.get("date", ""),
        [row.get("home_team", ""), row.get("away_team", "")],
        "⚡ Upset"
    )


def _make_value_win(row):
    suggested = row.get("suggested_bet")
    result = row.get("result")
    if not suggested or not result or suggested != result:
        return None

    label = suggested
    if label == "H":
        team = row.get("home_team", "Home side")
    elif label == "A":
        team = row.get("away_team", "Away side")
    else:
        team = "Draw"

    odds = _f(_odds_for(row, label))
    edge = f"{round(_fl(row.get('suggested_edge', 0)) * 100, 1)}"
    rule_name = row.get("bet_rule", "Value")
    roi = _pct(row.get("bet_rule_roi", 0))
    units = _f(_fl(row.get("bet_rule_roi", 0)) * 10, 1)
    result_label = _result_label(label)

    headline = _pick(VALUE_WIN_HEADLINES).format(
        team=team, result_label=result_label, odds=odds, edge=edge, rule_name=rule_name
    )
    body = _pick(VALUE_WIN_BODIES).format(
        team=team, odds=odds, edge=edge, rule_name=rule_name, roi=roi, units=units
    )
    return _article(
        "win", headline, body,
        row.get("league", ""), row.get("date", ""),
        [row.get("home_team", ""), row.get("away_team", "")],
        "✓ Win"
    )


def _make_value_loss(row):
    suggested = row.get("suggested_bet")
    result = row.get("result")
    if not suggested or not result or suggested == result:
        return None

    label = suggested
    if label == "H":
        team = row.get("home_team", "Home side")
    elif label == "A":
        team = row.get("away_team", "Away side")
    else:
        team = "Draw"

    odds_val = _odds_for(row, label)
    odds = _f(odds_val)
    pct = f"{round(_fl(row.get('home_win_probability' if label == 'H' else ('away_win_probability' if label == 'A' else 'draw_probability'), 0)) * 100)}"
    mkt_pct = _mkt_pct(odds_val)
    rule_name = row.get("bet_rule", "Value")

    headline = _pick(VALUE_LOSS_HEADLINES).format(
        team=team, odds=odds, rule_name=rule_name
    )
    body = _pick(VALUE_LOSS_BODIES).format(
        team=team, odds=odds, pct=pct, mkt_pct=mkt_pct, rule_name=rule_name
    )
    return _article(
        "loss", headline, body,
        row.get("league", ""), row.get("date", ""),
        [row.get("home_team", ""), row.get("away_team", "")],
        "✗ Loss"
    )


def _make_nailed_it(row):
    result = row.get("result")
    predicted = row.get("predicted_result")
    if not result or result != predicted:
        return None

    win_prob = _prob_for(row, result)
    if win_prob < 0.75:
        return None

    if result == "H":
        winner = row.get("home_team", "Home side")
    elif result == "A":
        winner = row.get("away_team", "Away side")
    else:
        winner = "The draw"

    pct = f"{round(win_prob * 100)}"
    headline = _pick(NAILED_IT_HEADLINES).format(winner=winner, pct=pct)
    body = _pick(NAILED_IT_BODIES).format(
        winner=winner, pct=pct, league=row.get("league", "this league")
    )
    return _article(
        "form", headline, body,
        row.get("league", ""), row.get("date", ""),
        [row.get("home_team", ""), row.get("away_team", "")],
        "📊 Model called it"
    )


def _make_goalfest(row):
    result = row.get("result")
    if not result:
        return None

    hg_str = row.get("home_goals", row.get("actual_home_goals", ""))
    ag_str = row.get("away_goals", row.get("actual_away_goals", ""))
    try:
        hg = int(float(hg_str))
        ag = int(float(ag_str))
    except (TypeError, ValueError):
        return None

    total = hg + ag
    if total < 4:
        return None

    xg_h = _f(row.get("expected_home_goals", 0))
    xg_a = _f(row.get("expected_away_goals", 0))
    o25_pct = f"{round(_fl(row.get('over_25_probability', 0)) * 100)}"
    home = row.get("home_team", "Home")
    away = row.get("away_team", "Away")
    league = row.get("league", "")

    headline = _pick(GOALFEST_HEADLINES).format(
        home=home, away=away, hg=hg, ag=ag, total=total, league=league
    )
    body = _pick(GOALFEST_BODIES).format(
        home=home, away=away, hg=hg, ag=ag, total=total,
        xg_h=xg_h, xg_a=xg_a, o25_pct=o25_pct, league=league
    )
    return _article(
        "goals", headline, body,
        league, row.get("date", ""),
        [home, away],
        "⚽ Goalfest"
    )


def _make_league_form(rows):
    """Generate a league form article for the best-performing league in recent matches."""
    league_stats = defaultdict(lambda: {"correct": 0, "total": 0})
    for row in rows:
        result = row.get("result")
        predicted = row.get("predicted_result")
        if not result or not predicted:
            continue
        lg = row.get("league", "Unknown")
        league_stats[lg]["total"] += 1
        if result == predicted:
            league_stats[lg]["correct"] += 1

    # Filter leagues with enough data
    qualified = {
        lg: stats for lg, stats in league_stats.items()
        if stats["total"] >= 5
    }
    if not qualified:
        return None

    best_league = max(qualified, key=lambda lg: qualified[lg]["correct"] / qualified[lg]["total"])
    stats = qualified[best_league]
    correct = stats["correct"]
    n = stats["total"]
    acc = f"{round(correct / n * 100)}"

    headline = _pick(LEAGUE_FORM_HEADLINES).format(league=best_league, acc=acc)
    body = _pick(LEAGUE_FORM_BODIES).format(
        league=best_league, n=n, correct=correct, acc=acc,
        comparison=random.choice(COMPARISON_COMMENTS)
    )
    return {
        "id": f"form_{best_league.replace(' ', '_').lower()}",
        "type": "form",
        "headline": headline,
        "body": body,
        "league": best_league,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "teams": [],
        "badge": "📊 Form",
    }


# ─── Main generator ───────────────────────────────────────────────────────────

def generate_news_feed(predictions_path):
    """
    Read predictions CSV and generate 8–12 news articles.

    Args:
        predictions_path: Path-like to predictions.csv

    Returns:
        list of article dicts
    """
    path = Path(predictions_path)
    if not path.exists():
        return []

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # Use only the most recent 30 settled matches
    settled = [r for r in rows if r.get("result")][-30:]

    articles = []
    seen_fixtures = set()

    # Priority order: upsets, value wins, goalfests, nailed-its, value losses
    generators = [
        ("upset", _make_upset),
        ("value_win", _make_value_win),
        ("goalfest", _make_goalfest),
        ("nailed_it", _make_nailed_it),
        ("value_loss", _make_value_loss),
    ]

    # Shuffle settled rows to get variety (but keep recency weighting by slicing)
    recent_first = list(reversed(settled))

    for story_type, gen_fn in generators:
        count_this_type = 0
        max_per_type = {"upset": 3, "value_win": 4, "goalfest": 2, "nailed_it": 3, "value_loss": 2}
        cap = max_per_type.get(story_type, 2)

        for row in recent_first:
            fixture_key = f"{row.get('home_team')}_{row.get('away_team')}_{row.get('date')}"
            if fixture_key in seen_fixtures:
                continue

            article = gen_fn(row)
            if article:
                articles.append(article)
                seen_fixtures.add(fixture_key)
                count_this_type += 1
                if count_this_type >= cap:
                    break

    # Add one league form article
    league_article = _make_league_form(settled)
    if league_article:
        articles.append(league_article)

    # Sort: upsets and value_wins first, then by date descending
    priority = {"upset": 0, "win": 1, "goals": 2, "form": 3, "loss": 4}
    articles.sort(key=lambda a: (priority.get(a["type"], 5), a.get("date", "")), reverse=False)

    # Limit to 12 most interesting
    return articles[:12]


def main():
    random.seed(42)  # deterministic for consistent results
    articles = generate_news_feed(PREDICTIONS_PATH)
    NEWS_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEWS_FEED_PATH.write_text(json.dumps(articles, indent=2), encoding="utf-8")
    print(f"Generated {len(articles)} news articles → {NEWS_FEED_PATH}")


if __name__ == "__main__":
    main()
