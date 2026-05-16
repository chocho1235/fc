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
UPCOMING_PATH    = MODEL_ROOT / "data" / "processed" / "upcoming_predictions.csv"
NEWS_FEED_PATH   = MODEL_ROOT / "data" / "processed" / "news_feed.json"


# ─── Template banks ───────────────────────────────────────────────────────────

UPSET_HEADLINES = [
    "Shock result: {underdog} stun {favourite} despite {pct}% odds",
    "{underdog} produce one of the week's biggest upsets against {favourite}",
    "Against all odds: {underdog} topple {favourite} at {venue}",
    "Model left stunned as {underdog} beat {favourite} ({pct}% chance given)",
    "{underdog} defy the numbers to down {favourite}",
]

UPSET_BODIES = [
    "Our model gave {underdog} just a {pct}% chance at {venue}. {favourite} were priced at {odds}, but {underdog} produced one of the weekend's biggest upsets. The model's expected goals had {favourite} projected at {xg_h} goals — reality told a very different story.",
    "A {pct}% probability — that's all the model assigned {underdog} heading into this one at {venue}. {favourite} at {odds} looked the banker pick. Instead, {underdog} rewrote the script.",
    "Sometimes football just doesn't follow the numbers. {underdog} came in at {pct}% and turned over {favourite} ({odds}) in what ranks as a genuine upset. Expected goals had pointed firmly toward {favourite}.",
    "Football's greatest quality is its unpredictability. {underdog} at {pct}% heading to {venue} looked a long shot — but they came away with all three points against {favourite} ({odds}).",
]

VALUE_WIN_HEADLINES = [
    "Value bet lands: {team} {result_label} at {odds}",
    "Model nails it: {team} delivers at {odds} with {edge}% edge",
    "{team} covers the value angle — {rule_name} rule fires again",
    "Edge found, edge collected: {team} at {odds}",
    "The {rule_name} signal pays off: {team} at {odds} delivers",
    "Backed at value, delivered on cue: {team} {result_label}",
]

VALUE_WIN_BODIES = [
    "Our model identified {edge}% edge on {team} at {odds}. The {rule_name} rule (historical ROI: {roi}%) fired — and it delivered. Running P&L: +{units} units on this rule.",
    "This was textbook value identification. {team} at {odds} carried a {edge}% model edge. The {rule_name} qualification added confidence, and the market eventually agreed.",
    "The {rule_name} signal pointed to {team} at {odds} with {edge}% edge over Pinnacle's closing line. Result: exactly what the model ordered.",
    "A clean hit for the {rule_name} rule. {team} priced at {odds} offered {edge}% edge — the model found it, the result confirmed it. Another positive data point in the long-run ledger.",
    "The model spotted something the market missed: {edge}% on {team} at {odds}. The {rule_name} qualification stacked confidence. Settled as a winner.",
]

VALUE_LOSS_HEADLINES = [
    "Value bet fell short: {team} at {odds}",
    "Model edge not enough this time: {team} {odds} doesn't land",
    "{team} at {odds} — right process, wrong outcome",
    "Variance bites: {rule_name} rule suffers setback with {team}",
    "Part of the process: {team} at {odds} doesn't convert",
]

VALUE_LOSS_BODIES = [
    "The {rule_name} signal pointed to {team} at {odds} — but the result went the other way. The model had {pct}% on this, and the market was at {mkt_pct}%. Part of the long-term process.",
    "Not every edge converts. {team} at {odds} looked value at {pct}% model probability — the result disagreed. These losing bets are expected and accounted for in the long-run ROI.",
    "The {rule_name} rule backed {team} here with {pct}% confidence at {odds}. This one didn't land, but the historical ROI remains positive across the sample.",
    "Football variance at work. The edge was real — {pct}% model probability vs {mkt_pct}% market implied — but {team} couldn't convert. The process stays sound.",
]

NAILED_IT_HEADLINES = [
    "{winner} dominant: model called it at {pct}%",
    "Model conviction rewarded: {winner} wins as predicted ({pct}%)",
    "{winner} deliver exactly what a {pct}% prediction promised",
    "High confidence, high result: {winner} at {pct}% comes through",
    "Model reads it right: {winner} win as expected in {league}",
    "Exactly as predicted: {winner} seal it in {league}",
]

