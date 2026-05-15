from download_fixtures import main as download_fixtures
from ensemble_predictions import main as ensemble_predictions
from export_frontend_data import main as export_frontend_data
from import_fpl_context import import_upcoming_window
from predict_upcoming_sklearn_fast import main as predict_upcoming
from train_dixon_coles import main as train_dixon_coles


def main():
    download_fixtures()
    import_upcoming_window(days=21, refresh_cache=True)
    predict_upcoming()
    train_dixon_coles(quick=True)   # fit DC params + predict upcoming; skip rolling backtest
    ensemble_predictions()          # blend DC probs into upcoming_predictions.csv
    export_frontend_data()


if __name__ == "__main__":
    main()
