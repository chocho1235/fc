from download_fixtures import main as download_fixtures
from ensemble_predictions import main as ensemble_predictions
from export_frontend_data import main as export_frontend_data
from generate_news import generate_news_feed
from import_fpl_context import import_upcoming_window
from predict_upcoming_sklearn_fast import main as predict_upcoming
from train_dixon_coles import main as train_dixon_coles
from train_1x2_model import PROCESSED_DIR


def main():
    download_fixtures()
    import_upcoming_window(days=21, refresh_cache=True)
    predict_upcoming()
    train_dixon_coles(quick=True)   # fit DC params + predict upcoming; skip rolling backtest
    ensemble_predictions()          # blend DC probs into upcoming_predictions.csv
    generate_news_feed(PROCESSED_DIR / "sklearn_rolling_predictions.csv")
    print("refresh: news feed regenerated")
    export_frontend_data()  # picks up fresh news_feed.json


if __name__ == "__main__":
    main()