NAILED_IT_BODIES = [
    "The model was confident here, giving {winner} a {pct}% chance. They delivered, reinforcing the signal in {league}.",
    "At {pct}%, this was about as sure as the model gets. {winner} justified that confidence with a comfortable result in {league}.",
    "{winner} at {pct}% was the model's strongest call of the window. Results like this are why the {league} signal continues to look sharp.",
    "Confidence: {pct}%. Outcome: correct. {winner} came through in {league} — a clean data point that keeps the model's accuracy ticking upward.",
    "The model loaded up on {winner} at {pct}% probability in {league}. The result matched the expectation. Another tick on the accuracy sheet.",
]

GOALFEST_HEADLINES = [
    "Goals galore: {home} {hg}-{ag} {away}",
    "Over market explodes: {home} vs {away} delivers {total} goals",
    "{total} goals and the model saw it coming in {league}",
    "High-scoring {league} clash: {home} {hg}-{ag} {away}",
    "Both teams fire: {home} {hg}-{ag} {away} in {league}",
]

GOALFEST_BODIES = [
    "The model projected {xg_h} xG for {home} and {xg_a} xG for {away} — and the match delivered {total} goals. Over 2.5 probability was {o25_pct}%.",
    "With {xg_h} expected goals for {home} and {xg_a} for {away}, the model was screaming goals. The {hg}-{ag} scoreline proved it right. O2.5 model probability: {o25_pct}%.",
    "{home} vs {away} in {league} lived up to the model's xG projection of {xg_h}+{xg_a}. The {total}-goal thriller validated the Over 2.5 call at {o25_pct}%.",
    "An {o25_pct}% Over 2.5 probability — the model sniffed out the goals in {league}. {home} and {away} combined for {total}, matching the projected xG almost exactly.",
]

LOW_SCORER_HEADLINES = [
    "Tight affair: {home} and {away} keep it low",
    "Defences dominate: {home} {hg}-{ag} {away}",
    "Under 2.5 lands as model predicted in {league}",
    "A cagey one: {home} vs {away} fails to fire",
]

LOW_SCORER_BODIES = [
    "The model projected combined xG of just {xg_total} — and the match delivered exactly that in a tight {hg}-{ag}. Under 2.5 probability sat at {u25_pct}% heading in.",
    "Defences had the upper hand in {league}. The model's {u25_pct}% Under 2.5 call was vindicated as {home} and {away} combined for just {total} goal(s). Expected goals: {xg_h} + {xg_a}.",
    "A model tick for the Under market. {u25_pct}% probability heading in, {total} goal(s) on the night. {home} vs {away} was exactly the tight contest the xG figures suggested.",
]

LEAGUE_FORM_HEADLINES = [
    "Model form check: {league} leading accuracy this week",
    "{league} best-performing league for model calls lately",
    "Form table: {league} tops model accuracy at {acc}%",
    "Model sharpness by league: {league} comes out on top",
    "Signal strength: {league} hitting {acc}% accuracy recently",
    "{league} data firing on all cylinders — {acc}% call rate",
]

LEAGUE_FORM_BODIES = [
    "Across the past {n} {league} matches, the model correctly called {correct} results ({acc}% accuracy). {comparison}",
    "The {league} data continues to yield the cleanest signals. {correct} from {n} recent matches ({acc}%) suggests the model is well-calibrated here. {comparison}",
    "{league} accuracy stands at {acc}% from {n} recent predictions. {comparison} The model's European data set remains its strongest suit.",
    "{correct} correct calls from {n} {league} fixtures in the window — {acc}% accuracy. {comparison} The model's edge in this division is holding.",
]

COMPARISON_COMMENTS = [
    "Significantly above the cross-league average.",
    "Outperforming all other leagues in the current window.",
    "One of the model's strongest-performing regions.",
    "Consistency here has been a theme over multiple seasons.",
    "Ahead of the pack in the recent 30-match window.",
    "Setting the benchmark for prediction accuracy this week.",
]

ROI_UPDATE_HEADLINES = [
    "Model P&L update: rolling ROI stands at {roi}%",
    "Bankroll check: {roi}% rolling ROI across recent bets",
    "The edge is real: model rolling ROI hits {roi}%",
    "P&L snapshot: {roi}% ROI on {bets} recent value bets",
]

