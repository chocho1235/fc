# Football AI 1X2 Model

First version of a football prediction model for:

- Home win
- Draw
- Away win

It uses historical Premier League and La Liga CSV data from football-data.co.uk and builds pre-match features from previous results, head-to-heads, rest days, and bookmaker odds where available.

## Quick Start

Full historical training/backtest:

```bash
cd football_ai_model
python3 src/download_data.py
python3 src/train_1x2_model.py
python3 src/export_frontend_data.py
```

Daily/fast upcoming refresh after the model has already been trained:

```bash
python3 src/refresh_upcoming.py
```

That fast path downloads the latest fixture feed, stores a fresh FPL availability snapshot for the next 21 days of Premier League fixtures, predicts upcoming matches from the saved model, and refreshes the website JSON. It does not rerun the rolling historical backtest.

The training script writes:

- `data/processed/predictions.csv`
- `reports/backtest_summary.txt`
- `models/one_x_two_model.json`

## What The First Model Uses

For each match, the model only uses information available before kickoff:

- Home and away recent form over 5 matches
- Home and away recent form over 10 matches
- Recent goals scored and conceded
- Head-to-head record before the match
- Rest days
- Home advantage
- Bookmaker implied probabilities from average odds, if present
- Market movement from opening to closing odds, if present
- Pre-match league-table importance signals
- External context from `data/external/match_context.csv`

## External Match Context

`data/external/match_context.csv` lets the model use things football-data.co.uk does not provide:

- Injuries
- Key absences
- Suspensions
- Expected lineup strength
- Rotation risk
- Derby/rivalry flag
- Motivation
- Weather severity
- Away travel fatigue

Use values known before kickoff. Keep numeric columns on a 0-1 scale where possible. For example, lineup strength should be `1.00` for full strength and lower when weakened.

## Importers

Weather works without an API key:

```bash
python3 src/import_weather_context.py --season 2526
```

Sportmonks requires an API token:

```bash
export SPORTMONKS_API_TOKEN="your-token"
python3 src/import_sportmonks_context.py --date 2026-05-12
```

API-Football free historical backfill, cached and request-capped:

```bash
export APIFOOTBALL_API_KEY="your-token"
python3 src/import_api_football_historical.py --seasons 2022 2023 2024 --max-network-requests 90 --sleep-seconds 6.5
```

The free plan is usually limited to selected historical seasons. The importer caches every JSON response in
`data/external/api_cache/api_football/`, so reruns skip already downloaded data and continue the backfill in batches.

After any context import, rerun:

```bash
python3 src/refresh_upcoming.py
```

Only rerun `train_1x2_model.py` when you add a new completed season, change model features, or deliberately want a fresh backtest.

## 24/7 Injury Snapshot Workflow

FPL does not provide exact historical match-by-match injuries. The safe approach is to collect snapshots from now onward:

```bash
python3 src/refresh_upcoming.py
```

Each run:

1. Downloads future fixtures.
2. Fetches current FPL player availability.
3. Saves the raw FPL snapshot in `data/external/api_cache/fpl/`.
4. Writes upcoming fixture context to `data/external/match_context.csv`.
5. Predicts upcoming matches using the saved sklearn models.
6. Exports `public/football-model-data.json` for the frontend.

Do not train past matches with injury data that was only known later. Once a fixture has been played, the pre-match snapshot already stored in `match_context.csv` can be used as historical context for future model training.

## Current Scope

This is intentionally narrow: Premier League and La Liga 1X2/Over 2.5. Once this is working, the next improvements are:

1. Add over/under 2.5 goals.
2. Add more leagues.
3. Add xG data.
4. Add injury and lineup data.
5. Build a dashboard for upcoming fixtures and value bets.
