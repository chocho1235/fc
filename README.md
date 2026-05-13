# Football Betting Model

Standalone multi-league betting dashboard and model for Premier League and La Liga.

It predicts:

- 1X2: home win, draw, away win
- Over 2.5 goals
- Value thresholds against bookmaker odds
- Injury and availability context from FPL snapshots
- Recent H2H form

## Local Run

```bash
npm install
npm run dev
```

Open the local URL shown by Vite.

## Refresh Upcoming Fixtures

Fast refresh, using the saved sklearn models:

```bash
python3 -m pip install -r football_ai_model/requirements.txt
python3 football_ai_model/src/refresh_upcoming.py
```

This downloads current fixtures, fetches FPL availability for upcoming Premier League matches, plus fixture coverage for supported leagues, updates `match_context.csv`, writes upcoming predictions, and exports `public/football-model-data.json`.

## Full Training

Only run this when changing features or adding historical data:

```bash
python3 football_ai_model/src/train_sklearn_1x2_model.py
python3 football_ai_model/src/refresh_upcoming.py
```

## GitHub Actions

`.github/workflows/refresh-football-model.yml` runs every 6 hours and commits refreshed fixture/context/prediction data back to the repository.

The workflow intentionally does not commit raw API cache snapshots, to avoid growing the repo by megabytes every run. The durable training context is stored in `football_ai_model/data/external/match_context.csv`.