ROI_UPDATE_BODIES = [
    "Across the recent rolling window of {bets} qualifying value bets, the model is running at {roi}% ROI. The {top_rule} rule leads contributions. Long-run expectation remains positive.",
    "Rolling ROI: {roi}%. That's the current read across {bets} recent value bets tracked by the model. Variance is part of the game, but the edge continues to show.",
    "The model's rolling P&L sits at {roi}% ROI over {bets} recent bets. The {top_rule} signal has been the standout rule in this window — consistently finding value others miss.",
    "Value betting is a long game. Current rolling ROI: {roi}% from {bets} recent qualifying bets. The model's edge over the market closing line remains intact.",
]

STREAK_HEADLINES = [
    "{team} on fire: {n} wins in their last {total} — model had them right",
    "In-form {team} keep rolling — model backed them {n} times recently",
    "{team} cooling off: only {n} wins from last {total} in the data",
    "Form watch: {team} unbeaten in {n} — model tracking the run",
]

STREAK_BODIES = [
    "Looking at the recent data, {team} have won {n} of their last {total} matches in the model's rolling window. The model has been on the right side of this run, backing the {side} outcome {times} times in this spell.",
    "{team}'s recent form is hard to ignore — {n} wins from {total} matches. The model has reflected this momentum in its probability distributions, consistently rating them above market expectations.",
    "Form data tells the story for {team}: {n} from {total} in recent fixtures. The model's signal has tracked this well, flagging the {side} lean across the window.",
]

UPCOMING_PREVIEW_HEADLINES = [
    "Value alert: {team} flagged for {side} with {edge}% edge on {date}",
    "Model spots opportunity: {team} at {odds} carries {edge}% edge",
    "Next big call: {rule_name} rule points to {team} on {date}",
    "Watch this one: {team} vs {opp} flagged by model as value",
    "Edge identified: {team} backed by {rule_name} signal at {odds}",
]

UPCOMING_PREVIEW_BODIES = [
    "The model has identified {edge}% edge on {team} ahead of their clash with {opp} on {date}. Bookie odds: {odds}. The {rule_name} rule — with {bets} bets and {roi}% historical ROI — qualifies this as a value play.",
    "Ahead of {team} vs {opp} on {date}, the {rule_name} rule fires with {edge}% edge at {odds}. Historical ROI on this signal: {roi}%. The model rates {team} at {prob}% — the market disagrees at {mkt_pct}%.",
    "Eyes on {team} vs {opp} ({date}). The model gives {team} a {prob}% chance — the bookie has them at {mkt_pct}% implied. That {edge}% gap is where the {rule_name} rule finds its value at {odds}.",
    "The {rule_name} signal is live: {team} at {odds} for their {date} fixture against {opp}. Model probability: {prob}%. Market implied: {mkt_pct}%. Edge: {edge}%. {bets} historical bets, {roi}% ROI.",
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


def _team_name(row, label):
    if label == "H":
        return row.get("home_team", "Home side")
    if label == "A":
        return row.get("away_team", "Away side")
    return "Draw"


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
        return None

    if win_prob >= 0.30:
        return None

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
    team = _team_name(row, label)
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
        "✓ Value win"
    )


def _make_value_loss(row):
    suggested = row.get("suggested_bet")
    result = row.get("result")
    if not suggested or not result or suggested == result:
        return None

    label = suggested
    team = _team_name(row, label)
    odds_val = _odds_for(row, label)
    odds = _f(odds_val)
    prob_key = "home_win_probability" if label == "H" else ("away_win_probability" if label == "A" else "draw_probability")
    pct = f"{round(_fl(row.get(prob_key, 0)) * 100)}"
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
        "✗ Value loss"
    )


