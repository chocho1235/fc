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

Fast refresh, using the saved models:

```bash
python3 -m pip install -r football_ai_model/requirements.txt
python3 football_ai_model/src/refresh_upcoming_sklearn.py
```

This downloads the current season's results, builds the fixture list with the best odds it can find (see below), writes upcoming predictions, logs new bets and settles finished ones, and exports `public/football-model-data.json`.

### Where fixtures and odds come from

`download_fixtures.py` merges three sources and maps every team name onto football-data's spelling (`team_names.py`):

- **ESPN's public scoreboard API**: free, no key. Fixtures for every league, plus one bookmaker's prices where ESPN shows them.
- **football-data.co.uk `fixtures.csv`**: free, no key. Only covers a few days but includes Pinnacle, market-average and best prices.
- **The Odds API**: optional. If the `ODDS_API_KEY` repo secret is set, prices from many bookmakers are added. It is called at most every 20 hours (about 400 requests a month), so the free plan is enough.

`data/external/fixture_sources.json` shows what each source returned on the last run, including any team names that could not be matched.

## The model

- LightGBM that starts from the bookmaker's no-vig probabilities and only learns corrections (`market_anchored.py`). Closing odds are not used, since they aren't known when a bet is placed.
- A bet is suggested when model probability × best available price is at least 5% above break even (`BET_THRESHOLD`).
- The backtest is walk-forward: each season is predicted by a model trained only on earlier seasons. `reports/backtest_summary.txt` shows the model's log loss next to the bookmaker's. If the model's number isn't lower, it isn't beating the market.
- Live bets are logged in `data/external/odds_snapshots.csv` and settled in `data/processed/closing_line_report.csv`, including closing line value (the price we took vs Pinnacle's closing price).

## Line-up watch (being faster than the bookies)

Official line-ups come out about an hour before kick-off. In smaller leagues prices often take a while to react, so knowing that regulars are missing can be worth something.

- `lineups.py` saves every match's starting XI from ESPN (free, no key) into `data/external/lineups/`, plus ESPN's opening and closing odds.
- `lineup_features.py` works out each team's usual XI from recent games and scores a line-up: share of the usual XI starting, regulars missing, keeper changed, changes from last game.
- `train_lineup_layer.py` learns how much those should move the pre-line-up price and tests it season by season (`reports/lineup_layer_report.txt`). If it doesn't beat the pre-line-up price, the watcher only logs and never suggests bets.
- `lineup_watch.py` runs every 10 minutes (`.github/workflows/lineup-watch.yml`). When line-ups appear for a game kicking off soon, it re-prices it and compares with the current price. Value is logged as a "line-up" bet and tracked against the closing price like every other bet.

**Phone alerts (optional):** install the free ntfy app, subscribe to a topic name only you know (e.g. `fc-alerts-8k2m9q`), and save the same name as the `NTFY_TOPIC` repository secret. GitHub's scheduler can start runs a few minutes late, so for the fastest alerts run `python3 football_ai_model/src/lineup_watch.py` every few minutes on your own computer.

## Full Training

Only run this when changing features or adding historical data:

```bash
python3 football_ai_model/src/download_data.py
python3 football_ai_model/src/train_sklearn_1x2_model.py
python3 football_ai_model/src/refresh_upcoming_sklearn.py
```

## GitHub Actions

`.github/workflows/refresh-football-model.yml` refreshes every 6 hours and retrains every Sunday at 03:00 UTC, committing the results back to the repository. Trigger it by hand from the Actions tab, ticking "Force full model retrain" for a retrain.

The workflow intentionally does not commit raw API cache snapshots, to avoid growing the repo by megabytes every run.
