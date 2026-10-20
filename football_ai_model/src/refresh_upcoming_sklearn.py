from pathlib import Path

from download_data import main as download_results
from download_fixtures import main as download_fixtures
from ensemble_predictions import main as ensemble_predictions
from export_frontend_data import main as export_frontend_data
from generate_news import generate_news_feed
from import_fpl_context import import_upcoming_window
from predict_upcoming_sklearn_fast import main as predict_upcoming
from track_closing_lines import main as track_closing_lines
from train_dixon_coles import main as train_dixon_coles

_SRC = Path(__file__).resolve().parent
_PROCESSED = _SRC.parent / "data" / "processed"


def main():
    download_results(current_only=True)  # latest results for form, Elo and settling bets
    download_fixtures()
    try:
        import_upcoming_window(days=21, refresh_cache=True)
    except Exception as exc:  # injury notes are display-only, never block a refresh
        print(f"refresh: FPL context skipped ({exc})")
    predict_upcoming()
    train_dixon_coles(quick=True)   # fit DC params + predict upcoming; skip rolling backtest
    ensemble_predictions()          # add DC goal estimates to upcoming_predictions.csv
    track_closing_lines()           # log new bets, settle finished ones
    generate_news_feed(_PROCESSED / "sklearn_rolling_predictions.csv")
    print("refresh: news feed regenerated")
    export_frontend_data()  # picks up fresh news_feed.json


if __name__ == "__main__":
    main()