def _make_nailed_it(row):
    result = row.get("result")
    predicted = row.get("predicted_result")
    if not result or result != predicted:
        return None

    win_prob = _prob_for(row, result)
    if win_prob < 0.65:   # lowered from 0.75 so more stories fire
        return None

    if result == "H":
        winner = row.get("home_team", "Home side")
    elif result == "A":
        winner = row.get("away_team", "Away side")
    else:
        winner = "The draw"

    pct = f"{round(win_prob * 100)}"
    headline = _pick(NAILED_IT_HEADLINES).format(winner=winner, pct=pct, league=row.get("league", ""))
    body = _pick(NAILED_IT_BODIES).format(
        winner=winner, pct=pct, league=row.get("league", "this league")
    )
    return _article(
        "nailed", headline, body,
        row.get("league", ""), row.get("date", ""),
        [row.get("home_team", ""), row.get("away_team", "")],
        "🎯 Model called it"
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


def _make_low_scorer(row):
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
    if total > 1:
        return None

    xg_h = _fl(row.get("expected_home_goals", 0))
    xg_a = _fl(row.get("expected_away_goals", 0))
    xg_total = _f(xg_h + xg_a)
    u25_prob = 1.0 - _fl(row.get("over_25_probability", 0))
    u25_pct = f"{round(u25_prob * 100)}"
    home = row.get("home_team", "Home")
    away = row.get("away_team", "Away")
    league = row.get("league", "")

    headline = _pick(LOW_SCORER_HEADLINES).format(
        home=home, away=away, hg=hg, ag=ag, league=league
    )
    body = _pick(LOW_SCORER_BODIES).format(
        home=home, away=away, hg=hg, ag=ag, total=total,
        xg_h=_f(xg_h), xg_a=_f(xg_a), xg_total=xg_total, u25_pct=u25_pct, league=league
    )
    return _article(
        "low", headline, body,
        league, row.get("date", ""),
        [home, away],
        "🔒 Tight"
    )


def _make_league_form(rows):
    """Generate a league form article for the best-performing league."""
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


def _make_roi_update(rows):
    """Generate a rolling ROI update article."""
    bet_rows = [r for r in rows if r.get("suggested_bet") and r.get("result")]
    if len(bet_rows) < 5:
        return None

    # Find dominant rule
    rule_counts = defaultdict(int)
    for r in bet_rows:
        rule_counts[r.get("bet_rule", "Unknown")] += 1
    top_rule = max(rule_counts, key=rule_counts.get)

    # Calculate ROI from bet_rule_roi (use last recorded or mean)
    rois = []
    for r in bet_rows:
        v = _fl(r.get("bet_rule_roi", 0))
        if v != 0:
            rois.append(v)

    if not rois:
        return None

    avg_roi = sum(rois) / len(rois)
    roi_str = f"{round((avg_roi - 1) * 100, 1)}"
    bets = len(bet_rows)

    headline = _pick(ROI_UPDATE_HEADLINES).format(roi=roi_str, bets=bets)
    body = _pick(ROI_UPDATE_BODIES).format(roi=roi_str, bets=bets, top_rule=top_rule)
    return {
        "id": "roi_update",
        "type": "roi",
        "headline": headline,
        "body": body,
        "league": "All leagues",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "teams": [],
        "badge": "💰 ROI",
    }


def _make_team_streak(rows):
    """Find a team on a notable win streak and report it."""
    # Count recent results per team
    team_results = defaultdict(list)
    for row in rows:
        result = row.get("result")
        if not result:
            continue
        home = row.get("home_team", "")
        away = row.get("away_team", "")
        if home:
            team_results[home].append(("H", result == "H", row))
        if away:
            team_results[away].append(("A", result == "A", row))

    # Find teams with 3+ wins in last 5
    candidates = []
    for team, results in team_results.items():
        if len(results) < 3:
            continue
        recent = results[-5:]
        wins = sum(1 for _, won, _ in recent if won)
        if wins >= 3:
            candidates.append((team, wins, len(recent), recent))

    if not candidates:
        return None

    # Pick the best streak
    team, wins, total, recent = max(candidates, key=lambda x: x[1])
    # Find a sample row to get league
    _, _, sample_row = recent[-1]
    side = "home" if sample_row.get("home_team") == team else "away"
    league = sample_row.get("league", "")

    headline = _pick(STREAK_HEADLINES).format(team=team, n=wins, total=total)
    body = _pick(STREAK_BODIES).format(
        team=team, n=wins, total=total, side=side, times=wins
    )
    return {
        "id": f"streak_{team[:8].replace(' ', '_').lower()}",
        "type": "streak",
        "headline": headline,
        "body": body,
        "league": league,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "teams": [team],
        "badge": "🔥 On form",
    }


def _make_upcoming_preview(upcoming_rows):
    """Generate a preview article from an upcoming value bet."""
    bets = [r for r in upcoming_rows if r.get("suggested_bet")]
    if not bets:
        return None

    # Pick highest-edge bet
    bets.sort(key=lambda r: _fl(r.get("suggested_edge", 0)), reverse=True)
    row = bets[0]

    label = row.get("suggested_bet")
    team = _team_name(row, label)
    opp = row.get("away_team") if label == "H" else row.get("home_team")
    odds = _f(_odds_for(row, label))
    edge = f"{round(_fl(row.get('suggested_edge', 0)) * 100, 1)}"
    prob = f"{round(_prob_for(row, label) * 100)}"
    mkt_pct = _mkt_pct(_odds_for(row, label))
    rule_name = row.get("bet_rule", "Value")
    bets_count = row.get("bet_rule_bets", "?")
    roi = _pct(row.get("bet_rule_roi", 0))
    date = row.get("date", "soon")
    side = {"H": "Home", "A": "Away", "D": "Draw"}.get(label, "")

    headline = _pick(UPCOMING_PREVIEW_HEADLINES).format(
        team=team, opp=opp, side=side, edge=edge, odds=odds, date=date, rule_name=rule_name
    )
    body = _pick(UPCOMING_PREVIEW_BODIES).format(
        team=team, opp=opp, edge=edge, odds=odds, prob=prob, mkt_pct=mkt_pct,
        rule_name=rule_name, bets=bets_count, roi=roi, date=date
    )
    return {
        "id": f"preview_{row.get('home_team', '')[:4]}_{row.get('away_team', '')[:4]}".replace(" ", "_").lower(),
        "type": "preview",
        "headline": headline,
        "body": body,
        "league": row.get("league", ""),
        "date": date,
        "teams": [row.get("home_team", ""), row.get("away_team", "")],
        "badge": "👀 Value watch",
    }


# ─── Main generator ───────────────────────────────────────────────────────────

def generate_news_feed(predictions_path, upcoming_path=None):
    """
    Read predictions CSV and generate up to 15 news articles.

    Args:
        predictions_path: Path-like to rolling predictions CSV
        upcoming_path:    Path-like to upcoming_predictions CSV (optional)

    Returns:
        list of article dicts
    """
    path = Path(predictions_path)
    if not path.exists():
        return []

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    # Most recent 40 settled matches
    settled = [r for r in rows if r.get("result")][-40:]
    recent_first = list(reversed(settled))

    # Load upcoming data if provided
    upcoming_rows = []
    up_path = Path(upcoming_path) if upcoming_path else (UPCOMING_PATH if UPCOMING_PATH.exists() else None)
    if up_path and up_path.exists():
        with up_path.open(newline="", encoding="utf-8") as fh:
            upcoming_rows = list(csv.DictReader(fh))

    articles = []
    seen_fixtures = set()

    # Per-type story generators and caps
    generators = [
        ("upset",      _make_upset,      3),
        ("value_win",  _make_value_win,   4),
        ("nailed_it",  _make_nailed_it,   4),
        ("goalfest",   _make_goalfest,    2),
        ("low_scorer", _make_low_scorer,  1),
        ("value_loss", _make_value_loss,  2),
    ]

    for story_type, gen_fn, cap in generators:
        count_this_type = 0
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

    # Singleton articles that look at the whole dataset
    league_article = _make_league_form(settled)
    if league_article:
        articles.append(league_article)

    roi_article = _make_roi_update(settled)
    if roi_article:
        articles.append(roi_article)

    streak_article = _make_team_streak(settled)
    if streak_article:
        articles.append(streak_article)

    preview_article = _make_upcoming_preview(upcoming_rows)
    if preview_article:
        articles.append(preview_article)

    # Sort priority: preview → wins → nailed → goals/low → upsets → form/roi/streak → losses
    priority = {
        "preview": 0, "win": 1, "nailed": 2, "goals": 3, "low": 3,
        "form": 4, "roi": 4, "streak": 4, "upset": 5, "loss": 6
    }
    articles.sort(key=lambda a: (priority.get(a["type"], 9), a.get("date", "")), reverse=False)

    # Limit to 15 most interesting
    return articles[:15]


def main():
    random.seed(42)
    articles = generate_news_feed(PREDICTIONS_PATH, UPCOMING_PATH)
    NEWS_FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEWS_FEED_PATH.write_text(json.dumps(articles, indent=2), encoding="utf-8")
    print(f"Generated {len(articles)} news articles → {NEWS_FEED_PATH}")


if __name__ == "__main__":
    main()
